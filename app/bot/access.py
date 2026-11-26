"""Контроль доступа: бот отвечает только операторам.

Проверка стоит перед ВСЕМИ хендлерами (сообщения и нажатия кнопок), поэтому
посторонний не увидит данные ни через /start, ни через /menu, ни через кнопку
в пересланном сообщении.
"""
from __future__ import annotations

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.config import get_settings
from app.db.base import async_session_factory
from app.db.models import User

DENIED_TEXT = "⛔ Доступ запрещён. Обратитесь к администратору."


async def is_allowed(tg_id: int) -> bool:
    """Доступ разрешён администраторам из ADMIN_IDS или активным пользователям в БД."""
    if tg_id in get_settings().admin_ids:
        return True
    async with async_session_factory() as session:
        result = await session.execute(
            select(User.id).where(User.tg_id == tg_id, User.is_active.is_(True))
        )
        return result.scalar_one_or_none() is not None


class AccessMiddleware(BaseMiddleware):
    """Не пропускает к хендлерам сообщения и нажатия от посторонних."""

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is not None and await is_allowed(user.id):
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer("Доступ запрещён", show_alert=True)
        elif isinstance(event, Message):
            await event.answer(DENIED_TEXT)
        return None
