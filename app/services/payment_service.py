"""Сервис платежей: регистрация, подтверждение/отклонение, разнос по начислениям."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import ChargeType, DataSource, PaymentStatus
from app.db.models import Charge, Payment, PaymentAllocation
from app.domain.allocation import ChargeOutstanding, allocate_payment, charge_status

# Приоритет погашения начислений внутри периода
_TYPE_ORDER = {ChargeType.penalty: 0, ChargeType.rent: 1, ChargeType.electricity: 2, ChargeType.other: 3}


async def register_payment(
    session: AsyncSession,
    lease_id: int,
    amount: Decimal,
    *,
    period: date | None = None,
    payment_date: date | None = None,
    proof_file: str | None = None,
    source: DataSource = DataSource.manual,
) -> Payment:
    """Создаёт платёж в статусе pending (ожидает подтверждения арендодателя)."""
    payment = Payment(
        lease_id=lease_id,
        amount=amount,
        period=period,
        payment_date=payment_date,
        proof_file=proof_file,
        source=source,
        status=PaymentStatus.pending,
    )
    session.add(payment)
    return payment


async def _outstanding_charges(session: AsyncSession, lease_id: int) -> list[Charge]:
    """Начисления с остатком > 0, в порядке погашения: период ↑, тип по приоритету."""
    result = await session.execute(
        select(Charge).where(Charge.lease_id == lease_id).order_by(Charge.period)
    )
    charges = [c for c in result.scalars().all() if (c.amount - c.paid_amount) > 0]
    charges.sort(key=lambda c: (c.period, _TYPE_ORDER.get(c.type, 9)))
    return charges


async def confirm_payment(
    session: AsyncSession, payment: Payment, confirmed_by_id: int | None, today: date
) -> dict:
    """Подтверждает платёж, разносит его по начислениям и обновляет их статусы.

    Возвращает сводку: разнесено, переплата, оставшаяся недоплата по начислениям.
    """
    charges = await _outstanding_charges(session, payment.lease_id)
    outstanding = [
        ChargeOutstanding(charge_id=c.id, outstanding=(c.amount - c.paid_amount)) for c in charges
    ]
    result = allocate_payment(payment.amount, outstanding)

    by_id = {c.id: c for c in charges}
    for alloc in result.allocations:
        charge = by_id[alloc.charge_id]
        charge.paid_amount = charge.paid_amount + alloc.amount
        charge.status = charge_status(
            charge.amount, charge.paid_amount, charge.due_date, today, already_sent=True
        )
        session.add(PaymentAllocation(payment_id=payment.id, charge_id=charge.id, amount=alloc.amount))

    total_allocated = payment.amount - result.leftover
    # Частичный платёж, если он не закрыл все начисления полностью
    remaining_debt = sum((c.amount - c.paid_amount) for c in charges)
    payment.status = PaymentStatus.partial if remaining_debt > 0 else PaymentStatus.confirmed
    payment.confirmed_by_id = confirmed_by_id
    payment.confirmed_at = datetime.now()

    return {
        "allocated": total_allocated,
        "leftover": result.leftover,
        "remaining_debt": remaining_debt if remaining_debt > 0 else Decimal("0"),
        "fully_paid": remaining_debt <= 0,
    }


async def reject_payment(session: AsyncSession, payment: Payment, rejected_by_id: int | None) -> None:
    """Отклоняет платёж (деньги не пришли / не подтверждены)."""
    payment.status = PaymentStatus.rejected
    payment.confirmed_by_id = rejected_by_id
    payment.confirmed_at = datetime.now()
