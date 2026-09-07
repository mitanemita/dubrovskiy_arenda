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
from app.email.sender import send_email, smtp_check
from app.services import admin_service, document_service
from app.services.billing_service import period_start
from app.utils.logger import logger

router = Router()


def _admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 Заполнить тестовыми данными", callback_data="adm:seed")],
        [InlineKeyboardButton(text="🗑 Очистить все данные", callback_data="adm:wipe_confirm")],
        [InlineKeyboardButton(text="📧 УПД на почту", callback_data="adm:doc_upd"),
         InlineKeyboardButton(text="📧 Квитанция на почту", callback_data="adm:doc_receipt")],
        [InlineKeyboardButton(text="🔌 Тест SMTP (без отправки)", callback_data="adm:smtp")],
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


async def _show_lease_picker(callback: CallbackQuery, kind: str) -> None:
    """Список активных договоров для выбора получателя письма (kind: upd|receipt)."""
    async with async_session_factory() as session:
        if not await _is_allowed(session, callback.from_user.id):
            await callback.answer("Доступ запрещён", show_alert=True)
            return
        lid = await _landlord_id(session, callback.from_user.id)
        rows = (await session.execute(
            select(Lease.id, Lease.contract_no, Tenant.name, Tenant.email)
            .join(Tenant, Tenant.id == Lease.tenant_id)
            .where(Tenant.landlord_id == lid, Lease.status == LeaseStatus.active)
            .order_by(Tenant.name)
        )).all() if lid else []
    if not rows:
        await edit_or_send(callback.message,
            "Нет активного договора. Сначала «🧪 Заполнить тестовыми данными».", reply_markup=back_kb())
        await callback.answer()
        return
    doc_name = "УПД" if kind == "upd" else "квитанцию"
    lines = [f"<b>Кому отправить {doc_name}?</b>"]
    kb_rows = []
    for lease_id, contract, name, email in rows:
        mail = email or "нет email"
        lines.append(f"• {name} · №{contract} · {mail}")
        kb_rows.append([InlineKeyboardButton(text=f"{name} (№{contract})", callback_data=f"adm:mail:{kind}:{lease_id}")])
    kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="nav:home")])
    await edit_or_send(callback.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await callback.answer()


@router.callback_query(F.data == "adm:doc_upd")
async def admin_doc_upd(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_lease_picker(callback, "upd")


@router.callback_query(F.data == "adm:doc_receipt")
async def admin_doc_receipt(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_lease_picker(callback, "receipt")


@router.callback_query(F.data.startswith("adm:mail:"))
async def admin_send_mail(callback: CallbackQuery, state: FSMContext) -> None:
    """Отправка выбранного документа выбранному арендатору на почту."""
    await state.clear()
    # Отвечаем на callback СРАЗУ: SMTP-отправка долгая, иначе query протухает.
    await callback.answer("Отправляю…")
    _, _, kind, raw_id = callback.data.split(":")
    lease_id = int(raw_id)
    await edit_or_send(callback.message, "⏳ Формирую документ и отправляю письмо…")
    period = period_start(date.today())
    async with async_session_factory() as session:
        if not await _is_allowed(session, callback.from_user.id):
            return
        try:
            if kind == "upd":
                pkg = await document_service.upd_email_package(session, lease_id, period, ChargeType.rent)
            else:
                pkg = await document_service.receipt_email_package(session, lease_id, period)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка генерации документа")
            await edit_or_send(callback.message, f"❌ Ошибка генерации документа: {exc}", reply_markup=back_kb())
            return
    if not pkg["to"]:
        await edit_or_send(callback.message,
            "У арендатора не указан email. Укажите его в карточке арендатора.", reply_markup=back_kb())
        return
    try:
        await send_email(pkg["to"], pkg["subject"], pkg["body"], attachment=pkg["pdf"], filename=pkg["filename"])
    except Exception as exc:  # noqa: BLE001 — реальная причина (проверка SMTP)
        logger.exception("Ошибка отправки письма")
        await edit_or_send(
            callback.message,
            f"❌ Не отправлено на {pkg['to']}:\n{exc}\n\n"
            "Похоже на сетевую проблему: сервер не может подключиться к SMTP. "
            "Проверьте «🔌 Тест SMTP», попробуйте SMTP_PORT=587 в .env, или "
            "разрешите исходящий SMTP у провайдера.",
            reply_markup=back_kb(),
        )
        return
    await edit_or_send(callback.message,
        f"✅ Отправлено на {pkg['to']}\nТема: {pkg['subject']}", reply_markup=back_kb())


@router.callback_query(F.data == "adm:smtp")
async def admin_smtp_test(callback: CallbackQuery, state: FSMContext) -> None:
    """Проверка доступности SMTP и логина без отправки письма."""
    await state.clear()
    await callback.answer("Проверяю…")
    await edit_or_send(callback.message, "⏳ Проверяю подключение к SMTP…")
    try:
        info = await smtp_check()
    except Exception as exc:  # noqa: BLE001 — показываем реальную причину
        logger.exception("Ошибка проверки SMTP")
        await edit_or_send(
            callback.message,
            f"❌ SMTP недоступен:\n{exc}\n\n"
            "Частая причина — провайдер/фаервол блокирует исходящий SMTP. "
            "Попробуйте SMTP_PORT=587 в .env; если и он закрыт — используйте "
            "почтовый релей/API или отправку через n8n.",
            reply_markup=back_kb(),
        )
        return
    await edit_or_send(callback.message, f"✅ SMTP OK: {info}", reply_markup=back_kb())


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
