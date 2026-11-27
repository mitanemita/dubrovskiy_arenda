"""The premises card returns to the same filtered page after edits."""
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
from app.db.enums import OrgType, TaxMode
from app.db.models import Landlord, Premises


def cb(data):
    return SimpleNamespace(data=data, message=SimpleNamespace(), from_user=SimpleNamespace(id=1), answer=AsyncMock())


def back(markup):
    return next(button.callback_data for row in markup.inline_keyboard for button in row
                if button.text == '◀️ К помещениям')


@pytest_asyncio.fixture
async def directory(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:', poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        landlord = Landlord(name='ИП', type=OrgType.ip, inn='710000000000', tax_mode=TaxMode.ausn)
        session.add(landlord)
        await session.flush()
        session.add_all(Premises(landlord_id=landlord.id, label=f'Склад {i:02}') for i in range(1, 24))
        await session.commit()
    screens = []
    async def show(message, text, reply_markup=None):
        screens.append((text, reply_markup))
    monkeypatch.setattr(hd, 'async_session_factory', factory)
    monkeypatch.setattr(hd, '_landlord_id', AsyncMock(return_value=1))
    monkeypatch.setattr(hd, 'edit_or_send', show)
    monkeypatch.setattr(hd, 'wiz_reply', show)
    yield factory, screens
    await engine.dispose()


async def test_filtered_page_survives_card_edits_and_site_readiness_actions(directory):
    factory, screens = directory
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))
    await hd.premises_list(cb('premlist:free:10'), state)
    text, markup = screens[-1]
    assert 'Стр. 2/3' in text
    picked = next(b.callback_data for row in markup.inline_keyboard for b in row if b.text == '11')
    pid = int(picked.split(':')[1])
    await hd.premises_card(cb(picked), state)
    assert back(screens[-1][1]) == 'premlist:free:10'

    await hd.premises_edit_menu(cb(f'pedit:{pid}'), state)
    await hd.premises_edit_field(cb(f'pfield:label:{pid}'), state)
    await hd.edit_value_save(SimpleNamespace(text='Переименованный склад'), state)
    assert back(screens[-1][1]) == 'premlist:free:10'
    assert 'Переименованный склад' in screens[-1][0]

    await hd.premises_readiness_menu(cb(f'pread:{pid}'), state)
    await hd.premises_set_readiness(cb(f'prset:{pid}:build'), state)
    assert back(screens[-1][1]) == 'premlist:free:10'
    await hd.premises_site_menu(cb(f'psite:{pid}'), state)
    await hd.premises_set_site(cb(f'pset:{pid}:kr'), state)
    assert back(screens[-1][1]) == 'premlist:free:10'
    await hd.premises_delete_confirm(cb(f'pdel:{pid}'), state)
    await hd.premises_card(cb(f'ppick:{pid}'), state)
    assert back(screens[-1][1]) == 'premlist:free:10'
    await hd.premises_list(cb(back(screens[-1][1])), state)
    assert 'Стр. 2/3' in screens[-1][0]
    async with factory() as session:
        p = await session.get(Premises, pid)
        assert p.label == 'Переименованный склад' and p.readiness == 'build' and p.site_id is not None


async def test_card_without_list_context_returns_to_premises_menu(directory):
    _, screens = directory
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))
    await hd.premises_card(cb('ppick:1'), state)
    assert back(screens[-1][1]) == 'dir:premises'
