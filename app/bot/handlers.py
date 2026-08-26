"""Хендлеры Telegram-бота: старт и решение по платежу."""
from __future__ import annotations

from datetime import date

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.keyboards import CB_PAY
from app.config import get_settings
from app.db.base import async_session_factory
from app.db.models import Payment, User
from app.services import confirmation_service
from app.utils.logger import logger

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
async def cmd_start(message: Message) -> None:
    async with async_session_factory() as session:
        allowed = await _is_allowed(session, message.from_user.id)
    if not allowed:
        await message.answer("⛔ Доступ запрещён. Обратитесь к администратору.")
        return
    await message.answer(
        "👋 Бот учёта аренды.\n"
        "Сюда приходят платежи на подтверждение и уведомления о нехватке данных."
    )


@router.callback_query(F.data.startswith(f"{CB_PAY}:"))
async def on_payment_decision(callback: CallbackQuery) -> None:
    """Обработка кнопок Подтвердить/Отклонить под платежом."""
    _, raw_id, action = callback.data.split(":")
    payment_id = int(raw_id)
    approve = action == "ok"

    async with async_session_factory() as session:
        if not await _is_allowed(session, callback.from_user.id):
            await callback.answer("Доступ запрещён", show_alert=True)
            return

        payment = await session.get(Payment, payment_id)
        if payment is None:
            await callback.answer("Платёж не найден", show_alert=True)
            return

        user = (
            await session.execute(select(User).where(User.tg_id == callback.from_user.id))
        ).scalar_one_or_none()

        try:
            result = await confirmation_service.process_payment_decision(
                session, payment, approve=approve, user_id=user.id if user else None, today=date.today()
            )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Ошибка обработки решения по платежу %s", payment_id)
            await callback.answer("Ошибка обработки", show_alert=True)
            return

    if approve:
        note = "оплата закрыта полностью" if result.get("fully_paid") else (
            f"частично, остаток {result.get('remaining_debt')} ₽"
        )
        text = f"✅ Платёж подтверждён ({note})."
    else:
        text = "❌ Платёж отклонён."

    try:
        await callback.message.edit_text(text)
    except Exception:
        await callback.message.answer(text)
    await callback.answer()
