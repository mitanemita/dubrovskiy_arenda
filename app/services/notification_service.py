"""Постановка Telegram-уведомлений арендодателю в очередь (таблица notifications).

Доставка — в app/bot/notifier.py; напоминания по задачам показываются в разделе «Задачи».
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import NotifChannel, NotifStatus
from app.db.models import Notification


async def enqueue(
    session: AsyncSession,
    *,
    landlord_id: int,
    channel: NotifChannel,
    type: str,
    subject: str | None = None,
    body: str | None = None,
    related_task_id: int | None = None,
) -> Notification:
    """Ставит уведомление в очередь (status=queued)."""
    notif = Notification(
        landlord_id=landlord_id,
        channel=channel,
        type=type,
        subject=subject,
        body=body,
        related_task_id=related_task_id,
        status=NotifStatus.queued,
    )
    session.add(notif)
    return notif
