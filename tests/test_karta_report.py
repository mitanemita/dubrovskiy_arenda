"""Отчёт «🗺 Карта базы»: данные из БД по литере, одно сообщение в чате."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot import handlers_admin as ha
from app.db.base import Base
from app.db.enums import OrgType, TaxMode
from app.db.models import Landlord
from app.services import directory_service, karta_service


def _poly(html: str, key: str) -> str:
    return re.search(rf'data-k="{key}"><polygon class="(c-[a-z]+)"', html).group(1)


async def _lease(session, lid, *, litera, label, rent, tenant="ООО Ромашка", inn="7100000001"):
    p = await directory_service.create_premises(session, landlord_id=lid, label=label, litera=litera)
    t = await directory_service.create_tenant(session, landlord_id=lid, name=tenant, type=OrgType.ooo, inn=inn)
    await session.flush()
    await directory_service.update_premises_field(session, p.id, "site", "Краснодар")
    await directory_service.create_lease(
        session, tenant_id=t.id, premises_id=p.id, contract_no=f"{litera}-1",
        contract_date=date(2026, 11, 1), rent_amount=Decimal(rent),
    )
    await session.flush()
    return p


@pytest_asyncio.fixture
async def landlord(session):
    lord = Landlord(name="ИП", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn)
    session.add(lord)
    await session.flush()
    return lord


async def test_unassigned_map_does_not_show_template_tenants(session, landlord):
    html, summary = await karta_service.render_html(session, landlord.id)
    assert _poly(html.decode(), "Д") == "c-unknown"
    assert "Жернев" not in html.decode()
    assert summary["counts"]["unknown"] > 0
    assert summary["issues"] == []


async def test_technical_areas_need_no_database_entries(session, landlord):
    html, summary = await karta_service.render_html(session, landlord.id)
    text = html.decode()
    for n in range(1, 10):
        key = f"T{n}" if n < 7 else f"2T{n}"
        assert _poly(text, key) == "c-tech"
        prefix = "i" if n < 7 else "j"
        assert re.search(rf'<div class="info a[12]" id="{prefix}-T{n}"[^>]*>\s*<h2>Техническое помещение</h2>', text)
    assert _poly(text, "Е8") == "c-tech"
    assert summary["counts"]["tech"] >= 10


async def test_real_cyrillic_t1_does_not_match_technical_area(session, landlord):
    await _lease(session, landlord.id, litera="Т1", label="Настоящая литера", rent="10000")
    html, summary = await karta_service.render_html(session, landlord.id)
    assert _poly(html.decode(), "T1") == "c-tech"
    assert summary["not_on_plan"] == ["Т1"]


async def test_lease_by_litera_colors_map(session, landlord):
    # Д в шаблоне свободна; договор на помещение с литерой «д » (регистр/пробел не важны)
    await _lease(session, landlord.id, litera="д ", label="Склад", rent="100000")
    # литера другой базы — на этом плане нет
    await _lease(session, landlord.id, litera="Б-17", label="Ангар", rent="5000", tenant="ИП Петров", inn="710000000001")
    html, summary = await karta_service.render_html(session, landlord.id, now=datetime(2026, 11, 13, 10, 0))
    html = html.decode()
    assert _poly(html, "Д") == "c-busy" and _poly(html, "2Д") == "c-busy"
    assert "ООО Ромашка" in html and "100000 ₽" in html
    assert "498,3 ₽/м²" not in html
    assert summary["not_on_plan"] == ["Б-17"]
    cap = karta_service.caption(summary)
    assert "13.11.2026" in cap and "Б-17" in cap and len(cap) < 1024


async def test_latin_lookalike_litera_matches(session, landlord):
    await _lease(session, landlord.id, litera="K1", label="Холодильник", rent="200000")  # латинская K
    html, summary = await karta_service.render_html(session, landlord.id)
    assert "200000 ₽" in html.decode() and summary["not_on_plan"] == []


@pytest.mark.parametrize("litera", ["Г3", "г 3", "ГЗ"])
async def test_g3_litera_matches_map_and_legacy_spelling(session, landlord, litera):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="Помещение", litera=litera)
    await session.flush()
    await directory_service.update_premises_field(session, p.id, "site", "Краснодар")
    p.readiness = "ready"
    p.area = Decimal("88.6")
    await session.flush()
    html, summary = await karta_service.render_html(session, landlord.id)
    text = html.decode()
    assert _poly(text, "Г3") == "c-free"
    assert re.search(r'data-a="Г3"[^>]*>88,6 м²</text>', text)
    assert summary["not_on_plan"] == []


def test_map_litera_identifiers_use_cyrillic_letter_and_optional_number():
    _, items = karta_service._template()
    for item in items:
        identifier = item["id"]
        if re.fullmatch(r"T[1-9]\d*", identifier):
            continue  # служебные технические помещения
        assert re.fullmatch(r"[А-ЯЁ]\d*", identifier), identifier
    assert karta_service._key("З") == "З"
    assert karta_service._key("ГЗ") == karta_service._key("Г3")


# --- Хендлеры: одно сообщение-панель ----------------------------------------
@pytest_asyncio.fixture
async def bot_env(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with f() as s:
        s.add(Landlord(name="ИП", type=OrgType.ip, inn="710000000000", tax_mode=TaxMode.ausn))
        await s.commit()
    monkeypatch.setattr(ha, "async_session_factory", f)
    yield
    await engine.dispose()


def _panel_msg(mid: int):
    chat = SimpleNamespace(id=1)
    doc = SimpleNamespace(message_id=mid + 1, chat=chat)
    return SimpleNamespace(
        message_id=mid, chat=chat,
        edit_text=AsyncMock(), delete=AsyncMock(), edit_media=AsyncMock(),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=mid + 2, chat=chat)),
        answer_document=AsyncMock(return_value=doc),
    )


async def test_map_button_replaces_panel_with_document(bot_env):
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))
    msg = _panel_msg(10)
    cb = SimpleNamespace(data="menu:map", message=msg, from_user=SimpleNamespace(id=1), answer=AsyncMock())
    await ha.base_map(cb, state)
    msg.answer_document.assert_awaited_once()
    file = msg.answer_document.await_args.args[0]
    assert file.filename.startswith("Karta-") and file.filename.endswith(".html")
    msg.delete.assert_awaited_once()            # прежняя панель удалена
    assert ha._PANELS[1] == 11                  # панель — это документ

    # «Обновить» — тот же документ, файл заменяется на месте
    doc = _panel_msg(11)
    cb2 = SimpleNamespace(data="map:refresh", message=doc, from_user=SimpleNamespace(id=1), answer=AsyncMock())
    await ha.base_map_refresh(cb2, state)
    doc.edit_media.assert_awaited_once()
    doc.answer_document.assert_not_awaited()


async def test_back_from_document_deletes_it(bot_env):
    from aiogram.exceptions import TelegramBadRequest
    doc = _panel_msg(20)
    doc.edit_text.side_effect = TelegramBadRequest(method=None, message="there is no text in the message to edit")
    await ha.edit_or_send(doc, "📊 Сводка")
    doc.answer.assert_awaited_once()
    doc.delete.assert_awaited_once()
    assert ha._PANELS[1] == 22


async def test_map_uses_only_explicit_krasnodar_bindings(session, landlord):
    from app.db.models import Lease, Premises, PropertySite, Tenant
    from sqlalchemy import select

    await _lease(session, landlord.id, litera="Д", label="Краснодар", rent="100000", tenant="Арендатор Краснодара")
    other = await directory_service.create_premises(session, landlord_id=landlord.id, label="Другой город", litera="Д")
    unassigned = await directory_service.create_premises(session, landlord_id=landlord.id, label="Без города", litera="Д")
    await session.flush()
    await directory_service.update_premises_field(session, other.id, "site", "Другой город")
    for i, p in enumerate((other, unassigned), start=1):
        t = await directory_service.create_tenant(session, landlord_id=landlord.id, name=f"Не Краснодар {i}",
                                                   type=OrgType.ooo, inn=f"710000000{i}")
        await session.flush()
        await directory_service.create_lease(session, tenant_id=t.id, premises_id=p.id,
            contract_no=f"X{i}", contract_date=date(2026, 11, 1), rent_amount=Decimal("900000"))
    await session.flush()
    html, summary = await karta_service.render_html(session, landlord.id)
    text = html.decode()
    assert _poly(text, "Д") == "c-busy"
    assert "Арендатор Краснодара" in text
    assert "Не Краснодар" not in text
    assert "900000 ₽" not in text
    assert summary["rent_total"] == Decimal("100000")
    # Никакие записи арендаторов и договоров при формировании карты не меняются.
    assert len((await session.execute(select(Tenant))).scalars().all()) == 3
    assert len((await session.execute(select(Lease))).scalars().all()) == 3
    assert len((await session.execute(select(Premises))).scalars().all()) == 3
    assert len((await session.execute(select(PropertySite))).scalars().all()) == 2


async def test_site_assignment_is_case_insensitive_and_reversible(session, landlord):
    from app.db.models import PropertySite
    from sqlalchemy import select

    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="А", litera="Д")
    q = await directory_service.create_premises(session, landlord_id=landlord.id, label="Б", litera="Е6")
    await session.flush()
    await directory_service.update_premises_field(session, p.id, "site", "  Краснодар ")
    await directory_service.update_premises_field(session, q.id, "site", "краснодар")
    assert p.site_id == q.site_id
    assert len((await session.execute(select(PropertySite))).scalars().all()) == 1
    await directory_service.update_premises_field(session, p.id, "site", "")
    assert p.site_id is None


async def test_map_is_rebuilt_when_location_changes(session, landlord):
    from app.db.models import Premises
    from sqlalchemy import select

    await _lease(session, landlord.id, litera="Д", label="Склад", rent="100000", tenant="Текущий")
    p = (await session.execute(select(Premises))).scalar_one()
    before, _ = await karta_service.render_html(session, landlord.id)
    assert _poly(before.decode(), "Д") == "c-busy"
    await directory_service.update_premises_field(session, p.id, "site", "Другой город")
    await session.flush()
    after, summary = await karta_service.render_html(session, landlord.id)
    assert _poly(after.decode(), "Д") == "c-unknown"
    assert "Текущий" not in after.decode()
    assert summary["rent_total"] == 0


@pytest.mark.parametrize("readiness,expected", [
    ("ready", "c-free"),
    ("notready", "c-notready"),
    ("closed", "c-closed"),
    ("tech", "c-tech"),
    ("build", "c-build"),
    (None, "c-free"),
])
async def test_map_readiness_colors_come_from_premises(session, landlord, readiness, expected):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="Склад", litera="Д")
    await session.flush()
    await directory_service.update_premises_field(session, p.id, "site", "Краснодар")
    p.readiness = readiness
    await session.flush()
    html, summary = await karta_service.render_html(session, landlord.id)
    text = html.decode()
    assert _poly(text, "Д") == expected
    assert _poly(text, "2Д") == expected
    assert "Нет данных</span>" in text
    assert summary["issues"] == []


async def test_new_premises_is_free_by_default_on_map(session, landlord):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="Г3", litera="Г3")
    await session.flush()
    assert p.readiness == "ready" and not p.is_occupied
    await directory_service.update_premises_field(session, p.id, "site", "Краснодар")
    html, _ = await karta_service.render_html(session, landlord.id)
    assert _poly(html.decode(), "Г3") == "c-free"


async def test_l_plan_and_room_data_in_rendered_map(session, landlord):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="Л", litera="Л")
    await session.flush()
    await directory_service.update_premises_field(session, p.id, "site", "Краснодар")
    p.area = Decimal("154.10")
    html, _ = await karta_service.render_html(session, landlord.id)
    text = html.decode()
    card = re.search(r'<div class="info a1" id="i-Л".*?</div></div>', text).group(0)
    assert 'id="p-Л"' in text and 'body:has(#i-Л:target) #p-Л{display:block}' in text
    assert len(re.findall(r'<li><span>Помещение \d+', card)) == 7
    assert '29,52 м²' in card and '61,4 м²' in card and 'приблизительно' not in card
    assert 'Нормальное' in card and 'Вода, туалет, свет' in card and 'Стандартные' in card
    assert 'Производство, склад' in card and 'Плохой' in card


async def test_e4_plan_and_room_data_survive_database_render(session, landlord):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="Е4", litera="Е4")
    await session.flush()
    await directory_service.update_premises_field(session, p.id, "site", "Краснодар")
    p.area = Decimal("71.32")
    html, _ = await karta_service.render_html(session, landlord.id)
    text = html.decode()
    card = re.search(r'<div class="info a1" id="i-Е4".*?</div></div>', text).group(0)
    assert 'id="p-Е4"' in text and 'body:has(#i-Е4:target) #p-Е4{display:block}' in text
    assert len(re.findall(r'<li><span>Помещение \d+', card)) == 6
    assert '39,2 м²' in card and '11,58 м²' in card and '3,54 м²' in card
    assert '<span>Площадь</span><span>71,32 м²</span>' in card


async def test_k3_plan_and_details_survive_database_render(session, landlord):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="К3", litera="К3")
    await session.flush()
    await directory_service.update_premises_field(session, p.id, "site", "Краснодар")
    p.area = Decimal("158.9")
    html, _ = await karta_service.render_html(session, landlord.id)
    text = html.decode()
    card = re.search(r'<div class="info a1" id="i-К3".*?</div></div>', text).group(0)
    assert 'id="p-К3"' in text and 'body:has(#i-К3:target) #p-К3{display:block}' in text
    assert len(re.findall(r'<li><span>Помещение \d+', card)) == 5
    assert '57 м²' in card and '36 м²' in card and '16,3 м²' in card
    assert 'Среднее' in card and 'Средний' in card and 'Свет' in card
    assert 'Распашные' in card and 'Производство или склад' in card
    assert '<span>Площадь</span><span>158,9 м²</span>' in card


async def test_all_rentable_litera_labels_follow_database_area(session, landlord):
    _, items = karta_service._template()
    rentable = [item for item in items if item.get("kind") != "tech" and item["id"] != "Е8"]
    for i, item in enumerate(rentable):
        p = await directory_service.create_premises(session, landlord_id=landlord.id,
                                                    label=item["id"], litera=item["id"])
        p.area = Decimal("100.25") + i
        await session.flush()
        await directory_service.update_premises_field(session, p.id, "site", "Краснодар")
    await session.flush()
    html, _ = await karta_service.render_html(session, landlord.id)
    text = html.decode()
    for i, item in enumerate(rentable):
        label = re.escape(item["id"])
        values = re.findall(rf'<text[^>]*data-a="{label}"[^>]*>([^<]*)</text>', text)
        assert len(values) == len(item.get("floors", [1])), item["id"]
        assert all(v == f"{100 + i},25 м²" for v in values), item["id"]


async def test_empty_area_label_clears_after_database_area_removed(session, landlord):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="Л", litera="Л")
    await session.flush()
    await directory_service.update_premises_field(session, p.id, "site", "Краснодар")
    p.area = Decimal("125.75")
    await session.flush()
    before, _ = await karta_service.render_html(session, landlord.id)
    assert re.search(r'<text[^>]*data-a="Л"[^>]*>125,75 м²</text>', before.decode())
    p.area = None
    await session.flush()
    after, _ = await karta_service.render_html(session, landlord.id)
    assert re.search(r'<text[^>]*data-a="Л"[^>]*></text>', after.decode())


async def test_i7_plan_and_room_data_survive_database_render(session, landlord):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="И7", litera="И7")
    await session.flush()
    await directory_service.update_premises_field(session, p.id, "site", "Краснодар")
    p.area = Decimal("145.5")
    html, _ = await karta_service.render_html(session, landlord.id)
    text = html.decode()
    card = re.search(r'<div class="info a1" id="i-И7".*?</div></div>', text).group(0)
    assert 'id="p-И7"' in text and 'body:has(#i-И7:target) #p-И7{display:block}' in text
    assert len(re.findall(r'<li><span>Помещение \d+', card)) == 2
    assert '63 м²' in card and '82,5 м²' in card
    assert '<span>Площадь</span><span>145,5 м²</span>' in card


@pytest.mark.parametrize("litera,area,count,first,second", [
    ("П", "130.30", 3, [1], [2, 3]),
    ("Д", "200.70", 11, list(range(1, 10)), [10, 11]),
])
async def test_two_floor_rooms_keep_total_count_and_show_selected_floor(session, landlord, litera, area, count, first, second):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label=litera, litera=litera)
    await session.flush()
    await directory_service.update_premises_field(session, p.id, "site", "Краснодар")
    p.area = Decimal(area)
    await session.flush()
    html, _ = await karta_service.render_html(session, landlord.id)
    text = html.decode()
    for prefix, numbers in (("i", first), ("j", second)):
        card = re.search(rf'<div class="info a[12]" id="{prefix}-{litera}".*?</div></div>', text).group(0)
        assert f'<span>Помещений (2 этажа)</span><span>{count}</span>' in card
        assert f'<span>Площадь</span><span>{area.rstrip("0").replace(".", ",")} м²</span>' in card
        assert [int(n) for n in re.findall(r'<li><span>Помещение (\d+)', card)] == numbers


async def test_e8_remains_technical_even_if_database_has_old_data(session, landlord):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="Е8", litera="Е8")
    await session.flush()
    await directory_service.update_premises_field(session, p.id, "site", "Краснодар")
    p.readiness = "tech"
    await session.flush()
    html, _ = await karta_service.render_html(session, landlord.id)
    assert _poly(html.decode(), "Е8") == "c-tech"

    p.readiness = "ready"
    await session.flush()
    html, _ = await karta_service.render_html(session, landlord.id)
    assert _poly(html.decode(), "Е8") == "c-tech"

    p.readiness = "tech"
    tenant = await directory_service.create_tenant(
        session, landlord_id=landlord.id, name="Арендатор Е8", type=OrgType.ooo, inn="7100000001"
    )
    await session.flush()
    await directory_service.create_lease(
        session, tenant_id=tenant.id, premises_id=p.id, contract_no="Е8-1",
        contract_date=date(2026, 11, 1), rent_amount=Decimal("10000"),
    )
    await session.flush()
    html, summary = await karta_service.render_html(session, landlord.id)
    assert _poly(html.decode(), "Е8") == "c-tech"
    assert "Арендатор Е8" not in html.decode()
    assert summary["rent_total"] == 0


async def test_map_uses_database_area_rent_and_rate(session, landlord):
    p = await _lease(session, landlord.id, litera="Д", label="Склад", rent="60000.50", tenant="Арендатор")
    p.area = Decimal("200")
    p.readiness = "notready"
    await directory_service.update_premises_field(session, p.id, "asking_rate", "300,25")
    await session.flush()
    html, summary = await karta_service.render_html(session, landlord.id)
    text = html.decode()
    card = re.search(r'<div class="info a1" id="i-Д".*?</div></div>', text).group(0)
    assert re.search(r'data-f="db_area"><span>Площадь</span><span>200 м²</span>', card)
    assert 'Площадь по плану' not in card and 'Площадь по учёту' not in card
    assert re.search(r'<text[^>]*data-a="Д"[^>]*>200 м²</text>', text)
    assert _poly(text, "Д") == "c-busy"  # действующий арендатор определяет занятость
    assert "Арендатор" in text and "60000,5 ₽" in text
    assert re.search(r'data-f="rate"><span>Предполагаемая ставка</span><span>300,25 ₽/м²</span>', text)
    assert re.search(r'data-f="fact_rate"><span>Фактическая ставка</span><span>300 ₽/м²</span>', text)
    assert "Не готово к сдаче" in text
    assert summary["rent_total"] == Decimal("60000.50")
    assert "60 000,50 ₽" in karta_service.caption(summary)


async def test_map_area_missing_does_not_show_old_plan_total(session, landlord):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="Д", litera="Д")
    await session.flush()
    await directory_service.update_premises_field(session, p.id, "site", "Краснодар")
    html, _ = await karta_service.render_html(session, landlord.id)
    text = html.decode()
    assert re.search(r'data-f="db_area"><span>Площадь</span><span>не указана</span>', text)
    assert re.search(r'<text[^>]*data-a="Д"[^>]*></text>', text)


async def test_m_shows_database_area_without_plan_area(session, landlord):
    p = await directory_service.create_premises(session, landlord_id=landlord.id, label="М", litera="М")
    await session.flush()
    await directory_service.update_premises_field(session, p.id, "site", "Краснодар")
    p.area = Decimal("123.45")
    await session.flush()
    html, _ = await karta_service.render_html(session, landlord.id)
    text = html.decode()
    card = re.search(r'<div class="info a1" id="i-М".*?</div></div>', text).group(0)
    assert re.search(r'data-f="db_area"><span>Площадь</span><span>123,45 м²</span>', card)
    assert 'Площадь по плану' not in card and 'Площадь по учёту' not in card


async def test_missing_or_ambiguous_area_does_not_invent_fact_rate(session, landlord):
    from app.db.models import Tenant

    p = await _lease(session, landlord.id, litera="Д", label="Склад", rent="100000", tenant="Первый")
    html, _ = await karta_service.render_html(session, landlord.id)
    assert "не рассчитана" in html.decode()
    p.area = Decimal("200")
    t = await directory_service.create_tenant(session, landlord_id=landlord.id, name="Второй",
                                               type=OrgType.ooo, inn="7100000002")
    await session.flush()
    await directory_service.create_lease(session, tenant_id=t.id, premises_id=p.id,
        contract_no="Второй", contract_date=date(2026, 11, 1), rent_amount=Decimal("20000"))
    await session.flush()
    html, _ = await karta_service.render_html(session, landlord.id)
    assert "Первый" in html.decode() and "Второй" in html.decode()
    assert "не рассчитана" in html.decode()
