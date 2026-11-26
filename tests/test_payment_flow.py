"""Интеграционный тест финансового цикла на SQLite: начисления → платежи → пеня."""
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.enums import ChargeStatus, ChargeType, OrgType, PaymentStatus, TaxMode
from app.db.models import Charge, Landlord, Lease, Meter, MeterReading, Premises, Tenant
from app.services import billing_service, payment_service, settings_service


@pytest_asyncio.fixture
async def lease(session):
    """Готовый арендодатель + помещение + счётчик + арендатор + договор."""
    landlord = Landlord(name="ИП Иванов", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn)
    session.add(landlord)
    await session.flush()
    await settings_service.ensure_defaults(session, landlord.id)
    # Фиксируем коэффициент = 1.0, чтобы суммы в тестах не зависели от значения по умолчанию
    await settings_service.set_setting(session, landlord.id, "electricity_coeff", "1.0")

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


async def test_penalty_uses_rate_from_settings(session, lease):
    """Ставка пени берётся из настроек («Ставка пени, %/день»), а не из договора."""
    period = date(2026, 4, 1)
    await billing_service.create_rent_charge(session, lease, period)
    await session.flush()
    landlord_id = (await session.get(Tenant, lease.tenant_id)).landlord_id
    await settings_service.set_setting(session, landlord_id, "penalty_rate", "0.1")
    await session.flush()

    penalty = await billing_service.upsert_penalty_charge(session, lease, period, today=date(2026, 4, 15))
    # 50000 * 0.1% * 10 дней = 500.00
    assert penalty.amount == Decimal("500.00")


async def test_electricity_charge_follows_readings(session, lease):
    """Ввод/исправление показаний создаёт и пересчитывает начисление за любой месяц."""
    from app.services import reading_service

    meter = (await session.execute(select(Meter))).scalars().one()
    april, may = date(2026, 4, 1), date(2026, 5, 1)
    today = date(2026, 6, 20)  # оба месяца уже прошли

    # Начисление за прошедший апрель создаётся по уже введённым показаниям (120 кВт·ч)
    [apr] = await billing_service.sync_electricity_charges(session, lease.premises_id, april, today)
    assert apr.amount == Decimal("1680.00")

    # Показания за май: 15350 -> 15450 = 100 кВт·ч
    await reading_service.upsert_reading(session, meter, period=may, curr_value=Decimal("15450"))
    await session.flush()
    [may_charge] = await billing_service.sync_electricity_charges(session, lease.premises_id, may, today)
    assert may_charge.amount == Decimal("1400.00")

    # Исправили апрель (15340 вместо 15350): апрель 110 кВт·ч, май автоматически 110 кВт·ч
    await reading_service.upsert_reading(
        session, meter, period=april, curr_value=Decimal("15340"), prev_value=Decimal("15230")
    )
    await session.flush()
    assert await reading_service.next_reading_period(session, meter.id, april) == may
    await billing_service.sync_electricity_charges(session, lease.premises_id, april, today)
    await billing_service.sync_electricity_charges(session, lease.premises_id, may, today)
    assert apr.amount == Decimal("1540.00")
    assert may_charge.amount == Decimal("1540.00")
    # начисление не задвоилось
    count = (await session.execute(
        select(Charge).where(Charge.type == ChargeType.electricity)
    )).scalars().all()
    assert len(count) == 2


async def test_reading_above_next_month_rejected(session, lease):
    from app.services import reading_service

    meter = (await session.execute(select(Meter))).scalars().one()
    with pytest.raises(ValueError):
        await reading_service.upsert_reading(session, meter, period=date(2026, 3, 1), curr_value=Decimal("99999"))


async def test_first_reading_is_baseline(session, lease):
    """Самое раннее показание счётчика — начальное: расход 0, начисления нет."""
    from app.services import reading_service

    premises = await session.get(Premises, lease.premises_id)
    meter = Meter(premises_id=premises.id, serial_no="М-2")
    session.add(meter)
    await session.flush()

    first = await reading_service.upsert_reading(session, meter, period=date(2026, 7, 1), curr_value=Decimal("5000"))
    await session.flush()
    assert first.consumption == Decimal("0.00")
    assert await billing_service.sync_electricity_charges(session, premises.id, date(2026, 7, 1), date(2026, 7, 2)) == []

    second = await reading_service.upsert_reading(session, meter, period=date(2026, 8, 1), curr_value=Decimal("5100"))
    await session.flush()
    assert second.consumption == Decimal("100.00")
    [charge] = await billing_service.sync_electricity_charges(session, premises.id, date(2026, 8, 1), date(2026, 8, 2))
    assert charge.amount == Decimal("1400.00")
