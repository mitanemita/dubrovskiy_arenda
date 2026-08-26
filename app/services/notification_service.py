"""Сервис уведомлений: постановка писем/TG-сообщений в очередь (таблица notifications).

Фактическая доставка (SMTP/Telegram) выполняется отдельными воркерами
(следующие подэтапы ШАГ 5). Здесь — только запись намерения в БД.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import NotifChannel, NotifStatus
from app.db.models import Notification


async def exists(session: AsyncSession, *, type: str, related_charge_id: int) -> bool:
    """Есть ли уже уведомление данного типа, привязанное к начислению (антидубль)."""
    result = await session.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.type == type, Notification.related_charge_id == related_charge_id)
    )
    return result.scalar_one() > 0


async def enqueue(
    session: AsyncSession,
    *,
    landlord_id: int,
    channel: NotifChannel,
    type: str,
    subject: str | None = None,
    body: str | None = None,
    tenant_id: int | None = None,
    related_charge_id: int | None = None,
    related_payment_id: int | None = None,
) -> Notification:
    """Ставит уведомление в очередь (status=queued)."""
    notif = Notification(
        landlord_id=landlord_id,
        tenant_id=tenant_id,
        channel=channel,
        type=type,
        subject=subject,
        body=body,
        related_charge_id=related_charge_id,
        related_payment_id=related_payment_id,
        status=NotifStatus.queued,
    )
    session.add(notif)
    return notif
