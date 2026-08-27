"""Сервис начислений: создание charges по аренде, электричеству и пене."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import ChargeStatus, ChargeType
from app.db.models import Charge, Lease, Meter, MeterReading, Tenant
from app.domain import billing
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


async def create_electricity_charge(session: AsyncSession, lease: Lease, period: date) -> Charge | None:
    """Начисление за электроэнергию по показаниям всех счётчиков помещения за период.

    Возвращает None, если за период нет ни одного показания (данных ещё нет —
    вызывающий код уведомит арендодателя, требование ТЗ п.10).
    """
    period = period_start(period)
    existing = await _existing_charge(session, lease.id, ChargeType.electricity, period)
    if existing:
        return existing

    landlord_id = await _landlord_id_for_lease(session, lease)
    tariff = await settings_service.get_decimal(session, landlord_id, "electricity_tariff")
    default_coeff = await settings_service.get_decimal(session, landlord_id, "electricity_coeff")

    meters = (
        await session.execute(select(Meter).where(Meter.premises_id == lease.premises_id))
    ).scalars().all()

    total = Decimal("0")
    found_any = False
    for meter in meters:
        reading = (
            await session.execute(
                select(MeterReading).where(
                    MeterReading.meter_id == meter.id, MeterReading.period == period
                )
            )
        ).scalars().first()
        if reading is None:
            continue
        found_any = True
        coeff = meter.coefficient if meter.coefficient is not None else default_coeff
        total += billing.electricity_amount(reading.consumption, tariff, coeff)

    if not found_any:
        return None

    due = billing.payment_due_date(period, lease.payment_day)
    charge = Charge(
        lease_id=lease.id,
        type=ChargeType.electricity,
        period=period,
        amount=total,
        due_date=due,
        status=ChargeStatus.draft,
        description=f"Электроэнергия за {period.strftime('%m.%Y')}",
    )
    session.add(charge)
    return charge


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

    Вызывается ежедневно при просрочке. Пеня пересчитывается на текущую дату.
    """
    period = period_start(period)
    base = await outstanding_principal(session, lease.id, period)
    if base <= 0:
        return None

    due = billing.payment_due_date(period, lease.payment_day)
    days = billing.days_overdue(due, today)
    if days <= 0:
        return None

    amount = billing.penalty_amount(base, lease.penalty_rate, days)

    charge = await _existing_charge(session, lease.id, ChargeType.penalty, period)
    if charge is None:
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
