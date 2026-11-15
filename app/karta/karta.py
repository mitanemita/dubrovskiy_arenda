#!/usr/bin/env python3
"""Интерактивная карта базы: данные литер -> HTML без исполняемого JS.

Источник правды — JSON внутри страницы:
    <script type="application/json" id="karta-data">[...]</script>
Из него собирается всё, что зависит от данных:
  * инфо-блоки между <!--info--> и <!--/info-->;
  * класс цвета полигона (c-free / c-busy / ...);
  * площадь в подписи на карте (<text data-a="К1">).
Картинки, контуры, CSS и планы скрипт не трогает.

Цвет литеры ВСЕГДА выводится из данных при каждой сборке:
  есть арендатор                 -> busy (синий);
  готовность начинается с не/нет -> notready (оранжевый);
  иначе                          -> free (зелёный).
Статусы closed / tech / build «липкие» — меняются только явным status=.
Значения «Нет», «—», «-», пусто считаются отсутствием арендатора.

Только стандартная библиотека Python 3.9+.

    python3 karta.py show   FILE [ЛИТЕРА]
    python3 karta.py set    FILE ЛИТЕРА поле=значение ...
    python3 karta.py render FILE
    python3 karta.py check  FILE            # проверка: цвет = арендатор/готовность
    python3 karta.py export FILE data.json
    python3 karta.py import FILE data.json
"""
from __future__ import annotations

import html as _html
import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal

# --------------------------------------------------------------------------- #
#  Справочники
# --------------------------------------------------------------------------- #
STATUS_COLOR = {
    "free": "#2eb872",
    "busy": "#3b6fe0",
    "notready": "#e8a020",
    "closed": "#8a909e",
    "tech": "#b9bec9",
    "build": "#d14b4b",
}
STATUS_BADGE = {
    "free": "Свободно, готово к сдаче",
    "busy": "Арендовано",
    "notready": "Не готово к сдаче",
    "closed": "Не сдаётся",
    "tech": "Техническое",
    "build": "Идёт стройка",
}
STICKY = {"closed", "tech", "build"}          # не пересчитываются автоматически
NO_TENANT = {"", "нет", "-", "—", "–", "none", "null", "0"}

TEXT_FIELDS = ("state", "comm", "doors", "purpose", "look", "ready", "tenant")
ROW_LABEL = {
    "state": "Состояние",
    "comm": "Коммуникации",
    "doors": "Двери",
    "purpose": "Назначение",
    "look": "Внешний вид",
    "ready": "Готовность",
    "tenant": "Арендатор",
}
DESCR_FIELDS = ("rooms", "area", "rooms_count", "state", "comm", "doors",
                "purpose", "look", "rate", "ready")

DATA_RE = re.compile(r'(<script type="application/json" id="karta-data">)(.*?)(</script>)', re.S)
INFO_RE = re.compile(r"(<!--info-->)(.*?)(<!--/info-->)", re.S)
POLY_RE = re.compile(r'(<a href="#[ij]-[^"]+" data-k="([^"]+)"><polygon class=")c-[a-z]+(")')
AREA_RE = re.compile(r'(<text[^>]*\bdata-a="([^"]+)"[^>]*>)[^<]*(</text>)')


# --------------------------------------------------------------------------- #
#  Форматирование и расчёты
# --------------------------------------------------------------------------- #
def _dec(x) -> Decimal | None:
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float, Decimal)):
        return Decimal(str(x))
    s = str(x).replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(s)
    except Exception:
        return None


