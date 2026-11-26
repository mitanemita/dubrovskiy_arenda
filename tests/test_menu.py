"""Структура меню: главное меню и раздел «Учёт и финансы»."""
import re
from pathlib import Path

from app.bot import keyboards


def _callbacks(kb) -> list[str]:
    return [b.callback_data for row in kb.inline_keyboard for b in row]


def test_main_menu_sections():
    assert _callbacks(keyboards.main_menu_kb()) == [
        "menu:directory", "menu:finance", "menu:tasks", "menu:settings",
    ]


def test_finance_menu_sections():
    assert _callbacks(keyboards.finance_menu_kb()) == [
        "menu:expense", "menu:readings", "menu:pay", "menu:reports", "menu:map", "nav:home",
    ]


def test_every_menu_button_has_handler():
    src = "".join(p.read_text(encoding="utf-8") for p in Path("app/bot").glob("handlers*.py"))
    handled = set(re.findall(r'F\.data == "([^"]+)"', src))
    for cb in _callbacks(keyboards.main_menu_kb()) + _callbacks(keyboards.finance_menu_kb()):
        assert cb in handled, cb
