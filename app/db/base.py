"""Базовый класс моделей, асинхронный движок и фабрика сессий."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(AsyncAttrs, DeclarativeBase):
    """Общий декларативный базовый класс для всех моделей."""


_settings = get_settings()

engine = create_async_engine(
    _settings.sqlalchemy_dsn,
    pool_pre_ping=True,
    future=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Зависимость FastAPI / контекст для получения сессии БД."""
    async with async_session_factory() as session:
        yield session
