"""Справочники в боте: помещения, арендаторы, договоры, счётчики.

Мастера создания на инлайн-кнопках. Ожидание текстового сообщения (FSM)
используется только для непредсказуемого ввода: наименования, ИНН, номера,
суммы, даты. Всё остальное (тип арендатора, выбор арендатора/помещения,
пропуск необязательных полей, отмена) — кнопки. Навигация через callback
не перехватывается FSM-состояниями, поэтому переходы работают всегда.

Бизнес-логика и валидация — в app.services.directory_service (покрыто тестами).
"""
from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.handlers_admin import _landlord_id, _parse_amount, _parse_date, edit_or_send
from app.db.base import async_session_factory
from app.db.enums import OrgType
from app.db.models import Lease, Premises, Tenant
from app.bot.keyboards import back_kb, cancel_kb, main_menu_kb
from app.services import directory_service

router = Router()

_ORG_TYPE_LABEL = {OrgType.ip: "ИП", OrgType.ooo: "ООО / юрлицо", OrgType.fiz: "Физлицо"}


# --- Хелперы ---------------------------------------------------------------
def _io_kb(skip_cb: str | None = None) -> InlineKeyboardMarkup:
    """Клавиатура под запросом ввода: необязательный «Пропустить» + «Отмена»."""
    rows: list[list[InlineKeyboardButton]] = []
    if skip_cb:
        rows.append([InlineKeyboardButton(text="⏭ Пропустить", callback_data=skip_cb)])
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _dir_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏢 Помещения", callback_data="dir:premises"),
         InlineKeyboardButton(text="👤 Арендаторы", callback_data="dir:tenants")],
        [InlineKeyboardButton(text="📄 Договоры", callback_data="dir:leases"),
         InlineKeyboardButton(text="🔌 Счётчики", callback_data="dir:meters")],
        [InlineKeyboardButton(text="🏦 Реквизиты арендодателя", callback_data="dir:landlord")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="nav:home")],
    ])


def _list_kb(add_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить", callback_data=add_cb)],
        [InlineKeyboardButton(text="◀️ Справочники", callback_data="menu:directory")],
    ])


# --- FSM -------------------------------------------------------------------
class PremisesFSM(StatesGroup):
    label = State()
    address = State()
    area = State()


class EditFSM(StatesGroup):
    """Универсальный ввод нового значения при редактировании (kind/id/field — в state)."""
    value = State()


class TenantFSM(StatesGroup):
    name = State()
    org_type = State()
    inn = State()
    kpp = State()
    address = State()
    email = State()
    phone = State()


class LandlordFSM(StatesGroup):
    value = State()


class LeaseFSM(StatesGroup):
    tenant = State()
    premises = State()
    contract_no = State()
    contract_date = State()
    rent = State()
    payment_day = State()


class MeterFSM(StatesGroup):
    premises = State()
    serial = State()
    label = State()
    coefficient = State()


# --- Меню справочников -----------------------------------------------------
@router.callback_query(F.data == "menu:directory")
async def directory_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_send(callback.message, "🗂 Справочники — что открыть?", reply_markup=_dir_menu_kb())
    await callback.answer()


# --- Помещения -------------------------------------------------------------
_PREM_PAGE = 10
_PREM_TITLES = {"free": "🟢 Свободные", "occ": "🔴 Занятые", "all": "📋 Все"}


def _status_label(is_occupied: bool) -> str:
    return "🔴 занято" if is_occupied else "🟢 свободно"


def _prem_submenu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Свободные", callback_data="premlist:free:0"),
         InlineKeyboardButton(text="🔴 Занятые", callback_data="premlist:occ:0")],
        [InlineKeyboardButton(text="📋 Все", callback_data="premlist:all:0")],
        [InlineKeyboardButton(text="➕ Добавить", callback_data="dadd:premises")],
        [InlineKeyboardButton(text="◀️ Справочники", callback_data="menu:directory")],
    ])


