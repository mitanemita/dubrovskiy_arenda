"""Paging preserves the period, target IDs and adjustment entry points."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot import handlers_admin as ha
from app.db.base import Base
from app.db.enums import ChargeType, ExpenseCategory, OrgType, TaxMode
from app.db.models import Charge, Expense, Landlord, Lease, Premises, Tenant


def _cb(data):
    return SimpleNamespace(data=data, message=SimpleNamespace(), from_user=SimpleNamespace(id=1), answer=AsyncMock())


def _state():
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


@pytest_asyncio.fixture
async def records(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:', poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(ha, 'async_session_factory', factory)
    monkeypatch.setattr(ha, '_landlord_id', AsyncMock(return_value=1))
    screens = []
    async def show(message, text, reply_markup=None):
        screens.append((text, reply_markup))
    monkeypatch.setattr(ha, 'edit_or_send', show)
    async with factory() as session:
        landlord = Landlord(name='ИП', type=OrgType.ip, inn='710000000000', tax_mode=TaxMode.ausn)
        session.add(landlord)
        await session.flush()
        tenant = Tenant(landlord_id=landlord.id, name='Арендатор', type=OrgType.ooo, inn='7100000001')
        premises = Premises(landlord_id=landlord.id, label='Склад')
        session.add_all([tenant, premises]); await session.flush()
        lease = Lease(tenant_id=tenant.id, premises_id=premises.id, contract_no='1',
                      contract_date=date(2026, 1, 1), rent_amount=Decimal('1000'))
        session.add(lease); await session.flush()
        for i in range(1, 24):
            session.add(Charge(lease_id=lease.id, type=ChargeType.rent, period=date(2026, 4, 1),
                               amount=Decimal(i), paid_amount=Decimal('0')))
            session.add(Expense(landlord_id=landlord.id, category=ExpenseCategory.other,
                                period=date(2026, 4, 1), amount=Decimal(i)))
        session.add(Charge(lease_id=lease.id, type=ChargeType.rent, period=date(2026, 5, 1),
                           amount=Decimal('999'), paid_amount=Decimal('0')))
        session.add(Expense(landlord_id=landlord.id, category=ExpenseCategory.other,
                            period=date(2026, 5, 1), amount=Decimal('999')))
        await session.commit()
    yield screens
    await engine.dispose()


def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


@pytest.mark.parametrize(('kind', 'handler', 'callback', 'pick'), [
    ('expense', ha.expense_list, 'exp_list:202604', 'ecorr'),
    ('charge', ha.lease_charges_list, 'lchg:1:202604', 'ccorr'),
])
async def test_paging_and_month_navigation(records, kind, handler, callback, pick):
    state = _state()
    await handler(_cb(callback), state)
    text, markup = records[-1]
    assert 'Апрель 2026' in text and 'Стр. 1/3' in text
    assert [b.text for b in _buttons(markup) if b.callback_data.startswith(pick + ':')] == [str(i) for i in range(1, 11)]
    assert len(markup.inline_keyboard[0]) == len(markup.inline_keyboard[1]) == 5
    assert callback + ':10' in [b.callback_data for b in _buttons(markup)]
    await handler(_cb(callback + ':10'), state)
    text, markup = records[-1]
    assert 'Стр. 2/3' in text and '11.' in text and '20.' in text and '21.' not in text
    assert next(b.callback_data for b in _buttons(markup) if b.text == '11') == pick + ':11'
    assert callback + ':0' in [b.callback_data for b in _buttons(markup)]
    assert callback + ':20' in [b.callback_data for b in _buttons(markup)]
    month = next(b.callback_data for b in _buttons(markup) if b.text == 'След. месяц ▶️')
    assert '202605' in month and len(month.split(':')) == len(callback.split(':'))
    await handler(_cb(month), state)
    assert 'Май 2026' in records[-1][0] and '999' in records[-1][0] and 'Стр. 1/1' in records[-1][0]
    await handler(_cb(callback + ':9990'), state)
    text, markup = records[-1]
    assert 'Стр. 3/3' in text and '23.' in text
    assert len([b for b in _buttons(markup) if b.callback_data.startswith(pick + ':')]) == 3


@pytest.mark.parametrize(('handler', 'callback', 'pick', 'entity'), [
    (ha.expense_list, 'exp_list:202604:10', ha.expense_correct_start, 'expense'),
    (ha.lease_charges_list, 'lchg:1:202604:10', ha.charge_correct_start, 'charge'),
])
async def test_second_page_starts_original_adjustment(records, handler, callback, pick, entity):
    state = _state()
    await handler(_cb(callback), state)
    markup = records[-1][1]
    target = next(b.callback_data for b in _buttons(markup) if b.text == '11')
    await pick(_cb(target), state)
    data = await state.get_data()
    assert data['entity_type'] == entity and data['entity_id'] == 11
    assert await state.get_state() == ha.AdjustFSM.amount.state


@pytest.mark.parametrize(('handler', 'callback', 'empty'), [
    (ha.expense_list, 'exp_list:202603:10', '— пусто'),
    (ha.lease_charges_list, 'lchg:1:202603:10', '— начислений нет'),
])
async def test_empty_period_keeps_month_and_back_navigation(records, handler, callback, empty):
    await handler(_cb(callback), _state())
    text, markup = records[-1]
    assert empty in text and 'Март 2026' in text
    assert any('Пред. месяц' in b.text for b in _buttons(markup))
    assert any('След. месяц' in b.text for b in _buttons(markup))
    assert any('К расходам' in b.text or 'К договору' in b.text for b in _buttons(markup))

@pytest.mark.parametrize(('handler', 'callback'), [
    (ha.expense_list, 'exp_list'),
    (ha.lease_charges_list, 'lchg:1'),
])
async def test_legacy_current_month_entry_works(records, monkeypatch, handler, callback):
    monkeypatch.setattr(ha.billing_service, 'period_start', lambda _: date(2026, 4, 1))
    await handler(_cb(callback), _state())
    assert 'Апрель 2026' in records[-1][0] and 'Стр. 1/3' in records[-1][0]
