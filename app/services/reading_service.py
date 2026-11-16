"""Сервис показаний счётчиков: приём/обновление, вычисление расхода."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import DataSource
from app.db.models import Meter, MeterReading
from app.domain.billing import consumption_from_readings
from app.services.billing_service import period_start


async def _previous_curr_value(session: AsyncSession, meter_id: int, period: date) -> Decimal | None:
    """Последнее известное показание до указанного периода (None — более ранних нет)."""
    result = await session.execute(
        select(MeterReading.curr_value)
        .where(MeterReading.meter_id == meter_id, MeterReading.period < period)
        .order_by(MeterReading.period.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def upsert_reading(
    session: AsyncSession,
    meter: Meter,
    *,
    period: date,
    curr_value: Decimal,
    prev_value: Decimal | None = None,
    reading_date: date | None = None,
    source: DataSource = DataSource.manual,
    created_by_id: int | None = None,
) -> MeterReading:
    """Создаёт или обновляет показание за период. prev берётся из истории, если не задан;
    самое раннее показание счётчика считается начальным (расход 0).

    Бросает ValueError, если расход отрицательный (ошибка данных — потребуется
    ручная корректировка через бота).
    """
    period = period_start(period)
    if prev_value is None:
        prev_value = await _previous_curr_value(session, meter.id, period)
    if prev_value is None:
        # Самое раннее показание счётчика — начальное: точка отсчёта, расход 0.
        prev_value = curr_value

    consumption = consumption_from_readings(prev_value, curr_value)

    # Следующее по времени показание считает расход от этого — пересчитываем его.
    following = (
        await session.execute(
            select(MeterReading)
            .where(MeterReading.meter_id == meter.id, MeterReading.period > period)
            .order_by(MeterReading.period)
            .limit(1)
        )
    ).scalars().first()
    if following is not None and following.curr_value < curr_value:
        raise ValueError(
            f"Показания больше, чем за {following.period.strftime('%m.%Y')} "
            f"({following.curr_value}) — проверьте данные счётчика"
        )

    reading = (
        await session.execute(
            select(MeterReading).where(
                MeterReading.meter_id == meter.id, MeterReading.period == period
            )
        )
    ).scalars().first()

    if reading is None:
        reading = MeterReading(meter_id=meter.id, period=period)
        session.add(reading)
    reading.prev_value = prev_value
    reading.curr_value = curr_value
    reading.consumption = consumption
    reading.source = source
    reading.reading_date = reading_date
    reading.created_by_id = created_by_id

    if following is not None:
        following.prev_value = curr_value
        following.consumption = consumption_from_readings(curr_value, following.curr_value)
    return reading


async def next_reading_period(session: AsyncSession, meter_id: int, period: date) -> date | None:
    """Период следующего показания счётчика после указанного (если есть)."""
    result = await session.execute(
        select(MeterReading.period)
        .where(MeterReading.meter_id == meter_id, MeterReading.period > period_start(period))
        .order_by(MeterReading.period)
        .limit(1)
    )
    return result.scalar_one_or_none()
