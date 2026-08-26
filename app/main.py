"""Точка входа FastAPI-приложения (вебхуки n8n + служебные эндпоинты)."""
from __future__ import annotations

from fastapi import FastAPI

from app.api.webhooks import router as webhooks_router

app = FastAPI(
    title="Система учёта аренды",
    version="0.1.0",
    description="Бэкенд документооборота аренды: вебхуки n8n, бизнес-логика, документы.",
)

app.include_router(webhooks_router)


@app.get("/health", tags=["service"])
async def health() -> dict[str, str]:
    """Проверка живости сервиса."""
    return {"status": "ok"}
