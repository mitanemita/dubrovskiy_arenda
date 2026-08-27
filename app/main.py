"""Точка входа FastAPI-приложения (вебхуки n8n + планировщик + служебные эндпоинты)."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.webhooks import router as webhooks_router
from app.utils.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Запуск/остановка планировщика вместе с приложением."""
    from app.scheduler.runner import build_scheduler

    scheduler = build_scheduler()
    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("Планировщик запущен")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Планировщик остановлен")


app = FastAPI(
    title="Система учёта аренды",
    version="0.1.0",
    description="Бэкенд документооборота аренды: вебхуки n8n, бизнес-логика, документы.",
    lifespan=lifespan,
)

app.include_router(webhooks_router)


@app.get("/health", tags=["service"])
async def health() -> dict[str, str]:
    """Проверка живости сервиса."""
    return {"status": "ok"}
