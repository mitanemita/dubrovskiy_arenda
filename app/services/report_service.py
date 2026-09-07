"""Отчёты для бота: платежи по помещениям, электричество."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import ChargeType, LeaseStatus, PaymentStatus
from app.db.models import Charge, Lease, Meter, MeterReading, Payment, Premises, Tenant
from app.services.billing_service import period_start


_CHARGE_TYPE_RU = {
    ChargeType.rent: "аренда",
    ChargeType.electricity: "электричество",
    ChargeType.penalty: "пеня",
    ChargeType.other: "прочее",
}


async def tenant_payment_status(session: AsyncSession, landlord_id: int, period: date) -> dict:
    """По каждому активному договору за период: начислено / оплачено / долг по типам / телефон.

    Возвращает {'rows': [...], 'paid': N, 'unpaid': M, 'total': K, 'total_debt': Decimal}.
    """
    period = period_start(period)
    leases = (await session.execute(
        select(Lease.id, Tenant.name, Tenant.phone, Premises.label)
        .join(Tenant, Tenant.id == Lease.tenant_id)
        .join(Premises, Premises.id == Lease.premises_id)
        .where(Tenant.landlord_id == landlord_id, Lease.status == LeaseStatus.active)
        .order_by(Tenant.name)
    )).all()

    rows: list[dict] = []
    paid_count = 0
    total_debt = Decimal("0")
    for lease_id, tenant_name, tenant_phone, premises_label in leases:
        charges = (await session.execute(
            select(Charge.type, Charge.amount, Charge.paid_amount).where(
                Charge.lease_id == lease_id, Charge.period == period
            )
        )).all()
        charged = sum((a for _, a, _ in charges), Decimal("0"))
        paid = sum((p for _, _, p in charges), Decimal("0"))
        debt = charged - paid
        # Что именно не оплачено — по типам начислений
        unpaid_parts = []
        for ctype, amount, paid_amount in charges:
            rem = amount - paid_amount
            if rem > 0:
                unpaid_parts.append(f"{_CHARGE_TYPE_RU.get(ctype, ctype.value)} {rem} ₽")
        is_paid = charged > 0 and debt <= 0
        if is_paid:
            paid_count += 1
        if debt > 0:
            total_debt += debt
        rows.append({
            "tenant": tenant_name, "phone": tenant_phone, "premises": premises_label,
            "charged": charged, "paid": paid, "debt": debt if debt > 0 else Decimal("0"),
            "is_paid": is_paid, "has_charges": charged > 0,
            "unpaid_detail": ", ".join(unpaid_parts),
        })
    return {
        "rows": rows,
        "paid": paid_count,
        "unpaid": len(rows) - paid_count,
        "total": len(rows),
        "total_debt": total_debt,
    }


async def monthly_summary(session: AsyncSession, landlord_id: int, period: date) -> dict:
    """Сводка за месяц: доход, расход, электричество, число должников."""
    from app.db.models import Expense

    period = period_start(period)
    # Доход = оплачено по начислениям за период
    income = (await session.execute(
        select(func.coalesce(func.sum(Charge.paid_amount), 0))
        .join(Lease, Lease.id == Charge.lease_id)
        .join(Tenant, Tenant.id == Lease.tenant_id)
        .where(Tenant.landlord_id == landlord_id, Charge.period == period)
    )).scalar_one()
    # Электричество (начислено) за период
    elec = (await session.execute(
        select(func.coalesce(func.sum(Charge.amount), 0))
        .join(Lease, Lease.id == Charge.lease_id)
        .join(Tenant, Tenant.id == Lease.tenant_id)
        .where(Tenant.landlord_id == landlord_id, Charge.period == period,
               Charge.type == ChargeType.electricity)
    )).scalar_one()
    # Расходы за период
    expense = (await session.execute(
        select(func.coalesce(func.sum(Expense.amount), 0))
        .where(Expense.landlord_id == landlord_id, Expense.period == period)
    )).scalar_one()
    status = await tenant_payment_status(session, landlord_id, period)
    return {
        "income": Decimal(str(income)),
        "expense": Decimal(str(expense)),
        "electricity": Decimal(str(elec)),
        "debtors": status["unpaid"],
        "total_debt": status["total_debt"],
        "leases": status["total"],
    }


async def payments_by_premises(session: AsyncSession, landlord_id: int) -> list[dict]:
    """Подтверждённые платежи в разрезе помещений."""
    result = await session.execute(
        select(Premises.label, Payment.amount, Payment.status)
        .join(Lease, Lease.premises_id == Premises.id)
        .join(Payment, Payment.lease_id == Lease.id)
        .where(Premises.landlord_id == landlord_id)
    )
    totals: dict[str, Decimal] = {}
    for label, amount, status in result.all():
        if status == PaymentStatus.confirmed or status == PaymentStatus.partial:
            totals[label] = totals.get(label, Decimal("0")) + amount
    return [{"premises": k, "confirmed_total": v} for k, v in sorted(totals.items())]


async def electricity_summary(session: AsyncSession, landlord_id: int, period: date) -> list[dict]:
    """Расход и начисление по электричеству за период в разрезе помещений."""
    period = period_start(period)
    # Показания по помещениям
    readings = await session.execute(
        select(Premises.label, MeterReading.consumption)
        .join(Meter, Meter.premises_id == Premises.id)
        .join(MeterReading, MeterReading.meter_id == Meter.id)
        .where(Premises.landlord_id == landlord_id, MeterReading.period == period)
    )
    consumption: dict[str, Decimal] = {}
    for label, cons in readings.all():
        consumption[label] = consumption.get(label, Decimal("0")) + cons

    # Начисления электричества по помещениям
    charges = await session.execute(
        select(Premises.label, Charge.amount)
        .join(Lease, Lease.premises_id == Premises.id)
        .join(Charge, Charge.lease_id == Lease.id)
        .where(
            Premises.landlord_id == landlord_id,
            Charge.type == ChargeType.electricity,
            Charge.period == period,
        )
    )
    amounts: dict[str, Decimal] = {}
    for label, amount in charges.all():
        amounts[label] = amounts.get(label, Decimal("0")) + amount

    labels = sorted(set(consumption) | set(amounts))
    return [
        {
            "premises": label,
            "consumption_kwh": consumption.get(label, Decimal("0")),
            "amount": amounts.get(label, Decimal("0")),
        }
        for label in labels
    ]
