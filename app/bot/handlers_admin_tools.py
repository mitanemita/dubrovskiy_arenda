"""Служебные команды администратора: /admin — заполнить БД демо-данными / очистить.

Только для операторов (admin_ids или активные пользователи). Предназначено для
проверки функционала бота на «живых» данных без ручного ввода.
"""
from __future__ import annotations

from datetime import date

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from app.bot.handlers import _is_allowed
from app.bot.handlers_admin import _landlord_id, edit_or_send
from app.bot.keyboards import back_kb, main_menu_kb
from app.db.enums import ChargeType, LeaseStatus
from app.db.base import async_session_factory
from app.db.models import Lease, Tenant
from app.documents import render
from app.services import admin_service, document_service
from app.services.billing_service import period_start
from app.utils.logger import logger

router = Router()


def _admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 Заполнить тестовыми данными", callback_data="adm:seed")],
        [InlineKeyboardButton(text="🗑 Очистить все данные", callback_data="adm:wipe_confirm")],
        [InlineKeyboardButton(text="📄 Тест: УПД (аренда)", callback_data="adm:doc_upd"),
         InlineKeyboardButton(text="🧾 Тест: квитанция", callback_data="adm:doc_receipt")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="nav:home")],
    ])


async def _first_active_lease_id(session, landlord_id: int) -> int | None:
    return (await session.execute(
        select(Lease.id).join(Tenant, Tenant.id == Lease.tenant_id)
        .where(Tenant.landlord_id == landlord_id, Lease.status == LeaseStatus.active)
        .order_by(Lease.id)
    )).scalars().first()


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


async def _send_test_document(callback: CallbackQuery, kind: str) -> None:
    """Генерирует PDF документа для первого активного договора и присылает его."""
    async with async_session_factory() as session:
        if not await _is_allowed(session, callback.from_user.id):
            await callback.answer("Доступ запрещён", show_alert=True)
            return
        lid = await _landlord_id(session, callback.from_user.id)
        lease_id = await _first_active_lease_id(session, lid) if lid else None
        if lease_id is None:
            await edit_or_send(callback.message,
                "Нет активного договора. Сначала «🧪 Заполнить тестовыми данными».",
                reply_markup=back_kb())
            await callback.answer()
            return
        period = period_start(date.today())
        try:
            if kind == "upd":
                context = await document_service.build_upd_context(session, lease_id, period, ChargeType.rent)
                pdf = render.render_pdf("upd.html", context)
                filename = f"УПД_демо_{lease_id}.pdf"
            else:
                pdf, filename = await document_service.receipt_pdf(session, lease_id, period)
        except Exception as exc:  # noqa: BLE001 — показываем причину оператору
            logger.exception("Ошибка генерации тестового документа")
            await edit_or_send(callback.message, f"❌ Ошибка генерации документа: {exc}", reply_markup=back_kb())
            await callback.answer()
            return
    await callback.message.answer_document(
        BufferedInputFile(pdf, filename=filename),
        caption="Тестовый документ сгенерирован ✅",
    )
    await callback.answer("Документ отправлен")


@router.callback_query(F.data == "adm:doc_upd")
async def admin_doc_upd(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _send_test_document(callback, "upd")


@router.callback_query(F.data == "adm:doc_receipt")
async def admin_doc_receipt(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _send_test_document(callback, "receipt")


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
