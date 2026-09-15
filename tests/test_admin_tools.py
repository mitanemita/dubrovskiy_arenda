"""Тесты служебных операций: заполнение демо-данными и очистка БД."""
import pytest_asyncio
from sqlalchemy import func, select

from datetime import date

from app.db.enums import OrgType, TaxMode
from app.db.models import Landlord, Lease, Meter, Premises, Task, Tenant
from app.services import admin_service, directory_service, report_service


@pytest_asyncio.fixture
async def landlord(session):
    lord = Landlord(name="ИП", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn)
    session.add(lord)
    await session.flush()
    return lord


async def _count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_seed_then_wipe(session, landlord):
    counts = await admin_service.seed_test_data(session, landlord.id)
    await session.flush()
    assert counts["premises"] == 3
    assert counts["leases"] == 2
    assert counts["payments"] == 2
    assert counts["charges"] == 4
    assert await _count(session, Premises) == 3
    assert await _count(session, Tenant) == 2
    assert await _count(session, Lease) == 2
    assert await _count(session, Meter) == 2

    # реквизиты арендодателя заполнены заглушками
    lord = await directory_service.get_landlord(session, landlord.id)
    assert lord.bank_name and lord.bik and lord.account and lord.kpp

    # отчёты не пустые: платежи по помещениям и электричество показывают данные
    pays = await report_service.payments_by_premises(session, landlord.id)
    assert len(pays) >= 2 and all(p["confirmed_total"] > 0 for p in pays)
    elec = await report_service.electricity_summary(session, landlord.id, date.today())
    assert len(elec) >= 2 and all(e["consumption_kwh"] > 0 and e["amount"] > 0 for e in elec)

    # задачи не относятся к seed/wipe (рабочие данные): своя задача переживает очистку
    session.add(Task(landlord_id=landlord.id, title="Рабочая задача"))
    await session.flush()

    # очистка удаляет всё бизнес-данное, арендодатель остаётся
    wiped = await admin_service.wipe_business_data(session, landlord.id)
    await session.flush()
    assert wiped.get("premises") == 3
    assert wiped.get("leases") == 2
    assert await _count(session, Premises) == 0
    assert await _count(session, Tenant) == 0
    assert await _count(session, Lease) == 0
    assert await _count(session, Meter) == 0
    assert await _count(session, Landlord) == 1
    assert await _count(session, Task) == 1  # задача сохранилась
    # отчёты снова пустые
    assert await report_service.payments_by_premises(session, landlord.id) == []


async def test_seed_is_idempotent(session, landlord):
    await admin_service.seed_test_data(session, landlord.id)
    await session.flush()
    await admin_service.seed_test_data(session, landlord.id)
    await session.flush()
    # повторный посев не плодит дубли (очищает перед созданием)
    assert await _count(session, Premises) == 3
    assert await _count(session, Tenant) == 2


async def test_wipe_empty(session, landlord):
    wiped = await admin_service.wipe_business_data(session, landlord.id)
    assert wiped == {}
