"""Сценарии навигации по задачам без обращения к Telegram."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot import handlers_admin as ui
from app.db.enums import NotifChannel, NotifStatus, OrgType, TaxMode
from app.db.models import Landlord, User
from app.services import notification_service, task_service


class State:
    def __init__(self, data=None):
        self.data = data or {}

    async def clear(self):
        self.data = {}

    async def get_data(self):
        return self.data


@pytest.fixture
def bot_db(session, monkeypatch):
    @asynccontextmanager
    async def factory():
        yield session
    monkeypatch.setattr(ui, "async_session_factory", factory)


async def make_landlord(session, name, tg_id):
    landlord = Landlord(name=name, type=OrgType.ip, inn=str(tg_id), tax_mode=TaxMode.ausn)
    session.add(landlord)
    await session.flush()
    session.add(User(landlord_id=landlord.id, tg_id=tg_id, name=name))
    await session.flush()
    return landlord


def callback(data="menu:tasks", tg_id=123):
    return SimpleNamespace(data=data, from_user=SimpleNamespace(id=tg_id), message=object(), answer=AsyncMock())


def buttons(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


async def test_open_tasks_marks_only_own_notifications_and_displays_paginated_list(session, bot_db, monkeypatch):
    landlord = await make_landlord(session, "Первый", 123)
    other = await make_landlord(session, "Второй", 456)
    for i in range(21):
        await task_service.create_task(session, landlord_id=landlord.id, title=f"{i} " + "<&" * 3000,
                                       assignee="<&" * 30)
    own_notif = await notification_service.enqueue(session, landlord_id=landlord.id, channel=NotifChannel.telegram,
                                                    type="task_reminder", subject="Срок", body="Задача")
    other_notif = await notification_service.enqueue(session, landlord_id=other.id, channel=NotifChannel.telegram,
                                                      type="task_reminder", subject="Срок", body="Задача")
    await session.flush()
    rendered = []

    async def show(message, text, reply_markup=None):
        rendered.append((text, reply_markup))

    monkeypatch.setattr(ui, "edit_or_send", show)
    cb = callback()
    await ui.tasks_menu(cb, State())
    assert own_notif.status == NotifStatus.sent
    assert other_notif.status == NotifStatus.queued
    text, markup = rendered[-1]
    assert len(text) < ui._TG_MAX_LEN
    assert "Стр. 1/2" in text and "&lt;&amp;" in text
    assert len([value for value in buttons(markup) if value.startswith("tpick:")]) == 20
    assert {"task_add", "task_bulk", "tasks:done", "tasks:editp:20", "tasks:refresh:0"} <= set(buttons(markup))
    assert not {"tasks:recent", "tasks:edit", "tasks:notif"} & set(buttons(markup))
    cb.answer.assert_awaited_once()
    await ui.tasks_edit_list(callback("tasks:editp:20"), State())
    assert "Стр. 2/2" in rendered[-1][0]
    assert len([value for value in buttons(rendered[-1][1]) if value.startswith("tpick:")]) == 1
    assert "tasks:refresh:20" in buttons(rendered[-1][1])

    new_task = await task_service.create_task(session, landlord_id=landlord.id, title="Новая на странице")
    new_notif = await notification_service.enqueue(session, landlord_id=landlord.id, channel=NotifChannel.telegram,
                                                    type="task_reminder", subject="Срок", body="Задача")
    await session.flush()
    refresh = callback("tasks:refresh:20")
    await ui.tasks_menu(refresh, State())
    assert "Стр. 2/2" in rendered[-1][0]
    assert f"tpick:{new_task.id}" in buttons(rendered[-1][1])
    assert new_notif.status == NotifStatus.sent
    assert other_notif.status == NotifStatus.queued
    refresh.answer.assert_awaited_once_with("Обновлено")


async def test_text_edit_from_card_saves_and_returns_to_card(session, bot_db, monkeypatch):
    landlord = await make_landlord(session, "Первый", 123)
    task = await task_service.create_task(session, landlord_id=landlord.id, title="Старый текст")
    await session.flush()
    assert f"taskedit:{task.id}" in buttons(ui._task_card_kb(task))
    rendered = []

    async def show(message, text, reply_markup=None):
        rendered.append((text, reply_markup))

    monkeypatch.setattr(ui, "wiz_reply", show)
    message = SimpleNamespace(text="Новый <текст>", from_user=SimpleNamespace(id=123))
    await ui.task_edit_title(message, State({"edit_id": task.id}))
    assert task.title == "Новый <текст>"
    assert "Новый &lt;текст&gt;" in rendered[-1][0]
    assert f"taskedit:{task.id}" in buttons(rendered[-1][1])


async def test_add_task_by_priority_returns_to_task_list(session, bot_db, monkeypatch):
    landlord = await make_landlord(session, "Первый", 123)
    rendered = []

    async def show(message, text, reply_markup=None):
        rendered.append((text, reply_markup))

    monkeypatch.setattr(ui, "edit_or_send", show)
    cb = callback("tp:add:1")
    await ui.task_add_priority(cb, State({"title": "Новая задача", "assignee": "Митя"}))
    tasks = await task_service.list_tasks(session, landlord.id)
    assert len(tasks) == 1 and tasks[0].title == "Новая задача"
    assert "Новая задача" in rendered[-1][0]
    assert f"tpick:{tasks[0].id}" in buttons(rendered[-1][1])
    cb.answer.assert_awaited_once()


async def test_done_list_displays_completion_and_legacy_dates(session, bot_db, monkeypatch):
    from datetime import datetime, timezone

    from app.db.enums import TaskStatus

    landlord = await make_landlord(session, "Первый", 123)
    new = await task_service.create_task(session, landlord_id=landlord.id, title="Новая")
    old = await task_service.create_task(session, landlord_id=landlord.id, title="Старая")
    await session.flush()
    new.status = TaskStatus.done
    new.completed_at = datetime(2026, 11, 25, 22, 30, tzinfo=timezone.utc)
    old.status = TaskStatus.done
    old.updated_at = datetime(2026, 11, 24, 22, 30, tzinfo=timezone.utc)
    await session.commit()
    monkeypatch.setattr(ui, "get_settings", lambda: SimpleNamespace(timezone="Europe/Moscow"))
    rendered = []

    async def show(message, text, reply_markup=None):
        rendered.append(text)

    monkeypatch.setattr(ui, "edit_or_send", show)
    await ui.tasks_done_list(callback("tasks:done"), State())
    assert "Новая · 👥 общая · выполнена 26.11.2026" in rendered[-1]
    assert "Старая · 👥 общая · изменена 25.11.2026" in rendered[-1]


async def test_completion_time_is_saved_and_not_reset(session, bot_db):
    from datetime import datetime, timezone

    landlord = await make_landlord(session, "Первый", 123)
    task = await task_service.create_task(session, landlord_id=landlord.id, title="Задача")
    await session.flush()
    before = datetime.now(timezone.utc)
    await task_service.mark_done(session, task.id)
    await session.flush()
    completed_at = task.completed_at
    assert before <= completed_at <= datetime.now(timezone.utc)
    await task_service.mark_done(session, task.id)
    await session.commit()
    assert task.completed_at == completed_at
    task_id = task.id
    session.expire_all()
    saved = await task_service.get_task(session, task_id)
    assert saved.completed_at.replace(tzinfo=timezone.utc) == completed_at
