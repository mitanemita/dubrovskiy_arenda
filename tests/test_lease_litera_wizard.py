"""Мастер «добавить договор»: после выбора помещения обязательно спрашивается литера."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot import handlers_directory as hd
from app.db.base import Base
from app.db.enums import OrgType, TaxMode
from app.db.models import Landlord, Lease, Premises, Tenant


@pytest_asyncio.fixture
async def factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(hd, "async_session_factory", f)
    screens: list[str] = []

    async def show(message, text, reply_markup=None):
        screens.append(text)
    monkeypatch.setattr(hd, "edit_or_send", show)
    monkeypatch.setattr(hd, "wiz_reply", show)
    async with f() as s:
        lord = Landlord(name="ИП", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn)
        s.add(lord)
        await s.flush()
        s.add_all([Premises(landlord_id=lord.id, label="Склад у ворот", litera="Д"),
                   Tenant(landlord_id=lord.id, name="ООО Ромашка", type=OrgType.ooo, inn="7100000001")])
        await s.commit()
    yield f, screens
    await engine.dispose()


def _state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _cb(data: str):
    return SimpleNamespace(data=data, message=SimpleNamespace(), from_user=SimpleNamespace(id=1), answer=AsyncMock())


def _msg(text: str):
    return SimpleNamespace(text=text, from_user=SimpleNamespace(id=1), chat=SimpleNamespace(id=1))


async def _run(state, factory, litera_input):
    f, screens = factory
    async with f() as s:
        pid = (await s.execute(select(Premises.id))).scalar_one()
        tid = (await s.execute(select(Tenant.id))).scalar_one()
    await state.set_state(hd.LeaseFSM.tenant)
    await hd.lease_pick_tenant(_cb(f"dlt:{tid}"), state)
    await hd.lease_pick_premises(_cb(f"dlp:{pid}"), state)
    assert await state.get_state() == hd.LeaseFSM.litera.state
    assert "литера" in screens[-1].lower()
    await litera_input()
    assert await state.get_state() == hd.LeaseFSM.contract_no.state
    await hd.lease_contract_no(_msg("7/2026"), state)
    await hd.lease_contract_date(_msg(date.today().strftime("%d.%m.%Y")), state)
    await hd.lease_rent(_msg("45000"), state)
    await hd.lease_payment_day(_msg("5"), state)
    async with f() as s:
        p = (await s.execute(select(Premises))).scalar_one()
        assert (await s.execute(select(Lease))).scalar_one().premises_id == p.id
        return p, screens


async def test_wizard_new_litera(factory):
    state = _state()

    async def enter():
        await hd.lease_litera(_msg("   "), state)          # пусто — не принимается
        assert await state.get_state() == hd.LeaseFSM.litera.state
        await hd.lease_litera(_msg("Корпус 3"), state)      # литеры другой базы
    p, screens = await _run(state, factory, enter)
    assert p.litera == "Корпус 3" and p.is_occupied
    assert "литера Корпус 3" in screens[-1]


async def test_wizard_keep_litera(factory):
    state = _state()

    async def keep():
        await hd.lease_keep_litera(_cb("dll:keep"), state)
    p, _ = await _run(state, factory, keep)
    assert p.litera == "Д"


async def test_site_buttons_assign_and_remove_without_changing_lease_data(factory):
    from app.db.models import PropertySite

    f, screens = factory
    state = _state()
    async with f() as s:
        pid = (await s.execute(select(Premises.id))).scalar_one()
        before = (await s.execute(select(Tenant.name))).scalars().all()
    await hd.premises_site_menu(_cb(f"psite:{pid}"), state)
    assert "Выберите площадку" in screens[-1]
    await hd.premises_set_site(_cb(f"pset:{pid}:kr"), state)
    async with f() as s:
        p = await s.get(Premises, pid)
        site = await s.get(PropertySite, p.site_id)
        assert site.name == "Краснодар" and site.template_key == "krasnodar"
        assert (await s.execute(select(Tenant.name))).scalars().all() == before
    assert "Краснодар" in screens[-1]
    await hd.premises_set_site(_cb(f"pset:{pid}:none"), state)
    async with f() as s:
        assert (await s.get(Premises, pid)).site_id is None
        assert (await s.execute(select(Tenant.name))).scalars().all() == before


async def test_premises_readiness_selected_from_buttons(factory):
    f, screens = factory
    state = _state()
    async with f() as s:
        pid = (await s.execute(select(Premises.id))).scalar_one()
    await hd.premises_readiness_menu(_cb(f"pread:{pid}"), state)
    assert "готовность" in screens[-1].lower()
    await hd.premises_set_readiness(_cb(f"prset:{pid}:build"), state)
    async with f() as s:
        assert (await s.get(Premises, pid)).readiness == "build"
    assert "Стройка" in screens[-1]
