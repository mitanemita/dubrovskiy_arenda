"""Доступ к боту: посторонние не проходят ни к сообщениям, ни к кнопкам."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot import access


@pytest.fixture
def allow_only_111(monkeypatch):
    async def fake(tg_id: int) -> bool:
        return tg_id == 111
    monkeypatch.setattr(access, "is_allowed", fake)


async def test_stranger_is_blocked(allow_only_111):
    handler = AsyncMock()
    event = SimpleNamespace(from_user=SimpleNamespace(id=999), answer=AsyncMock())
    assert await access.AccessMiddleware()(handler, event, {}) is None
    handler.assert_not_called()


async def test_operator_passes(allow_only_111):
    handler = AsyncMock(return_value="ok")
    event = SimpleNamespace(from_user=SimpleNamespace(id=111), answer=AsyncMock())
    assert await access.AccessMiddleware()(handler, event, {}) == "ok"
    handler.assert_awaited_once()


def test_access_checked_for_messages_and_buttons():
    """Проверка доступа подключена и к сообщениям, и к нажатиям кнопок."""
    from pathlib import Path

    src = Path("app/bot/runner.py").read_text(encoding="utf-8")
    assert "dp.message.outer_middleware(AccessMiddleware())" in src
    assert "dp.callback_query.outer_middleware(AccessMiddleware())" in src