@router.callback_query(F.data == "dir:premises")
async def premises_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_send(callback.message, "<b>🏢 Помещения</b> — что показать?", reply_markup=_prem_submenu_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("premlist:"))
async def premises_list(callback: CallbackQuery, state: FSMContext) -> None:
    """Постраничный список помещений с фильтром (все/свободные/занятые) и занятостью."""
    await state.clear()
    _, flt, raw_off = callback.data.split(":")
    offset = int(raw_off)
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        items = await directory_service.list_premises(session, lid) if lid else []
        occ = await directory_service.active_occupants(session, lid) if lid else {}

    if flt == "free":
        items = [p for p in items if not p.is_occupied]
    elif flt == "occ":
        items = [p for p in items if p.is_occupied]

    total = len(items)
    offset = max(0, min(offset, (total - 1) // _PREM_PAGE * _PREM_PAGE if total else 0))
    page = items[offset:offset + _PREM_PAGE]

    lines = [f"<b>🏢 Помещения — {_PREM_TITLES.get(flt, '')}</b>"]
    if not items:
        lines.append("— пусто")
    else:
        pages_total = (total + _PREM_PAGE - 1) // _PREM_PAGE
        lines.append(f"Стр. {offset // _PREM_PAGE + 1}/{pages_total}. Нажмите № для карточки:")
        for idx, p in enumerate(page, start=offset + 1):
            area = f", {p.area} м²" if p.area is not None else ""
            who = ""
            if p.is_occupied and occ.get(p.id):
                who = " · " + ", ".join(occ[p.id])
            lines.append(f"{idx}. {p.label}{area} · {_status_label(p.is_occupied)}{who}")

    num_buttons = [
        InlineKeyboardButton(text=str(offset + idx), callback_data=f"ppick:{p.id}")
        for idx, p in enumerate(page, start=1)
    ]
    rows = [num_buttons[i:i + 5] for i in range(0, len(num_buttons), 5)]
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="◀️ Пред.", callback_data=f"premlist:{flt}:{offset - _PREM_PAGE}"))
    if offset + _PREM_PAGE < total:
        nav.append(InlineKeyboardButton(text="След. ▶️", callback_data=f"premlist:{flt}:{offset + _PREM_PAGE}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="dir:premises")])
    await edit_or_send(callback.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


async def _render_premises_card(message, pid: int) -> bool:
    """Карточка помещения: занятость (авто), правка полей, удаление."""
    async with async_session_factory() as session:
        p = await session.get(Premises, pid)
        occ = await directory_service.active_occupants(session, p.landlord_id) if p else {}
    if p is None:
        return False
    area = f", {p.area} м²" if p.area is not None else ""
    text = f"<b>Помещение</b>\n{p.label}{area}\nСтатус: {_status_label(p.is_occupied)}"
    if p.is_occupied and occ.get(p.id):
        text += "\nЗанимает: " + ", ".join(occ[p.id])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"pedit:{p.id}"),
         InlineKeyboardButton(text="🗑 Удалить", callback_data=f"pdel:{p.id}")],
        [InlineKeyboardButton(text="◀️ К помещениям", callback_data="dir:premises")],
    ])
    await edit_or_send(message, text, reply_markup=kb)
    return True


@router.callback_query(F.data.startswith("ppick:"))
async def premises_card(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    pid = int(callback.data.split(":", 1)[1])
    if not await _render_premises_card(callback.message, pid):
        await callback.answer("Помещение не найдено", show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data.startswith("pedit:"))
async def premises_edit_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    pid = int(callback.data.split(":", 1)[1])
    rows = [[InlineKeyboardButton(text=title, callback_data=f"pfield:{field}:{pid}")]
            for field, title in directory_service.PREMISES_FIELDS.items()]
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"ppick:{pid}")])
    await edit_or_send(callback.message, "Что изменить?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("pfield:"))
async def premises_edit_field(callback: CallbackQuery, state: FSMContext) -> None:
    _, field, pid = callback.data.split(":")
    await state.update_data(edit_kind="premises", edit_id=int(pid), edit_field=field)
    await state.set_state(EditFSM.value)
    await edit_or_send(
        callback.message,
        f"Введите новое значение — {directory_service.PREMISES_FIELDS[field]} (или «-» чтобы очистить):",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pdel:"))
async def premises_delete_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    pid = int(callback.data.split(":", 1)[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"pdelok:{pid}")],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"ppick:{pid}")],
    ])
    await edit_or_send(callback.message, "Удалить помещение?", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("pdelok:"))
