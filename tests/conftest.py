"""Общие фикстуры тестов. Используем SQLite (in-memory), чтобы не требовать PostgreSQL."""
import os

# ВАЖНО: задаём тестовые переменные до импорта app.config/app.db.base
# (настройки и движок создаются при импорте).
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("WEBHOOK_TOKEN", "test-token")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
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


@pytest_asyncio.fixture
async def client(session):
    """HTTP-клиент к FastAPI-приложению с подменой сессии БД на тестовую."""
    from app.api.deps import get_db
    from app.main import app

    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
