"""Карта Краснодара по явно привязанным к площадке помещениям.

Без привязки к площадке существующие записи не распределяются автоматически.
Данные об арендаторах из шаблона не используются как текущие сведения.
"""
from __future__ import annotations

import copy
from datetime import datetime
from decimal import Decimal
from html import escape
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import LeaseStatus
from app.db.models import Lease, Premises, PropertySite, Tenant
from app.karta import karta

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "karta" / "Karta-krasnodar.html"


@lru_cache(maxsize=1)
def _template() -> tuple[str, list[dict]]:
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    return html, karta.load(html)


def _key(litera: str | None) -> str:
    """Сравнение литер без учёта регистра и пробелов; латиница-двойник -> кириллица."""
    s = "".join((litera or "").split()).upper()
    s = s.translate(str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ"))
    # Исторически на плане Г3 была ошибочно записана двумя буквами «ГЗ».
    return "Г3" if s == "ГЗ" else s


async def _krasnodar_by_litera(session: AsyncSession, landlord_id: int) -> dict[str, dict]:
    """Учитывает только помещения, которые пользователь отнёс к Краснодару."""
    sites = (await session.execute(select(PropertySite).where(
        PropertySite.landlord_id == landlord_id
    ))).scalars().all()
    site_ids = [site.id for site in sites if site.template_key == "krasnodar"]
    if not site_ids:
        return {}
    premises = (await session.execute(select(Premises).where(
        Premises.landlord_id == landlord_id, Premises.site_id.in_(site_ids)
    ).order_by(Premises.id))).scalars().all()
    active = (await session.execute(
        select(Premises.id, Tenant.name, Lease.rent_amount)
        .join(Lease, Lease.premises_id == Premises.id)
        .join(Tenant, Tenant.id == Lease.tenant_id)
        .where(Premises.landlord_id == landlord_id, Premises.site_id.in_(site_ids),
               Lease.status == LeaseStatus.active)
        .order_by(Premises.id, Lease.id)
    )).all()
    by_premises: dict[int, list] = {}
    for pid, tenant, rent in active:
        by_premises.setdefault(pid, []).append((tenant, rent))
    result: dict[str, dict] = {}
    for p in premises:
        if not p.litera:
            continue
        r = result.setdefault(_key(p.litera), {
            "litera": p.litera, "tenants": [], "rent": Decimal(0), "uncertain": False,
            "rates": [], "readiness": [], "total_area": Decimal(0), "area_known": True,
            "rent_area": Decimal(0), "rent_area_known": True,
        })
        leases = by_premises.get(p.id, [])
        r["readiness"].append(p.readiness)
        r["rates"].append(p.asking_rate)
        if p.area is None or p.area <= 0:
            r["area_known"] = False
        else:
            r["total_area"] += p.area
        if p.is_occupied and not leases:
            r["uncertain"] = True
        if leases:
            if p.area is None or p.area <= 0 or len(leases) != 1:
                r["rent_area_known"] = False
            else:
                r["rent_area"] += p.area
        for tenant, rent in leases:
            if tenant not in r["tenants"]:
                r["tenants"].append(tenant)
            r["rent"] += rent or Decimal(0)
    return result


async def build_data(session: AsyncSession, landlord_id: int) -> tuple[list[dict], list[str]]:
    """План Краснодара и привязанные к нему литеры, отсутствующие на плане."""
    _, base = _template()
    data = copy.deepcopy(base)
    assigned = await _krasnodar_by_litera(session, landlord_id)
    on_plan = set()
    readiness_labels = {
        "ready": "Готово к сдаче", "notready": "Не готово к сдаче",
        "closed": "Не сдаётся", "tech": "Техническое", "build": "Стройка",
    }
    statuses = {"ready": "free", "notready": "notready", "closed": "closed",
                "tech": "tech", "build": "build"}
    for item in data:
        k = _key(item["id"])
        # Технические участки заданы планом и не требуют записей в справочнике.
        if item.get("kind") == "tech" or item["id"] == "Е8":
            if item["id"] == "Е8":
                on_plan.add(k)
            karta.update(data, item["id"], status="tech")
            continue
        on_plan.add(k)
        info = assigned.get(k)
        # Описания и цены из шаблона не являются текущими данными учёта.
        for field in ("tenant", "rent_fact", "rate", "ready", "kind", "badge"):
            item.pop(field, None)
        item["db_area"] = None
        item["fact_area"] = None
        item["ready"] = "Не указана"
        if not info:
            karta.update(data, item["id"], status="unknown")
            continue
        if info["area_known"]:
            item["db_area"] = str(info["total_area"])
        if info["tenants"] and info["rent_area_known"] and not info["uncertain"]:
            item["fact_area"] = str(info["rent_area"])
        rates = set(info["rates"])
        if len(rates) == 1 and None not in rates:
            item["rate"] = str(next(iter(rates)))
        elif len(rates) > 1:
            item["rate_note"] = "разные или не указаны"
        # Для ранее созданных помещений без заданной готовности свободно — значение по умолчанию.
        readiness = {r if r is not None else "ready" for r in info["readiness"]}
        if len(readiness) == 1:
            value = next(iter(readiness))
            if value in readiness_labels:
                item["ready"] = readiness_labels[value]
        else:
            value = None
        if info["uncertain"]:
            status = "unknown"
        elif info["tenants"]:
            status = "busy"
        else:
            status = statuses.get(value, "unknown")
        if status == "tech":
            item["kind"] = "tech"
        fields = {"status": status}
        if info["tenants"]:
            fields.update(tenant=", ".join(info["tenants"]), rent_fact=str(info["rent"]))
        karta.update(data, item["id"], **fields)
    not_on_plan = sorted(v["litera"] for k, v in assigned.items() if k not in on_plan)
    return data, not_on_plan


async def unassigned_count(session: AsyncSession, landlord_id: int) -> int:
    """Помещения без выбранной площадки не включаются ни в одну карту."""
    from sqlalchemy import func
    return (await session.execute(select(func.count(Premises.id)).where(
        Premises.landlord_id == landlord_id, Premises.site_id.is_(None)
    ))).scalar_one()


def summarize(data: list[dict], not_on_plan: list[str], *, now: datetime) -> dict:
    counts = {"busy": 0, "free": 0, "notready": 0, "closed": 0, "tech": 0,
              "build": 0, "unknown": 0, "other": 0}
    rent_total, no_rent = Decimal(0), 0
    for item in data:
        st = karta.compute_status(item)
        counts[st if st in counts else "other"] += 1
        if st == "busy":
            rf = karta._dec(item.get("rent_fact"))
            if rf is None:
                no_rent += 1
            else:
                rent_total += rf
    return {"counts": counts, "rent_total": rent_total, "no_rent": no_rent,
            "not_on_plan": not_on_plan, "now": now, "issues": karta.check(data)}


async def render_html(session: AsyncSession, landlord_id: int, *, now: datetime | None = None) -> tuple[bytes, dict]:
    """Готовый HTML (bytes) и сводка для подписи к файлу."""
    html, _ = _template()
    data, not_on_plan = await build_data(session, landlord_id)
    summary = summarize(data, not_on_plan, now=now or datetime.now())
    summary["unassigned"] = await unassigned_count(session, landlord_id)
    out = karta.apply(html, data)
    out = out.replace("</head>", "<style>.c-unknown{fill:#fff;stroke:#555;stroke-dasharray:20,12}</style></head>", 1)
    out = out.replace('<span><i style="background:#d14b4b"></i>Стройка</span>',
                      '<span><i style="background:#d14b4b"></i>Стройка</span>'
                      '<span><i style="background:#fff;border:1px dashed #555"></i>Нет данных</span>', 1)
    return out.encode("utf-8"), summary


def caption(summary: dict, *, link: bool = False) -> str:
    """Подпись к файлу карты (HTML-разметка Telegram, < 1024 символов)."""
    c = summary["counts"]
    total = summary["rent_total"].quantize(Decimal("0.01"))
    rent = (f"{total:,.2f}" if total % 1 else f"{total:,.0f}").replace(",", " ").replace(".", ",")
    lines = [
        f"<b>🗺 Краснодар на {summary['now']:%d.%m.%Y %H:%M}</b>",
        f"🔵 Арендовано {c['busy']} · 🟢 свободно {c['free']} · 🟠 не готово {c['notready']}",
        f"⚫ Не сдаётся {c.get('closed', 0)} · ⚙️ техническое {c.get('tech', 0)} · 🔴 стройка {c.get('build', 0)}",
        f"⚪ Нет подтверждённых данных: {c.get('unknown', 0)}",
        f"💰 Фактическая рента: {rent} ₽" + (f" (нет суммы у {summary['no_rent']})" if summary["no_rent"] else ""),
    ]
    if summary["not_on_plan"]:
        raw_names = ", ".join(summary["not_on_plan"][:15])
        parts, size = [], 0
        for char in raw_names:
            part = escape(char)
            if size + len(part) > 250:
                parts.append("…")
                break
            parts.append(part)
            size += len(part)
        lines.append(f"⚠️ Нет на этом плане: {''.join(parts)}")
    if summary["issues"]:
        lines.append("⚠️ Есть расхождения статусов — см. лог")
    lines.append("На карте только помещения с площадкой «Краснодар».")
    if summary.get("unassigned"):
        lines.append(f"⚠️ Без площадки: {summary['unassigned']} помещений.")
    lines.append("Нажмите «🗺 Открыть карту» — карта всегда по актуальным данным."
                 if link else "Откройте файл в браузере.")
    return "\n".join(lines)