async def premises_delete(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    pid = int(callback.data.split(":", 1)[1])
    async with async_session_factory() as session:
        try:
            ok = await directory_service.delete_premises(session, pid)
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            await edit_or_send(callback.message, f"❌ {exc}", reply_markup=_prem_submenu_kb())
            await callback.answer()
            return
    await edit_or_send(callback.message, "🗑 Помещение удалено." if ok else "Не найдено.", reply_markup=_prem_submenu_kb())
    await callback.answer()


@router.callback_query(F.data == "dadd:premises")
async def premises_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(PremisesFSM.label)
    await edit_or_send(callback.message, "Название/№ помещения:", reply_markup=_io_kb())
    await callback.answer()


@router.message(PremisesFSM.label)
async def premises_label(message: Message, state: FSMContext) -> None:
    if not message.text.strip():
        await message.answer("❌ Название не может быть пустым. Повторите:", reply_markup=_io_kb())
        return
    await state.update_data(label=message.text.strip())
    await state.set_state(PremisesFSM.address)
    await message.answer("Адрес помещения (или пропустите):", reply_markup=_io_kb("dsk:prem_addr"))


@router.callback_query(PremisesFSM.address, F.data == "dsk:prem_addr")
async def premises_skip_addr(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(address=None)
    await state.set_state(PremisesFSM.area)
    await edit_or_send(callback.message, "Площадь, м² (или пропустите):", reply_markup=_io_kb("dsk:prem_area"))
    await callback.answer()


@router.message(PremisesFSM.address)
async def premises_address(message: Message, state: FSMContext) -> None:
    await state.update_data(address=message.text.strip())
    await state.set_state(PremisesFSM.area)
    await message.answer("Площадь, м² (или пропустите):", reply_markup=_io_kb("dsk:prem_area"))


@router.callback_query(PremisesFSM.area, F.data == "dsk:prem_area")
async def premises_skip_area(callback: CallbackQuery, state: FSMContext) -> None:
    await _premises_finish(callback.message, callback.from_user.id, state, area=None)
    await callback.answer()


@router.message(PremisesFSM.area)
async def premises_area(message: Message, state: FSMContext) -> None:
    area = _parse_amount(message.text)
    if area is None or area <= 0:
        await message.answer("❌ Введите положительное число или пропустите:", reply_markup=_io_kb("dsk:prem_area"))
        return
    await _premises_finish(message, message.from_user.id, state, area=area)


async def _premises_finish(message: Message, tg_id: int, state: FSMContext, *, area) -> None:
    """Создаёт помещение (по умолчанию свободно — занятость появится при привязке договора)."""
    data = await state.get_data()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, tg_id)
        try:
            p = await directory_service.create_premises(
                session, landlord_id=lid, label=data["label"], address=data.get("address"),
                area=area, is_occupied=False,
            )
            await session.flush()
            label = p.label
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            await state.clear()
            await message.answer(f"❌ {exc}", reply_markup=main_menu_kb())
            return
    await state.clear()
    await message.answer(f"✅ Помещение «{label}» добавлено (🟢 свободно).", reply_markup=_list_kb("dadd:premises"))


# --- Арендаторы ------------------------------------------------------------
@router.callback_query(F.data == "dir:tenants")
async def tenants_list(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        items = await directory_service.list_tenants(session, lid) if lid else []
    lines = ["<b>👤 Арендаторы</b> (нажмите № для карточки):"]
    if not items:
        lines.append("— пусто")
    for i, t in enumerate(items, start=1):
        lines.append(f"{i}. {t.name} · ИНН {t.inn}")
    num_buttons = [InlineKeyboardButton(text=str(i), callback_data=f"tnpick:{t.id}") for i, t in enumerate(items, start=1)]
    rows = [num_buttons[i:i + 5] for i in range(0, len(num_buttons), 5)]
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="dadd:tenants")])
    rows.append([InlineKeyboardButton(text="◀️ Справочники", callback_data="menu:directory")])
    await edit_or_send(callback.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


async def _render_tenant_card(message, tid: int) -> bool:
    async with async_session_factory() as session:
        t = await session.get(Tenant, tid)
    if t is None:
        return False
    lines = [
        f"<b>Арендатор</b>\n{t.name}",
        f"Тип: {_ORG_TYPE_LABEL.get(t.type, t.type.value)}",
        f"ИНН: {t.inn}" + (f" · КПП: {t.kpp}" if t.kpp else ""),
        f"Адрес: {t.address or '—'}",
        f"Email: {t.email or '—'} · Тел: {t.phone or '—'}",
    ]
    rows = [[InlineKeyboardButton(text=title, callback_data=f"tnfield:{field}:{tid}")]
            for field, title in directory_service.TENANT_FIELDS.items()]
    rows.append([InlineKeyboardButton(text="🗑 Удалить арендатора", callback_data=f"tndel:{tid}")])
    rows.append([InlineKeyboardButton(text="◀️ К арендаторам", callback_data="dir:tenants")])
    await edit_or_send(message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    return True


@router.callback_query(F.data.startswith("tnpick:"))
async def tenant_card(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    tid = int(callback.data.split(":", 1)[1])
    if not await _render_tenant_card(callback.message, tid):
        await callback.answer("Арендатор не найден", show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data.startswith("tnfield:"))
async def tenant_edit_field(callback: CallbackQuery, state: FSMContext) -> None:
    _, field, tid = callback.data.split(":")
    await state.update_data(edit_kind="tenant", edit_id=int(tid), edit_field=field)
    await state.set_state(EditFSM.value)
    await edit_or_send(
        callback.message,
        f"Введите новое значение — {directory_service.TENANT_FIELDS[field]} (или «-» чтобы очистить):",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tndel:"))
async def tenant_delete_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    tid = int(callback.data.split(":", 1)[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"tndelok:{tid}")],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"tnpick:{tid}")],
    ])
    await edit_or_send(
        callback.message,
        "Удалить арендатора со всеми его договорами? Помещения освободятся.",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tndelok:"))
async def tenant_delete(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    tid = int(callback.data.split(":", 1)[1])
    async with async_session_factory() as session:
        ok = await directory_service.delete_tenant(session, tid)
        await session.commit()
    await edit_or_send(callback.message, "🗑 Арендатор удалён." if ok else "Не найдено.", reply_markup=_list_kb("dadd:tenants"))
    await callback.answer()


@router.callback_query(F.data == "dadd:tenants")
async def tenant_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(TenantFSM.name)
    await edit_or_send(callback.message, "Наименование арендатора (как в документах):", reply_markup=_io_kb())
    await callback.answer()


@router.message(TenantFSM.name)
async def tenant_name(message: Message, state: FSMContext) -> None:
    if not message.text.strip():
        await message.answer("❌ Наименование не может быть пустым. Повторите:", reply_markup=_io_kb())
        return
    await state.update_data(name=message.text.strip())
    await state.set_state(TenantFSM.org_type)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ИП", callback_data="dt:ip"),
         InlineKeyboardButton(text="ООО", callback_data="dt:ooo"),
         InlineKeyboardButton(text="Физлицо", callback_data="dt:fiz")],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="nav:home")],
    ])
    await message.answer("Тип арендатора:", reply_markup=kb)


