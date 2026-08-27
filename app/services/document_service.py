"""Сборка данных из БД -> контекст -> PDF (УПД 5.03 и квитанция), запись в documents."""
from __future__ import annotations

import base64
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import (
    ChargeType,
    DocFormat,
    DocSentStatus,
    DocType,
)
from app.db.models import Charge, Document, Landlord, Lease, Meter, MeterReading, Premises, Tenant
from app.documents import context as ctx
from app.documents import render
from app.domain import billing
from app.domain.money import money, rubles_kopecks_in_words
from app.services import settings_service
from app.services.billing_service import period_start

OUTPUT_DIR = Path("data/documents")

_TAX_LABEL = "Без НДС"


async def _load_bundle(session: AsyncSession, lease_id: int) -> tuple[Lease, Tenant, Premises, Landlord]:
    lease = await session.get(Lease, lease_id)
    if lease is None:
        raise ValueError(f"Договор id={lease_id} не найден")
    tenant = await session.get(Tenant, lease.tenant_id)
    premises = await session.get(Premises, lease.premises_id)
    landlord = await session.get(Landlord, tenant.landlord_id)
    return lease, tenant, premises, landlord


def _party(entity) -> dict:
    return {
        "name": entity.name,
        "inn": entity.inn,
        "kpp": getattr(entity, "kpp", None),
        "address": getattr(entity, "address", None),
    }


def _signature_data_uri(landlord: Landlord) -> str | None:
    path = landlord.signature_path
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    data = base64.b64encode(p.read_bytes()).decode()
    return f"data:image/png;base64,{data}"


async def build_upd_context(
    session: AsyncSession, lease_id: int, period: date, kind: ChargeType, doc_date: date | None = None
) -> dict:
    """Контекст УПД 5.03 (статус 2, без НДС) для аренды или электричества."""
    period = period_start(period)
    doc_date = doc_date or date.today()
    lease, tenant, premises, landlord = await _load_bundle(session, lease_id)
    status = await settings_service.get_int(session, landlord.id, "upd_status")

    lines: list[dict] = []
    if kind == ChargeType.rent:
        lines.append({
            "n": 1,
            "name": (
                f"Аренда нежилого помещения по адресу {premises.address or premises.label} "
                f"по договору аренды № {lease.contract_no} от {ctx.date_dmy(lease.contract_date)} "
                f"за {ctx.period_ru(period)}"
            ),
            "unit": "мес", "qty": 1,
            "price": money(lease.rent_amount), "amount": money(lease.rent_amount), "tax": _TAX_LABEL,
        })
        prefix = "А"
    elif kind == ChargeType.electricity:
        tariff = await settings_service.get_decimal(session, landlord.id, "electricity_tariff")
        default_coeff = await settings_service.get_decimal(session, landlord.id, "electricity_coeff")
        meters = (await session.execute(select(Meter).where(Meter.premises_id == premises.id))).scalars().all()
        n = 0
        for meter in meters:
            reading = (await session.execute(
                select(MeterReading).where(MeterReading.meter_id == meter.id, MeterReading.period == period)
            )).scalars().first()
            if reading is None:
                continue
            n += 1
            coeff = meter.coefficient if meter.coefficient is not None else default_coeff
            amount = billing.electricity_amount(reading.consumption, tariff, coeff)
            lines.append({
                "n": n,
                "name": (
                    f"Возмещение переменной части арендной платы (электроэнергия) "
                    f"по договору № {lease.contract_no} за {ctx.period_ru(period)}, "
                    f"счётчик {meter.serial_no or meter.label or n}"
                ),
                "unit": "кВт·ч", "qty": money(reading.consumption),
                "price": money(tariff), "amount": amount, "tax": _TAX_LABEL,
            })
        prefix = "Э"
    else:
        raise ValueError("kind должен быть rent или electricity")

    total = money(sum((row["amount"] for row in lines), Decimal("0")))

    return {
        "status": status,
        "version": "5.03",
        "number": f"УПД-{prefix}-{lease.contract_no}/{period.strftime('%m-%y')}",
        "doc_date_str": ctx.date_ru(doc_date),
        "seller": _party(landlord),
        "buyer": _party(tenant),
        "contract_no": lease.contract_no,
        "contract_date_str": ctx.date_dmy(lease.contract_date),
        "lines": lines,
        "total": total,
        "total_words": rubles_kopecks_in_words(total),
        "signature_data_uri": _signature_data_uri(landlord),
    }


