"""Менеджер задач: приоритет + дата напоминания."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import TaskPriority, TaskStatus
from app.db.models import Task

# Порядок сортировки по приоритету: высокий -> средний -> низкий
_PRIORITY_ORDER = {TaskPriority.high: 0, TaskPriority.medium: 1, TaskPriority.low: 2}

PRIORITY_LABEL = {TaskPriority.high: "🔴 Высокий", TaskPriority.medium: "🟡 Средний", TaskPriority.low: "🟢 Низкий"}


async def create_task(
    session: AsyncSession,
    *,
    landlord_id: int,
    title: str,
    priority: TaskPriority = TaskPriority.medium,
    due_date: date | None = None,
    description: str | None = None,
    created_by_id: int | None = None,
) -> Task:
    task = Task(
        landlord_id=landlord_id,
        title=title,
        priority=priority,
        due_date=due_date,
        description=description,
        created_by_id=created_by_id,
    )
    session.add(task)
    return task


async def list_tasks(session: AsyncSession, landlord_id: int, *, include_done: bool = False) -> list[Task]:
    """Задачи арендодателя, отсортированные по приоритету и дате."""
    query = select(Task).where(Task.landlord_id == landlord_id)
    if not include_done:
        query = query.where(Task.status == TaskStatus.open)
    tasks = list((await session.execute(query)).scalars().all())
    tasks.sort(key=lambda t: (_PRIORITY_ORDER.get(t.priority, 9), t.due_date or date.max))
    return tasks


async def get_task(session: AsyncSession, task_id: int) -> Task | None:
    return await session.get(Task, task_id)


async def mark_done(session: AsyncSession, task_id: int) -> Task | None:
    task = await session.get(Task, task_id)
    if task is not None:
        task.status = TaskStatus.done
    return task


async def due_tasks(session: AsyncSession, today: date) -> list[Task]:
    """Открытые задачи с наступившей датой напоминания, по которым напоминание ещё не слали."""
    result = await session.execute(
        select(Task).where(
            Task.status == TaskStatus.open,
            Task.remind_sent.is_(False),
            Task.due_date.is_not(None),
            Task.due_date <= today,
        )
    )
    return list(result.scalars().all())


def mark_reminded(task: Task) -> None:
    task.remind_sent = True
