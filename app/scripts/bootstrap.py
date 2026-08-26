"""Первичная инициализация: арендодатель + владельцы-операторы + настройки.

Запуск один раз после первого деплоя:
    python -m app.scripts.bootstrap

Идемпотентно: повторный запуск не создаёт дубли. Реквизиты арендодателя
и параметры потом правятся через бота.
"""
from __future__ import annotations

import asyncio
import os

from sqlalchemy import select

from app.config import get_settings
from app.db.base import async_session_factory
from app.db.enums import OrgType, TaxMode, UserRole
from app.db.models import Landlord, User
from app.services import settings_service
from app.utils.logger import logger


async def bootstrap() -> None:
    settings = get_settings()
    async with async_session_factory() as session:
        landlord = (await session.execute(select(Landlord).limit(1))).scalar_one_or_none()
        if landlord is None:
            landlord = Landlord(
                name=os.environ.get("LANDLORD_NAME", "Мой бизнес"),
                type=OrgType.ip,
                inn=os.environ.get("LANDLORD_INN", "000000000000"),
                tax_mode=TaxMode.ausn,
            )
            session.add(landlord)
            await session.flush()
            logger.info("Создан арендодатель id=%s (%s)", landlord.id, landlord.name)
        else:
            logger.info("Арендодатель уже существует id=%s", landlord.id)

        await settings_service.ensure_defaults(session, landlord.id)

        for tg_id in settings.admin_ids:
            exists = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
            if exists is None:
                session.add(User(landlord_id=landlord.id, tg_id=tg_id, role=UserRole.owner, name="Владелец"))
                logger.info("Добавлен владелец tg_id=%s", tg_id)

        await session.commit()
    logger.info("Инициализация завершена. Реквизиты и настройки правьте через бота.")


if __name__ == "__main__":
    asyncio.run(bootstrap())
