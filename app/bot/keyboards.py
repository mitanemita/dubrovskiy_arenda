"""Инлайн-клавиатуры бота.

Вся навигация — на инлайн-кнопках под сообщениями (callback_data).
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Разделы главного меню: callback-ключ -> подпись кнопки
MENU_SECTIONS: dict[str, str] = {
    "directory": "🗂 Справочники",
    "finance": "💼 Учёт и финансы",
    "tasks": "📝 Задачи",
    "settings": "⚙️ Настройки",
}

# Подразделы «Учёт и финансы»: callback-ключ -> подпись кнопки
FINANCE_SECTIONS: dict[str, str] = {
    "expense": "💸 Расходы",
    "readings": "🔢 Показания",
    "pay": "💰 Отметить оплату",
    "reports": "📊 Отчёты",
    "map": "🗺 Карта базы",
}


def _menu_kb(sections: dict[str, str]) -> list[list[InlineKeyboardButton]]:
    """Кнопки разделов по две в ряд."""
    items = [
        InlineKeyboardButton(text=title, callback_data=f"menu:{key}")
        for key, title in sections.items()
    ]
    return [items[i : i + 2] for i in range(0, len(items), 2)]


def main_menu_kb() -> InlineKeyboardMarkup:
    """Главное меню на инлайн-кнопках (по две в ряд)."""
    return InlineKeyboardMarkup(inline_keyboard=_menu_kb(MENU_SECTIONS))


def finance_menu_kb() -> InlineKeyboardMarkup:
    """Меню раздела «Учёт и финансы»."""
    rows = _menu_kb(FINANCE_SECTIONS)
    rows.append([InlineKeyboardButton(text="◀️ В меню", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_kb() -> InlineKeyboardMarkup:
    """Одна кнопка «В меню»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ В меню", callback_data="nav:home")]]
    )


def finance_back_kb() -> InlineKeyboardMarkup:
    """Одна кнопка возврата в «Учёт и финансы»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ Учёт и финансы", callback_data="menu:finance")]]
    )


def cancel_kb() -> InlineKeyboardMarkup:
    """Одна кнопка «Отмена» (прерывает ввод и возвращает в меню)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✖️ Отмена", callback_data="nav:home")]]
    )
