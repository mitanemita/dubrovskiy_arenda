"""Сервис настроек «ключ-значение» (правятся через бота)."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting
from app.db.seed_defaults import DEFAULT_SETTINGS


async def get_setting(session: AsyncSession, landlord_id: int, key: str) -> str | None:
    """Значение настройки (строка) или None, если не задано."""
    result = await session.execute(
        select(Setting.value).where(Setting.landlord_id == landlord_id, Setting.key == key)
    )
    return result.scalar_one_or_none()


async def _get_with_default(session: AsyncSession, landlord_id: int, key: str) -> str:
    value = await get_setting(session, landlord_id, key)
    if value is not None:
        return value
    if key in DEFAULT_SETTINGS:
        return DEFAULT_SETTINGS[key][0]
    raise KeyError(f"Неизвестная настройка: {key}")


async def get_decimal(session: AsyncSession, landlord_id: int, key: str) -> Decimal:
    """Настройка как Decimal (с учётом значения по умолчанию)."""
    return Decimal(await _get_with_default(session, landlord_id, key))


async def get_int(session: AsyncSession, landlord_id: int, key: str) -> int:
    """Настройка как int (с учётом значения по умолчанию)."""
    return int(await _get_with_default(session, landlord_id, key))


async def set_setting(session: AsyncSession, landlord_id: int, key: str, value: str) -> Setting:
    """Создаёт или обновляет настройку. Коммит — на стороне вызывающего кода."""
    result = await session.execute(
        select(Setting).where(Setting.landlord_id == landlord_id, Setting.key == key)
    )
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = Setting(landlord_id=landlord_id, key=key, value=value)
        session.add(setting)
    else:
        setting.value = value
    return setting


async def ensure_defaults(session: AsyncSession, landlord_id: int) -> None:
    """Заполняет отсутствующие настройки значениями по умолчанию (при создании арендодателя)."""
    result = await session.execute(select(Setting.key).where(Setting.landlord_id == landlord_id))
    existing = set(result.scalars().all())
    for key, (value, _desc) in DEFAULT_SETTINGS.items():
        if key not in existing:
            session.add(Setting(landlord_id=landlord_id, key=key, value=value))
