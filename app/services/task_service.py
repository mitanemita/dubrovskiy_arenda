"""Менеджер задач: приоритет задаёт срок и график напоминаний.

Приоритет:  1 — 3 дня,  2 — 7 дней,  3 — 25 дней (срок = дата создания + N дней).
Напоминания: приоритет 1 — за 1 день и в день; 2 — за 2 дня и в день; 3 — за 5 дней и в день.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import TaskPriority, TaskStatus
from app.db.models import Task

# Приоритет -> число (для отображения) и обратно
PRIORITY_NUM = {TaskPriority.high: 1, TaskPriority.medium: 2, TaskPriority.low: 3}
NUM_PRIORITY = {1: TaskPriority.high, 2: TaskPriority.medium, 3: TaskPriority.low}

# Срок до дедлайна (дней) и за сколько дней предварительное напоминание
DURATION_DAYS = {TaskPriority.high: 3, TaskPriority.medium: 7, TaskPriority.low: 25}
LEAD_DAYS = {TaskPriority.high: 1, TaskPriority.medium: 2, TaskPriority.low: 5}

PRIORITY_LABEL = {
    TaskPriority.high: "🔴 1 (3 дня)",
    TaskPriority.medium: "🟡 2 (7 дней)",
    TaskPriority.low: "🟢 3 (25 дней)",
}


def due_from_priority(priority: TaskPriority, created: date) -> date:
    """Срок задачи = дата создания + длительность по приоритету."""
    return created + timedelta(days=DURATION_DAYS[priority])


async def create_task(
    session: AsyncSession,
    *,
    landlord_id: int,
    title: str,
    priority: TaskPriority = TaskPriority.medium,
    description: str | None = None,
    created_by_id: int | None = None,
    due_date: date | None = None,
    today: date | None = None,
) -> Task:
    """Создаёт задачу. Срок — из приоритета, либо явный (due_date, «на число»)."""
    today = today or date.today()
    task = Task(
        landlord_id=landlord_id,
        title=title,
        priority=priority,
        due_date=due_date or due_from_priority(priority, today),
        description=description,
        created_by_id=created_by_id,
    )
    session.add(task)
    return task


async def set_due_date(session: AsyncSession, task_id: int, due_date: date) -> Task | None:
    """Переносит задачу на конкретную дату и сбрасывает флаги напоминаний."""
    task = await session.get(Task, task_id)
    if task is None:
        return None
    task.due_date = due_date
    task.remind_pre_sent = False
    task.remind_due_sent = False
    return task


async def create_tasks_bulk(
    session: AsyncSession,
    *,
    landlord_id: int,
    titles: list[str],
    priority: TaskPriority = TaskPriority.medium,
    created_by_id: int | None = None,
    today: date | None = None,
) -> list[Task]:
    """Добавляет несколько задач списком (по одной на строку)."""
    tasks = []
    for title in titles:
        title = title.strip()
        if title:
            tasks.append(await create_task(
                session, landlord_id=landlord_id, title=title, priority=priority,
                created_by_id=created_by_id, today=today,
            ))
    return tasks


async def list_tasks(session: AsyncSession, landlord_id: int, *, include_done: bool = False) -> list[Task]:
    """Задачи от ближайших к дальним (по сроку)."""
    query = select(Task).where(Task.landlord_id == landlord_id)
    if not include_done:
        query = query.where(Task.status == TaskStatus.open)
    tasks = list((await session.execute(query)).scalars().all())
    tasks.sort(key=lambda t: (t.due_date or date.max, PRIORITY_NUM.get(t.priority, 9)))
    return tasks


async def get_task(session: AsyncSession, task_id: int) -> Task | None:
    return await session.get(Task, task_id)


async def update_task(
    session: AsyncSession,
    task_id: int,
    *,
    title: str | None = None,
    priority: TaskPriority | None = None,
) -> Task | None:
    """Редактирование задачи. При смене приоритета срок и напоминания пересчитываются."""
    task = await session.get(Task, task_id)
    if task is None:
        return None
    if title is not None:
        task.title = title
    if priority is not None and priority != task.priority:
        task.priority = priority
        task.due_date = due_from_priority(priority, task.created_at.date())
        task.remind_pre_sent = False
        task.remind_due_sent = False
    return task


async def delete_task(session: AsyncSession, task_id: int) -> bool:
    task = await session.get(Task, task_id)
    if task is None:
        return False
    await session.delete(task)
    return True


async def mark_done(session: AsyncSession, task_id: int) -> Task | None:
    task = await session.get(Task, task_id)
    if task is not None:
        task.status = TaskStatus.done
    return task


async def open_with_due(session: AsyncSession) -> list[Task]:
    """Открытые задачи со сроком (для планировщика напоминаний)."""
    result = await session.execute(
        select(Task).where(Task.status == TaskStatus.open, Task.due_date.is_not(None))
    )
    return list(result.scalars().all())
