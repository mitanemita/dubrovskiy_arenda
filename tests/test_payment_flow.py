"""Интеграционный тест финансового цикла на SQLite: начисления → платежи → пеня."""
from datetime import date
from decimal import Decimal

import pytest_asyncio

from app.db.enums import ChargeStatus, ChargeType, OrgType, PaymentStatus, TaxMode
from app.db.models import Landlord, Lease, Meter, MeterReading, Premises, Tenant
from app.services import billing_service, payment_service, settings_service


@pytest_asyncio.fixture
async def lease(session):
    """Готовый арендодатель + помещение + счётчик + арендатор + договор."""
    landlord = Landlord(name="ИП Иванов", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn)
    session.add(landlord)
    await session.flush()
    await settings_service.ensure_defaults(session, landlord.id)

    premises = Premises(landlord_id=landlord.id, label="Склад №3", address="г. Узловая, ул. Складская, 3")
    session.add(premises)
    await session.flush()

    meter = Meter(premises_id=premises.id, serial_no="М-1", label="Основной")
    session.add(meter)

    tenant = Tenant(landlord_id=landlord.id, name="ООО Ромашка", type=OrgType.ooo,
                    inn="7100000001", kpp="710001001", email="t@ex.ru")
    session.add(tenant)
    await session.flush()

    lease = Lease(
        tenant_id=tenant.id, premises_id=premises.id, contract_no="17/2024-АР",
        contract_date=date(2024, 3, 1), rent_amount=Decimal("50000.00"), payment_day=5,
        penalty_rate=Decimal("0.5"),
    )
    session.add(lease)
    await session.flush()
    # Показания за апрель 2026: 15230 -> 15350 = 120 кВт·ч
    session.add(MeterReading(
        meter_id=meter.id, period=date(2026, 4, 1),
        prev_value=Decimal("15230"), curr_value=Decimal("15350"), consumption=Decimal("120"),
    ))
    await session.flush()
    return lease


async def test_charges_created(session, lease):
    period = date(2026, 4, 1)
    rent = await billing_service.create_rent_charge(session, lease, period)
    elec = await billing_service.create_electricity_charge(session, lease, period)
    await session.flush()

    assert rent.amount == Decimal("50000.00")
    assert rent.due_date == date(2026, 4, 5)
    assert elec is not None
    # тариф 14 по умолчанию, без коэффициента у счётчика -> 120*14 = 1680.00
    assert elec.amount == Decimal("1680.00")


async def test_electricity_none_without_readings(session, lease):
    # период без показаний -> None (арендодателя надо уведомить)
    elec = await billing_service.create_electricity_charge(session, lease, date(2026, 5, 1))
    assert elec is None


async def test_partial_payment_then_full(session, lease):
    period = date(2026, 4, 1)
    rent = await billing_service.create_rent_charge(session, lease, period)
    elec = await billing_service.create_electricity_charge(session, lease, period)
    await session.flush()

    # Частичный платёж 50 000: закрывает аренду, электричество остаётся
    p1 = await payment_service.register_payment(session, lease.id, Decimal("50000.00"))
    await session.flush()
    summary1 = await payment_service.confirm_payment(session, p1, confirmed_by_id=None, today=date(2026, 4, 6))
    await session.flush()

    assert rent.status == ChargeStatus.paid
    assert elec.status == ChargeStatus.draft  # не оплачено и документ ещё не выставлялся
    assert summary1["fully_paid"] is False
    assert summary1["remaining_debt"] == Decimal("1680.00")
    assert p1.status == PaymentStatus.partial

    # Второй платёж на остаток 1 680 -> всё закрыто
    p2 = await payment_service.register_payment(session, lease.id, Decimal("1680.00"))
    await session.flush()
    summary2 = await payment_service.confirm_payment(session, p2, confirmed_by_id=None, today=date(2026, 4, 6))
    await session.flush()

    assert elec.status == ChargeStatus.paid
    assert summary2["fully_paid"] is True
    assert p2.status == PaymentStatus.confirmed


async def test_penalty_on_full_amount(session, lease):
    period = date(2026, 4, 1)
    await billing_service.create_rent_charge(session, lease, period)
    await billing_service.create_electricity_charge(session, lease, period)
    await session.flush()

    # Просрочка 7 дней (срок 05.04, сегодня 12.04), пеня на всю сумму 51 680
    penalty = await billing_service.upsert_penalty_charge(session, lease, period, today=date(2026, 4, 12))
    await session.flush()
    assert penalty is not None
    assert penalty.type == ChargeType.penalty
    # 51680 * 0.5% * 7 = 1808.80
    assert penalty.amount == Decimal("1808.80")


async def test_overpayment_leftover(session, lease):
    period = date(2026, 4, 1)
    await billing_service.create_rent_charge(session, lease, period)
    await billing_service.create_electricity_charge(session, lease, period)
    await session.flush()

    p = await payment_service.register_payment(session, lease.id, Decimal("52000.00"))
    await session.flush()
    summary = await payment_service.confirm_payment(session, p, confirmed_by_id=None, today=date(2026, 4, 6))
    assert summary["leftover"] == Decimal("320.00")
    assert summary["fully_paid"] is True
