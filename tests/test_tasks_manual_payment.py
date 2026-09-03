"""Тесты менеджера задач, напоминаний по задачам и ручной отметки оплаты."""
from datetime import date
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import select

from app.db.enums import (
    ChargeStatus,
    ChargeType,
    LeaseStatus,
    NotifChannel,
    OrgType,
    PaymentStatus,
    TaskPriority,
    TaskStatus,
    TaxMode,
)
from app.db.models import Charge, Landlord, Lease, Notification, Premises, Task, Tenant
from app.scheduler import jobs
from app.services import billing_service, confirmation_service, payment_service, task_service


@pytest_asyncio.fixture
async def landlord(session):
    lord = Landlord(name="ИП Иванов", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn)
    session.add(lord)
    await session.flush()
    return lord


@pytest_asyncio.fixture
async def lease(session, landlord):
    premises = Premises(landlord_id=landlord.id, label="Склад")
    session.add(premises)
    await session.flush()
    tenant = Tenant(landlord_id=landlord.id, name="ООО Ромашка", type=OrgType.ooo, inn="7100000001", email="t@ex.ru")
    session.add(tenant)
    await session.flush()
    lease = Lease(tenant_id=tenant.id, premises_id=premises.id, contract_no="17/2024-АР",
                  contract_date=date(2024, 3, 1), rent_amount=Decimal("50000.00"), payment_day=5,
                  status=LeaseStatus.active)
    session.add(lease)
    await session.flush()
    return lease


# --- Задачи ---
async def test_create_and_list_sorted_by_priority(session, landlord):
    await task_service.create_task(session, landlord_id=landlord.id, title="Низкая", priority=TaskPriority.low)
    await task_service.create_task(session, landlord_id=landlord.id, title="Высокая", priority=TaskPriority.high)
    await task_service.create_task(session, landlord_id=landlord.id, title="Средняя", priority=TaskPriority.medium)
    await session.flush()

    tasks = await task_service.list_tasks(session, landlord.id)
    assert [t.title for t in tasks] == ["Высокая", "Средняя", "Низкая"]


async def test_mark_done_hides_from_open_list(session, landlord):
    t = await task_service.create_task(session, landlord_id=landlord.id, title="Задача")
    await session.flush()
    await task_service.mark_done(session, t.id)
    await session.flush()
    assert t.status == TaskStatus.done
    assert await task_service.list_tasks(session, landlord.id) == []


async def test_task_reminder_on_due_date(session, landlord):
    await task_service.create_task(
        session, landlord_id=landlord.id, title="Позвонить юристу",
        priority=TaskPriority.high, due_date=date(2026, 4, 10),
    )
    await session.flush()

    # до срока — тишина
    assert await jobs.generate_task_reminders(session, date(2026, 4, 9)) == 0
    # в срок — напоминание
    assert await jobs.generate_task_reminders(session, date(2026, 4, 10)) == 1
    await session.flush()
    notif = (await session.execute(select(Notification).where(Notification.type == "task_reminder"))).scalars().first()
    assert notif is not None and notif.channel == NotifChannel.telegram
    assert "Позвонить юристу" in notif.body

    # повторно не дублируется (remind_sent)
    assert await jobs.generate_task_reminders(session, date(2026, 4, 11)) == 0


# --- Ручная отметка оплаты ---
async def test_manual_payment_full(session, lease):
    await billing_service.create_rent_charge(session, lease, date(2026, 4, 1))
    await session.flush()

    payment = await payment_service.register_payment(session, lease.id, Decimal("50000.00"))
    await session.flush()
    result = await confirmation_service.process_payment_decision(
        session, payment, approve=True, user_id=None, today=date(2026, 4, 6)
    )
    await session.flush()

    assert result["fully_paid"] is True
    assert payment.status == PaymentStatus.confirmed
    rent = (await session.execute(select(Charge).where(Charge.type == ChargeType.rent))).scalars().first()
    assert rent.status == ChargeStatus.paid
    # арендатор уведомлён
    assert "payment_confirmed" in (await session.execute(select(Notification.type))).scalars().all()
