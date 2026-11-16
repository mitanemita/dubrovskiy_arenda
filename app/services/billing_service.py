"""Сервис начислений: создание charges по аренде, электричеству и пене."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import ChargeStatus, ChargeType, LeaseStatus
from app.db.models import Charge, Lease, Meter, MeterReading, Tenant
from app.domain import billing
from app.domain.allocation import charge_status
from app.services import settings_service


async def _landlord_id_for_lease(session: AsyncSession, lease: Lease) -> int:
    """landlord_id по договору без ленивой подгрузки связей (безопасно для async)."""
    result = await session.execute(select(Tenant.landlord_id).where(Tenant.id == lease.tenant_id))
    return result.scalar_one()


def period_start(day: date) -> date:
    """Нормализует дату к первому числу месяца (период = месяц)."""
    return date(day.year, day.month, 1)


async def _existing_charge(
    session: AsyncSession, lease_id: int, charge_type: ChargeType, period: date
) -> Charge | None:
    result = await session.execute(
        select(Charge).where(
            Charge.lease_id == lease_id,
            Charge.type == charge_type,
            Charge.period == period,
        )
    )
    return result.scalars().first()


async def create_rent_charge(session: AsyncSession, lease: Lease, period: date) -> Charge:
    """Начисление аренды за период (идемпотентно по lease+type+period)."""
    period = period_start(period)
    existing = await _existing_charge(session, lease.id, ChargeType.rent, period)
    if existing:
        return existing

    due = billing.payment_due_date(period, lease.payment_day)
    charge = Charge(
        lease_id=lease.id,
        type=ChargeType.rent,
        period=period,
        amount=lease.rent_amount,
        due_date=due,
        status=ChargeStatus.draft,
        description=f"Аренда за {period.strftime('%m.%Y')}",
    )
    session.add(charge)
    return charge


async def _electricity_total(session: AsyncSession, lease: Lease, period: date) -> Decimal | None:
    """Стоимость электроэнергии по показаниям всех счётчиков помещения за период.

    None — если за период нет ни одного показания.
    """
    landlord_id = await _landlord_id_for_lease(session, lease)
    tariff = await settings_service.get_decimal(session, landlord_id, "electricity_tariff")
    default_coeff = await settings_service.get_decimal(session, landlord_id, "electricity_coeff")

    rows = (
        await session.execute(
            select(Meter.coefficient, MeterReading.consumption)
            .join(MeterReading, MeterReading.meter_id == Meter.id)
            .where(Meter.premises_id == lease.premises_id, MeterReading.period == period)
        )
    ).all()
    if not rows:
        return None
    total = Decimal("0")
    for coefficient, consumption in rows:
        coeff = coefficient if coefficient is not None else default_coeff
        total += billing.electricity_amount(consumption, tariff, coeff)
    return total


async def readings_missing(session: AsyncSession, lease: Lease, period: date) -> bool:
    """Есть ли у помещения договора счётчик без показаний за период."""
    period = period_start(period)
    meter_ids = (
        await session.execute(select(Meter.id).where(Meter.premises_id == lease.premises_id))
    ).scalars().all()
    if not meter_ids:
        return False
    with_reading = (
        await session.execute(
            select(MeterReading.meter_id).where(
                MeterReading.meter_id.in_(meter_ids), MeterReading.period == period
            )
        )
    ).scalars().all()
    return len(set(with_reading)) < len(meter_ids)


async def create_electricity_charge(session: AsyncSession, lease: Lease, period: date) -> Charge | None:
    """Начисление за электроэнергию за период (идемпотентно: существующее не меняется).

    Возвращает None, если за период нет ни одного показания или расход нулевой
    (например, введено только начальное показание счётчика).
    """
    period = period_start(period)
    existing = await _existing_charge(session, lease.id, ChargeType.electricity, period)
    if existing:
        return existing

    total = await _electricity_total(session, lease, period)
    if total is None or total <= 0:
        return None

    charge = Charge(
        lease_id=lease.id,
        type=ChargeType.electricity,
        period=period,
        amount=total,
        due_date=billing.payment_due_date(period, lease.payment_day),
        status=ChargeStatus.draft,
        description=f"Электроэнергия за {period.strftime('%m.%Y')}",
    )
    session.add(charge)
    return charge


async def sync_electricity_charges(
    session: AsyncSession, premises_id: int, period: date, today: date
) -> list[Charge]:
    """После ввода/исправления показаний: создаёт или пересчитывает начисление
    электричества за период по активным договорам помещения (за любой месяц)."""
    period = period_start(period)
    leases = (
        await session.execute(
            select(Lease).where(Lease.premises_id == premises_id, Lease.status == LeaseStatus.active)
        )
    ).scalars().all()
    charges: list[Charge] = []
    for lease in leases:
        charge = await _existing_charge(session, lease.id, ChargeType.electricity, period)
        if charge is None:
            charge = await create_electricity_charge(session, lease, period)
        else:
            total = await _electricity_total(session, lease, period)
            if total is not None and total != charge.amount:
                charge.amount = total
                charge.status = charge_status(
                    charge.amount, charge.paid_amount, charge.due_date, today, already_sent=True
                )
        if charge is not None:
            charges.append(charge)
    return charges


async def outstanding_principal(session: AsyncSession, lease_id: int, period: date) -> Decimal:
    """Неоплаченная сумма основного долга (аренда+электричество, без пени) за период."""
    period = period_start(period)
    result = await session.execute(
        select(Charge).where(
            Charge.lease_id == lease_id,
            Charge.period == period,
            Charge.type.in_([ChargeType.rent, ChargeType.electricity]),
        )
    )
    total = Decimal("0")
    for ch in result.scalars().all():
        total += (ch.amount - ch.paid_amount)
    return total if total > 0 else Decimal("0")


async def upsert_penalty_charge(
    session: AsyncSession, lease: Lease, period: date, today: date
) -> Charge | None:
    """Создаёт/пересчитывает пеню за период на всю неоплаченную сумму.

    Вызывается ежедневно при просрочке. Пеня пересчитывается на текущую дату
    по ставке из настроек (penalty_rate, %/день).
    """
    period = period_start(period)
    base = await outstanding_principal(session, lease.id, period)
    if base <= 0:
        return None

    due = billing.payment_due_date(period, lease.payment_day)
    days = billing.days_overdue(due, today)
    if days <= 0:
        return None

    landlord_id = await _landlord_id_for_lease(session, lease)
    rate = await settings_service.get_decimal(session, landlord_id, "penalty_rate")
    amount = billing.penalty_amount(base, rate, days)

    charge = await _existing_charge(session, lease.id, ChargeType.penalty, period)
    if charge is None:
        if amount <= 0:  # ставка пени 0 — пеню не начисляем
            return None
        charge = Charge(
            lease_id=lease.id,
            type=ChargeType.penalty,
            period=period,
            amount=amount,
            due_date=today,
            status=ChargeStatus.issued,
            description=f"Пеня за просрочку ({days} дн.) за {period.strftime('%m.%Y')}",
        )
        session.add(charge)
    else:
        # Пересчёт: пеня не может стать меньше уже оплаченной части
        charge.amount = amount
        charge.description = f"Пеня за просрочку ({days} дн.) за {period.strftime('%m.%Y')}"
    return charge
