"""Тесты обработки решения арендодателя по платежу."""
from datetime import date
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import select

from app.db.enums import ChargeStatus, ChargeType, LeaseStatus, OrgType, PaymentStatus, TaxMode
from app.db.models import Charge, Landlord, Lease, Notification, Premises, Tenant
from app.services import billing_service, confirmation_service, payment_service, settings_service


@pytest_asyncio.fixture
async def env(session):
    landlord = Landlord(name="ИП Иванов", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn)
    session.add(landlord)
    await session.flush()
    await settings_service.ensure_defaults(session, landlord.id)
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
    # начисление аренды за апрель
    await billing_service.create_rent_charge(session, lease, date(2026, 4, 1))
    await session.flush()
    return {"landlord": landlord, "tenant": tenant, "lease": lease}


async def _notif_types(session):
    return (await session.execute(select(Notification.type))).scalars().all()


async def test_confirm_full_payment(session, env):
    payment = await payment_service.register_payment(session, env["lease"].id, Decimal("50000.00"))
    await session.flush()

    result = await confirmation_service.process_payment_decision(
        session, payment, approve=True, user_id=None, today=date(2026, 4, 6)
    )
    await session.flush()

    assert result["approved"] is True and result["fully_paid"] is True
    assert payment.status == PaymentStatus.confirmed
    rent = (await session.execute(select(Charge).where(Charge.type == ChargeType.rent))).scalars().first()
    assert rent.status == ChargeStatus.paid
    assert "payment_confirmed" in await _notif_types(session)


async def test_confirm_partial_payment_issues_shortfall(session, env):
    payment = await payment_service.register_payment(session, env["lease"].id, Decimal("30000.00"))
    await session.flush()

    result = await confirmation_service.process_payment_decision(
        session, payment, approve=True, user_id=None, today=date(2026, 4, 6)
    )
    await session.flush()

    assert result["fully_paid"] is False
    assert result["remaining_debt"] == Decimal("20000.00")
    assert payment.status == PaymentStatus.partial
    assert "payment_confirmed_partial" in await _notif_types(session)


async def test_reject_payment(session, env):
    payment = await payment_service.register_payment(session, env["lease"].id, Decimal("50000.00"))
    await session.flush()

    result = await confirmation_service.process_payment_decision(
        session, payment, approve=False, user_id=None
    )
    await session.flush()

    assert result["approved"] is False
    assert payment.status == PaymentStatus.rejected
    # начисление осталось неоплаченным
    rent = (await session.execute(select(Charge).where(Charge.type == ChargeType.rent))).scalars().first()
    assert rent.paid_amount == Decimal("0")
    assert "payment_rejected" in await _notif_types(session)
