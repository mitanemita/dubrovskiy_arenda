"""Хендлеры Telegram-бота: вход (/start, /version) и проверка доступа."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import select

from app.bot.handlers_admin import show_main_menu
from app.config import get_settings
from app.db.base import async_session_factory
from app.db.models import User
from app.version import version_line

router = Router()


async def _is_allowed(session, tg_id: int) -> bool:
    """Доступ разрешён администраторам из настроек или активным пользователям в БД."""
    if tg_id in get_settings().admin_ids:
        return True
    result = await session.execute(
        select(User.id).where(User.tg_id == tg_id, User.is_active.is_(True))
    )
    return result.scalar_one_or_none() is not None


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with async_session_factory() as session:
        allowed = await _is_allowed(session, message.from_user.id)
    if not allowed:
        await message.answer("⛔ Доступ запрещён. Обратитесь к администратору.")
        return
    await message.answer(f"👋 Бот учёта аренды.\n<code>{version_line()}</code>")
    await show_main_menu(message)


@router.message(Command("version"))
async def cmd_version(message: Message) -> None:
    """Показывает версию бота — чтобы проверить, что запущена новая сборка."""
    await message.answer(f"Версия: <code>{version_line()}</code>")
