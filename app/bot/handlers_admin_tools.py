"""Служебные команды администратора: /admin — заполнить БД демо-данными / очистить.

Только для операторов (admin_ids или активные пользователи). Предназначено для
проверки функционала бота на «живых» данных без ручного ввода.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.handlers import _is_allowed
from app.bot.handlers_admin import _landlord_id, edit_or_send
from app.bot.keyboards import main_menu_kb
from app.db.base import async_session_factory
from app.services import admin_service

router = Router()


def _admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 Заполнить тестовыми данными", callback_data="adm:seed")],
        [InlineKeyboardButton(text="🗑 Очистить все данные", callback_data="adm:wipe_confirm")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="nav:home")],
    ])


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with async_session_factory() as session:
        allowed = await _is_allowed(session, message.from_user.id)
    if not allowed:
        await message.answer("⛔ Доступ запрещён.")
        return
    await message.answer(
        "🛠 <b>Админ-панель (тестирование)</b>\n"
        "• Заполнить БД демо-данными (помещения, арендаторы, договоры, счётчик, задачи).\n"
        "• Очистить все бизнес-данные (арендодатель и настройки сохраняются).",
        reply_markup=_admin_menu_kb(),
    )


@router.callback_query(F.data == "adm:seed")
async def admin_seed(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session_factory() as session:
        if not await _is_allowed(session, callback.from_user.id):
            await callback.answer("Доступ запрещён", show_alert=True)
            return
        lid = await _landlord_id(session, callback.from_user.id)
        counts = await admin_service.seed_test_data(session, lid)
        await session.commit()
    summary = ", ".join(f"{k}: {v}" for k, v in counts.items())
    await edit_or_send(
        callback.message,
        f"✅ Демо-данные созданы ({summary}).\nРеквизиты арендодателя заполнены заглушками.",
        reply_markup=main_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:wipe_confirm")
async def admin_wipe_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить всё", callback_data="adm:wipe")],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="nav:home")],
    ])
    await edit_or_send(
        callback.message,
        "⚠️ Удалить <b>ВСЕ</b> договоры, арендаторов, помещения, счётчики, начисления, "
        "платежи, задачи и расходы? Это действие необратимо.",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data == "adm:wipe")
async def admin_wipe(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session_factory() as session:
        if not await _is_allowed(session, callback.from_user.id):
            await callback.answer("Доступ запрещён", show_alert=True)
            return
        lid = await _landlord_id(session, callback.from_user.id)
        counts = await admin_service.wipe_business_data(session, lid)
        await session.commit()
    summary = ", ".join(f"{k}: {v}" for k, v in counts.items()) or "данных не было"
    await edit_or_send(callback.message, f"🗑 Данные очищены ({summary}).", reply_markup=main_menu_kb())
    await callback.answer()