async def build_receipt_context(
    session: AsyncSession, lease_id: int, period: date, today: date | None = None
) -> dict:
    """Контекст квитанции: аренда+электричество (+пеня при просрочке)."""
    period = period_start(period)
    today = today or date.today()
    lease, tenant, premises, landlord = await _load_bundle(session, lease_id)

    charges = (await session.execute(
        select(Charge).where(
            Charge.lease_id == lease_id,
            Charge.period == period,
            Charge.type.in_([ChargeType.rent, ChargeType.electricity]),
        ).order_by(Charge.type)
    )).scalars().all()

    lines: list[dict] = []
    principal_outstanding = Decimal("0")
    for ch in charges:
        outstanding = money(ch.amount - ch.paid_amount)
        if outstanding <= 0:
            continue
        principal_outstanding += outstanding
        name = "Аренда помещения" if ch.type == ChargeType.rent else "Электроэнергия"
        lines.append({"name": name, "qty": "1", "amount": outstanding, "penalty": False})

    due = billing.payment_due_date(period, lease.payment_day)
    days = billing.days_overdue(due, today)
    overdue = days > 0 and principal_outstanding > 0

    if overdue:
        penalty = billing.penalty_amount(principal_outstanding, lease.penalty_rate, days)
        lines.append({
            "name": f"Пеня за просрочку ({days} дн.)", "qty": f"{days} дн.",
            "amount": penalty, "penalty": True,
        })

    total = money(sum((row["amount"] for row in lines), Decimal("0")))

    return {
        "period_str": ctx.period_ru(period),
        "due_str": ctx.date_dmy(due),
        "overdue": overdue,
        "days_overdue": days,
        "penalty_rate": lease.penalty_rate,
        "receiver": {
            "name": landlord.name, "inn": landlord.inn, "bank_name": landlord.bank_name,
            "bik": landlord.bik, "account": landlord.account, "corr_account": landlord.corr_account,
        },
        "payer": {
            "name": tenant.name, "contract_no": lease.contract_no,
            "contract_date_str": ctx.date_dmy(lease.contract_date),
        },
        "lines": lines,
        "total": total,
        "total_words": rubles_kopecks_in_words(total),
    }


async def receipt_pdf(session: AsyncSession, lease_id: int, period: date, today: date | None = None) -> tuple[bytes, str]:
    """PDF квитанции + имя файла."""
    context = await build_receipt_context(session, lease_id, period, today)
    pdf = render.render_pdf("receipt.html", context)
    period = period_start(period)
    filename = f"Квитанция_{lease_id}_{period.strftime('%Y-%m')}.pdf"
    return pdf, filename


async def generate_upd(
    session: AsyncSession, lease_id: int, period: date, kind: ChargeType, doc_date: date | None = None
) -> Document:
    """Формирует УПД (PDF), сохраняет файл и запись в documents."""
    period = period_start(period)
    context = await build_upd_context(session, lease_id, period, kind, doc_date)
    pdf = render.render_pdf("upd.html", context)

    lease, tenant, premises, landlord = await _load_bundle(session, lease_id)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_no = context["number"].replace("/", "-")
    path = OUTPUT_DIR / f"{safe_no}.pdf"
    path.write_bytes(pdf)

    doc = Document(
        landlord_id=landlord.id, lease_id=lease_id, doc_type=DocType.upd, doc_format=DocFormat.pdf,
        upd_version="5.03", upd_status=context["status"], number=context["number"], period=period,
        file_path=str(path), email_to=tenant.email, sent_status=DocSentStatus.generated,
    )
    session.add(doc)
    return doc
