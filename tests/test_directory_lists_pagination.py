"""Pagination keeps list numbers and card navigation aligned with the same data."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot import handlers_directory as hd
from app.db.enums import OrgType
from app.db.models import Lease, Premises, Tenant


def _state():
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _cb(data):
    return SimpleNamespace(data=data, message=SimpleNamespace(), from_user=SimpleNamespace(id=1), answer=AsyncMock())


@pytest.fixture
def directory(monkeypatch):
    tenants = [SimpleNamespace(id=i, landlord_id=1, name=f'Арендатор {i:02}', inn=f'{i:010}',
                               type=OrgType.ooo, kpp=None, address=None, email=None, phone=None)
               for i in range(1, 24)]
    premises = [SimpleNamespace(id=1, landlord_id=1, label='Склад', litera='Д')]
    leases = [SimpleNamespace(id=i, tenant_id=i, premises_id=1, contract_no=f'{i:02}',
                              rent_amount=Decimal('1000'), payment_day=5,
                              status=SimpleNamespace(value='active')) for i in range(1, 24)]
    meters = [(SimpleNamespace(id=i, premises_id=1, serial_no=f'М-{i:02}', label=None, coefficient=None), 'Склад')
              for i in range(1, 24)]
    async def get(model, identity):
        return next((x for x in {Tenant: tenants, Premises: premises, Lease: leases}.get(model, [m for m, _ in meters])
                     if x.id == identity), None)

    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, model, identity): return await get(model, identity)

    monkeypatch.setattr(hd, 'async_session_factory', Session)
    monkeypatch.setattr(hd, '_landlord_id', AsyncMock(return_value=1))
    monkeypatch.setattr(hd.directory_service, 'list_tenants', AsyncMock(side_effect=lambda *a: tenants))
    monkeypatch.setattr(hd.directory_service, 'list_premises', AsyncMock(side_effect=lambda *a: premises))
    monkeypatch.setattr(hd.directory_service, 'list_leases', AsyncMock(side_effect=lambda *a: leases))
    monkeypatch.setattr(hd.directory_service, 'list_meters', AsyncMock(side_effect=lambda *a: meters))
    monkeypatch.setattr(hd.directory_service, 'premises_title', lambda p: p.label)
    screens = []
    async def show(message, text, reply_markup=None):
        screens.append((text, reply_markup))
    monkeypatch.setattr(hd, 'edit_or_send', show)
    monkeypatch.setattr(hd, 'wiz_reply', show)
    return tenants, leases, meters, screens


@pytest.mark.parametrize(('kind', 'handler', 'prefix', 'name'), [
    ('tenants', hd.tenants_list, 'tnpick', 'Арендатор'),
    ('leases', hd.leases_list, 'lpick', '№'),
    ('meters', hd.meters_list, 'mpick', 'Склад · М-'),
])
async def test_list_navigation_and_clamping(directory, kind, handler, prefix, name):
    _, _, _, screens = directory
    state = _state()
    await handler(_cb(f'dir:{kind}'), state)
    text, markup = screens[-1]
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert 'Стр. 1/3' in text and f'{prefix}:10' in [b.callback_data for b in buttons]
    assert f'{prefix}:11' not in [b.callback_data for b in buttons]
    assert f'dir:{kind}:10' in [b.callback_data for b in buttons]
    await handler(_cb(f'dir:{kind}:10'), state)
    text, markup = screens[-1]
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert 'Стр. 2/3' in text and f'11. {name}' in text
    assert next(b.text for b in buttons if b.callback_data == f'{prefix}:11') == '11'
    assert f'dir:{kind}:0' in [b.callback_data for b in buttons]
    assert f'dir:{kind}:20' in [b.callback_data for b in buttons]
    await handler(_cb(f'dir:{kind}:9990'), state)
    text, markup = screens[-1]
    assert 'Стр. 3/3' in text and f'23. {name}' in text
    assert len([b for row in markup.inline_keyboard for b in row if b.callback_data.startswith(prefix + ':')]) == 3


@pytest.mark.parametrize(('kind', 'handler', 'pick'), [
    ('tenants', hd.tenants_list, hd.tenant_card),
    ('leases', hd.leases_list, hd.lease_card),
    ('meters', hd.meters_list, hd.meter_card),
])
async def test_card_returns_to_its_list_page(directory, kind, handler, pick):
    _, _, _, screens = directory
    state = _state()
    prefix = {'tenants': 'tnpick', 'leases': 'lpick', 'meters': 'mpick'}[kind]
    await pick(_cb(f'{prefix}:13'), state)
    _, markup = screens[-1]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert f'dir:{kind}:10' in callbacks
    await handler(_cb(f'dir:{kind}:10'), state)
    assert 'Стр. 2/3' in screens[-1][0]


@pytest.mark.parametrize(('kind', 'handler', 'data_index'), [
    ('tenants', hd.tenants_list, 0), ('leases', hd.leases_list, 1), ('meters', hd.meters_list, 2),
])
async def test_empty_list_keeps_add_and_back(directory, kind, handler, data_index):
    directory[data_index].clear()
    await handler(_cb(f'dir:{kind}:100'), _state())
    text, markup = directory[3][-1]
    assert '— пусто' in text
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert f'dadd:{kind}' in callbacks and 'menu:directory' in callbacks
