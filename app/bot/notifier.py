"""Доставка Telegram-уведомлений из очереди (таблица notifications)."""
from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import payment_decision_kb
from app.db.enums import NotifChannel
from app.services import notification_dispatch
from app.utils.logger import logger


class Sender(Protocol):
    """Минимальный интерфейс отправки (реализуется aiogram Bot.send_message)."""

    async def __call__(self, chat_id: int, text: str, reply_markup=None) -> None: ...


async def dispatch_telegram(session: AsyncSession, send: Sender) -> dict:
    """Отправляет все очередные TG-уведомления. Возвращает статистику.

    Для типа payment_confirm_request прикрепляет кнопки Подтвердить/Отклонить.
    """
    stats = {"sent": 0, "failed": 0}
    pending = await notification_dispatch.get_pending(session, NotifChannel.telegram)

    for notif in pending:
        recipients = await notification_dispatch.landlord_tg_ids(session, notif.landlord_id)
        if not recipients:
            notification_dispatch.mark_failed(notif)
            stats["failed"] += 1
            continue

        markup = None
        if notif.type == "payment_confirm_request" and notif.related_payment_id:
            markup = payment_decision_kb(notif.related_payment_id)

        text = f"<b>{notif.subject or 'Уведомление'}</b>\n{notif.body or ''}".strip()

        ok = True
        for chat_id in recipients:
            try:
                await send(chat_id=chat_id, text=text, reply_markup=markup)
            except Exception:
                ok = False
                logger.exception("Не удалось отправить TG-уведомление %s в чат %s", notif.id, chat_id)

        if ok:
            notification_dispatch.mark_sent(notif)
            stats["sent"] += 1
        else:
            notification_dispatch.mark_failed(notif)
            stats["failed"] += 1

    return stats
