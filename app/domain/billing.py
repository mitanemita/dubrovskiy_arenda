"""Чистые расчёты начислений: аренда, электричество, пеня, просрочка."""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from app.domain.money import money


def electricity_amount(
    consumption: Decimal | int | float,
    tariff: Decimal | int | float,
    coefficient: Decimal | int | float | None = None,
) -> Decimal:
    """Стоимость электроэнергии: расход × тариф × коэффициент (если задан).

    consumption — кВт·ч (curr - prev), tariff — ₽/кВт·ч,
    coefficient — необязательный множитель (напр. 0.93 для УСН).
    """
    total = Decimal(str(consumption)) * Decimal(str(tariff))
    if coefficient is not None:
        total *= Decimal(str(coefficient))
    return money(total)


def consumption_from_readings(prev_value: Decimal | float, curr_value: Decimal | float) -> Decimal:
    """Расход по показаниям счётчика. Отрицательный расход недопустим."""
    prev = Decimal(str(prev_value))
    curr = Decimal(str(curr_value))
    diff = curr - prev
    if diff < 0:
        raise ValueError("Текущие показания меньше предыдущих — проверьте данные счётчика")
    return money(diff)


def payment_due_date(period: date, payment_day: int) -> date:
    """Срок оплаты в месяце периода по дню оплаты договора (с учётом длины месяца)."""
    if not 1 <= payment_day <= 31:
        raise ValueError("payment_day должен быть в диапазоне 1..31")
    last_day = calendar.monthrange(period.year, period.month)[1]
    day = min(payment_day, last_day)
    return date(period.year, period.month, day)


def days_overdue(due_date: date, today: date) -> int:
    """Число дней просрочки (0, если срок не наступил)."""
    delta = (today - due_date).days
    return max(delta, 0)


def penalty_amount(
    base_amount: Decimal | int | float,
    penalty_rate_percent: Decimal | int | float,
    days: int,
) -> Decimal:
    """Пеня = база × (ставка% / 100) × дни. База — вся неоплаченная сумма (аренда+коммуналка)."""
    if days <= 0:
        return money(0)
    base = Decimal(str(base_amount))
    rate = Decimal(str(penalty_rate_percent)) / Decimal("100")
    return money(base * rate * Decimal(days))
