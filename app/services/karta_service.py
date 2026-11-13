"""Карта базы из актуальных данных БД (для отчётов бота).

Шаблон `app/karta/Karta-krasnodar.html` содержит план, контуры и описания литер
(JSON внутри страницы). Поверх него накладываются данные БД:
  * помещение сопоставляется с контуром по `premises.litera`;
  * есть активный договор -> арендатор = имя(ена) арендатора, фактическая рента =
    сумма аренды за само помещение (`Lease.rent_amount`);
  * договора нет -> остаётся то, что записано в шаблоне (договоры ещё заносятся).
Цвет/бейдж/расчёты считает `karta.apply` — те же правила, что в CLI:
арендатор -> синий; нет арендатора и готово -> зелёный; не готово -> оранжевый.
"""
from __future__ import annotations

import copy
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import LeaseStatus
from app.db.models import Lease, Premises, Tenant
from app.karta import karta

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "karta" / "Karta-krasnodar.html"


@lru_cache(maxsize=1)
def _template() -> tuple[str, list[dict]]:
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    return html, karta.load(html)


def _key(litera: str | None) -> str:
    """Сравнение литер без учёта регистра и пробелов; латиница-двойник -> кириллица."""
    s = "".join((litera or "").split()).upper()
    return s.translate(str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ"))


async def _active_by_litera(session: AsyncSession, landlord_id: int) -> dict[str, dict]:
    rows = (await session.execute(
        select(Premises.litera, Premises.label, Tenant.name, Lease.rent_amount)
        .join(Lease, Lease.premises_id == Premises.id)
        .join(Tenant, Tenant.id == Lease.tenant_id)
        .where(Premises.landlord_id == landlord_id, Lease.status == LeaseStatus.active)
        .order_by(Premises.id, Lease.id)
    )).all()
    result: dict[str, dict] = {}
    for litera, label, tenant, rent in rows:
        if not litera:
            continue
        r = result.setdefault(_key(litera), {"litera": litera, "tenants": [], "rent": Decimal(0)})
        if tenant not in r["tenants"]:
            r["tenants"].append(tenant)
        r["rent"] += rent or Decimal(0)
    return result


async def build_data(session: AsyncSession, landlord_id: int) -> tuple[list[dict], list[str]]:
    """Данные карты (формат karta.py) + литеры из БД, которых нет на этом плане."""
    _, base = _template()
    data = copy.deepcopy(base)
    active = await _active_by_litera(session, landlord_id)
    on_plan = set()
    for item in data:
        k = _key(item["id"])
        on_plan.add(k)
        lease = active.get(k)
        if lease is None or item.get("kind") == "tech":
            continue
        # Тот же вход, что у CLI: пересчёт статуса и сброс устаревшего бейджа.
        karta.update(data, item["id"], tenant=", ".join(lease["tenants"]), rent_fact=str(lease["rent"]))
    not_on_plan = sorted(v["litera"] for k, v in active.items() if k not in on_plan)
    return data, not_on_plan


def summarize(data: list[dict], not_on_plan: list[str], *, now: datetime) -> dict:
    counts = {"busy": 0, "free": 0, "notready": 0, "other": 0}
    rent_total, no_rent = Decimal(0), 0
    for item in data:
        if item.get("kind") == "tech":
            continue
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
    out = karta.apply(html, data)
    return out.encode("utf-8"), summarize(data, not_on_plan, now=now or datetime.now())


def caption(summary: dict) -> str:
    """Подпись к файлу карты (HTML-разметка Telegram, < 1024 символов)."""
    c = summary["counts"]
    rent = f"{int(summary['rent_total']):,}".replace(",", " ")
    lines = [
        f"<b>🗺 Карта базы на {summary['now']:%d.%m.%Y %H:%M}</b>",
        f"🔵 Арендовано {c['busy']} · 🟢 свободно {c['free']} · 🟠 не готово {c['notready']}",
        f"💰 Фактическая рента: {rent} ₽" + (f" (нет суммы у {summary['no_rent']})" if summary["no_rent"] else ""),
    ]
    if summary["not_on_plan"]:
        names = ", ".join(summary["not_on_plan"][:15])
        lines.append(f"⚠️ Нет на этом плане: {names}")
    if summary["issues"]:
        lines.append("⚠️ Есть расхождения статусов — см. лог")
    lines.append("Откройте файл в браузере.")
    return "\n".join(lines)
