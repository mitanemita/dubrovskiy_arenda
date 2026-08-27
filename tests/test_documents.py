"""Тесты генерации документов: реквизиты УПД 5.03, рендер, квитанция."""
from datetime import date
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import select

from app.db.enums import ChargeType, LeaseStatus, OrgType, TaxMode
from app.db.models import Charge, Landlord, Lease, Meter, MeterReading, Premises, Tenant
from app.documents import render
from app.services import billing_service, document_service, settings_service


@pytest_asyncio.fixture
async def env(session):
    landlord = Landlord(
        name="ИП Иванов Иван Иванович", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn,
        address="300000, г. Тула, ул. Примерная, 1", bank_name="ПАО Сбербанк",
        bik="047003608", account="40802810000000000123", corr_account="30101810300000000608",
    )
    session.add(landlord)
    await session.flush()
    await settings_service.ensure_defaults(session, landlord.id)
    await settings_service.set_setting(session, landlord.id, "electricity_coeff", "1.0")

    premises = Premises(landlord_id=landlord.id, label="Склад №3", address="г. Узловая, ул. Складская, 3")
    session.add(premises)
    await session.flush()
    meter = Meter(premises_id=premises.id, serial_no="М-100")
    session.add(meter)
    tenant = Tenant(landlord_id=landlord.id, name="ООО «Ромашка»", type=OrgType.ooo,
                    inn="7100000001", kpp="710001001", address="г. Узловая", email="t@ex.ru")
    session.add(tenant)
    await session.flush()
    lease = Lease(tenant_id=tenant.id, premises_id=premises.id, contract_no="17/2024-АР",
                  contract_date=date(2024, 3, 1), rent_amount=Decimal("50000.00"), payment_day=5,
                  penalty_rate=Decimal("0.5"), status=LeaseStatus.active)
    session.add(lease)
    await session.flush()
    session.add(MeterReading(meter_id=meter.id, period=date(2026, 4, 1),
                             prev_value=Decimal("15230"), curr_value=Decimal("15350"), consumption=Decimal("120")))
    await session.flush()
    return {"landlord": landlord, "tenant": tenant, "lease": lease, "premises": premises}


async def test_upd_rent_requisites(session, env):
    c = await document_service.build_upd_context(session, env["lease"].id, date(2026, 4, 1), ChargeType.rent)
    assert c["status"] == 2  # статус 2 (АУСН, без НДС)
    assert c["version"] == "5.03"
    assert c["seller"]["inn"] == "710000000000"
    assert c["buyer"]["inn"] == "7100000001" and c["buyer"]["kpp"] == "710001001"
    assert c["contract_no"] == "17/2024-АР"
    # все строки без НДС
    assert all(row["tax"] == "Без НДС" for row in c["lines"])
    # итог = сумме строк
    assert c["total"] == sum(row["amount"] for row in c["lines"]) == Decimal("50000.00")
    assert "Пятьдесят тысяч" in c["total_words"]


async def test_upd_electricity_requisites(session, env):
    c = await document_service.build_upd_context(session, env["lease"].id, date(2026, 4, 1), ChargeType.electricity)
    assert len(c["lines"]) == 1
    # 120 кВт·ч × 14 (коэфф. 1.0) = 1680
    assert c["total"] == Decimal("1680.00")
    assert c["lines"][0]["tax"] == "Без НДС"
    assert c["number"].startswith("УПД-Э-")


async def test_upd_html_render_contains_key_fields(session, env):
    c = await document_service.build_upd_context(session, env["lease"].id, date(2026, 4, 1), ChargeType.rent)
    html = render.render_html("upd.html", c)
    assert "Универсальный передаточный документ" in html
    assert "Статус: 2" in html
    assert "Без НДС" in html
    assert "710000000000" in html


async def test_upd_pdf_generated_and_recorded(session, env):
    doc = await document_service.generate_upd(session, env["lease"].id, date(2026, 4, 1), ChargeType.rent)
    await session.flush()
    assert doc.upd_version == "5.03" and doc.upd_status == 2
    assert doc.file_path.endswith(".pdf")
    from pathlib import Path
    assert Path(doc.file_path).exists()
    assert Path(doc.file_path).read_bytes()[:5] == b"%PDF-"


async def test_receipt_context_normal_and_overdue(session, env):
    # начисления за апрель
    await billing_service.create_rent_charge(session, env["lease"], date(2026, 4, 1))
    await billing_service.create_electricity_charge(session, env["lease"], date(2026, 4, 1))
    await session.flush()

    # обычная (срок не наступил)
    normal = await document_service.build_receipt_context(session, env["lease"].id, date(2026, 4, 1), today=date(2026, 4, 3))
    assert normal["overdue"] is False
    assert normal["total"] == Decimal("51680.00")

    # просроченная (7 дней) -> добавляется пеня на всю сумму
    overdue = await document_service.build_receipt_context(session, env["lease"].id, date(2026, 4, 1), today=date(2026, 4, 12))
    assert overdue["overdue"] is True
    assert overdue["days_overdue"] == 7
    penalty_lines = [r for r in overdue["lines"] if r["penalty"]]
    assert penalty_lines and penalty_lines[0]["amount"] == Decimal("1808.80")
    assert overdue["total"] == Decimal("53488.80")
