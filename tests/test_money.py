"""Тесты денежных хелперов."""
from decimal import Decimal

from app.domain.money import money


def test_money_rounding():
    assert money(Decimal("10.005")) == Decimal("10.01")  # полукруг вверх
    assert money("1680") == Decimal("1680.00")
    assert money(120 * 14) == Decimal("1680.00")
