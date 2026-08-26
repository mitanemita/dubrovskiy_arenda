"""Тесты задач планировщика: начисления, напоминания, просрочка."""
from datetime import date
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import select

from app.db.enums import ChargeStatus, ChargeType, LeaseStatus, OrgType, TaxMode
from app.db.models import Charge, Landlord, Lease, Meter, MeterReading, Notification, Premises, Tenant
from app.scheduler import jobs
from app.services import settings_service


@pytest_asyncio.fixture
async def env(session):
    landlord = Landlord(name="ИП Иванов", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn)
    session.add(landlord)
    await session.flush()
    await settings_service.ensure_defaults(session, landlord.id)
    await settings_service.set_setting(session, landlord.id, "electricity_coeff", "1.0")

    premises = Premises(landlord_id=landlord.id, label="Склад №3")
    session.add(premises)
    await session.flush()
    meter = Meter(premises_id=premises.id, serial_no="М-100")
    session.add(meter)
    tenant = Tenant(landlord_id=landlord.id, name="ООО Ромашка", type=OrgType.ooo, inn="7100000001", email="t@ex.ru")
    session.add(tenant)
    await session.flush()
    lease = Lease(
        tenant_id=tenant.id, premises_id=premises.id, contract_no="17/2024-АР",
        contract_date=date(2024, 3, 1), rent_amount=Decimal("50000.00"), payment_day=5,
        penalty_rate=Decimal("0.5"), status=LeaseStatus.active,
    )
    session.add(lease)
    await session.flush()
    return {"landlord": landlord, "meter": meter, "tenant": tenant, "lease": lease}


async def _notif_types(session):
    rows = (await session.execute(select(Notification.type))).scalars().all()
    return rows


async def test_generate_period_charges_with_readings(session, env):
    session.add(MeterReading(meter_id=env["meter"].id, period=date(2026, 4, 1),
                             prev_value=Decimal("15230"), curr_value=Decimal("15350"), consumption=Decimal("120")))
    await session.flush()

    stats = await jobs.generate_period_charges(session, date(2026, 4, 1))
    await session.flush()

    assert stats == {"rent": 1, "electricity": 1, "missing_readings": 0, "invoices": 1}
    charges = (await session.execute(select(Charge))).scalars().all()
    kinds = {c.type: c for c in charges}
    assert kinds[ChargeType.rent].status == ChargeStatus.issued
    assert kinds[ChargeType.electricity].amount == Decimal("1680.00")
    assert "invoice_new" in await _notif_types(session)


async def test_generate_period_charges_missing_readings(session, env):
    stats = await jobs.generate_period_charges(session, date(2026, 4, 1))
    await session.flush()
    assert stats["missing_readings"] == 1
    # электричество не создано, но аренда и уведомление арендодателю — есть
    assert "missing_readings" in await _notif_types(session)
    assert "invoice_new" in await _notif_types(session)


async def test_reminder_two_days_before(session, env):
    # период апрель, срок 05.04 -> напоминание 03.04
    await jobs.generate_period_charges(session, date(2026, 4, 1))
    await session.flush()

    stats = await jobs.run_daily(session, date(2026, 4, 3))
    await session.flush()
    assert stats["reminders"] == 1
    assert "reminder" in await _notif_types(session)

    # повторный прогон в тот же день не дублирует напоминание
    stats2 = await jobs.run_daily(session, date(2026, 4, 3))
    assert stats2["reminders"] == 0


async def test_no_reminder_if_paid(session, env):
    await jobs.generate_period_charges(session, date(2026, 4, 1))
    await session.flush()
    # помечаем аренду оплаченной
    rent = (await session.execute(
        select(Charge).where(Charge.type == ChargeType.rent)
    )).scalars().first()
    rent.paid_amount = rent.amount
    await session.flush()

    stats = await jobs.run_daily(session, date(2026, 4, 3))
    assert stats["reminders"] == 0


async def test_overdue_creates_penalty_and_notice(session, env):
    session.add(MeterReading(meter_id=env["meter"].id, period=date(2026, 4, 1),
                             prev_value=Decimal("15230"), curr_value=Decimal("15350"), consumption=Decimal("120")))
    await session.flush()
    await jobs.generate_period_charges(session, date(2026, 4, 1))
    await session.flush()

    # срок 05.04, сегодня 12.04 -> 7 дней просрочки
    stats = await jobs.run_daily(session, date(2026, 4, 12))
    await session.flush()
    assert stats["overdue"] == 1

    penalty = (await session.execute(
        select(Charge).where(Charge.type == ChargeType.penalty)
    )).scalars().first()
    assert penalty is not None
    # база 51 680 (аренда+электричество) × 0.5% × 7 = 1808.80
    assert penalty.amount == Decimal("1808.80")
    assert "overdue" in await _notif_types(session)

    # начисления помечены overdue
    rent = (await session.execute(select(Charge).where(Charge.type == ChargeType.rent))).scalars().first()
    assert rent.status == ChargeStatus.overdue


async def test_penalty_recalculated_daily(session, env):
    await jobs.generate_period_charges(session, date(2026, 4, 1))
    await session.flush()

    await jobs.run_daily(session, date(2026, 4, 10))  # 5 дней
    await session.flush()
    p1 = (await session.execute(select(Charge).where(Charge.type == ChargeType.penalty))).scalars().first()
    amount_day5 = p1.amount

    await jobs.run_daily(session, date(2026, 4, 12))  # 7 дней
    await session.flush()
    p2 = (await session.execute(select(Charge).where(Charge.type == ChargeType.penalty))).scalars().all()
    # пеня одна на период, пересчитана вверх
    assert len(p2) == 1
    assert p2[0].amount > amount_day5
