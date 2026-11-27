"""Карта базы: цвет литеры следует за арендатором, сборка детерминирована."""
from __future__ import annotations

import copy
import importlib.util
import re
from pathlib import Path

KARTA_DIR = Path(__file__).resolve().parent.parent / "app" / "karta"
_spec = importlib.util.spec_from_file_location("karta", KARTA_DIR / "karta.py")
karta = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(karta)

HTML = (KARTA_DIR / "Karta-krasnodar.html").read_text(encoding="utf-8")


def _poly(html: str, key: str) -> str:
    return re.search(rf'data-k="{key}"><polygon class="(c-[a-z]+)"', html).group(1)


def _badge(html: str, block_id: str) -> str:
    m = re.search(rf'id="{block_id}" data-lit="[^"]+" data-status="([a-z]+)"><h2>[^<]*</h2>'
                  r'<span class="badge" style="background:([^"]+)">', html)
    return m.group(1), m.group(2)


def test_saved_file_is_consistent():
    data = karta.load(HTML)
    assert karta.check(data) == []
    assert karta.apply(HTML, data) == HTML


def test_p_total_count_and_area_stay_constant_while_rooms_follow_floor():
    data = karta.load(HTML)
    item = next(x for x in data if x["id"] == "П")
    for floor in (1, 2):
        card = karta.render_info(item, floor)
        assert '<span>Помещений (2 этажа)</span><span>3</span>' in card
        assert '<span>Общая площадь</span><span>130,3 м²</span>' in card
        if floor == 1:
            assert 'Помещение 1' in card and 'Помещение 2' not in card and 'Помещение 3' not in card
        else:
            assert 'Помещение 1' not in card and 'Помещение 2' in card and 'Помещение 3' in card
        assert 'Площадь этажа' not in card


def test_zh3_footprint_matches_zh2():
    def points(key):
        return re.search(rf'data-k="{key}"><polygon[^>]*points="([^"]+)"', HTML).group(1)
    assert points("2Ж3") == points("Ж2")


def test_d_total_count_and_area_stay_constant_while_rooms_follow_floor():
    item = next(x for x in karta.load(HTML) if x["id"] == "Д")
    floor1 = karta.render_info(item, 1)
    floor2 = karta.render_info(item, 2)
    for card in (floor1, floor2):
        assert '<span>Помещений (2 этажа)</span><span>11</span>' in card
        assert '<span>Общая площадь</span><span>200,7 м²</span>' in card
        assert 'Площадь этажа' not in card
    assert 'Помещение 9' in floor1 and 'Помещение 10' not in floor1
    assert 'Помещение 9' not in floor2 and 'Помещение 10' in floor2 and 'Помещение 11' in floor2


def test_tenant_switches_color_on_both_floors():
    data = karta.load(HTML)
    karta.update(data, "Д", tenant="Иванов", rent_fact="100000")
    out = karta.apply(HTML, data)
    assert _poly(out, "Д") == "c-busy" and _poly(out, "2Д") == "c-busy"
    assert _badge(out, "i-Д") == ("busy", "#3b6fe0")
    assert _badge(out, "j-Д") == ("busy", "#3b6fe0")
    assert "498,3 ₽/м²" in out  # фактическая ставка = 100000 / 200,7

    karta.update(data, "Д", tenant="нет")
    out = karta.apply(HTML, data)
    assert _poly(out, "Д") == "c-free" and _poly(out, "2Д") == "c-free"
    assert "rent_fact" not in next(x for x in data if x["id"] == "Д")


def test_removing_tenant_respects_readiness():
    data = karta.load(HTML)
    karta.update(data, "К1", tenant="Нет", ready="Не готов")
    assert _poly(karta.apply(HTML, data), "К1") == "c-notready"


def test_sticky_statuses_do_not_follow_tenant():
    data = karta.load(HTML)
    karta.update(data, "З", tenant="Петров")
    assert _poly(karta.apply(HTML, data), "З") == "c-build"
    karta.update(data, "З", status="auto")
    assert _poly(karta.apply(HTML, data), "З") == "c-busy"


def test_render_ignores_stale_stored_status():
    """Даже если в JSON записан устаревший статус — цвет берётся из арендатора."""
    data = copy.deepcopy(karta.load(HTML))
    item = next(x for x in data if x["id"] == "Р2")
    item["tenant"] = "Сидоров"  # статус в данных остался free
    out = karta.apply(HTML, data)
    assert _poly(out, "Р2") == "c-busy"
    assert _badge(out, "i-Р2") == ("busy", "#3b6fe0")


def test_round_trip():
    data = karta.load(HTML)
    karta.update(data, "Е6", tenant="Иванов", rent_fact=20000, rate=650)
    karta.update(data, "Е6", tenant="Нет", rate=600, badge="Свободно, готово к сдаче")
    assert karta.apply(HTML, data) == HTML


def test_color_rule_table():
    """Нет арендатора + готово → зелёный; нет арендатора + не готово → оранжевый."""
    data = karta.load(HTML)
    cases = [
        ({"tenant": "Нет", "ready": "Готов"}, "c-free"),
        ({"tenant": "нет", "ready": "Не готов"}, "c-notready"),
        ({"tenant": "", "ready": "Нет"}, "c-notready"),
        ({"tenant": "Петров", "ready": "Не готов"}, "c-busy"),
    ]
    for fields, expected in cases:
        karta.update(data, "Р2", **fields)
        assert _poly(karta.apply(HTML, data), "Р2") == expected, fields


def test_all_colored_polygons_are_supported_by_renderer():
    """Каждый окрашиваемый контур имеет запись, включая контуры второго этажа."""
    colored = re.findall(r'<polygon\b[^>]*class="c-[a-z]+"[^>]*>', HTML)
    matched = list(karta.POLY_RE.finditer(HTML))
    assert len(matched) == len(colored) == 48
    data = karta.load(HTML)
    keys = {it["id"] if floor == 1 else f"2{it['id']}"
            for it in data for floor in it.get("floors", [1])}
    assert all(match.group(2) in keys for match in matched)
