"""Тест доставки TG-уведомлений из очереди (без реального Telegram)."""
from datetime import date
from decimal import Decimal

import pytest_asyncio

from app.db.enums import LeaseStatus, NotifChannel, NotifStatus, OrgType, TaxMode
from app.db.models import Landlord, Lease, Premises, Tenant, User
from app.services import notification_service, payment_service


class FakeSender:
    def __init__(self):
        self.calls = []

    async def __call__(self, chat_id: int, text: str, reply_markup=None) -> None:
        self.calls.append({"chat_id": chat_id, "text": text, "markup": reply_markup})


@pytest_asyncio.fixture
async def env(session):
    landlord = Landlord(name="ИП Иванов", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn)
    session.add(landlord)
    await session.flush()
    session.add(User(landlord_id=landlord.id, tg_id=555001, name="Владелец"))
    premises = Premises(landlord_id=landlord.id, label="Склад")
    session.add(premises)
    await session.flush()
    tenant = Tenant(landlord_id=landlord.id, name="ООО Ромашка", type=OrgType.ooo, inn="7100000001")
    session.add(tenant)
    await session.flush()
    lease = Lease(tenant_id=tenant.id, premises_id=premises.id, contract_no="17/2024-АР",
                  contract_date=date(2024, 3, 1), rent_amount=Decimal("50000.00"), payment_day=5,
                  status=LeaseStatus.active)
    session.add(lease)
    await session.flush()
    return {"landlord": landlord, "lease": lease, "tenant": tenant}


async def test_dispatch_plain_notification(session, env):
    from app.bot import notifier

    await notification_service.enqueue(
        session, landlord_id=env["landlord"].id, channel=NotifChannel.telegram,
        type="missing_readings", subject="Нет показаний", body="Внесите показания",
    )
    await session.flush()

    fake = FakeSender()
    stats = await notifier.dispatch_telegram(session, fake)
    await session.flush()

    assert stats["sent"] == 1
    assert fake.calls[0]["chat_id"] == 555001
    assert "Нет показаний" in fake.calls[0]["text"]
    assert fake.calls[0]["markup"] is None


async def test_dispatch_confirm_request_has_buttons(session, env):
    from app.bot import notifier

    payment = await payment_service.register_payment(session, env["lease"].id, Decimal("50000.00"))
    await session.flush()
    notif = await notification_service.enqueue(
        session, landlord_id=env["landlord"].id, channel=NotifChannel.telegram,
        type="payment_confirm_request", subject="Новый платёж", body="Подтвердить?",
        related_payment_id=payment.id,
    )
    await session.flush()

    fake = FakeSender()
    await notifier.dispatch_telegram(session, fake)
    await session.flush()

    assert notif.status == NotifStatus.sent
    markup = fake.calls[0]["markup"]
    assert markup is not None
    # в клавиатуре есть callback с id платежа
    cbs = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert f"pay:{payment.id}:ok" in cbs and f"pay:{payment.id}:no" in cbs
