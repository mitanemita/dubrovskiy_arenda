"""Инлайн-клавиатуры бота.

Вся навигация — на инлайн-кнопках под сообщениями (callback_data). Обычная
reply-клавиатура не используется: она мешала FSM (ввод текста «залипал»)
и требовала отдельного роутинга по тексту кнопок.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Префиксы callback_data
CB_PAY = "pay"      # pay:<payment_id>:<ok|no>
CB_MENU = "menu"    # menu:<section>
CB_NAV = "nav"      # nav:home

# Разделы главного меню: callback-ключ -> подпись кнопки
MENU_SECTIONS: dict[str, str] = {
    "directory": "🗂 Справочники",
    "settings": "⚙️ Настройки",
    "expense": "💸 Расходы",
    "readings": "🔢 Показания",
    "tasks": "📝 Задачи",
    "pay": "💰 Отметить оплату",
    "reports": "📊 Отчёты",
}


def main_menu_kb() -> InlineKeyboardMarkup:
    """Главное меню на инлайн-кнопках (по две в ряд)."""
    items = [
        InlineKeyboardButton(text=title, callback_data=f"{CB_MENU}:{key}")
        for key, title in MENU_SECTIONS.items()
    ]
    rows = [items[i : i + 2] for i in range(0, len(items), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_kb() -> InlineKeyboardMarkup:
    """Одна кнопка «В меню»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ В меню", callback_data=f"{CB_NAV}:home")]]
    )


def cancel_kb() -> InlineKeyboardMarkup:
    """Одна кнопка «Отмена» (прерывает ввод и возвращает в меню)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✖️ Отмена", callback_data=f"{CB_NAV}:home")]]
    )


def task_reminder_kb(task_id: int) -> InlineKeyboardMarkup:
    """Кнопки в напоминании по задаче: выполнить / сменить категорию / перенести на дату.

    Те же callback'и, что и в карточке задачи: хендлер сам отличает напоминание от
    карточки по тексту сообщения и после действия удаляет именно напоминание, чтобы
    не копить их в чате.
    """
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
