"""Сервис расходов: ручные и авто-фиксированные (серверная, зарплаты)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import ExpenseCategory, ExpenseMode
from app.db.models import Expense
from app.services import settings_service
from app.services.billing_service import period_start

# Соответствие «фиксированная категория -> ключ настройки с суммой»
_FIXED_SETTINGS = {
    ExpenseCategory.server: "server_cost",
    ExpenseCategory.salary: "salary_cost",
}


async def add_expense(
    session: AsyncSession,
    *,
    landlord_id: int,
    category: ExpenseCategory,
    amount: Decimal,
    period: date,
    mode: ExpenseMode = ExpenseMode.manual,
    description: str | None = None,
    created_by_id: int | None = None,
) -> Expense:
    """Добавляет расход (по умолчанию ручной)."""
    expense = Expense(
        landlord_id=landlord_id,
        category=category,
        mode=mode,
        amount=amount,
        period=period_start(period),
        description=description,
        created_by_id=created_by_id,
    )
    session.add(expense)
    return expense


async def _auto_expense_exists(
    session: AsyncSession, landlord_id: int, category: ExpenseCategory, period: date
) -> bool:
    result = await session.execute(
        select(Expense.id).where(
            Expense.landlord_id == landlord_id,
            Expense.category == category,
            Expense.mode == ExpenseMode.auto,
            Expense.period == period,
        )
    )
    return result.first() is not None


async def generate_fixed_expenses(session: AsyncSession, landlord_id: int, today: date) -> int:
    """Создаёт авто-фиксированные расходы (серверная, зарплаты) за период из настроек.

    Идемпотентно: повторный запуск не дублирует. Возвращает число созданных.
    """
    period = period_start(today)
    created = 0
    for category, setting_key in _FIXED_SETTINGS.items():
        amount = await settings_service.get_decimal(session, landlord_id, setting_key)
        if amount <= 0:
            continue
        if await _auto_expense_exists(session, landlord_id, category, period):
            continue
        session.add(
            Expense(
                landlord_id=landlord_id,
                category=category,
                mode=ExpenseMode.auto,
                amount=amount,
                period=period,
                description=f"Авто-расход ({setting_key}) за {period.strftime('%m.%Y')}",
            )
        )
        created += 1
    return created


async def list_expenses(session: AsyncSession, landlord_id: int, period: date) -> list[Expense]:
    result = await session.execute(
        select(Expense).where(Expense.landlord_id == landlord_id, Expense.period == period_start(period))
        .order_by(Expense.category)
    )
    return list(result.scalars().all())


async def monthly_total(session: AsyncSession, landlord_id: int, period: date) -> Decimal:
    expenses = await list_expenses(session, landlord_id, period)
    return sum((e.amount for e in expenses), Decimal("0"))
