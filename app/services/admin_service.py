"""Служебные операции для тестирования: заполнение БД демо-данными и очистка.

Используется командой /admin в боте. Только для проверки функционала —
удаляет ВСЕ бизнес-данные арендодателя (договоры, арендаторов, помещения,
счётчики, начисления, платежи, задачи, расходы и т.п.), сохраняя самого
арендодателя, операторов и настройки.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import LeaseStatus, OrgType
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


async def seed_test_data(session: AsyncSession, landlord_id: int) -> dict[str, int]:
    """Создаёт демо-набор: помещения, арендаторы, договоры, счётчики, задачи.

    Предварительно очищает существующие бизнес-данные, чтобы не плодить дубли.
    Также заполняет реквизиты арендодателя-заглушки, если они пустые (для документов).
    """
    await wipe_business_data(session, landlord_id)

    landlord = await session.get(Landlord, landlord_id)
    if landlord is not None:
        landlord.address = landlord.address or "300000, г. Тула, ул. Демонстрационная, д. 1"
        landlord.kpp = landlord.kpp or "710001001"
        landlord.bank_name = landlord.bank_name or "ПАО «Демо-Банк»"
        landlord.bik = landlord.bik or "044525225"
        landlord.account = landlord.account or "40702810900000000001"
        landlord.corr_account = landlord.corr_account or "30101810400000000225"

    today = date.today()

    prem_a = Premises(landlord_id=landlord_id, label="Помещение А1", address="г. Тула, ул. Демонстрационная, 1",
                      area=Decimal("120.00"), is_occupied=True)
    prem_b = Premises(landlord_id=landlord_id, label="Помещение Б2 (свободно)", address="г. Тула, ул. Демонстрационная, 3",
                      area=Decimal("55.50"), is_occupied=False)
    session.add_all([prem_a, prem_b])
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
    lease2 = Lease(tenant_id=tenant2.id, premises_id=prem_a.id, contract_no="18/2026-АР",
                   contract_date=today - timedelta(days=30), rent_amount=Decimal("15000.00"),
                   payment_day=10, status=LeaseStatus.active)
    session.add_all([lease1, lease2])
    await session.flush()

    meter = Meter(premises_id=prem_a.id, serial_no="М-1001", label="Основной", coefficient=Decimal("1.0"))
    session.add(meter)

    tasks = [
        Task(landlord_id=landlord_id, title="Демо: проверить показания счётчика", due_date=today + timedelta(days=3)),
        Task(landlord_id=landlord_id, title="Демо: продлить договор 17/2026-АР", due_date=today + timedelta(days=14)),
        Task(landlord_id=landlord_id, title="Демо: просроченная задача", due_date=today - timedelta(days=2)),
    ]
    session.add_all(tasks)
    await session.flush()

    return {"premises": 2, "tenants": 2, "leases": 2, "meters": 1, "tasks": len(tasks)}
