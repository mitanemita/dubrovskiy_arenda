"""Работа с очередью уведомлений (aiogram-free, тестируется отдельно).

Фактическая отправка выполняется в bot/notifier.py (Telegram) и email-воркере.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.enums import NotifChannel, NotifStatus
from app.db.models import Notification, User


async def get_pending(session: AsyncSession, channel: NotifChannel, limit: int = 50) -> list[Notification]:
    """Уведомления в очереди по каналу (status=queued)."""
    result = await session.execute(
        select(Notification)
        .where(Notification.channel == channel, Notification.status == NotifStatus.queued)
        .order_by(Notification.id)
        .limit(limit)
    )
    return list(result.scalars().all())


async def landlord_tg_ids(session: AsyncSession, landlord_id: int) -> list[int]:
    """Telegram ID получателей-арендодателей: активные пользователи, иначе ADMIN_IDS."""
    result = await session.execute(
        select(User.tg_id).where(User.landlord_id == landlord_id, User.is_active.is_(True))
    )
    ids = list(result.scalars().all())
    if ids:
        return ids
    return get_settings().admin_ids


def mark_sent(notification: Notification) -> None:
    notification.status = NotifStatus.sent
    notification.sent_at = datetime.now()


def mark_failed(notification: Notification) -> None:
    notification.status = NotifStatus.failed
