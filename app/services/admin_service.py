"""Служебные операции для тестирования: заполнение БД демо-данными и очистка.

Используется командой /admin в боте. Только для проверки функционала —
удаляет ВСЕ бизнес-данные арендодателя (договоры, арендаторов, помещения,
счётчики, начисления, платежи, задачи, расходы и т.п.), сохраняя самого
арендодателя, операторов и настройки.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import DataSource, ExpenseCategory, ExpenseMode, LeaseStatus, OrgType
from app.db.models import (
    Adjustment,
    Charge,
    Document,
    Expense,
    Landlord,
    Lease,
    Meter,
    MeterReading,
    Notification,
    Payment,
    PaymentAllocation,
    Premises,
    Task,
    Tenant,
)
from app.services import billing_service, payment_service, reading_service, settings_service


async def wipe_business_data(session: AsyncSession, landlord_id: int) -> dict[str, int]:
    """Удаляет все бизнес-данные арендодателя. Возвращает число удалённых по типам."""
    lease_ids = (await session.execute(
        select(Lease.id).join(Tenant, Tenant.id == Lease.tenant_id).where(Tenant.landlord_id == landlord_id)
    )).scalars().all()
    premises_ids = (await session.execute(
        select(Premises.id).where(Premises.landlord_id == landlord_id)
    )).scalars().all()
    meter_ids = (await session.execute(
        select(Meter.id).join(Premises, Premises.id == Meter.premises_id).where(Premises.landlord_id == landlord_id)
    )).scalars().all()
    charge_ids = (await session.execute(
        select(Charge.id).where(Charge.lease_id.in_(lease_ids))
    )).scalars().all() if lease_ids else []
    payment_ids = (await session.execute(
        select(Payment.id).where(Payment.lease_id.in_(lease_ids))
    )).scalars().all() if lease_ids else []

    counts: dict[str, int] = {}

    async def _del(stmt, key: str) -> None:
        res = await session.execute(stmt)
        counts[key] = (res.rowcount or 0)

    if payment_ids or charge_ids:
        await _del(delete(PaymentAllocation).where(
            (PaymentAllocation.payment_id.in_(payment_ids)) | (PaymentAllocation.charge_id.in_(charge_ids))
        ), "allocations")
    if lease_ids:
        await _del(delete(Payment).where(Payment.lease_id.in_(lease_ids)), "payments")
        await _del(delete(Charge).where(Charge.lease_id.in_(lease_ids)), "charges")
    if meter_ids:
        await _del(delete(MeterReading).where(MeterReading.meter_id.in_(meter_ids)), "readings")
        await _del(delete(Meter).where(Meter.id.in_(meter_ids)), "meters")
    if lease_ids:
        await _del(delete(Lease).where(Lease.id.in_(lease_ids)), "leases")
    await _del(delete(Tenant).where(Tenant.landlord_id == landlord_id), "tenants")
    await _del(delete(Premises).where(Premises.landlord_id == landlord_id), "premises")
    await _del(delete(Task).where(Task.landlord_id == landlord_id), "tasks")
    await _del(delete(Expense).where(Expense.landlord_id == landlord_id), "expenses")
    await _del(delete(Adjustment).where(Adjustment.landlord_id == landlord_id), "adjustments")
    await _del(delete(Document).where(Document.landlord_id == landlord_id), "documents")
    await _del(delete(Notification).where(Notification.landlord_id == landlord_id), "notifications")
    return {k: v for k, v in counts.items() if v}


async def _lease_total(session: AsyncSession, lease_id: int, period: date) -> Decimal:
    """Сумма начислений договора за период (для тестового платежа)."""
    total = (await session.execute(
        select(func.coalesce(func.sum(Charge.amount), 0)).where(
            Charge.lease_id == lease_id, Charge.period == period
        )
    )).scalar_one()
    return Decimal(str(total))


async def seed_test_data(session: AsyncSession, landlord_id: int) -> dict[str, int]:
    """Создаёт полный согласованный демо-набор за текущий месяц.

    Помещения (в т.ч. свободное), арендаторы, договоры, счётчики, показания,
    начисления (аренда+электричество) и платежи (один полный — подтверждён,
    один частичный) — чтобы отчёты «Платежи по помещениям» и «Электричество»
    показывали данные. Предварительно очищает бизнес-данные, чтобы не плодить дубли.
    Также заполняет реквизиты арендодателя-заглушки, если они пустые (для документов).
    """
    await wipe_business_data(session, landlord_id)
    await settings_service.ensure_defaults(session, landlord_id)

    landlord = await session.get(Landlord, landlord_id)
    if landlord is not None:
        landlord.address = landlord.address or "300000, г. Тула, ул. Демонстрационная, д. 1"
        landlord.kpp = landlord.kpp or "710001001"
        landlord.bank_name = landlord.bank_name or "ПАО «Демо-Банк»"
        landlord.bik = landlord.bik or "044525225"
        landlord.account = landlord.account or "40702810900000000001"
        landlord.corr_account = landlord.corr_account or "30101810400000000225"

    today = date.today()
    period = billing_service.period_start(today)

    prem_a = Premises(landlord_id=landlord_id, label="Помещение А1", address="г. Тула, ул. Демонстрационная, 1",
                      area=Decimal("120.00"), is_occupied=True)
    prem_b = Premises(landlord_id=landlord_id, label="Помещение Б2", address="г. Тула, ул. Демонстрационная, 3",
                      area=Decimal("55.50"), is_occupied=True)
    prem_c = Premises(landlord_id=landlord_id, label="Помещение В3 (свободно)", address="г. Тула, ул. Демонстрационная, 5",
                      area=Decimal("30.00"), is_occupied=False)
    session.add_all([prem_a, prem_b, prem_c])
    await session.flush()

    tenant1 = Tenant(landlord_id=landlord_id, name="ООО «Ромашка»", type=OrgType.ooo, inn="7100000001",
                     kpp="710001001", address="г. Тула, ул. Цветочная, 10", email="romashka@example.com", phone="+79000000001")
    tenant2 = Tenant(landlord_id=landlord_id, name="ИП Петров П.П.", type=OrgType.ip, inn="710000000002",
                     address="г. Тула, ул. Садовая, 5", email="petrov@example.com", phone="+79000000002")
    session.add_all([tenant1, tenant2])
    await session.flush()

    lease1 = Lease(tenant_id=tenant1.id, premises_id=prem_a.id, contract_no="17/2026-АР",
                   contract_date=today - timedelta(days=90), rent_amount=Decimal("50000.00"),
                   payment_day=5, status=LeaseStatus.active)
    lease2 = Lease(tenant_id=tenant2.id, premises_id=prem_b.id, contract_no="18/2026-АР",
                   contract_date=today - timedelta(days=30), rent_amount=Decimal("15000.00"),
                   payment_day=10, status=LeaseStatus.active)
    session.add_all([lease1, lease2])
    await session.flush()

    meter_a = Meter(premises_id=prem_a.id, serial_no="М-1001", label="Основной", coefficient=Decimal("1.0"))
    meter_b = Meter(premises_id=prem_b.id, serial_no="М-2002", label="Основной", coefficient=None)
    session.add_all([meter_a, meter_b])
    await session.flush()

    # Показания за текущий месяц (расход A=350, B=120 кВт·ч)
    await reading_service.upsert_reading(session, meter_a, period=period,
                                         curr_value=Decimal("15350"), prev_value=Decimal("15000"), source=DataSource.manual)
    await reading_service.upsert_reading(session, meter_b, period=period,
                                         curr_value=Decimal("8120"), prev_value=Decimal("8000"), source=DataSource.manual)
    # Показания должны быть в БД до расчёта электричества (движок с autoflush=False).
    await session.flush()

    # Начисления за текущий месяц: аренда + электричество
    for lease in (lease1, lease2):
        await billing_service.create_rent_charge(session, lease, period)
        await billing_service.create_electricity_charge(session, lease, period)
    await session.flush()

    # Платёж №1 — полный (договор 1): станет «подтверждён»
    total1 = await _lease_total(session, lease1.id, period)
    pay1 = await payment_service.register_payment(session, lease1.id, total1, period=period, payment_date=today)
    await session.flush()
    await payment_service.confirm_payment(session, pay1, confirmed_by_id=None, today=today)

    # Платёж №2 — частичный (договор 2): только аренда, электричество остаётся долгом
    pay2 = await payment_service.register_payment(session, lease2.id, Decimal("15000.00"), period=period, payment_date=today)
    await session.flush()
    await payment_service.confirm_payment(session, pay2, confirmed_by_id=None, today=today)

    expenses = [
        Expense(landlord_id=landlord_id, category=ExpenseCategory.travel, mode=ExpenseMode.manual,
                amount=Decimal("5000.00"), period=period, description="Демо: командировка"),
        Expense(landlord_id=landlord_id, category=ExpenseCategory.repair, mode=ExpenseMode.manual,
                amount=Decimal("12000.00"), period=period, description="Демо: ремонт"),
    ]
    session.add_all(expenses)

    tasks = [
        Task(landlord_id=landlord_id, title="Демо: проверить показания счётчика", due_date=today + timedelta(days=3)),
        Task(landlord_id=landlord_id, title="Демо: продлить договор 17/2026-АР", due_date=today + timedelta(days=14)),
        Task(landlord_id=landlord_id, title="Демо: просроченная задача", due_date=today - timedelta(days=2)),
    ]
    session.add_all(tasks)
    await session.flush()

    return {"premises": 3, "tenants": 2, "leases": 2, "meters": 2, "readings": 2,
            "charges": 4, "payments": 2, "expenses": len(expenses), "tasks": len(tasks)}
