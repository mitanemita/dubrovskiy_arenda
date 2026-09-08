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
async def test_due_date_from_priority(session, landlord):
    # приоритет 1 -> 3 дня, 2 -> 7, 3 -> 25
    t1 = await task_service.create_task(session, landlord_id=landlord.id, title="P1",
                                        priority=TaskPriority.high, today=date(2026, 4, 1))
    t2 = await task_service.create_task(session, landlord_id=landlord.id, title="P2",
                                        priority=TaskPriority.medium, today=date(2026, 4, 1))
    t3 = await task_service.create_task(session, landlord_id=landlord.id, title="P3",
                                        priority=TaskPriority.low, today=date(2026, 4, 1))
    await session.flush()
    assert t1.due_date == date(2026, 4, 4)
    assert t2.due_date == date(2026, 4, 8)
    assert t3.due_date == date(2026, 4, 26)


async def test_list_sorted_nearest_first(session, landlord):
    await task_service.create_task(session, landlord_id=landlord.id, title="Дальняя",
                                   priority=TaskPriority.low, today=date(2026, 4, 1))    # due 26.04
    await task_service.create_task(session, landlord_id=landlord.id, title="Ближняя",
                                   priority=TaskPriority.high, today=date(2026, 4, 1))   # due 04.04
    await session.flush()
    tasks = await task_service.list_tasks(session, landlord.id)
    assert [t.title for t in tasks] == ["Ближняя", "Дальняя"]


def test_parse_task_line_priority():
    title, pri, due = task_service.parse_task_line("Позвонить электрику 1")
    assert title == "Позвонить электрику" and pri == TaskPriority.high and due is None


def test_parse_task_line_default_priority():
    title, pri, due = task_service.parse_task_line("Задача без метки")
    assert title == "Задача без метки" and pri == TaskPriority.medium and due is None


def test_parse_task_line_date():
    title, pri, due = task_service.parse_task_line("Вывоз камней литера А 15.12.2026")
    assert title == "Вывоз камней литера А" and due == date(2026, 12, 15)


def test_parse_task_line_strips_leading_number():
    title, pri, due = task_service.parse_task_line("12. Уборка помещений 3")
    assert title == "Уборка помещений" and pri == TaskPriority.low


def test_parse_task_line_keeps_numbers_in_title():
    # число внутри текста не считается приоритетом, только последний токен
    title, pri, due = task_service.parse_task_line("ждем смету дмитрий строитель 258 кв. м. 1")
    assert title == "ждем смету дмитрий строитель 258 кв. м" and pri == TaskPriority.high


async def test_create_from_lines_mixed(session, landlord):
    lines = [
        "1. Позвонить электрику 1",
        "Уборка территории 3",
        "Вывоз камней литера А 15.12.2026",
        "Задача без метки",
        "   ",
    ]
    created = await task_service.create_tasks_from_lines(
        session, landlord_id=landlord.id, lines=lines, today=date(2026, 9, 6)
    )
    await session.flush()
    assert len(created) == 4
    by_title = {t.title: t for t in created}
    assert by_title["Позвонить электрику"].priority == TaskPriority.high
    assert by_title["Уборка территории"].priority == TaskPriority.low
    assert by_title["Вывоз камней литера А"].due_date == date(2026, 12, 15)
    assert by_title["Задача без метки"].priority == TaskPriority.medium


async def test_bulk_create(session, landlord):
    created = await task_service.create_tasks_bulk(
        session, landlord_id=landlord.id,
        titles=["Задача A", "  ", "Задача B", "Задача C"], priority=TaskPriority.medium,
    )
    await session.flush()
    assert len(created) == 3  # пустая строка пропущена
    assert len(await task_service.list_tasks(session, landlord.id)) == 3


async def test_edit_recomputes_due_and_resets_reminders(session, landlord):
    t = await task_service.create_task(session, landlord_id=landlord.id, title="Старая",
                                       priority=TaskPriority.high, today=date(2026, 4, 1))
    await session.flush()
    t.remind_pre_sent = True
    t.remind_due_sent = True
    await session.flush()

    await task_service.update_task(session, t.id, title="Новая", priority=TaskPriority.low)
    await session.flush()
    assert t.title == "Новая"
    assert t.due_date == t.created_at.date() + __import__("datetime").timedelta(days=25)
    assert t.remind_pre_sent is False and t.remind_due_sent is False


async def test_create_with_manual_date(session, landlord):
    t = await task_service.create_task(
        session, landlord_id=landlord.id, title="На число",
        priority=TaskPriority.medium, due_date=date(2026, 12, 31),
    )
    await session.flush()
    assert t.due_date == date(2026, 12, 31)


async def test_set_due_date_resets_reminders(session, landlord):
    t = await task_service.create_task(session, landlord_id=landlord.id, title="Перенос",
                                       priority=TaskPriority.high, today=date(2026, 4, 1))
    await session.flush()
    t.remind_pre_sent = True
    t.remind_due_sent = True
    await session.flush()

    await task_service.set_due_date(session, t.id, date(2026, 5, 20))
    await session.flush()
    assert t.due_date == date(2026, 5, 20)
    assert t.remind_pre_sent is False and t.remind_due_sent is False


