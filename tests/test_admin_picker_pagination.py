"""Страницы выбора счётчика и договора в разделе учёта."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot import handlers_admin as ha
from app.db.base import Base
from app.db.enums import ChargeType, OrgType, TaxMode
from app.db.models import Charge, Landlord, Lease, Meter, Premises, Tenant


@pytest_asyncio.fixture
async def picker_env(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ha, "async_session_factory", factory)
    monkeypatch.setattr(ha, "_tasks_notif_count", AsyncMock(return_value=0))
    screens = []

    async def show(message, text, reply_markup=None):
        screens.append((text, reply_markup))

    monkeypatch.setattr(ha, "edit_or_send", show)
    async with factory() as session:
        session.add(Landlord(name="ИП", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn))
        await session.commit()
    yield factory, screens
    await engine.dispose()


def _cb(data):
    return SimpleNamespace(data=data, from_user=SimpleNamespace(id=1), message=SimpleNamespace(), answer=AsyncMock())


def _state():
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _buttons(screen):
    return [button for row in screen[1].inline_keyboard for button in row]


async def _populate(factory, count):
    async with factory() as session:
        p = Premises(landlord_id=1, label="Склад")
        t = Tenant(landlord_id=1, name="Арендатор", type=OrgType.ooo, inn="7100000001")
        session.add_all([p, t])
        await session.flush()
        session.add_all([Meter(premises_id=p.id, serial_no=f"M{i}") for i in range(count)])
        session.add_all([Lease(tenant_id=t.id, premises_id=p.id, contract_no=str(i),
                               contract_date=date(2026, 1, 1), rent_amount=Decimal("1000"))
                         for i in range(count)])
        await session.commit()


@pytest.mark.parametrize("menu,expected", [
    (ha.readings_menu, "Счётчиков пока нет."),
    (ha.payment_manual_menu, "Активных договоров нет."),
])
async def test_empty_pickers(picker_env, menu, expected):
    _, screens = picker_env
    await menu(_cb("menu"), _state())
    assert expected in screens[-1][0]
    assert any(b.callback_data == "menu:finance" for b in _buttons(screens[-1]))


async def test_meter_pages_preserve_state_and_select_correct_item(picker_env):
    factory, screens = picker_env
    await _populate(factory, 21)
    state = _state()
    await ha.readings_menu(_cb("menu:readings"), state)
    buttons = _buttons(screens[-1])
    assert len([b for b in buttons if b.callback_data.startswith("mr:")]) == 10
    assert any(b.callback_data == "mrpg:10" for b in buttons)
    await state.update_data(marker="preserved")
    await ha.readings_picker_page(_cb("mrpg:10"), state)
    assert (await state.get_data())["marker"] == "preserved"
    buttons = _buttons(screens[-1])
    assert any(b.callback_data == "mrpg:0" for b in buttons)
    assert any(b.callback_data == "mrpg:20" for b in buttons)
    await ha.readings_picker_page(_cb("mrpg:999"), state)
    buttons = _buttons(screens[-1])
    assert [b.callback_data for b in buttons if b.callback_data.startswith("mr:")] == ["mr:21"]
    await ha.reading_pick(_cb("mr:21"), state)
    assert (await state.get_data())["meter_id"] == 21
    assert await state.get_state() == ha.ReadingFSM.value.state


async def test_payment_pages_and_selection(picker_env, monkeypatch):
    factory, screens = picker_env
    await _populate(factory, 11)
    state = _state()
    await ha.payment_manual_menu(_cb("menu:pay"), state)
    assert any(b.callback_data == "pmpg:10" for b in _buttons(screens[-1]))
    await state.update_data(marker="preserved")
    await ha.payment_picker_page(_cb("pmpg:10"), state)
    assert (await state.get_data())["marker"] == "preserved"
    buttons = _buttons(screens[-1])
    assert [b.callback_data for b in buttons if b.callback_data.startswith("pm:")] == ["pm:11"]
    assert any(b.callback_data == "pmpg:0" for b in buttons)
    monkeypatch.setattr(ha.billing_service, "create_rent_charge", AsyncMock())
    monkeypatch.setattr(ha.billing_service, "create_electricity_charge", AsyncMock())
    await ha.payment_manual_pick(_cb("pm:11"), state)
    assert (await state.get_data())["lease_id"] == 11
    assert any(b.callback_data == "paylump:11" for b in _buttons(screens[-1]))


async def test_charge_pages_filter_paid_and_preserve_selection(picker_env, monkeypatch):
    factory, screens = picker_env
    await _populate(factory, 1)
    async with factory() as session:
        session.add_all([Charge(lease_id=1, type=ChargeType.other, period=date(2026, 1, 1),
                                amount=Decimal("100"), paid_amount=Decimal("0")) for _ in range(21)])
        session.add(Charge(lease_id=1, type=ChargeType.other, period=date(2026, 1, 1),
                           amount=Decimal("100"), paid_amount=Decimal("100")))
        await session.commit()
    monkeypatch.setattr(ha.billing_service, "create_rent_charge", AsyncMock())
    monkeypatch.setattr(ha.billing_service, "create_electricity_charge", AsyncMock())
    state = _state()
    await ha.payment_manual_pick(_cb("pm:1"), state)
    buttons = _buttons(screens[-1])
    assert len([b for b in buttons if b.callback_data.startswith("payc:")]) == 10
    assert any(b.callback_data == "pcpg:1:10" for b in buttons)
    assert "Стр. 1/3" in screens[-1][0]
    await state.update_data(marker="preserved")
    await ha.payment_charge_page(_cb("pcpg:1:20"), state)
    assert (await state.get_data())["marker"] == "preserved"
    buttons = _buttons(screens[-1])
    assert [b.callback_data for b in buttons if b.callback_data.startswith("payc:")] == ["payc:21"]
    assert any(b.callback_data == "pcpg:1:10" for b in buttons)
    assert any(b.callback_data == "paylump:1" for b in buttons)
    assert any(b.callback_data == "menu:pay" for b in buttons)
    await ha.payment_charge_start(_cb("payc:21"), state)
    assert (await state.get_data())["charge_id"] == 21
    assert await state.get_state() == ha.PayFSM.amount.state
