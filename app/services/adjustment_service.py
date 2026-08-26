"""Сервис корректировок доходов/расходов за любой месяц с аудит-логом (ТЗ п.13)."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Adjustment, Charge, Expense

# Разрешённые сущности для корректировки: тип -> (модель, поле суммы)
_ENTITIES = {
    "charge": (Charge, "amount"),
    "expense": (Expense, "amount"),
}


async def _log(
    session: AsyncSession,
    *,
    landlord_id: int,
    user_id: int | None,
    entity_type: str,
    entity_id: int,
    field: str,
    old_value,
    new_value,
    reason: str | None,
) -> Adjustment:
    adj = Adjustment(
        landlord_id=landlord_id,
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        field=field,
        old_value=str(old_value),
        new_value=str(new_value),
        reason=reason,
    )
    session.add(adj)
    return adj


async def correct_amount(
    session: AsyncSession,
    *,
    landlord_id: int,
    user_id: int | None,
    entity_type: str,
    entity_id: int,
    new_amount: Decimal,
    reason: str | None = None,
) -> Adjustment:
    """Меняет сумму начисления/расхода и пишет запись в аудит.

    entity_type: 'charge' (доход/начисление) или 'expense' (расход).
    """
    if entity_type not in _ENTITIES:
        raise ValueError(f"Недопустимый тип для корректировки: {entity_type}")

    model, field = _ENTITIES[entity_type]
    obj = await session.get(model, entity_id)
    if obj is None:
        raise ValueError(f"{entity_type} c id={entity_id} не найден")

    old_amount = getattr(obj, field)
    setattr(obj, field, new_amount)

    return await _log(
        session,
        landlord_id=landlord_id,
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        field=field,
        old_value=old_amount,
        new_value=new_amount,
        reason=reason,
    )


async def list_adjustments(session: AsyncSession, landlord_id: int, limit: int = 50) -> list[Adjustment]:
    result = await session.execute(
        select(Adjustment)
        .where(Adjustment.landlord_id == landlord_id)
        .order_by(Adjustment.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
