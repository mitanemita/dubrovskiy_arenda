"""Денежные хелперы: округление до копеек."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")


def money(value: Decimal | int | float | str) -> Decimal:
    """Приводит значение к денежному Decimal с округлением до копеек (полукруг вверх)."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)
