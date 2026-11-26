"""Карта базы по ссылке: подпись токена, веб-страница, кнопка в боте, откат на файл."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot import handlers_admin as ha
from app.bot import karta_web
from app.config import get_settings
from app.services import karta_link, karta_service
from tests.test_karta_report import _panel_msg, bot_env  # noqa: F401  (фикстура)


@pytest.fixture(autouse=True)
def cfg(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "bot_token", "123:test")
    monkeypatch.setattr(s, "public_base_url", "")

    def _base(url: str):
        monkeypatch.setattr(s, "public_base_url", url)
    return _base


def _state():
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _cb(msg, data="menu:map"):
    return SimpleNamespace(data=data, message=msg, from_user=SimpleNamespace(id=1), answer=AsyncMock())


def test_token_roundtrip_and_tamper():
    t = karta_link.make_token(7, days=1, now=1000)
    assert karta_link.verify_token(t, now=1001) == 7
    assert karta_link.verify_token(t, now=1000 + 86401) is None          # истёк
    _, exp, sig = t.split(".")
    assert karta_link.verify_token(f"8.{exp}.{sig}", now=1001) is None   # подмена арендодателя
    assert karta_link.verify_token("garbage", now=1001) is None


def test_map_url_needs_base(cfg):
    assert karta_link.map_url(1) is None
    cfg("https://karta.example.ru/")
    assert karta_link.map_url(1).startswith("https://karta.example.ru/karta/1.")


async def test_web_page_renders_map(bot_env, monkeypatch):  # noqa: F811
    from aiohttp.test_utils import TestClient, TestServer
    # страница берёт ту же тестовую БД, что и бот
    monkeypatch.setattr(karta_web, "async_session_factory", ha.async_session_factory)
    async with TestClient(TestServer(karta_web.build_app())) as client:
        r = await client.get(f"/karta/{karta_link.make_token(1)}")
        assert r.status == 200 and r.headers["Cache-Control"] == "no-store"
        assert "Интерактивная карта базы" in await r.text()
        assert (await client.get("/karta/1.9999999999.bad")).status == 404
        assert (await client.get("/health")).status == 200


async def test_web_not_started_without_base_url():
    assert await karta_web.start() is None


async def test_web_starts_on_port(cfg, monkeypatch):
    import aiohttp
    from aiohttp.test_utils import unused_port
    port = unused_port()
    cfg("http://1.2.3.4:%d" % port)
    monkeypatch.setattr(get_settings(), "karta_port", port)
    runner = await karta_web.start()
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(f"http://127.0.0.1:{port}/health") as r:
                assert r.status == 200
    finally:
        await runner.cleanup()


async def test_no_base_url_keeps_file(bot_env):  # noqa: F811
    msg = _panel_msg(10)
    await ha.base_map(_cb(msg), _state())
    msg.answer_document.assert_awaited_once()


async def test_https_gives_webapp_button(bot_env, cfg):  # noqa: F811
    cfg("https://karta.example.ru")
    msg = _panel_msg(10)
    msg.chat.type = "private"
    await ha.base_map(_cb(msg), _state())
    msg.answer_document.assert_not_awaited()
    assert "Открыть карту" in msg.edit_text.await_args.args[0]
    btn = msg.edit_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert btn.web_app and btn.web_app.url.startswith("https://karta.example.ru/karta/")

    # «Обновить» под старым сообщением-файлом -> панель заменяется текстом со ссылкой
    doc = _panel_msg(20)
    doc.edit_text.side_effect = TelegramBadRequest(method=None, message="there is no text in the message to edit")
    await ha.base_map_refresh(_cb(doc, "map:refresh"), _state())
    assert doc.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].web_app
    doc.delete.assert_awaited_once()


async def test_http_gives_url_button(bot_env, cfg):  # noqa: F811
    cfg("http://1.2.3.4:8080")
    msg = _panel_msg(10)
    await ha.base_map(_cb(msg), _state())
    btn = msg.edit_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert btn.url.startswith("http://1.2.3.4:8080/karta/") and btn.web_app is None


async def test_rejected_link_falls_back_to_file(bot_env, cfg):  # noqa: F811
    cfg("http://localhost:8080")

    def _reject_links(*_a, reply_markup=None, **_k):  # Telegram отвергает кнопку-ссылку
        if reply_markup and any(b.url for row in reply_markup.inline_keyboard for b in row):
            raise TelegramBadRequest(method=None, message="BUTTON_URL_INVALID")

    msg = _panel_msg(10)
    msg.edit_text.side_effect = _reject_links
    msg.answer.side_effect = _reject_links
    await ha.base_map(_cb(msg), _state())
    msg.answer_document.assert_awaited_once()          # карта всё равно пришла — файлом


def test_caption_link_hint():
    s = {"counts": {"busy": 1, "free": 0, "notready": 0}, "rent_total": Decimal(1), "no_rent": 0,
         "not_on_plan": [], "now": datetime(2026, 1, 1), "issues": []}
    assert "Открыть карту" in karta_service.caption(s, link=True)
    assert "Откройте файл" in karta_service.caption(s)