def test_due_color_icon_by_days_left():
    today = date(2026, 9, 8)
    # просрочена — чёрный
    assert task_service.due_color_icon(date(2026, 9, 7), today) == "⚫"
    # 0–3 дня — красный (граница 3 включительно)
    assert task_service.due_color_icon(today, today) == "🔴"
    assert task_service.due_color_icon(date(2026, 9, 11), today) == "🔴"
    # 3–7 дней — жёлтый (4..7)
    assert task_service.due_color_icon(date(2026, 9, 12), today) == "🟡"
    assert task_service.due_color_icon(date(2026, 9, 15), today) == "🟡"
    # 7–25 дней — зелёный (8..25)
    assert task_service.due_color_icon(date(2026, 9, 16), today) == "🟢"
    assert task_service.due_color_icon(date(2026, 10, 3), today) == "🟢"
    # 25+ дней — синий
    assert task_service.due_color_icon(date(2026, 10, 4), today) == "🔵"
    # без срока — белый
    assert task_service.due_color_icon(None, today) == "⚪"


async def test_reminder_subject_uses_day_color_not_category(session, landlord):
    # Срок сегодня → 0 дней → красный кружок; в теме нет «(🟡 2 (7 дней))».
    await task_service.create_task(session, landlord_id=landlord.id, title="Позвонить электрику",
                                   priority=TaskPriority.medium, today=date(2026, 4, 1))
    await session.flush()
    await jobs.generate_task_reminders(session, date(2026, 4, 8))  # день срока (medium=7 дней)
    await session.flush()
    notif = (await session.execute(
        select(Notification).where(Notification.type == "task_reminder"))).scalars().first()
    assert notif is not None
    assert notif.subject == "Сегодня срок задачи 🔴"
    assert "2 (7 дней)" not in notif.subject
    assert notif.body.startswith("🔴 Позвонить электрику")


async def test_reminder_carries_task_id(session, landlord):
    await task_service.create_task(session, landlord_id=landlord.id, title="С кнопками",
                                   priority=TaskPriority.high, today=date(2026, 4, 1))
    await session.flush()
    await jobs.generate_task_reminders(session, date(2026, 4, 4))  # день срока
    await session.flush()
    notif = (await session.execute(select(Notification).where(Notification.type == "task_reminder"))).scalars().first()
    assert notif is not None and notif.related_task_id is not None


async def test_delete_task(session, landlord):
    t = await task_service.create_task(session, landlord_id=landlord.id, title="Удалить")
    await session.flush()
    assert await task_service.delete_task(session, t.id) is True
    await session.flush()
    assert await task_service.list_tasks(session, landlord.id) == []


async def test_reminders_pre_and_due(session, landlord):
    # приоритет 1: срок через 3 дня, предвар. за 1 день (03.04), в день (04.04)
    await task_service.create_task(session, landlord_id=landlord.id, title="Сдать отчёт",
                                   priority=TaskPriority.high, today=date(2026, 4, 1))
    await session.flush()

    assert await jobs.generate_task_reminders(session, date(2026, 4, 2)) == 0  # рано
    assert await jobs.generate_task_reminders(session, date(2026, 4, 3)) == 1  # за 1 день
    assert await jobs.generate_task_reminders(session, date(2026, 4, 3)) == 0  # не дублируется
    assert await jobs.generate_task_reminders(session, date(2026, 4, 4)) == 1  # в день срока
    assert await jobs.generate_task_reminders(session, date(2026, 4, 5)) == 0  # уже отправлено

    notifs = (await session.execute(select(Notification).where(Notification.type == "task_reminder"))).scalars().all()
    assert len(notifs) == 2 and all(n.channel == NotifChannel.telegram for n in notifs)


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


# --- Адресат и выполненные задачи ---
async def test_task_assignee_create_update(session, landlord):
    t = await task_service.create_task(session, landlord_id=landlord.id, title="Позвонить", assignee="Митя")
    await session.flush()
    assert t.assignee == "Митя"
    await task_service.update_task(session, t.id, assignee="Алексей")
    await session.flush()
    assert t.assignee == "Алексей"
    # сделать общей (None)
    await task_service.update_task(session, t.id, assignee=None)
    await session.flush()
    assert t.assignee is None
    # без указания assignee поле не меняется
    await task_service.update_task(session, t.id, title="Позвонить снова")
    await session.flush()
    assert t.assignee is None and t.title == "Позвонить снова"


async def test_list_done_tasks(session, landlord):
    a = await task_service.create_task(session, landlord_id=landlord.id, title="A")
    b = await task_service.create_task(session, landlord_id=landlord.id, title="B")
    await session.flush()
    await task_service.mark_done(session, a.id)
    await session.flush()
    done = await task_service.list_done_tasks(session, landlord.id)
    assert [t.title for t in done] == ["A"]
    # открытые не попадают в выполненные
    open_tasks = await task_service.list_tasks(session, landlord.id)
    assert {t.title for t in open_tasks} == {"B"}
