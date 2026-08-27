"""Разнос платежей по начислениям и определение статусов (чистые функции)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.db.enums import ChargeStatus
from app.domain.money import money


@dataclass(frozen=True)
class ChargeOutstanding:
    """Начисление к погашению: id и остаток (amount - paid_amount)."""

    charge_id: int
    outstanding: Decimal


@dataclass(frozen=True)
class Allocation:
    """Результат разноса: сколько денег отнесено на конкретное начисление."""

    charge_id: int
    amount: Decimal


@dataclass(frozen=True)
class AllocationResult:
    allocations: list[Allocation]
    leftover: Decimal  # нераспределённый остаток платежа (переплата)


def allocate_payment(amount: Decimal | int | float, charges: list[ChargeOutstanding]) -> AllocationResult:
    """Распределяет сумму платежа по начислениям по порядку (FIFO).

    Порядок списка = приоритет погашения (например: старые периоды, затем аренда,
    электричество, пеня). Поддерживает частичную оплату и переплату (leftover).
    """
    remaining = money(amount)
    if remaining < 0:
        raise ValueError("Сумма платежа не может быть отрицательной")

    allocations: list[Allocation] = []
    for ch in charges:
        if remaining <= 0:
            break
        outstanding = money(ch.outstanding)
        if outstanding <= 0:
            continue
        take = min(remaining, outstanding)
        allocations.append(Allocation(charge_id=ch.charge_id, amount=take))
        remaining -= take

    return AllocationResult(allocations=allocations, leftover=money(remaining))


def charge_status(
    amount: Decimal | int | float,
    paid_amount: Decimal | int | float,
    due_date: date | None,
    today: date,
    *,
    already_sent: bool = False,
) -> ChargeStatus:
    """Статус начисления по оплате и сроку.

    - оплачено полностью -> paid
    - оплачено частично  -> partial
    - не оплачено и срок прошёл -> overdue
    - иначе -> sent (если документ отправлен) либо issued
    """
    amount = money(amount)
    paid = money(paid_amount)

    if paid >= amount and amount > 0:
        return ChargeStatus.paid
    if paid > 0:
        return ChargeStatus.partial
    if due_date is not None and today > due_date:
        return ChargeStatus.overdue
    return ChargeStatus.sent if already_sent else ChargeStatus.issued


def shortfall(amount: Decimal | int | float, paid_amount: Decimal | int | float) -> Decimal:
    """Недоплата (сколько ещё должен арендатор). 0, если оплачено полностью/сверх."""
    diff = money(amount) - money(paid_amount)
    return diff if diff > 0 else money(0)
