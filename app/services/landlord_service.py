"""Определение арендодателя (система однопользовательская: один арендодатель)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Landlord


async def get_default_landlord_id(session: AsyncSession) -> int | None:
    """ID единственного арендодателя."""
    result = await session.execute(select(Landlord.id).order_by(Landlord.id).limit(1))
    return result.scalar_one_or_none()
