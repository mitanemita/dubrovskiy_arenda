"""Санити-тесты целевой схемы БД (ШАГ 4)."""
from app.db.base import Base
from app.db import models


EXPECTED_TABLES = {
    "landlords", "users", "premises", "tenants", "leases",
    "meters", "meter_readings", "charges", "payments", "payment_allocations",
    "expenses", "settings", "documents", "notifications", "adjustments", "tasks",
}


def test_all_tables_present():
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_lease_has_payment_and_penalty_fields():
    cols = {c.name for c in models.Lease.__table__.columns}
    assert {"payment_day", "penalty_rate", "penalty_base"} <= cols


def test_charge_status_values():
    values = {s.value for s in models.enums.ChargeStatus}
    assert values == {"draft", "issued", "sent", "paid", "partial", "overdue"}


def test_payment_status_values():
    values = {s.value for s in models.enums.PaymentStatus}
    assert values == {"pending", "confirmed", "rejected", "partial"}


def test_allocation_links_payment_and_charge():
    cols = {c.name for c in models.PaymentAllocation.__table__.columns}
    assert {"payment_id", "charge_id", "amount"} <= cols
