"""Тесты расходов, авто-фиксированных расходов, корректировок с аудитом, отчётов."""
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.db.enums import (
    ChargeType,
    ExpenseCategory,
    ExpenseMode,
    LeaseStatus,
    OrgType,
    PaymentStatus,
    TaxMode,
)
from app.db.models import Adjustment, Charge, Expense, Landlord, Lease, Payment, Premises, Tenant
from app.services import (
    adjustment_service,
    billing_service,
    expense_service,
    report_service,
    settings_service,
)


@pytest_asyncio.fixture
async def env(session):
    landlord = Landlord(name="ИП Иванов", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn)
    session.add(landlord)
    await session.flush()
    await settings_service.ensure_defaults(session, landlord.id)
    premises = Premises(landlord_id=landlord.id, label="Склад")
    session.add(premises)
    await session.flush()
    tenant = Tenant(landlord_id=landlord.id, name="ООО Ромашка", type=OrgType.ooo, inn="7100000001")
    session.add(tenant)
    await session.flush()
    lease = Lease(tenant_id=tenant.id, premises_id=premises.id, contract_no="17/2024-АР",
                  contract_date=date(2024, 3, 1), rent_amount=Decimal("50000.00"), payment_day=5,
                  status=LeaseStatus.active)
    session.add(lease)
    await session.flush()
    return {"landlord": landlord, "lease": lease, "premises": premises}


async def test_add_manual_expense(session, env):
    exp = await expense_service.add_expense(
        session, landlord_id=env["landlord"].id, category=ExpenseCategory.repair,
        amount=Decimal("12000.00"), period=date(2026, 4, 15), description="Ремонт кровли",
    )
    await session.flush()
    assert exp.mode == ExpenseMode.manual
    assert exp.period == date(2026, 4, 1)  # нормализовано к началу месяца


async def test_generate_fixed_expenses_idempotent(session, env):
    await settings_service.set_setting(session, env["landlord"].id, "server_cost", "3000")
    await settings_service.set_setting(session, env["landlord"].id, "salary_cost", "40000")
    await session.flush()

    created = await expense_service.generate_fixed_expenses(session, env["landlord"].id, date(2026, 4, 1))
    await session.flush()
    assert created == 2

    # повторный запуск не дублирует
    created2 = await expense_service.generate_fixed_expenses(session, env["landlord"].id, date(2026, 4, 1))
    assert created2 == 0
    total = (await session.execute(select(func.count()).select_from(Expense))).scalar_one()
    assert total == 2


async def test_fixed_expense_skipped_when_zero(session, env):
    # по умолчанию server_cost/salary_cost = 0 -> ничего не создаётся
    created = await expense_service.generate_fixed_expenses(session, env["landlord"].id, date(2026, 5, 1))
    assert created == 0


async def test_correct_charge_amount_logs_audit(session, env):
    charge = await billing_service.create_rent_charge(session, env["lease"], date(2026, 4, 1))
    await session.flush()

    adj = await adjustment_service.correct_amount(
        session, landlord_id=env["landlord"].id, user_id=None,
        entity_type="charge", entity_id=charge.id, new_amount=Decimal("45000.00"),
        reason="Скидка по договорённости",
    )
    await session.flush()

    assert charge.amount == Decimal("45000.00")
    assert adj.old_value == "50000.00"
    assert adj.new_value == "45000.00"
    assert adj.entity_type == "charge"
    count = (await session.execute(select(func.count()).select_from(Adjustment))).scalar_one()
    assert count == 1


async def test_correct_invalid_entity(session, env):
    with pytest.raises(ValueError):
        await adjustment_service.correct_amount(
            session, landlord_id=env["landlord"].id, user_id=None,
            entity_type="payment", entity_id=1, new_amount=Decimal("1"),
        )


async def test_taxes_expense_manual(session, env):
    exp = await expense_service.add_expense(
        session, landlord_id=env["landlord"].id, category=ExpenseCategory.taxes,
        amount=Decimal("8000.00"), period=date(2026, 4, 10), description="АУСН за апрель",
    )
    await session.flush()
    assert exp.category == ExpenseCategory.taxes
    assert exp.mode == ExpenseMode.manual


def test_accountant_role_exists():
    from app.db.enums import UserRole
    assert UserRole.accountant.value == "accountant"


async def test_payments_by_premises(session, env):
    p = Payment(lease_id=env["lease"].id, amount=Decimal("50000.00"), status=PaymentStatus.confirmed)
    session.add(p)
    await session.flush()

    report = await report_service.payments_by_premises(session, env["landlord"].id)
    assert report == [{"premises": "Склад", "confirmed_total": Decimal("50000.00")}]
