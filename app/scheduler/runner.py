"""Обвязка APScheduler: расписание запуска задач с открытием сессии БД."""
from __future__ import annotations

from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.db.base import async_session_factory
from app.scheduler import jobs
from app.utils.logger import logger


async def _run_generate_period_charges() -> None:
    async with async_session_factory() as session:
        try:
            stats = await jobs.generate_period_charges(session, date.today())
            exp_stats = await jobs.generate_fixed_expenses(session, date.today())
            await session.commit()
            logger.info("Начисления за период сформированы: %s; %s", stats, exp_stats)
        except Exception:
            await session.rollback()
            logger.exception("Ошибка задачи generate_period_charges")


async def _run_daily() -> None:
    async with async_session_factory() as session:
        try:
            stats = await jobs.run_daily(session, date.today())
            await session.commit()
            logger.info("Ежедневная задача выполнена: %s", stats)
        except Exception:
            await session.rollback()
            logger.exception("Ошибка задачи run_daily")


def build_scheduler() -> AsyncIOScheduler:
    """Создаёт планировщик с задачами (без запуска)."""
    tz = get_settings().timezone
    scheduler = AsyncIOScheduler(timezone=tz)
    # Начало периода — 1-е число месяца
    scheduler.add_job(
        _run_generate_period_charges,
        CronTrigger(day=1, hour=6, minute=0),
        id="monthly_charges",
        replace_existing=True,
    )
    # Ежедневно — напоминания и просрочка
    scheduler.add_job(
        _run_daily,
        CronTrigger(hour=8, minute=0),
        id="daily",
        replace_existing=True,
    )
    return scheduler
