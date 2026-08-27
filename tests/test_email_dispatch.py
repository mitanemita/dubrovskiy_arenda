"""Тест диспетчера email-очереди (без реального SMTP и WeasyPrint)."""
from datetime import date
from decimal import Decimal

import pytest_asyncio

from app.db.enums import LeaseStatus, NotifChannel, NotifStatus, OrgType, TaxMode
from app.db.models import Landlord, Lease, Premises, Tenant
from app.services import billing_service, email_service, notification_service


class FakeEmail:
    def __init__(self):
        self.sent = []

    async def __call__(self, to, subject, body, attachment=None, filename="document.pdf"):
        self.sent.append({"to": to, "subject": subject, "attachment": attachment, "filename": filename})


async def _fake_receipt(session, lease_id, period):
    return b"%PDF-FAKE", "receipt.pdf"


@pytest_asyncio.fixture
async def env(session):
    landlord = Landlord(name="ИП Иванов", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn)
    session.add(landlord)
    await session.flush()
    premises = Premises(landlord_id=landlord.id, label="Склад")
    session.add(premises)
    await session.flush()
    tenant = Tenant(landlord_id=landlord.id, name="ООО Ромашка", type=OrgType.ooo, inn="7100000001", email="t@ex.ru")
    session.add(tenant)
    await session.flush()
    lease = Lease(tenant_id=tenant.id, premises_id=premises.id, contract_no="17/2024-АР",
                  contract_date=date(2024, 3, 1), rent_amount=Decimal("50000.00"), payment_day=5,
                  status=LeaseStatus.active)
    session.add(lease)
    await session.flush()
    rent = await billing_service.create_rent_charge(session, lease, date(2026, 4, 1))
    await session.flush()
    return {"landlord": landlord, "tenant": tenant, "lease": lease, "rent": rent}


async def test_plain_email_no_attachment(session, env):
    await notification_service.enqueue(
        session, landlord_id=env["landlord"].id, channel=NotifChannel.email,
        type="payment_confirmed", tenant_id=env["tenant"].id,
        subject="Оплата подтверждена", body="Спасибо",
    )
    await session.flush()

    fake = FakeEmail()
    stats = await email_service.dispatch_email(session, fake, receipt_pdf=_fake_receipt)
    assert stats["sent"] == 1
    assert fake.sent[0]["to"] == "t@ex.ru"
    assert fake.sent[0]["attachment"] is None


async def test_invoice_email_has_receipt_attachment(session, env):
    await notification_service.enqueue(
        session, landlord_id=env["landlord"].id, channel=NotifChannel.email,
        type="invoice_new", tenant_id=env["tenant"].id,
        subject="Квитанция", body="во вложении", related_charge_id=env["rent"].id,
    )
    await session.flush()

    fake = FakeEmail()
    stats = await email_service.dispatch_email(session, fake, receipt_pdf=_fake_receipt)
    assert stats["sent"] == 1
    assert fake.sent[0]["attachment"] == b"%PDF-FAKE"
    assert fake.sent[0]["filename"] == "receipt.pdf"


async def test_email_without_tenant_email_skipped(session, env):
    env["tenant"].email = None
    await session.flush()
    notif = await notification_service.enqueue(
        session, landlord_id=env["landlord"].id, channel=NotifChannel.email,
        type="payment_confirmed", tenant_id=env["tenant"].id, subject="x", body="y",
    )
    await session.flush()

    fake = FakeEmail()
    stats = await email_service.dispatch_email(session, fake, receipt_pdf=_fake_receipt)
    assert stats["skipped"] == 1
    assert notif.status == NotifStatus.failed
    assert fake.sent == []
