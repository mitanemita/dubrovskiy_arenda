"""Отчёты для бота: платежи по помещениям, электричество."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import ChargeType, LeaseStatus, PaymentStatus
from app.db.models import Charge, Lease, Meter, MeterReading, Payment, Premises, Tenant
from app.services.billing_service import period_start


async def tenant_payment_status(session: AsyncSession, landlord_id: int, period: date) -> dict:
    """По каждому активному договору за период: начислено / оплачено / долг / статус.

    Возвращает {'rows': [...], 'paid': N, 'unpaid': M, 'total_debt': Decimal}.
    """
    period = period_start(period)
    leases = (await session.execute(
        select(Lease.id, Tenant.name, Premises.label)
        .join(Tenant, Tenant.id == Lease.tenant_id)
        .join(Premises, Premises.id == Lease.premises_id)
        .where(Tenant.landlord_id == landlord_id, Lease.status == LeaseStatus.active)
        .order_by(Tenant.name)
    )).all()

    rows: list[dict] = []
    paid_count = 0
    total_debt = Decimal("0")
    for lease_id, tenant_name, premises_label in leases:
        charges = (await session.execute(
            select(Charge.amount, Charge.paid_amount).where(
                Charge.lease_id == lease_id, Charge.period == period
            )
        )).all()
        charged = sum((a for a, _ in charges), Decimal("0"))
        paid = sum((p for _, p in charges), Decimal("0"))
        debt = charged - paid
        is_paid = charged > 0 and debt <= 0
        if is_paid:
            paid_count += 1
        if debt > 0:
            total_debt += debt
        rows.append({
            "tenant": tenant_name, "premises": premises_label,
            "charged": charged, "paid": paid, "debt": debt if debt > 0 else Decimal("0"),
            "is_paid": is_paid, "has_charges": charged > 0,
        })
    return {
        "rows": rows,
        "paid": paid_count,
        "unpaid": len(rows) - paid_count,
        "total": len(rows),
        "total_debt": total_debt,
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
