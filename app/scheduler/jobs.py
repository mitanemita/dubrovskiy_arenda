"""Задачи планировщика (чистые async-функции над сессией — тестируются напрямую).

Обвязка расписания — в app/scheduler/runner.py. Здесь только бизнес-логика,
чтобы её можно было прогнать в тестах с фиксированной датой `today`.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import ChargeStatus, ChargeType, LeaseStatus, NotifChannel
from app.db.models import Charge, Landlord, Lease, Tenant
from app.domain import billing
from app.domain.allocation import charge_status
from app.services import billing_service, expense_service, notification_service, settings_service


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


async def _rent_charge(session: AsyncSession, lease_id: int, period: date) -> Charge | None:
    result = await session.execute(
        select(Charge).where(
            Charge.lease_id == lease_id, Charge.type == ChargeType.rent, Charge.period == period
        )
    )
    return result.scalars().first()


async def generate_period_charges(session: AsyncSession, today: date) -> dict:
    """Начало периода: создать начисления и выставить квитанцию каждому арендатору.

    Аренда создаётся всегда; электричество — если есть показания, иначе арендодателю
    уходит уведомление о нехватке данных (ТЗ п.10).
    """
    period = billing_service.period_start(today)
    stats = {"rent": 0, "electricity": 0, "missing_readings": 0, "invoices": 0}

    for lease, landlord_id in await _active_leases(session):
        rent = await billing_service.create_rent_charge(session, lease, period)
        rent.status = ChargeStatus.issued
        stats["rent"] += 1

        elec = await billing_service.create_electricity_charge(session, lease, period)
        if elec is None:
            stats["missing_readings"] += 1
            await notification_service.enqueue(
                session,
                landlord_id=landlord_id,
                channel=NotifChannel.telegram,
                type="missing_readings",
                subject="Нет показаний счётчика",
                body=(
                    f"По договору №{lease.contract_no} за {period.strftime('%m.%Y')} "
                    f"нет показаний электросчётчика — квитанция выставлена без электроэнергии. "
                    f"Внесите показания через бота."
                ),
            )
        else:
            elec.status = ChargeStatus.issued
            stats["electricity"] += 1

        await session.flush()  # получить rent.id для привязки уведомления
        await notification_service.enqueue(
            session,
            landlord_id=landlord_id,
            channel=NotifChannel.email,
            type="invoice_new",
            tenant_id=lease.tenant_id,
            subject=f"Квитанция за {period.strftime('%m.%Y')}",
            body="Квитанция на оплату во вложении.",
            related_charge_id=rent.id,
        )
        stats["invoices"] += 1

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


async def run_daily(session: AsyncSession, today: date) -> dict:
    """Ежедневно: напоминание за N дней до срока и обработка просрочки (пеня + квитанция)."""
    stats = {"reminders": 0, "overdue": 0}

    for lease, landlord_id in await _active_leases(session):
        reminder_days = await settings_service.get_int(session, landlord_id, "reminder_days_before")

        for period in await _unpaid_periods(session, lease.id):
            outstanding = await billing_service.outstanding_principal(session, lease.id, period)
            if outstanding <= 0:
                continue

            due = billing.payment_due_date(period, lease.payment_day)
            rent = await _rent_charge(session, lease.id, period)
            anchor_id = rent.id if rent else None

            # Напоминание за N дней до срока (один раз на период)
            if (due - today).days == reminder_days and anchor_id is not None:
                if not await notification_service.exists(session, type="reminder", related_charge_id=anchor_id):
                    await notification_service.enqueue(
                        session,
                        landlord_id=landlord_id,
                        channel=NotifChannel.email,
                        type="reminder",
                        tenant_id=lease.tenant_id,
                        subject=f"Напоминание об оплате за {period.strftime('%m.%Y')}",
                        body=f"Напоминаем об оплате до {due.strftime('%d.%m.%Y')}. Квитанция во вложении.",
                        related_charge_id=anchor_id,
                    )
                    stats["reminders"] += 1

            # Просрочка: пересчёт пени + ежедневная квитанция «просрочено»
            if today > due:
                await billing_service.upsert_penalty_charge(session, lease, period, today)
                await _mark_overdue(session, lease.id, period, today)
                days = billing.days_overdue(due, today)
                await notification_service.enqueue(
                    session,
                    landlord_id=landlord_id,
                    channel=NotifChannel.email,
                    type="overdue",
                    tenant_id=lease.tenant_id,
                    subject=f"Оплата просрочена — {period.strftime('%m.%Y')}",
                    body=(
                        f"Оплата за {period.strftime('%m.%Y')} просрочена на {days} дн. "
                        f"Начислена пеня. Актуальная квитанция во вложении."
                    ),
                    related_charge_id=anchor_id,
                )
                stats["overdue"] += 1

    return stats
