"""Зависимости FastAPI: авторизация вебхуков и сессия БД."""
from __future__ import annotations

import hmac
from collections.abc import AsyncGenerator

from fastapi import Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.base import async_session_factory


async def verify_webhook_token(x_webhook_token: str = Header(default="")) -> None:
    """Проверяет общий секрет n8n (постоянное по времени сравнение). Fail-closed."""
    expected = get_settings().webhook_token
    if not expected or not hmac.compare_digest(x_webhook_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный или отсутствующий токен вебхука",
        )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Сессия БД на время запроса (коммит/откат вокруг обработчика)."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
