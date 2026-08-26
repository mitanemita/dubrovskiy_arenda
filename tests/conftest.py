"""Общие фикстуры тестов. Используем SQLite (in-memory), чтобы не требовать PostgreSQL."""
import os

# ВАЖНО: задаём тестовый DSN до импорта app.db.base (движок создаётся при импорте).
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db import models  # noqa: F401  (регистрирует модели в metadata)


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """Изолированная async-сессия на in-memory SQLite (единое соединение)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s

    await engine.dispose()
