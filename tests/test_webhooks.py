"""Тесты вебхуков n8n: авторизация, приём платежей и показаний, сопоставление."""
from datetime import date
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import func, select

from app.db.enums import LeaseStatus, NotifChannel, OrgType, PaymentStatus, TaxMode
from app.db.models import Landlord, Lease, Meter, MeterReading, Notification, Payment, Premises, Tenant

AUTH = {"X-Webhook-Token": "test-token"}


@pytest_asyncio.fixture
async def seeded(session):
    """Арендодатель + помещение + счётчик + арендатор + активный договор."""
    landlord = Landlord(name="ИП Иванов", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn)
    session.add(landlord)
    await session.flush()
    premises = Premises(landlord_id=landlord.id, label="Склад №3")
    session.add(premises)
    await session.flush()
    meter = Meter(premises_id=premises.id, serial_no="М-100")
    session.add(meter)
    tenant = Tenant(landlord_id=landlord.id, name="ООО Ромашка", type=OrgType.ooo, inn="7100000001", email="t@ex.ru")
    session.add(tenant)
    await session.flush()
    lease = Lease(
        tenant_id=tenant.id, premises_id=premises.id, contract_no="17/2024-АР",
        contract_date=date(2024, 3, 1), rent_amount=Decimal("50000.00"), payment_day=5,
        status=LeaseStatus.active,
    )
    session.add(lease)
    await session.flush()
    return {"landlord": landlord, "premises": premises, "meter": meter, "tenant": tenant, "lease": lease}


async def test_auth_required(client, seeded):
    resp = await client.post("/webhook/incoming-payment", json={"amount": "1000"})
    assert resp.status_code == 401


async def test_incoming_payment_matched(client, session, seeded):
    resp = await client.post(
        "/webhook/incoming-payment",
        headers=AUTH,
        json={"amount": "50000.00", "contract_no": "17/2024-АР", "payment_date": "2026-04-04",
              "proof_file": "receipts/abc.jpg"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched"] is True and body["payment_id"]

    payment = await session.get(Payment, body["payment_id"])
    assert payment.status == PaymentStatus.pending
    assert payment.amount == Decimal("50000.00")

    # Уведомления: письмо арендатору + TG арендодателю
    notifs = (await session.execute(select(Notification))).scalars().all()
    types = {n.type for n in notifs}
    assert "payment_received" in types
    assert "payment_confirm_request" in types
    channels = {n.channel for n in notifs}
    assert NotifChannel.email in channels and NotifChannel.telegram in channels


async def test_incoming_payment_unmatched(client, session, seeded):
    resp = await client.post(
        "/webhook/incoming-payment",
        headers=AUTH,
        json={"amount": "9999.00", "contract_no": "НЕТ-ТАКОГО"},
    )
    assert resp.status_code == 200
    assert resp.json()["matched"] is False

    # Платёж не создан, но есть алерт арендодателю
    count = (await session.execute(select(func.count()).select_from(Payment))).scalar_one()
    assert count == 0
    notifs = (await session.execute(select(Notification).where(Notification.type == "payment_unmatched"))).scalars().all()
    assert len(notifs) == 1


async def test_meter_reading_matched(client, session, seeded):
    resp = await client.post(
        "/webhook/meter-reading",
        headers=AUTH,
        json={"meter_serial": "М-100", "period": "2026-04-01",
              "prev_value": "15230", "curr_value": "15350"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched"] is True
    assert Decimal(body["consumption"]) == Decimal("120.00")

    reading = await session.get(MeterReading, body["reading_id"])
    assert reading.consumption == Decimal("120.00")


async def test_meter_reading_auto_prev_from_history(client, session, seeded):
    # первое показание задаёт базу, второе считает prev из истории
    await client.post("/webhook/meter-reading", headers=AUTH,
                      json={"meter_serial": "М-100", "period": "2026-04-01",
                            "prev_value": "15000", "curr_value": "15100"})
    resp = await client.post("/webhook/meter-reading", headers=AUTH,
                             json={"meter_serial": "М-100", "period": "2026-05-01",
                                   "curr_value": "15250"})
    assert resp.status_code == 200
    assert Decimal(resp.json()["consumption"]) == Decimal("150.00")  # 15250 - 15100


async def test_meter_reading_negative_rejected(client, seeded):
    resp = await client.post(
        "/webhook/meter-reading",
        headers=AUTH,
        json={"meter_serial": "М-100", "period": "2026-04-01",
              "prev_value": "15350", "curr_value": "15230"},
    )
    assert resp.status_code == 422


async def test_meter_reading_unmatched(client, session, seeded):
    resp = await client.post(
        "/webhook/meter-reading",
        headers=AUTH,
        json={"meter_serial": "НЕТ", "period": "2026-04-01", "curr_value": "100"},
    )
    assert resp.status_code == 200
    assert resp.json()["matched"] is False
    notifs = (await session.execute(select(Notification).where(Notification.type == "reading_unmatched"))).scalars().all()
    assert len(notifs) == 1
