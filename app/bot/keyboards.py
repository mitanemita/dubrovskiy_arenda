"""Инлайн-клавиатуры бота."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Префиксы callback_data
CB_PAY = "pay"  # pay:<payment_id>:<ok|no>


def task_reminder_kb(task_id: int) -> InlineKeyboardMarkup:
    """Кнопки в напоминании по задаче: выполнить / сменить категорию / перенести на дату."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Выполнено", callback_data=f"taskdone:{task_id}"),
            InlineKeyboardButton(text="🏷 Категория", callback_data=f"taskcat:{task_id}"),
            InlineKeyboardButton(text="📅 Дата", callback_data=f"taskdate:{task_id}"),
        ]]
    )


def payment_decision_kb(payment_id: int) -> InlineKeyboardMarkup:
    """Кнопки подтверждения/отклонения платежа."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"{CB_PAY}:{payment_id}:ok"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"{CB_PAY}:{payment_id}:no"),
            ]
        ]
    )
