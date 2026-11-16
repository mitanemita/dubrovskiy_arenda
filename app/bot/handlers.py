"""Хендлеры Telegram-бота: вход (/start, /version).

Доступ проверяет AccessMiddleware (app/bot/access.py) — до любого хендлера.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.handlers_admin import show_main_menu
from app.version import version_line

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(f"👋 Бот учёта аренды.\n<code>{version_line()}</code>")
    await show_main_menu(message)


@router.message(Command("version"))
async def cmd_version(message: Message) -> None:
    """Показывает версию бота — чтобы проверить, что запущена новая сборка."""
    await message.answer(f"Версия: <code>{version_line()}</code>")
