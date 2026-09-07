"""Тесты справочников: помещения, арендаторы, договоры, счётчики."""
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.enums import LeaseStatus, OrgType, TaxMode
from app.db.models import Lease, Meter, Premises, Tenant, Landlord
from app.services import directory_service


@pytest_asyncio.fixture
async def landlord(session):
    lord = Landlord(name="ИП Иванов", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn)
    session.add(lord)
    await session.flush()
    return lord


# --- Помещения ---
async def test_create_premises(session, landlord):
    p = await directory_service.create_premises(
        session, landlord_id=landlord.id, label="Склад А", address="ул. Ленина, 1", area=Decimal("120.5")
    )
    await session.flush()
    assert p.id is not None
    items = await directory_service.list_premises(session, landlord.id)
    assert [x.label for x in items] == ["Склад А"]


async def test_create_premises_empty_label(session, landlord):
    with pytest.raises(ValueError):
        await directory_service.create_premises(session, landlord_id=landlord.id, label="   ")


async def test_create_premises_bad_area(session, landlord):
    with pytest.raises(ValueError):
        await directory_service.create_premises(
            session, landlord_id=landlord.id, label="Х", area=Decimal("0")
        )


# --- Арендаторы ---
async def test_create_tenant(session, landlord):
    t = await directory_service.create_tenant(
        session, landlord_id=landlord.id, name="ООО Ромашка", type=OrgType.ooo, inn="7100000001", email="t@ex.ru"
    )
    await session.flush()
    assert t.inn == "7100000001"
    items = await directory_service.list_tenants(session, landlord.id)
    assert len(items) == 1


async def test_create_tenant_ip_inn12(session, landlord):
    t = await directory_service.create_tenant(
        session, landlord_id=landlord.id, name="ИП Петров", type=OrgType.ip, inn="710000000000"
    )
    await session.flush()
    assert t.type == OrgType.ip


@pytest.mark.parametrize("inn", ["123", "abcdefghij", "7100000001234", ""])
async def test_create_tenant_bad_inn(session, landlord, inn):
    with pytest.raises(ValueError):
        await directory_service.create_tenant(
            session, landlord_id=landlord.id, name="Х", type=OrgType.ooo, inn=inn
        )


# --- Договоры ---
async def test_create_lease(session, landlord):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="Склад")
    t = await directory_service.create_tenant(
        session, landlord_id=landlord.id, name="ООО Ромашка", type=OrgType.ooo, inn="7100000001"
    )
    await session.flush()
    lease = await directory_service.create_lease(
        session, tenant_id=t.id, premises_id=p.id, contract_no="17/2024-АР",
        contract_date=date(2024, 3, 1), rent_amount=Decimal("50000"), payment_day=10,
    )
    await session.flush()
    assert lease.status == LeaseStatus.active
    assert lease.payment_day == 10
    items = await directory_service.list_leases(session, landlord.id)
    assert len(items) == 1


async def test_create_lease_defaults(session, landlord):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="Склад")
    t = await directory_service.create_tenant(
        session, landlord_id=landlord.id, name="ООО Ромашка", type=OrgType.ooo, inn="7100000001"
    )
    await session.flush()
    lease = await directory_service.create_lease(
        session, tenant_id=t.id, premises_id=p.id, contract_no="1",
        contract_date=date(2024, 3, 1), rent_amount=Decimal("1000"),
    )
    await session.flush()
    assert lease.payment_day == 5
    assert lease.penalty_rate == Decimal("0.5")


@pytest.mark.parametrize("rent,payday", [(Decimal("0"), 5), (Decimal("1000"), 0), (Decimal("1000"), 32)])
async def test_create_lease_validation(session, landlord, rent, payday):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="Склад")
    t = await directory_service.create_tenant(
        session, landlord_id=landlord.id, name="ООО", type=OrgType.ooo, inn="7100000001"
    )
    await session.flush()
    with pytest.raises(ValueError):
        await directory_service.create_lease(
            session, tenant_id=t.id, premises_id=p.id, contract_no="1",
            contract_date=date(2024, 3, 1), rent_amount=rent, payment_day=payday,
        )


# --- Счётчики ---
async def test_create_meter(session, landlord):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="Склад")
    await session.flush()
    m = await directory_service.create_meter(
        session, premises_id=p.id, serial_no="М-100", coefficient=Decimal("0.93")
    )
    await session.flush()
    items = await directory_service.list_meters(session, landlord.id)
    assert len(items) == 1
    meter, prem_label = items[0]
    assert meter.serial_no == "М-100"
    assert prem_label == "Склад"


async def test_create_meter_bad_coeff(session, landlord):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="Склад")
    await session.flush()
    with pytest.raises(ValueError):
        await directory_service.create_meter(session, premises_id=p.id, coefficient=Decimal("0"))
