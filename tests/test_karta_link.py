"""Карта базы по ссылке: подпись токена, страница API, кнопка в боте, откат на файл."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot import handlers_admin as ha
from app.config import get_settings
from app.db.enums import OrgType, TaxMode
from app.db.models import Landlord
from app.services import karta_link, karta_service

# фикстура bot_env — из соседнего теста отчёта
from tests.test_karta_report import _panel_msg, bot_env  # noqa: F401


@pytest.fixture
def base_url(monkeypatch):
    def _set(url: str):
        monkeypatch.setattr(get_settings(), "public_base_url", url)
    yield _set


def test_token_roundtrip_and_tamper():
    t = karta_link.make_token(7, days=1, now=1000)
    assert karta_link.verify_token(t, now=1001) == 7
    assert karta_link.verify_token(t, now=1000 + 86401) is None          # истёк
    _, exp, sig = t.split(".")
    assert karta_link.verify_token(f"8.{exp}.{sig}", now=1001) is None   # подмена арендодателя
    assert karta_link.verify_token("garbage", now=1001) is None


def test_map_url_needs_base(base_url):
    base_url("")
    assert karta_link.map_url(1) is None
    base_url("https://karta.example.ru/")
    assert karta_link.map_url(1).startswith("https://karta.example.ru/karta/1.")


async def test_api_page_renders_fresh_map(client, session):
    lord = Landlord(name="ИП", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn)
    session.add(lord)
    await session.flush()
    r = await client.get(f"/karta/{karta_link.make_token(lord.id)}")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-store"
    assert "Интерактивная карта базы" in r.text
    assert (await client.get("/karta/1.9999999999.bad")).status_code == 404


def _cb(msg, data="rep:map:202611"):
    return SimpleNamespace(data=data, message=msg, from_user=SimpleNamespace(id=1), answer=AsyncMock())


async def test_bot_sends_webapp_button_when_https(bot_env, base_url):  # noqa: F811
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))
    base_url("https://karta.example.ru")
    msg = _panel_msg(10)
    msg.chat.type = "private"
    await ha.report_map(_cb(msg), state)
    msg.answer_document.assert_not_awaited()            # файла больше нет
    text = msg.edit_text.await_args.args[0]
    kb = msg.edit_text.await_args.kwargs["reply_markup"]
    btn = kb.inline_keyboard[0][0]
    assert btn.web_app and btn.web_app.url.startswith("https://karta.example.ru/karta/")
    assert "Открыть карту" in text

    # «Обновить» на старом сообщении-файле: панель заменяется текстом со ссылкой
    from aiogram.exceptions import TelegramBadRequest
    doc = _panel_msg(20)
    doc.edit_text.side_effect = TelegramBadRequest(method=None, message="there is no text in the message to edit")
    await ha.report_map_refresh(_cb(doc, "rep:mapre:202611"), state)
    doc.answer.assert_awaited_once()
    assert doc.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].web_app
    doc.delete.assert_awaited_once()


async def test_bot_http_uses_url_button(bot_env, base_url):  # noqa: F811
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))
    base_url("http://1.2.3.4:8000")
    msg = _panel_msg(10)
    await ha.report_map(_cb(msg), state)
    btn = msg.edit_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert btn.url.startswith("http://1.2.3.4:8000/karta/") and btn.web_app is None


async def test_bot_falls_back_to_file_if_link_rejected(bot_env, base_url):  # noqa: F811
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))
    from aiogram.exceptions import TelegramBadRequest
    base_url("http://localhost:8000")
    msg = _panel_msg(10)

    def _reject_links(*_a, reply_markup=None, **_k):  # Telegram отвергает только кнопку-ссылку
        if reply_markup and any(b.url for row in reply_markup.inline_keyboard for b in row):
            raise TelegramBadRequest(method=None, message="BUTTON_URL_INVALID")

    msg.edit_text.side_effect = _reject_links
    msg.answer.side_effect = _reject_links
    await ha.report_map(_cb(msg), state)
    msg.answer_document.assert_awaited_once()           # карта всё равно пришла — файлом


def test_caption_link_hint():
    from datetime import datetime
    from decimal import Decimal
    s = {"counts": {"busy": 1, "free": 0, "notready": 0}, "rent_total": Decimal(1), "no_rent": 0,
         "not_on_plan": [], "now": datetime(2026, 1, 1), "issues": []}
    assert "Открыть карту" in karta_service.caption(s, link=True)
    assert "Откройте файл" in karta_service.caption(s)
