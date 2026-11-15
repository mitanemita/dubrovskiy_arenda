"""Отчёт «🗺 Карта базы»: данные из БД по литере, одно сообщение в чате."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot import handlers_admin as ha
from app.db.base import Base
from app.db.enums import OrgType, TaxMode
from app.db.models import Landlord
from app.services import directory_service, karta_service


def _poly(html: str, key: str) -> str:
    return re.search(rf'data-k="{key}"><polygon class="(c-[a-z]+)"', html).group(1)


async def _lease(session, lid, *, litera, label, rent, tenant="ООО Ромашка", inn="7100000001"):
    p = await directory_service.create_premises(session, landlord_id=lid, label=label, litera=litera)
    t = await directory_service.create_tenant(session, landlord_id=lid, name=tenant, type=OrgType.ooo, inn=inn)
    await session.flush()
    await directory_service.create_lease(
        session, tenant_id=t.id, premises_id=p.id, contract_no=f"{litera}-1",
        contract_date=date(2026, 11, 1), rent_amount=Decimal(rent),
    )
    await session.flush()


@pytest_asyncio.fixture
async def landlord(session):
    lord = Landlord(name="ИП", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn)
    session.add(lord)
    await session.flush()
    return lord


async def test_map_without_leases_equals_template(session, landlord):
    html, summary = await karta_service.render_html(session, landlord.id)
    assert html == karta_service.TEMPLATE_PATH.read_bytes()
    assert summary["issues"] == []


async def test_lease_by_litera_colors_map(session, landlord):
    # Д в шаблоне свободна; договор на помещение с литерой «д » (регистр/пробел не важны)
    await _lease(session, landlord.id, litera="д ", label="Склад", rent="100000")
    # литера другой базы — на этом плане нет
    await _lease(session, landlord.id, litera="Б-17", label="Ангар", rent="5000", tenant="ИП Петров", inn="710000000001")
    html, summary = await karta_service.render_html(session, landlord.id, now=datetime(2026, 11, 13, 10, 0))
    html = html.decode()
    assert _poly(html, "Д") == "c-busy" and _poly(html, "2Д") == "c-busy"
    assert "ООО Ромашка" in html and "100000 ₽" in html and "498,3 ₽/м²" in html
    assert summary["not_on_plan"] == ["Б-17"]
    cap = karta_service.caption(summary)
    assert "13.11.2026" in cap and "Б-17" in cap and len(cap) < 1024


async def test_latin_lookalike_litera_matches(session, landlord):
    await _lease(session, landlord.id, litera="K1", label="Холодильник", rent="200000")  # латинская K
    html, summary = await karta_service.render_html(session, landlord.id)
    assert "200000 ₽" in html.decode() and summary["not_on_plan"] == []


# --- Хендлеры: одно сообщение-панель ----------------------------------------
@pytest_asyncio.fixture
async def bot_env(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with f() as s:
        s.add(Landlord(name="ИП", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn))
        await s.commit()
    monkeypatch.setattr(ha, "async_session_factory", f)
    yield
    await engine.dispose()


def _panel_msg(mid: int):
    chat = SimpleNamespace(id=1)
    doc = SimpleNamespace(message_id=mid + 1, chat=chat)
    return SimpleNamespace(
        message_id=mid, chat=chat,
        edit_text=AsyncMock(), delete=AsyncMock(), edit_media=AsyncMock(),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=mid + 2, chat=chat)),
        answer_document=AsyncMock(return_value=doc),
    )


async def test_map_button_replaces_panel_with_document(bot_env):
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))
    msg = _panel_msg(10)
    cb = SimpleNamespace(data="rep:map:202611", message=msg, from_user=SimpleNamespace(id=1), answer=AsyncMock())
    await ha.report_map(cb, state)
    msg.answer_document.assert_awaited_once()
    file = msg.answer_document.await_args.args[0]
    assert file.filename.startswith("Karta-") and file.filename.endswith(".html")
    msg.delete.assert_awaited_once()            # прежняя панель удалена
    assert ha._PANELS[1] == 11                  # панель — это документ

    # «Обновить» — тот же документ, файл заменяется на месте
    doc = _panel_msg(11)
    cb2 = SimpleNamespace(data="rep:mapre:202611", message=doc, from_user=SimpleNamespace(id=1), answer=AsyncMock())
    await ha.report_map_refresh(cb2, state)
    doc.edit_media.assert_awaited_once()
    doc.answer_document.assert_not_awaited()


async def test_back_from_document_deletes_it(bot_env):
    from aiogram.exceptions import TelegramBadRequest
    doc = _panel_msg(20)
    doc.edit_text.side_effect = TelegramBadRequest(method=None, message="there is no text in the message to edit")
    await ha.edit_or_send(doc, "📊 Сводка")
    doc.answer.assert_awaited_once()
    doc.delete.assert_awaited_once()
    assert ha._PANELS[1] == 22