@router.callback_query(TenantFSM.org_type, F.data.startswith("dt:"))
async def tenant_type(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(org_type=callback.data.split(":", 1)[1])
    await state.set_state(TenantFSM.inn)
    await edit_or_send(callback.message, "ИНН (10 цифр для юрлица, 12 — для ИП/физлица):", reply_markup=_io_kb())
    await callback.answer()


@router.message(TenantFSM.inn)
async def tenant_inn(message: Message, state: FSMContext) -> None:
    inn = message.text.strip()
    if not inn.isdigit() or len(inn) not in (10, 12):
        await message.answer("❌ ИНН — 10 или 12 цифр. Повторите:", reply_markup=_io_kb())
        return
    await state.update_data(inn=inn)
    await state.set_state(TenantFSM.kpp)
    await message.answer(
        "КПП (9 цифр, обычно у ООО; для ИП/физлица — пропустите):",
        reply_markup=_io_kb("dsk:ten_kpp"),
    )


@router.callback_query(TenantFSM.kpp, F.data == "dsk:ten_kpp")
async def tenant_skip_kpp(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(kpp=None)
    await state.set_state(TenantFSM.address)
    await edit_or_send(callback.message, 
        "Адрес арендатора (нужен для УПД) или пропустите:", reply_markup=_io_kb("dsk:ten_addr")
    )
    await callback.answer()


@router.message(TenantFSM.kpp)
async def tenant_kpp(message: Message, state: FSMContext) -> None:
    kpp = message.text.strip()
    if not kpp.isdigit() or len(kpp) != 9:
        await message.answer("❌ КПП — 9 цифр. Повторите или пропустите:", reply_markup=_io_kb("dsk:ten_kpp"))
        return
    await state.update_data(kpp=kpp)
    await state.set_state(TenantFSM.address)
    await message.answer("Адрес арендатора (нужен для УПД) или пропустите:", reply_markup=_io_kb("dsk:ten_addr"))


@router.callback_query(TenantFSM.address, F.data == "dsk:ten_addr")
async def tenant_skip_addr(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(address=None)
    await state.set_state(TenantFSM.email)
    await edit_or_send(callback.message, 
        "Email арендатора (для отправки документов) или пропустите:", reply_markup=_io_kb("dsk:ten_email")
    )
    await callback.answer()


@router.message(TenantFSM.address)
async def tenant_address(message: Message, state: FSMContext) -> None:
    await state.update_data(address=message.text.strip())
    await state.set_state(TenantFSM.email)
    await message.answer("Email арендатора (для отправки документов) или пропустите:", reply_markup=_io_kb("dsk:ten_email"))


@router.callback_query(TenantFSM.email, F.data == "dsk:ten_email")
async def tenant_skip_email(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(email=None)
    await state.set_state(TenantFSM.phone)
    await edit_or_send(callback.message, "Телефон арендатора или пропустите:", reply_markup=_io_kb("dsk:ten_phone"))
    await callback.answer()


@router.message(TenantFSM.email)
async def tenant_email(message: Message, state: FSMContext) -> None:
    await state.update_data(email=message.text.strip())
    await state.set_state(TenantFSM.phone)
    await message.answer("Телефон арендатора или пропустите:", reply_markup=_io_kb("dsk:ten_phone"))


@router.callback_query(TenantFSM.phone, F.data == "dsk:ten_phone")
async def tenant_skip_phone(callback: CallbackQuery, state: FSMContext) -> None:
    await _tenant_finish(callback.message, callback.from_user.id, state, phone=None)
    await callback.answer()


@router.message(TenantFSM.phone)
async def tenant_phone(message: Message, state: FSMContext) -> None:
    await _tenant_finish(message, message.from_user.id, state, phone=message.text.strip())


async def _tenant_finish(message: Message, tg_id: int, state: FSMContext, *, phone: str | None) -> None:
    data = await state.get_data()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, tg_id)
        try:
            t = await directory_service.create_tenant(
                session,
                landlord_id=lid,
                name=data["name"],
                type=OrgType(data["org_type"]),
                inn=data["inn"],
                kpp=data.get("kpp"),
                address=data.get("address"),
                email=data.get("email"),
                phone=phone,
            )
            await session.flush()
            tid = t.id
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            await state.clear()
            await message.answer(f"❌ {exc}", reply_markup=main_menu_kb())
            return
    await state.clear()
    await message.answer(f"✅ Арендатор #{tid} «{data['name']}» добавлен.", reply_markup=_list_kb("dadd:tenants"))


# --- Договоры --------------------------------------------------------------
@router.callback_query(F.data == "dir:leases")
async def leases_list(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        items = await directory_service.list_leases(session, lid) if lid else []
        tmap = {t.id: t.name for t in (await directory_service.list_tenants(session, lid) if lid else [])}
        pmap = {p.id: p.label for p in (await directory_service.list_premises(session, lid) if lid else [])}
    lines = ["<b>📄 Договоры</b> (нажмите № для карточки):"]
    if not items:
        lines.append("— пусто")
    for i, l in enumerate(items, start=1):
        lines.append(f"{i}. №{l.contract_no} · {tmap.get(l.tenant_id, '?')} · {pmap.get(l.premises_id, '?')} · {l.rent_amount} ₽")
    num_buttons = [InlineKeyboardButton(text=str(i), callback_data=f"lpick:{l.id}") for i, l in enumerate(items, start=1)]
    rows = [num_buttons[i:i + 5] for i in range(0, len(num_buttons), 5)]
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="dadd:leases")])
    rows.append([InlineKeyboardButton(text="◀️ Справочники", callback_data="menu:directory")])
    await edit_or_send(callback.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


async def _render_lease_card(message, lease_id: int) -> bool:
    async with async_session_factory() as session:
        l = await session.get(Lease, lease_id)
        if l is None:
            return False
        tenant = await session.get(Tenant, l.tenant_id)
        premises = await session.get(Premises, l.premises_id)
    text = (
        f"<b>Договор №{l.contract_no}</b>\n"
        f"Арендатор: {tenant.name if tenant else '?'}\n"
        f"Помещение: {premises.label if premises else '?'}\n"
        f"Аренда: {l.rent_amount} ₽ · день оплаты: {l.payment_day}\n"
        f"Статус: {l.status.value}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Аренда ₽", callback_data=f"lrent:{lease_id}"),
         InlineKeyboardButton(text="📅 День оплаты", callback_data=f"lday:{lease_id}")],
        [InlineKeyboardButton(text="🏠 Сменить помещение", callback_data=f"lprem:{lease_id}")],
        [InlineKeyboardButton(text="🗑 Удалить договор", callback_data=f"ldel:{lease_id}")],
        [InlineKeyboardButton(text="◀️ К договорам", callback_data="dir:leases")],
    ])
    await edit_or_send(message, text, reply_markup=kb)
    return True


