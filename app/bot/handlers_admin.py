"""Админ-хендлеры бота: настройки, расходы, показания, корректировки, отчёты.

UI-обёртки над сервисами (settings/expense/reading/adjustment/report).
Бизнес-логика и расчёты — в сервисах и покрыты тестами.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from sqlalchemy import select

from app.db.base import async_session_factory
from app.db.enums import ExpenseCategory
from app.db.models import User
from app.services import (
    adjustment_service,
    expense_service,
    matching_service,
    report_service,
    settings_service,
)

router = Router()

# Настройки, доступные для правки через бота: ключ -> подпись
_EDITABLE_SETTINGS = {
    "electricity_tariff": "Тариф ₽/кВт·ч",
    "electricity_coeff": "Коэффициент электричества",
    "penalty_rate": "Ставка пени, %/день",
    "reminder_days_before": "Напоминание за N дней",
    "server_cost": "Серверная, ₽/мес",
    "salary_cost": "Зарплаты, ₽/мес",
}

_EXPENSE_CHOICES = {
    "travel": "Командировочные",
    "repair": "Текущий ремонт",
    "docs": "Документация",
    "other": "Прочее",
}


class SettingFSM(StatesGroup):
    value = State()


class ExpenseFSM(StatesGroup):
    amount = State()


class AdjustFSM(StatesGroup):
    amount = State()
    reason = State()


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="💸 Расход")],
            [KeyboardButton(text="✏️ Корректировка"), KeyboardButton(text="📊 Отчёты")],
        ],
        resize_keyboard=True,
    )


async def _landlord_id(session, tg_id: int) -> int | None:
    """landlord_id оператора: по пользователю, иначе единственный арендодатель."""
    result = await session.execute(select(User.landlord_id).where(User.tg_id == tg_id))
    lid = result.scalar_one_or_none()
    if lid is not None:
        return lid
    return await matching_service.get_default_landlord_id(session)


def _parse_amount(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ".").replace(" ", ""))
    except (InvalidOperation, ValueError):
        return None


# --- Меню ------------------------------------------------------------------
@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    await message.answer("Главное меню:", reply_markup=main_menu())


# --- Настройки -------------------------------------------------------------
@router.message(F.text == "⚙️ Настройки")
async def settings_menu(message: Message) -> None:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=title, callback_data=f"set:{key}")]
            for key, title in _EDITABLE_SETTINGS.items()
        ]
    )
    await message.answer("Выберите параметр для изменения:", reply_markup=kb)


@router.callback_query(F.data.startswith("set:"))
async def settings_pick(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":", 1)[1]
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        current = await settings_service.get_setting(session, lid, key) if lid else None
    await state.update_data(setting_key=key)
    await state.set_state(SettingFSM.value)
    await callback.message.answer(
        f"{_EDITABLE_SETTINGS.get(key, key)} (текущее: {current}).\nВведите новое значение:"
    )
    await callback.answer()


@router.message(SettingFSM.value)
async def settings_save(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = data["setting_key"]
    if _parse_amount(message.text) is None:
        await message.answer("❌ Введите число. Повторите:")
        return
    async with async_session_factory() as session:
        lid = await _landlord_id(session, message.from_user.id)
        await settings_service.set_setting(session, lid, key, message.text.replace(",", ".").strip())
        await session.commit()
    await state.clear()
    await message.answer("✅ Значение сохранено.", reply_markup=main_menu())


# --- Расходы ---------------------------------------------------------------
@router.message(F.text == "💸 Расход")
async def expense_menu(message: Message) -> None:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=title, callback_data=f"exp:{code}")]
            for code, title in _EXPENSE_CHOICES.items()
        ]
    )
    await message.answer("Категория расхода:", reply_markup=kb)


@router.callback_query(F.data.startswith("exp:"))
async def expense_pick(callback: CallbackQuery, state: FSMContext) -> None:
    code = callback.data.split(":", 1)[1]
    await state.update_data(expense_category=code)
    await state.set_state(ExpenseFSM.amount)
    await callback.message.answer(f"Расход «{_EXPENSE_CHOICES[code]}». Введите сумму, ₽:")
    await callback.answer()


@router.message(ExpenseFSM.amount)
async def expense_save(message: Message, state: FSMContext) -> None:
    amount = _parse_amount(message.text)
    if amount is None or amount <= 0:
        await message.answer("❌ Введите положительную сумму. Повторите:")
        return
    data = await state.get_data()
    category = ExpenseCategory(data["expense_category"])
    async with async_session_factory() as session:
        lid = await _landlord_id(session, message.from_user.id)
        await expense_service.add_expense(
            session, landlord_id=lid, category=category, amount=amount, period=date.today()
        )
        await session.commit()
    await state.clear()
    await message.answer(f"✅ Расход {amount} ₽ добавлен.", reply_markup=main_menu())


# --- Корректировка (с аудитом) --------------------------------------------
@router.message(F.text == "✏️ Корректировка")
async def adjust_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AdjustFSM.amount)
    await message.answer(
        "Корректировка суммы. Введите: <тип> <id> <новая_сумма>\n"
        "тип: charge (начисление) или expense (расход)\n"
        "Пример: charge 12 45000"
    )


@router.message(AdjustFSM.amount)
async def adjust_parse(message: Message, state: FSMContext) -> None:
    parts = message.text.split()
    if len(parts) != 3 or parts[0] not in ("charge", "expense"):
        await message.answer("❌ Формат: <charge|expense> <id> <сумма>. Повторите:")
        return
    amount = _parse_amount(parts[2])
    if amount is None or amount < 0:
        await message.answer("❌ Некорректная сумма. Повторите:")
        return
    await state.update_data(entity_type=parts[0], entity_id=int(parts[1]), new_amount=str(amount))
    await state.set_state(AdjustFSM.reason)
    await message.answer("Укажите причину корректировки:")


@router.message(AdjustFSM.reason)
async def adjust_save(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, message.from_user.id)
        user = (
            await session.execute(select(User).where(User.tg_id == message.from_user.id))
        ).scalar_one_or_none()
        try:
            await adjustment_service.correct_amount(
                session,
                landlord_id=lid,
                user_id=user.id if user else None,
                entity_type=data["entity_type"],
                entity_id=data["entity_id"],
                new_amount=Decimal(data["new_amount"]),
                reason=message.text,
            )
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            await state.clear()
            await message.answer(f"❌ {exc}", reply_markup=main_menu())
            return
    await state.clear()
    await message.answer("✅ Корректировка сохранена (записана в аудит).", reply_markup=main_menu())


# --- Отчёты ----------------------------------------------------------------
@router.message(F.text == "📊 Отчёты")
async def reports(message: Message) -> None:
    async with async_session_factory() as session:
        lid = await _landlord_id(session, message.from_user.id)
        if lid is None:
            await message.answer("Нет данных.")
            return
        by_prem = await report_service.payments_by_premises(session, lid)
        elec = await report_service.electricity_summary(session, lid, date.today())

    lines = ["<b>Платежи по помещениям (подтверждённые):</b>"]
    lines += [f"• {r['premises']}: {r['confirmed_total']} ₽" for r in by_prem] or ["— нет"]
    lines.append("\n<b>Электричество за текущий месяц:</b>")
    lines += [
        f"• {r['premises']}: {r['consumption_kwh']} кВт·ч = {r['amount']} ₽" for r in elec
    ] or ["— нет"]
    await message.answer("\n".join(lines), reply_markup=main_menu())
