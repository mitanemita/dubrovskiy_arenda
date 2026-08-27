"""Тесты расчётов начислений: электричество, пеня, срок оплаты, просрочка."""
from datetime import date
from decimal import Decimal

import pytest

from app.domain import billing


def test_electricity_without_coefficient():
    # 120 кВт·ч × 14 ₽ = 1680.00
    assert billing.electricity_amount(120, 14) == Decimal("1680.00")


def test_electricity_with_coefficient():
    # 120 × 14 × 0.93 = 1562.40
    assert billing.electricity_amount(120, 14, Decimal("0.93")) == Decimal("1562.40")


def test_consumption_from_readings():
    assert billing.consumption_from_readings(15230, 15350) == Decimal("120.00")


def test_consumption_negative_raises():
    with pytest.raises(ValueError):
        billing.consumption_from_readings(15350, 15230)


def test_payment_due_date_normal():
    assert billing.payment_due_date(date(2026, 4, 1), 5) == date(2026, 4, 5)


def test_payment_due_date_clamped_to_month_end():
    # payment_day=31 в феврале -> 28.02
    assert billing.payment_due_date(date(2026, 2, 1), 31) == date(2026, 2, 28)


def test_days_overdue():
    assert billing.days_overdue(date(2026, 5, 5), date(2026, 5, 12)) == 7
    assert billing.days_overdue(date(2026, 5, 5), date(2026, 5, 5)) == 0
    assert billing.days_overdue(date(2026, 5, 5), date(2026, 5, 1)) == 0  # срок не наступил


def test_penalty_on_full_amount():
    # (50000 + 1680) × 0.5% × 7 дней = 1808.80  (пеня на всю сумму)
    base = Decimal("51680.00")
    assert billing.penalty_amount(base, Decimal("0.5"), 7) == Decimal("1808.80")


def test_penalty_zero_when_not_overdue():
    assert billing.penalty_amount(51680, Decimal("0.5"), 0) == Decimal("0.00")