@router.callback_query(F.data.startswith("lpick:"))
async def lease_card(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not await _render_lease_card(callback.message, int(callback.data.split(":", 1)[1])):
        await callback.answer("Договор не найден", show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data.startswith("lrent:"))
async def lease_edit_rent(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(edit_kind="lease_rent", edit_id=int(callback.data.split(":", 1)[1]))
    await state.set_state(EditFSM.value)
    await edit_or_send(callback.message, "Новая сумма аренды, ₽:", reply_markup=cancel_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("lday:"))
async def lease_edit_day(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(edit_kind="lease_day", edit_id=int(callback.data.split(":", 1)[1]))
    await state.set_state(EditFSM.value)
    await edit_or_send(callback.message, "Новый день оплаты (1..31):", reply_markup=cancel_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("lprem:"))
async def lease_reassign_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    lease_id = int(callback.data.split(":", 1)[1])
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        premises = await directory_service.list_premises(session, lid) if lid else []
    rows = [[InlineKeyboardButton(text=f"{p.label} · {_status_label(p.is_occupied)}", callback_data=f"lpremok:{lease_id}:{p.id}")]
            for p in premises]
    rows.append([InlineKeyboardButton(text="◀️ Отмена", callback_data=f"lpick:{lease_id}")])
    await edit_or_send(callback.message, "Выберите новое помещение:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("lpremok:"))
async def lease_reassign_apply(callback: CallbackQuery, state: FSMContext) -> None:
    _, lease_id, premises_id = callback.data.split(":")
    async with async_session_factory() as session:
        await directory_service.update_lease(session, int(lease_id), premises_id=int(premises_id))
        await session.commit()
    await callback.answer("Помещение изменено")
    await _render_lease_card(callback.message, int(lease_id))


@router.callback_query(F.data.startswith("ldel:"))
async def lease_delete_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    lease_id = int(callback.data.split(":", 1)[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"ldelok:{lease_id}")],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"lpick:{lease_id}")],
    ])
    await edit_or_send(callback.message, "Удалить договор со всеми начислениями и платежами? Помещение освободится.", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("ldelok:"))
