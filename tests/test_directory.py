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


# --- Статус помещения (свободно/занято) ---
async def test_premises_status_default_and_toggle(session, landlord):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="Склад")
    await session.flush()
    assert p.is_occupied is False
    await directory_service.set_premises_status(session, p.id, True)
    await session.flush()
    assert p.is_occupied is True
    await directory_service.set_premises_status(session, p.id, False)
    await session.flush()
    assert p.is_occupied is False


async def test_create_premises_occupied(session, landlord):
    p = await directory_service.create_premises(
        session, landlord_id=landlord.id, label="Офис", is_occupied=True
    )
    await session.flush()
    assert p.is_occupied is True


async def _prem_tenant_lease(session, landlord):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="Склад")
    t = await directory_service.create_tenant(
        session, landlord_id=landlord.id, name="ООО", type=OrgType.ooo, inn="7100000001"
    )
    await session.flush()
    lease = await directory_service.create_lease(
        session, tenant_id=t.id, premises_id=p.id, contract_no="1",
        contract_date=date(2024, 1, 1), rent_amount=Decimal("1000"),
    )
    await session.flush()
    return p, t, lease


async def test_lease_marks_premises_occupied(session, landlord):
    p, t, lease = await _prem_tenant_lease(session, landlord)
    assert p.is_occupied is True


async def test_delete_lease_frees_premises(session, landlord):
    p, t, lease = await _prem_tenant_lease(session, landlord)
    await directory_service.delete_lease(session, lease.id)
    await session.flush()
    await session.refresh(p)
    assert p.is_occupied is False


async def test_delete_tenant_frees_premises(session, landlord):
    p, t, lease = await _prem_tenant_lease(session, landlord)
    await directory_service.delete_tenant(session, t.id)
    await session.flush()
    await session.refresh(p)
    assert p.is_occupied is False


async def test_reassign_lease_moves_occupancy(session, landlord):
    p, t, lease = await _prem_tenant_lease(session, landlord)
    p2 = await directory_service.create_premises(session, landlord_id=landlord.id, label="Офис")
    await session.flush()
    await directory_service.update_lease(session, lease.id, premises_id=p2.id)
    await session.flush()
    await session.refresh(p)
    await session.refresh(p2)
    assert p.is_occupied is False
    assert p2.is_occupied is True


async def test_delete_premises_blocked_with_lease(session, landlord):
    p, t, lease = await _prem_tenant_lease(session, landlord)
    with pytest.raises(ValueError):
        await directory_service.delete_premises(session, p.id)


async def test_update_tenant_field(session, landlord):
    t = await directory_service.create_tenant(
        session, landlord_id=landlord.id, name="ООО", type=OrgType.ooo, inn="7100000001"
    )
    await session.flush()
    await directory_service.update_tenant_field(session, t.id, "kpp", "710001001")
    await directory_service.update_tenant_field(session, t.id, "email", "a@b.ru")
    await session.flush()
    assert t.kpp == "710001001" and t.email == "a@b.ru"
    with pytest.raises(ValueError):
        await directory_service.update_tenant_field(session, t.id, "kpp", "12")


async def test_active_occupants(session, landlord):
    """active_occupants возвращает арендаторов по активным договорам на помещение."""
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="Склад", is_occupied=True)
    empty = await directory_service.create_premises(session, landlord_id=landlord.id, label="Пустой")
    t = await directory_service.create_tenant(
        session, landlord_id=landlord.id, name="ООО Ромашка", type=OrgType.ooo, inn="7100000001"
    )
    await session.flush()
    await directory_service.create_lease(
        session, tenant_id=t.id, premises_id=p.id, contract_no="1",
        contract_date=date(2024, 1, 1), rent_amount=Decimal("1000"),
    )
    await session.flush()
    occ = await directory_service.active_occupants(session, landlord.id)
    assert occ.get(p.id) == ["ООО Ромашка"]
    assert empty.id not in occ


# --- Данные для документов ---
async def test_create_tenant_with_kpp_and_address(session, landlord):
    """Поля, нужные для УПД: kpp и address."""
    t = await directory_service.create_tenant(
        session, landlord_id=landlord.id, name="ООО Ромашка", type=OrgType.ooo,
        inn="7100000001", kpp="710001001", address="г. Тула, ул. Ленина, 1",
    )
    await session.flush()
    assert t.kpp == "710001001"
    assert t.address == "г. Тула, ул. Ленина, 1"


async def test_update_landlord_fields(session, landlord):
    await directory_service.update_landlord_field(session, landlord.id, "address", "г. Тула, ул. Мира, 5")
    await directory_service.update_landlord_field(session, landlord.id, "bank_name", "АО Банк")
    await directory_service.update_landlord_field(session, landlord.id, "bik", "044525225")
    await directory_service.update_landlord_field(session, landlord.id, "account", "40702810900000000001")
    await session.flush()
    lord = await directory_service.get_landlord(session, landlord.id)
    assert lord.address == "г. Тула, ул. Мира, 5"
    assert lord.bank_name == "АО Банк"
    assert lord.bik == "044525225"
    assert lord.account == "40702810900000000001"


@pytest.mark.parametrize("field,value", [
    ("bik", "12345"),          # не 9 цифр
    ("account", "123"),        # не 20 цифр
    ("kpp", "1234"),           # не 9 цифр
    ("inn", "abc"),            # не цифры
    ("name", "   "),           # пустое имя
])
async def test_update_landlord_validation(session, landlord, field, value):
    with pytest.raises(ValueError):
        await directory_service.update_landlord_field(session, landlord.id, field, value)


async def test_update_landlord_clear_optional(session, landlord):
    """Пустое значение необязательного поля очищает его (None)."""
    await directory_service.update_landlord_field(session, landlord.id, "kpp", "710001001")
    await session.flush()
    await directory_service.update_landlord_field(session, landlord.id, "kpp", "")
    await session.flush()
    lord = await directory_service.get_landlord(session, landlord.id)
    assert lord.kpp is None
