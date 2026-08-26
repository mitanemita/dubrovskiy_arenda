"""Тесты разноса платежей и статусов начислений (частичная/полная/переплата)."""
from datetime import date
from decimal import Decimal

from app.db.enums import ChargeStatus
from app.domain.allocation import (
    ChargeOutstanding,
    allocate_payment,
    charge_status,
    shortfall,
)

RENT = ChargeOutstanding(charge_id=1, outstanding=Decimal("50000.00"))
ELEC = ChargeOutstanding(charge_id=2, outstanding=Decimal("1680.00"))


def test_full_payment_covers_all():
    res = allocate_payment(Decimal("51680.00"), [RENT, ELEC])
    assert {(a.charge_id, a.amount) for a in res.allocations} == {
        (1, Decimal("50000.00")),
        (2, Decimal("1680.00")),
    }
    assert res.leftover == Decimal("0.00")


def test_partial_payment_fifo():
    # платёж 50 000 закрывает аренду полностью, электричество не трогает
    res = allocate_payment(Decimal("50000.00"), [RENT, ELEC])
    assert res.allocations == [type(res.allocations[0])(charge_id=1, amount=Decimal("50000.00"))]
    assert res.leftover == Decimal("0.00")


def test_partial_payment_splits():
    # платёж 50 500 -> 50 000 на аренду + 500 на электричество (частично)
    res = allocate_payment(Decimal("50500.00"), [RENT, ELEC])
    amounts = {a.charge_id: a.amount for a in res.allocations}
    assert amounts == {1: Decimal("50000.00"), 2: Decimal("500.00")}
    assert res.leftover == Decimal("0.00")


def test_multiple_payments_close_one_charge():
    # арендатор платит двумя платежами: сначала 20 000, потом 30 000
    p1 = allocate_payment(Decimal("20000.00"), [RENT])
    assert p1.allocations[0].amount == Decimal("20000.00")
    remaining = ChargeOutstanding(charge_id=1, outstanding=Decimal("30000.00"))
    p2 = allocate_payment(Decimal("30000.00"), [remaining])
    assert p2.allocations[0].amount == Decimal("30000.00")
    assert p2.leftover == Decimal("0.00")


def test_overpayment_leftover():
    res = allocate_payment(Decimal("52000.00"), [RENT, ELEC])
    assert res.leftover == Decimal("320.00")


def test_charge_status_paid():
    assert charge_status(50000, 50000, date(2026, 5, 5), date(2026, 5, 4)) == ChargeStatus.paid


def test_charge_status_partial():
    assert charge_status(50000, 20000, date(2026, 5, 5), date(2026, 5, 10)) == ChargeStatus.partial


def test_charge_status_overdue():
    assert charge_status(50000, 0, date(2026, 5, 5), date(2026, 5, 12)) == ChargeStatus.overdue


def test_charge_status_issued_before_due():
    assert charge_status(50000, 0, date(2026, 5, 5), date(2026, 5, 1)) == ChargeStatus.issued


def test_shortfall():
    assert shortfall(51680, 50000) == Decimal("1680.00")
    assert shortfall(51680, 51680) == Decimal("0.00")
    assert shortfall(51680, 52000) == Decimal("0.00")
