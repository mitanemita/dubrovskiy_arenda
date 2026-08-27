"""Инлайн-клавиатуры бота."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Префиксы callback_data
CB_PAY = "pay"  # pay:<payment_id>:<ok|no>


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