async def lease_delete(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    lease_id = int(callback.data.split(":", 1)[1])
    async with async_session_factory() as session:
        ok = await directory_service.delete_lease(session, lease_id)
        await session.commit()
    await edit_or_send(callback.message, "🗑 Договор удалён." if ok else "Не найдено.", reply_markup=_list_kb("dadd:leases"))
    await callback.answer()


# --- Универсальная правка значений (помещение/арендатор/договор) ------------
@router.message(EditFSM.value)
async def edit_value_save(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    kind = data.get("edit_kind")
    eid = data.get("edit_id")
    raw = message.text.strip()
    value = "" if raw == "-" else raw
    async with async_session_factory() as session:
        try:
            if kind == "premises":
                await directory_service.update_premises_field(session, eid, data["edit_field"], value)
                await session.commit()
                await state.clear()
                await _render_premises_card(message, eid)
                return
            if kind == "tenant":
                await directory_service.update_tenant_field(session, eid, data["edit_field"], value)
                await session.commit()
                await state.clear()
                await _render_tenant_card(message, eid)
                return
            if kind == "lease_rent":
                amount = _parse_amount(raw)
                if amount is None or amount <= 0:
                    await message.answer("❌ Введите положительную сумму. Повторите:", reply_markup=cancel_kb())
                    return
                await directory_service.update_lease(session, eid, rent_amount=amount)
                await session.commit()
                await state.clear()
                await _render_lease_card(message, eid)
                return
            if kind == "lease_day":
                if not raw.isdigit() or not 1 <= int(raw) <= 31:
                    await message.answer("❌ День оплаты — число 1..31. Повторите:", reply_markup=cancel_kb())
                    return
                await directory_service.update_lease(session, eid, payment_day=int(raw))
                await session.commit()
                await state.clear()
                await _render_lease_card(message, eid)
                return
        except ValueError as exc:
            await session.rollback()
            await message.answer(f"❌ {exc}\nПовторите ввод:", reply_markup=cancel_kb())
            return
    await state.clear()
    await message.answer("Готово.", reply_markup=main_menu_kb())


@router.callback_query(F.data == "dadd:leases")
async def lease_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        tenants = await directory_service.list_tenants(session, lid) if lid else []
    if not tenants:
        await edit_or_send(callback.message, 
            "Сначала добавьте арендатора (раздел «Арендаторы»).", reply_markup=_dir_menu_kb()
        )
        await callback.answer()
        return
    await state.set_state(LeaseFSM.tenant)
    rows = [[InlineKeyboardButton(text=f"{t.name} (ИНН {t.inn})", callback_data=f"dlt:{t.id}")] for t in tenants]
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="nav:home")])
    await edit_or_send(callback.message, "Выберите арендатора:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(LeaseFSM.tenant, F.data.startswith("dlt:"))
async def lease_pick_tenant(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(tenant_id=int(callback.data.split(":", 1)[1]))
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        premises = await directory_service.list_premises(session, lid) if lid else []
    if not premises:
        await state.clear()
        await edit_or_send(callback.message, 
            "Сначала добавьте помещение (раздел «Помещения»).", reply_markup=_dir_menu_kb()
        )
        await callback.answer()
        return
    await state.set_state(LeaseFSM.premises)
    rows = [[InlineKeyboardButton(text=p.label, callback_data=f"dlp:{p.id}")] for p in premises]
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="nav:home")])
    await edit_or_send(callback.message, "Выберите помещение:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(LeaseFSM.premises, F.data.startswith("dlp:"))
async def lease_pick_premises(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(premises_id=int(callback.data.split(":", 1)[1]))
    await state.set_state(LeaseFSM.contract_no)
    await edit_or_send(callback.message, "Номер договора:", reply_markup=_io_kb())
    await callback.answer()


@router.message(LeaseFSM.contract_no)
async def lease_contract_no(message: Message, state: FSMContext) -> None:
    if not message.text.strip():
        await message.answer("❌ Номер договора не может быть пустым. Повторите:", reply_markup=_io_kb())
        return
    await state.update_data(contract_no=message.text.strip())
    await state.set_state(LeaseFSM.contract_date)
    await message.answer("Дата договора ДД.ММ.ГГГГ:", reply_markup=_io_kb())


@router.message(LeaseFSM.contract_date)
async def lease_contract_date(message: Message, state: FSMContext) -> None:
    d = _parse_date(message.text)
    if d is None:
        await message.answer("❌ Формат ДД.ММ.ГГГГ. Повторите:", reply_markup=_io_kb())
        return
    await state.update_data(contract_date=d.isoformat())
    await state.set_state(LeaseFSM.rent)
    await message.answer("Сумма аренды в месяц, ₽:", reply_markup=_io_kb())


@router.message(LeaseFSM.rent)
async def lease_rent(message: Message, state: FSMContext) -> None:
    rent = _parse_amount(message.text)
    if rent is None or rent <= 0:
        await message.answer("❌ Введите положительную сумму. Повторите:", reply_markup=_io_kb())
        return
    await state.update_data(rent=str(rent))
    await state.set_state(LeaseFSM.payment_day)
    await message.answer(
        "День оплаты (1..31) или пропустите (по умолчанию 5):",
        reply_markup=_io_kb("dsk:lease_payday"),
    )


@router.callback_query(LeaseFSM.payment_day, F.data == "dsk:lease_payday")
async def lease_skip_payday(callback: CallbackQuery, state: FSMContext) -> None:
    await _lease_finish(callback.message, callback.from_user.id, state, payment_day=5)
    await callback.answer()


@router.message(LeaseFSM.payment_day)
async def lease_payment_day(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 31:
        await message.answer("❌ День оплаты — число 1..31. Повторите или пропустите:", reply_markup=_io_kb("dsk:lease_payday"))
        return
    await _lease_finish(message, message.from_user.id, state, payment_day=int(raw))


async def _lease_finish(message: Message, tg_id: int, state: FSMContext, *, payment_day: int) -> None:
    from datetime import date as _date

    from app.services import billing_service

    data = await state.get_data()
    async with async_session_factory() as session:
        try:
            lease = await directory_service.create_lease(
                session,
                tenant_id=data["tenant_id"],
                premises_id=data["premises_id"],
                contract_no=data["contract_no"],
                contract_date=_date.fromisoformat(data["contract_date"]),
                rent_amount=Decimal(data["rent"]),
                payment_day=payment_day,
            )
            await session.flush()
            lease_id = lease.id
            # Сразу начисляем аренду за текущий месяц, чтобы договор был виден в отчёте.
            await billing_service.create_rent_charge(session, lease, _date.today())
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            await state.clear()
            await message.answer(f"❌ {exc}", reply_markup=main_menu_kb())
            return
    await state.clear()
    await message.answer(
        f"✅ Договор №{data['contract_no']} создан (день оплаты {payment_day}). Аренда за текущий месяц начислена.",
        reply_markup=_list_kb("dadd:leases"),
    )


# --- Счётчики --------------------------------------------------------------
@router.callback_query(F.data == "dir:meters")
async def meters_list(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        items = await directory_service.list_meters(session, lid) if lid else []
    lines = ["<b>🔌 Счётчики</b> (нажмите № для карточки):"]
    if not items:
        lines.append("— пусто")
    num_buttons = []
    for i, (m, prem) in enumerate(items, start=1):
        title = m.serial_no or m.label or "счётчик"
        coeff = f" · k={m.coefficient}" if m.coefficient is not None else ""
        lines.append(f"{i}. {prem} · {title}{coeff}")
        num_buttons.append(InlineKeyboardButton(text=str(i), callback_data=f"mpick:{m.id}"))
    rows = [num_buttons[i:i + 5] for i in range(0, len(num_buttons), 5)]
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="dadd:meters")])
    rows.append([InlineKeyboardButton(text="◀️ Справочники", callback_data="menu:directory")])
    await edit_or_send(callback.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("mpick:"))
async def meter_card(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    mid = int(callback.data.split(":", 1)[1])
    async with async_session_factory() as session:
        from app.db.models import Meter
        m = await session.get(Meter, mid)
        prem = await session.get(Premises, m.premises_id) if m else None
    if m is None:
        await callback.answer("Счётчик не найден", show_alert=True)
        return
    title = m.serial_no or m.label or "счётчик"
    coeff = m.coefficient if m.coefficient is not None else "по умолчанию"
    text = f"<b>Счётчик</b>\n{title}\nПомещение: {prem.label if prem else '?'}\nКоэффициент: {coeff}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить счётчик", callback_data=f"mdel:{mid}")],
        [InlineKeyboardButton(text="◀️ К счётчикам", callback_data="dir:meters")],
    ])
    await edit_or_send(callback.message, text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("mdel:"))
