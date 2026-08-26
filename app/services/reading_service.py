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


async def _previous_curr_value(session: AsyncSession, meter_id: int, period: date) -> Decimal:
    """Последнее известное текущее показание до указанного периода (для авто-prev)."""
    result = await session.execute(
        select(MeterReading.curr_value)
        .where(MeterReading.meter_id == meter_id, MeterReading.period < period)
        .order_by(MeterReading.period.desc())
        .limit(1)
    )
    value = result.scalar_one_or_none()
    return value if value is not None else Decimal("0")


async def upsert_reading(
    session: AsyncSession,
    meter: Meter,
    *,
    period: date,
    curr_value: Decimal,
    prev_value: Decimal | None = None,
    reading_date: date | None = None,
    source: DataSource = DataSource.n8n,
    created_by_id: int | None = None,
) -> MeterReading:
    """Создаёт или обновляет показание за период. prev берётся из истории, если не задан.

    Бросает ValueError, если расход отрицательный (ошибка данных — потребуется
    ручная корректировка через бота).
    """
    period = period_start(period)
    if prev_value is None:
        prev_value = await _previous_curr_value(session, meter.id, period)

    consumption = consumption_from_readings(prev_value, curr_value)

    existing = (
        await session.execute(
            select(MeterReading).where(
                MeterReading.meter_id == meter.id, MeterReading.period == period
            )
        )
    ).scalars().first()

    if existing is None:
        reading = MeterReading(
            meter_id=meter.id,
            period=period,
            prev_value=prev_value,
            curr_value=curr_value,
            consumption=consumption,
            source=source,
            reading_date=reading_date,
            created_by_id=created_by_id,
        )
        session.add(reading)
        return reading

    existing.prev_value = prev_value
    existing.curr_value = curr_value
    existing.consumption = consumption
    existing.source = source
    existing.reading_date = reading_date
    existing.created_by_id = created_by_id
    return existing
