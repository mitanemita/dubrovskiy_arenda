"""Задачи планировщика (чистые async-функции над сессией — тестируются напрямую).

Обвязка расписания — в app/scheduler/runner.py. Здесь только бизнес-логика,
чтобы её можно было прогнать в тестах с фиксированной датой `today`.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import ChargeStatus, ChargeType, LeaseStatus, NotifChannel
from app.db.models import Charge, Landlord, Lease, Tenant
from app.domain import billing
from app.domain.allocation import charge_status
from app.services import (
    billing_service,
    expense_service,
    notification_service,
    task_service,
)
from app.services.task_service import due_color_icon


async def generate_fixed_expenses(session: AsyncSession, today: date) -> dict:
    """Авто-фиксированные расходы (серверная, зарплаты) для всех арендодателей."""
    landlord_ids = (await session.execute(select(Landlord.id))).scalars().all()
    created = 0
    for landlord_id in landlord_ids:
        created += await expense_service.generate_fixed_expenses(session, landlord_id, today)
    return {"fixed_expenses": created}


async def _active_leases(session: AsyncSession) -> list[tuple[Lease, int]]:
    """Активные договоры вместе с landlord_id (через арендатора)."""
    result = await session.execute(
        select(Lease, Tenant.landlord_id)
        .join(Tenant, Tenant.id == Lease.tenant_id)
        .where(Lease.status == LeaseStatus.active)
    )
    return [(row[0], row[1]) for row in result.all()]


async def generate_period_charges(session: AsyncSession, today: date) -> dict:
    """Начало периода: создать начисления по активным договорам.

    Аренда создаётся всегда; электричество — если есть показания, иначе арендодателю
    уходит Telegram-уведомление о нехватке данных.
    """
    period = billing_service.period_start(today)
    stats = {"rent": 0, "electricity": 0, "missing_readings": 0}

    for lease, landlord_id in await _active_leases(session):
        rent = await billing_service.create_rent_charge(session, lease, period)
        if rent.status == ChargeStatus.draft:
            rent.status = ChargeStatus.issued
        stats["rent"] += 1

        elec = await billing_service.create_electricity_charge(session, lease, period)
        if elec is not None:
            if elec.status == ChargeStatus.draft:
                elec.status = ChargeStatus.issued
            stats["electricity"] += 1
        elif await billing_service.readings_missing(session, lease, period):
            stats["missing_readings"] += 1
            await notification_service.enqueue(
                session,
                landlord_id=landlord_id,
                channel=NotifChannel.telegram,
                type="missing_readings",
                subject="Нет показаний счётчика",
                body=(
                    f"По договору №{lease.contract_no} за {period.strftime('%m.%Y')} "
                    f"нет показаний электросчётчика — электричество не начислено. "
                    f"Внесите показания через бота: начисление появится сразу."
                ),
            )

    return stats


async def _unpaid_periods(session: AsyncSession, lease_id: int) -> list[date]:
    """Периоды, где по аренде/электричеству есть неоплаченный остаток."""
    result = await session.execute(
        select(Charge.period)
        .where(
            Charge.lease_id == lease_id,
            Charge.type.in_([ChargeType.rent, ChargeType.electricity]),
            Charge.amount > Charge.paid_amount,
        )
        .distinct()
    )
    return sorted(result.scalars().all())


async def _mark_overdue(session: AsyncSession, lease_id: int, period: date, today: date) -> None:
    """Проставляет статус overdue неоплаченным начислениям периода."""
    result = await session.execute(
        select(Charge).where(
            Charge.lease_id == lease_id,
            Charge.period == period,
            Charge.type.in_([ChargeType.rent, ChargeType.electricity]),
        )
    )
    for ch in result.scalars().all():
        if ch.amount > ch.paid_amount:
            ch.status = charge_status(ch.amount, ch.paid_amount, ch.due_date, today, already_sent=True)


async def generate_task_reminders(session: AsyncSession, today: date) -> int:
    """Напоминания по задачам: предварительное (за N дней) и в день срока."""
    count = 0
    for task in await task_service.open_with_due(session):
        due = task.due_date
        lead = task_service.LEAD_DAYS.get(task.priority, 1)
        pre_day = due - timedelta(days=lead)
        due_str = due.strftime("%d.%m.%Y")
        # Цвет — по остатку дней до срока (а не по категории задачи).
        icon = due_color_icon(due, today)

        # Предварительное напоминание (за N дней)
        if not task.remind_pre_sent and pre_day <= today < due:
            await notification_service.enqueue(
                session, landlord_id=task.landlord_id, channel=NotifChannel.telegram,
                type="task_reminder",
                subject=f"Скоро срок задачи {icon}",
                body=f"{icon} {task.title}\nСрок: {due_str}" + (f"\n{task.description}" if task.description else ""),
                related_task_id=task.id,
            )
            task.remind_pre_sent = True
            count += 1

        # Напоминание в день срока (и позже, если пропущено)
        if not task.remind_due_sent and today >= due:
            await notification_service.enqueue(
                session, landlord_id=task.landlord_id, channel=NotifChannel.telegram,
                type="task_reminder",
                subject=f"Сегодня срок задачи {icon}",
                body=f"{icon} {task.title}\nСрок: {due_str}" + (f"\n{task.description}" if task.description else ""),
                related_task_id=task.id,
            )
            task.remind_due_sent = True
            count += 1
    return count


async def run_daily(session: AsyncSession, today: date) -> dict:
    """Ежедневно: напоминания по задачам и просрочка (пересчёт пени + статус overdue)."""
    stats = {"overdue": 0, "task_reminders": 0}
    stats["task_reminders"] = await generate_task_reminders(session, today)

    for lease, _landlord_id in await _active_leases(session):
        for period in await _unpaid_periods(session, lease.id):
            outstanding = await billing_service.outstanding_principal(session, lease.id, period)
            if outstanding <= 0:
                continue
            if today > billing.payment_due_date(period, lease.payment_day):
                await billing_service.upsert_penalty_charge(session, lease, period, today)
                await _mark_overdue(session, lease.id, period, today)
                stats["overdue"] += 1

    return stats