def _num(d: Decimal, places: int = 1) -> str:
    """84 -> '84', 32.3 -> '32,3' (без разделителя тысяч, как на карте)."""
    q = d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    s = format(q, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.replace(".", ",")


def _json_num(d: Decimal):
    return int(d) if d == d.to_integral_value() else float(d)


def has_tenant(item: dict) -> bool:
    t = item.get("tenant")
    return t is not None and str(t).strip().lower() not in NO_TENANT


def total_area(item: dict) -> Decimal | None:
    rooms = item.get("rooms")
    if rooms:
        return sum((_dec(r.get("area")) or Decimal(0) for r in rooms), Decimal(0))
    return _dec(item.get("area"))


def rooms_count(item: dict):
    rooms = item.get("rooms")
    return len(rooms) if rooms else item.get("rooms_count")


def monthly_cost(item: dict) -> Decimal | None:
    a, r = total_area(item), _dec(item.get("rate"))
    return None if a is None or r is None else a * r


def fact_rate(item: dict) -> Decimal | None:
    a, f = total_area(item), _dec(item.get("rent_fact"))
    if a is None or f is None or a == 0:
        return None
    return (f / a).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def compute_status(item: dict) -> str:
    """Статус (= цвет) из данных литеры. Главное правило — арендатор."""
    st = item.get("status")
    if st in STICKY or item.get("kind") == "tech":
        return st or "tech"
    if has_tenant(item):
        return "busy"
    ready = str(item.get("ready") or "").strip().lower()
    if ready.startswith("не") or ready.startswith("нет"):
        return "notready"
    return "free"


def badge_text(item: dict, status: str) -> str:
    # Свой текст бейджа держится, только пока статус не поменялся
    if item.get("badge") and item.get("status") == status:
        return item["badge"]
    return STATUS_BADGE[status]


def normalize(data: list[dict]) -> list[dict]:
    """Приводит сохранённые status/badge к вычисленным (цвет = данные)."""
    for it in data:
        st = compute_status(it)
        if it.get("status") != st:
            it["status"] = st
            it["badge"] = STATUS_BADGE[st]
    return data


# --------------------------------------------------------------------------- #
#  Рендер
# --------------------------------------------------------------------------- #
def _e(s) -> str:
    return _html.escape(str(s), quote=False)


def _row(f: str, label: str, value) -> str:
    return f'<div class="row" data-f="{f}"><span>{label}</span><span>{_e(value)}</span></div>'


def _rate_txt(item) -> str:
    r = _dec(item.get("rate"))
    return f"{_num(r)} ₽/м²" if r is not None else str(item.get("rate"))


def render_info(item: dict, floor: int) -> str:
    lit = item["id"]
    st = compute_status(item)
    cls, pref = ("a1", "i") if floor == 1 else ("a2", "j")
    is_tech = item.get("kind") == "tech"
    title = "Техническое помещение" if is_tech else f"Литера {_e(lit)}" + (" · 2 этаж" if floor == 2 else "")
    out = [f'<div class="info {cls}" id="{pref}-{lit}" data-lit="{lit}" data-status="{st}">',
           f"<h2>{title}</h2>",
           f'<span class="badge" style="background:{STATUS_COLOR[st]}">{_e(badge_text(item, st))}</span>']
    described = any(item.get(k) not in (None, "", []) for k in DESCR_FIELDS)
    hint = item.get("hint") or ("Помещение без литеры." if is_tech else None)
    if hint and not described:
        out.append(f'<p class="hint">{_e(hint)}</p>')
        if has_tenant(item):
            out.append(_row("tenant", "Арендатор", item["tenant"]))
        out.append("</div>")
        return "".join(out)

    rc, area = rooms_count(item), total_area(item)
    if rc is not None:
        out.append(_row("rooms_count", "Помещений", rc))
    if area is not None:
        out.append(_row("area", "Общая площадь", f"{_num(area)} м²"))
    rooms = item.get("rooms") or []
    if rooms:
        lis = []
        for n, r in enumerate(rooms, 1):
            extra = []
            if r.get("note"):
                extra.append(r["note"])
            rf = r.get("floor")
            if rf and rf != floor:
                extra.append("2 этаж" if rf == 2 else "первый этаж")
            name = f"Помещение {n}" + (f" ({'; '.join(extra)})" if extra else "")
            lis.append(f"<li><span>{_e(name)}</span><span>{_num(_dec(r.get('area')) or Decimal(0))} м²</span></li>")
        out.append('<ul class="rooms">' + "".join(lis) + "</ul>")
    for f in ("state", "comm", "doors", "purpose", "look"):
        if item.get(f) not in (None, ""):
            out.append(_row(f, ROW_LABEL[f], item[f]))
    tenant = has_tenant(item)
    if item.get("rate") not in (None, ""):
        out.append(_row("rate", "Ставка", _rate_txt(item)))
    if tenant:
        fr = fact_rate(item)
        out.append(_row("fact_rate", "Фактическая ставка", f"{_num(fr)} ₽/м²" if fr is not None else "—"))
    cost = monthly_cost(item)
    if cost is not None:
        out.append(_row("cost", "Стоимость в месяц", f"{_num(cost, 0)} ₽"))
    if tenant:
        rf = _dec(item.get("rent_fact"))
        out.append(_row("rent_fact", "Фактическая рента", f"{_num(rf, 0)} ₽" if rf is not None else "—"))
    for f in ("ready", "tenant"):
        if item.get(f) not in (None, ""):
            out.append(_row(f, ROW_LABEL[f], item[f]))
    out.append("</div>")
    return "".join(out)


def render_blocks(data: list[dict]) -> str:
    parts = [render_info(it, 2) for it in data if 2 in it.get("floors", [1])]
    parts += [render_info(it, 1) for it in data if 1 in it.get("floors", [1])]
    return "".join(parts)


def dump_json(data: list[dict]) -> str:
    s = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return s.replace("</", "<\\/")


def load(html: str) -> list[dict]:
    m = DATA_RE.search(html)
    if not m:
        raise ValueError("В HTML нет блока <script id=\"karta-data\">")
    return json.loads(m.group(2))


def apply(html: str, data: list[dict]) -> str:
    """Пересобирает страницу из данных (детерминированно)."""
    by_key = {}
    for it in data:
        for fl in it.get("floors", [1]):
            by_key[it["id"] if fl == 1 else f"2{it['id']}"] = it
    by_id = {it["id"]: it for it in data}

    def poly(m):
        it = by_key.get(m.group(2))
        return m.group(0) if it is None else f"{m.group(1)}c-{compute_status(it)}{m.group(3)}"

    def area(m):
        it = by_id.get(m.group(2))
        a = total_area(it) if it else None
        return m.group(0) if a is None else f"{m.group(1)}{_num(a)} м²{m.group(3)}"

    html = POLY_RE.sub(poly, html)
    html = AREA_RE.sub(area, html)
    html = INFO_RE.sub(lambda m: m.group(1) + render_blocks(data) + m.group(3), html, count=1)
    html = DATA_RE.sub(lambda m: m.group(1) + dump_json(data) + m.group(3), html, count=1)
    return html


# --------------------------------------------------------------------------- #
#  Изменение данных (единый вход для CLI и бота)
# --------------------------------------------------------------------------- #
def _parse_rooms(s: str) -> list[dict]:
    rooms = []
    for part in [p.strip() for p in s.split(";") if p.strip()]:
        fl = None
        if "@" in part:
            part, f = part.split("@", 1)
            fl = int(f)
        note = None
        if ":" in part:
            part, note = part.split(":", 1)
        r = {"area": _json_num(_dec(part))}
        if note:
            r["note"] = note.strip()
        if fl:
            r["floor"] = fl
        rooms.append(r)
    return rooms


def update(data: list[dict], lit: str, **fields) -> dict:
    """Меняет поля литеры и пересчитывает статус. Возвращает литеру."""
    item = next((x for x in data if x["id"] == lit), None)
    if item is None:
        raise KeyError(f"Литера {lit} не найдена")
    explicit = None
    for k, v in fields.items():
        if isinstance(v, str):
            v = v.strip()
        if k == "status":
            if v not in (*STATUS_COLOR, "auto"):
                raise ValueError(f"status: {'|'.join(STATUS_COLOR)}|auto")
            explicit = v
            continue
        if v in (None, ""):
            item.pop(k, None)
            continue
        if k == "rooms":
            item["rooms"] = _parse_rooms(v) if isinstance(v, str) else v
            item.pop("area", None)
            item.pop("rooms_count", None)
        elif k in ("rate", "rent_fact", "area"):
            d = _dec(v)
            item[k] = _json_num(d) if d is not None else v
        elif k == "rooms_count":
            item[k] = int(v)
        elif k == "badge":
            item[k] = v
        elif k in TEXT_FIELDS:
            item[k] = v
        else:
            raise KeyError(f"Неизвестное поле: {k}")
    if not has_tenant(item):
        item.pop("rent_fact", None)
    if any(item.get(k) not in (None, "", []) for k in DESCR_FIELDS):
        item.pop("hint", None)
    old = item.get("status")
    if explicit and explicit != "auto":
        item["status"] = explicit
    else:
        if explicit == "auto":
            item["status"] = None
        item["status"] = compute_status(item)
    if item["status"] != old and "badge" not in fields:
        item["badge"] = STATUS_BADGE[item["status"]]
    return item


def check(data: list[dict]) -> list[str]:
    """Список расхождений «сохранённый статус ≠ вычисленный из данных»."""
    return [f"{it['id']}: status={it.get('status')} ожидается {compute_status(it)}"
            for it in data if it.get("status") != compute_status(it)]


def summary(item: dict) -> str:
    st = compute_status(item)
    a, c, fr = total_area(item), monthly_cost(item), fact_rate(item)
    lines = [f"Литера {item['id']} — {badge_text(item, st)} ({st})"]
    if a is not None:
        lines.append(f"Площадь: {_num(a)} м²")
    for k in ("state", "comm", "look"):
        if item.get(k):
            lines.append(f"{ROW_LABEL[k]}: {item[k]}")
    if item.get("rate") is not None:
        lines.append(f"Ставка: {_rate_txt(item)}")
    if c is not None:
        lines.append(f"Стоимость в месяц: {_num(c, 0)} ₽")
    if has_tenant(item):
        rf = _dec(item.get("rent_fact"))
        lines.append(f"Фактическая рента: {_num(rf, 0) + ' ₽' if rf is not None else '—'}")
        lines.append(f"Фактическая ставка: {_num(fr) + ' ₽/м²' if fr is not None else '—'}")
    for k in ("ready", "tenant"):
        if item.get(k):
            lines.append(f"{ROW_LABEL[k]}: {item[k]}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _write(p, s):
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd, path, rest = argv[0], argv[1], argv[2:]
    html = _read(path)
    data = load(html)
    if cmd == "show":
        for it in data:
            if not rest or it["id"] == rest[0]:
                print(summary(it), end="\n\n")
    elif cmd == "set":
        lit, kv = rest[0], {}
        for a in rest[1:]:
            k, _, v = a.partition("=")
            kv[k] = v
        it = update(data, lit, **kv)
        _write(path, apply(html, data))
        print(summary(it))
    elif cmd == "render":
        _write(path, apply(html, data))
    elif cmd == "check":
        bad = check(data)
        print("\n".join(bad) if bad else "OK: цвет всех литер соответствует данным")
        return 1 if bad else 0
    elif cmd == "export":
        _write(rest[0], json.dumps(data, ensure_ascii=False, indent=1))
    elif cmd == "import":
        _write(path, apply(html, json.loads(_read(rest[0]))))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
