"""Отчёты для бота: платежи по помещениям, электричество."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import ChargeType, PaymentStatus
from app.db.models import Charge, Lease, Meter, MeterReading, Payment, Premises
from app.services.billing_service import period_start


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