async def meter_delete_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    mid = int(callback.data.split(":", 1)[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"mdelok:{mid}")],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"mpick:{mid}")],
    ])
    await edit_or_send(callback.message, "Удалить счётчик со всеми его показаниями?", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("mdelok:"))
async def meter_delete(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    mid = int(callback.data.split(":", 1)[1])
    async with async_session_factory() as session:
        ok = await directory_service.delete_meter(session, mid)
        await session.commit()
    await edit_or_send(callback.message, "🗑 Счётчик удалён." if ok else "Не найдено.", reply_markup=_list_kb("dadd:meters"))
    await callback.answer()


@router.callback_query(F.data == "dadd:meters")
async def meter_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        premises = await directory_service.list_premises(session, lid) if lid else []
    if not premises:
        await edit_or_send(callback.message, 
            "Сначала добавьте помещение (раздел «Помещения»).", reply_markup=_dir_menu_kb()
        )
        await callback.answer()
        return
    await state.set_state(MeterFSM.premises)
    rows = [[InlineKeyboardButton(text=p.label, callback_data=f"dmp:{p.id}")] for p in premises]
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="nav:home")])
    await edit_or_send(callback.message, "Помещение счётчика:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(MeterFSM.premises, F.data.startswith("dmp:"))
async def meter_pick_premises(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(premises_id=int(callback.data.split(":", 1)[1]))
    await state.set_state(MeterFSM.serial)
    await edit_or_send(callback.message, "Серийный номер счётчика или пропустите:", reply_markup=_io_kb("dsk:meter_serial"))
    await callback.answer()


@router.callback_query(MeterFSM.serial, F.data == "dsk:meter_serial")
async def meter_skip_serial(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(serial=None)
    await state.set_state(MeterFSM.label)
    await edit_or_send(callback.message, "Метка/название счётчика или пропустите:", reply_markup=_io_kb("dsk:meter_label"))
    await callback.answer()


@router.message(MeterFSM.serial)
async def meter_serial(message: Message, state: FSMContext) -> None:
    await state.update_data(serial=message.text.strip())
    await state.set_state(MeterFSM.label)
    await message.answer("Метка/название счётчика или пропустите:", reply_markup=_io_kb("dsk:meter_label"))


@router.callback_query(MeterFSM.label, F.data == "dsk:meter_label")
async def meter_skip_label(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(label=None)
    await state.set_state(MeterFSM.coefficient)
    await edit_or_send(callback.message, "Коэффициент (напр. 0.93) или пропустите:", reply_markup=_io_kb("dsk:meter_coeff"))
    await callback.answer()


@router.message(MeterFSM.label)
async def meter_label(message: Message, state: FSMContext) -> None:
    await state.update_data(label=message.text.strip())
    await state.set_state(MeterFSM.coefficient)
    await message.answer("Коэффициент (напр. 0.93) или пропустите:", reply_markup=_io_kb("dsk:meter_coeff"))


@router.callback_query(MeterFSM.coefficient, F.data == "dsk:meter_coeff")
async def meter_skip_coeff(callback: CallbackQuery, state: FSMContext) -> None:
    await _meter_finish(callback.message, callback.from_user.id, state, coefficient=None)
    await callback.answer()


@router.message(MeterFSM.coefficient)
async def meter_coefficient(message: Message, state: FSMContext) -> None:
    coeff = _parse_amount(message.text)
    if coeff is None or coeff <= 0:
        await message.answer("❌ Введите положительное число или пропустите:", reply_markup=_io_kb("dsk:meter_coeff"))
        return
    await _meter_finish(message, message.from_user.id, state, coefficient=coeff)


async def _meter_finish(message: Message, tg_id: int, state: FSMContext, *, coefficient: Decimal | None) -> None:
    data = await state.get_data()
    async with async_session_factory() as session:
        try:
            meter = await directory_service.create_meter(
                session,
                premises_id=data["premises_id"],
                serial_no=data.get("serial"),
                label=data.get("label"),
                coefficient=coefficient,
            )
            await session.flush()
            meter_id = meter.id
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            await state.clear()
            await message.answer(f"❌ {exc}", reply_markup=main_menu_kb())
            return
    await state.clear()
    await message.answer(f"✅ Счётчик #{meter_id} добавлен.", reply_markup=_list_kb("dadd:meters"))


# --- Реквизиты арендодателя ------------------------------------------------
def _landlord_view(landlord) -> str:
    from app.services.directory_service import LANDLORD_FIELDS
    lines = ["<b>🏦 Реквизиты арендодателя</b>", "(нажмите поле, чтобы изменить)", ""]
    for field, title in LANDLORD_FIELDS.items():
        val = getattr(landlord, field, None) or "—"
        lines.append(f"{title}: {val}")
    return "\n".join(lines)


def _landlord_kb() -> InlineKeyboardMarkup:
    from app.services.directory_service import LANDLORD_FIELDS
    items = [InlineKeyboardButton(text=title, callback_data=f"dlf:{field}")
             for field, title in LANDLORD_FIELDS.items()]
    rows = [items[i:i + 2] for i in range(0, len(items), 2)]
    rows.append([InlineKeyboardButton(text="◀️ Справочники", callback_data="menu:directory")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "dir:landlord")
async def landlord_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        landlord = await directory_service.get_landlord(session, lid) if lid else None
    if landlord is None:
        await edit_or_send(callback.message, "Арендодатель не найден.", reply_markup=back_kb())
        await callback.answer()
        return
    await edit_or_send(callback.message, _landlord_view(landlord), reply_markup=_landlord_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("dlf:"))
async def landlord_field_pick(callback: CallbackQuery, state: FSMContext) -> None:
    from app.services.directory_service import LANDLORD_FIELDS
    field = callback.data.split(":", 1)[1]
    if field not in LANDLORD_FIELDS:
        await callback.answer("Неизвестное поле", show_alert=True)
        return
    await state.update_data(landlord_field=field)
    await state.set_state(LandlordFSM.value)
    await edit_or_send(callback.message, f"Введите новое значение — {LANDLORD_FIELDS[field]}:", reply_markup=_io_kb())
    await callback.answer()


@router.message(LandlordFSM.value)
async def landlord_field_save(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field = data["landlord_field"]
    async with async_session_factory() as session:
        lid = await _landlord_id(session, message.from_user.id)
        try:
            landlord = await directory_service.update_landlord_field(session, lid, field, message.text)
            await session.commit()
            await session.refresh(landlord)
            view = _landlord_view(landlord)
        except ValueError as exc:
            await session.rollback()
            await message.answer(f"❌ {exc}\nПовторите ввод:", reply_markup=_io_kb())
            return
    await state.clear()
    await message.answer("✅ Сохранено.\n\n" + view, reply_markup=_landlord_kb())
