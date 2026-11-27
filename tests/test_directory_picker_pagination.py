"""Directory pickers keep their state and select the intended database objects."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot import handlers_directory as hd
from app.db.base import Base
from app.db.enums import OrgType
from app.db.models import Landlord, Lease, Premises, PropertySite, Tenant


@pytest_asyncio.fixture
async def directory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add_all([
            Landlord(id=1, name="Свой", type=OrgType.ip, inn="111111111111"),
            Landlord(id=2, name="Другой", type=OrgType.ip, inn="222222222222"),
        ])
        session.add_all([
            Tenant(id=i, landlord_id=1, name=f"Арендатор {i:02}", type=OrgType.ooo, inn=f"{i:010}")
            for i in range(1, 22)
        ])
        session.add(Tenant(id=99, landlord_id=2, name="Чужой", type=OrgType.ooo, inn="9999999999"))
        session.add_all([
            Premises(id=i, landlord_id=1, label=f"Склад {i:02}", litera=f"Л{i}")
            for i in range(1, 22)
        ])
        session.add(Premises(id=99, landlord_id=2, label="Чужой", litera="Я"))
        session.add_all([PropertySite(id=i, landlord_id=1, name=f"Площадка {i:02}") for i in range(1, 22)])
        session.add(PropertySite(id=99, landlord_id=2, name="Чужая площадка"))
        session.add(Lease(id=1, tenant_id=1, premises_id=1, contract_no="7",
                          contract_date=date(2026, 1, 1), rent_amount=Decimal("1000")))
        await session.commit()
    monkeypatch.setattr(hd, "async_session_factory", factory)
    monkeypatch.setattr(hd, "_landlord_id", AsyncMock(return_value=1))
    screens = []

    async def show(message, text, reply_markup=None):
        screens.append((text, reply_markup))
    monkeypatch.setattr(hd, "edit_or_send", show)
    monkeypatch.setattr(hd, "wiz_reply", show)
    yield factory, screens
    await engine.dispose()


def _state():
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _cb(data):
    return SimpleNamespace(data=data, message=SimpleNamespace(), from_user=SimpleNamespace(id=1), answer=AsyncMock())


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


async def test_lease_wizard_page_selection_and_back_keep_context(directory):
    _, screens = directory
    state = _state()
    await hd.lease_add(_cb("dadd:leases"), state)
    assert await state.get_state() == hd.LeaseFSM.tenant.state
    assert "Стр. 1/3" in screens[-1][0]
    assert len(screens[-1][1].inline_keyboard[0]) == 5
    assert "dlt:11" not in [b.callback_data for b in _buttons(screens[-1][1])]

    await hd.lease_tenant_page(_cb("dltpage:10"), state)
    assert "Стр. 2/3" in screens[-1][0]
    assert next(b.text for b in _buttons(screens[-1][1]) if b.callback_data == "dlt:12") == "12"
    await hd.lease_pick_tenant(_cb("dlt:12"), state)
    assert await state.get_state() == hd.LeaseFSM.premises.state
    assert (await state.get_data())["tenant_id"] == 12

    await hd.lease_premises_page(_cb("dlppage:10"), state)
    assert "Стр. 2/3" in screens[-1][0]
    assert (await state.get_data())["tenant_id"] == 12
    await hd.lease_back_to_tenants(_cb("dlback:tenant"), state)
    assert await state.get_state() == hd.LeaseFSM.tenant.state
    assert "Стр. 2/3" in screens[-1][0]

    await hd.lease_pick_tenant(_cb("dlt:12"), state)
    await hd.lease_premises_page(_cb("dlppage:10"), state)
    await hd.lease_pick_premises(_cb("dlp:12"), state)
    data = await state.get_data()
    assert await state.get_state() == hd.LeaseFSM.litera.state
    assert (data["tenant_id"], data["premises_id"], data["premises_litera"]) == (12, 12, "Л12")


async def test_meter_selection_paginates_and_clamps(directory):
    _, screens = directory
    state = _state()
    await hd.meter_add(_cb("dadd:meters"), state)
    assert await state.get_state() == hd.MeterFSM.premises.state
    await hd.meter_premises_page(_cb("dmppage:9990"), state)
    assert "Стр. 3/3" in screens[-1][0]
    assert [b.callback_data for b in _buttons(screens[-1][1]) if b.callback_data.startswith("dmp:")] == ["dmp:21"]
    await hd.meter_pick_premises(_cb("dmp:21"), state)
    assert await state.get_state() == hd.MeterFSM.serial.state
    assert (await state.get_data())["premises_id"] == 21


async def test_lease_picker_displays_site_status_and_escapes_names(directory):
    factory, screens = directory
    async with factory() as session:
        premises = await session.get(Premises, 12)
        premises.site_id = 1
        premises.label = "Склад <12> & ворота"
        premises.readiness = "notready"
        await session.commit()
    state = _state()
    await hd.lease_add(_cb("dadd:leases"), state)
    await hd.lease_pick_tenant(_cb("dlt:1"), state)
    assert "арендовано" in screens[-1][0]  # активный договор, даже если флаг не выставлен
    await hd.lease_premises_page(_cb("dlppage:10"), state)
    text = screens[-1][0]
    assert "Склад &lt;12&gt; &amp; ворота" in text
    assert "Площадка 01" in text and "не готово" in text


async def test_wizard_rejects_another_landlords_objects(directory):
    state = _state()
    await hd.lease_add(_cb("dadd:leases"), state)
    foreign_tenant = _cb("dlt:99")
    await hd.lease_pick_tenant(foreign_tenant, state)
    foreign_tenant.answer.assert_awaited_once_with("Арендатор не найден", show_alert=True)
    assert await state.get_state() == hd.LeaseFSM.tenant.state
    assert "tenant_id" not in await state.get_data()

    await hd.lease_pick_tenant(_cb("dlt:1"), state)
    foreign_premises = _cb("dlp:99")
    await hd.lease_pick_premises(foreign_premises, state)
    foreign_premises.answer.assert_awaited_once_with("Помещение не найдено", show_alert=True)
    assert await state.get_state() == hd.LeaseFSM.premises.state
    assert "premises_id" not in await state.get_data()

    await hd.meter_add(_cb("dadd:meters"), state)
    foreign_meter = _cb("dmp:99")
    await hd.meter_pick_premises(foreign_meter, state)
    foreign_meter.answer.assert_awaited_once_with("Помещение не найдено", show_alert=True)
    assert await state.get_state() == hd.MeterFSM.premises.state


async def test_site_and_lease_reassignment_page_and_validate(directory):
    factory, screens = directory
    state = _state()
    await hd.premises_site_menu(_cb("psite:1:10"), state)
    assert "Стр. 2/3" in screens[-1][0]
    assert "psite:1:20" in [b.callback_data for b in _buttons(screens[-1][1])]
    foreign_site = _cb("pset:1:99")
    await hd.premises_set_site(foreign_site, state)
    foreign_site.answer.assert_awaited_once_with("Площадка не найдена", show_alert=True)

    await hd.lease_reassign_menu(_cb("lprem:1:10"), state)
    assert "Стр. 2/3" in screens[-1][0]
    assert "lpremok:1:12" in [b.callback_data for b in _buttons(screens[-1][1])]
    foreign_premises = _cb("lpremok:1:99")
    await hd.lease_reassign_apply(foreign_premises, state)
    foreign_premises.answer.assert_awaited_once_with("Договор или помещение не найдено", show_alert=True)
    async with factory() as session:
        assert (await session.get(Lease, 1)).premises_id == 1
        assert (await session.get(Premises, 1)).site_id is None
    await hd.lease_reassign_apply(_cb("lpremok:1:12"), state)
    async with factory() as session:
        assert (await session.get(Lease, 1)).premises_id == 12
