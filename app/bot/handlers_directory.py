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
from app.db.models import Premises
from app.bot.keyboards import back_kb, main_menu_kb
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
    status = State()


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
def _status_label(is_occupied: bool) -> str:
    return "🔴 занято" if is_occupied else "🟢 свободно"


@router.callback_query(F.data == "dir:premises")
async def premises_list(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        items = await directory_service.list_premises(session, lid) if lid else []
    lines = ["<b>🏢 Помещения</b> (нажмите номер для статуса):"]
    if not items:
        lines.append("— пусто")
    for p in items:
        area = f", {p.area} м²" if p.area is not None else ""
        lines.append(f"#{p.id} · {p.label}{area} · {_status_label(p.is_occupied)}")
    num_buttons = [InlineKeyboardButton(text=f"#{p.id}", callback_data=f"ppick:{p.id}") for p in items]
    rows = [num_buttons[i:i + 5] for i in range(0, len(num_buttons), 5)]
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="dadd:premises")])
    rows.append([InlineKeyboardButton(text="◀️ Справочники", callback_data="menu:directory")])
    await edit_or_send(callback.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("ppick:"))
async def premises_card(callback: CallbackQuery, state: FSMContext) -> None:
    """Карточка помещения: смена статуса свободно/занято."""
    await state.clear()
    pid = int(callback.data.split(":", 1)[1])
    async with async_session_factory() as session:
        p = await session.get(Premises, pid)
    if p is None:
        await callback.answer("Помещение не найдено", show_alert=True)
        return
    area = f", {p.area} м²" if p.area is not None else ""
    text = f"<b>Помещение #{p.id}</b>\n{p.label}{area}\nСтатус: {_status_label(p.is_occupied)}"
    toggle_to = 0 if p.is_occupied else 1
    toggle_txt = "🟢 Пометить свободным" if p.is_occupied else "🔴 Пометить занятым"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_txt, callback_data=f"pstat:{p.id}:{toggle_to}")],
        [InlineKeyboardButton(text="◀️ К помещениям", callback_data="dir:premises")],
    ])
    await edit_or_send(callback.message, text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("pstat:"))
async def premises_set_status(callback: CallbackQuery, state: FSMContext) -> None:
    _, raw_id, raw_val = callback.data.split(":")
    async with async_session_factory() as session:
        await directory_service.set_premises_status(session, int(raw_id), bool(int(raw_val)))
        await session.commit()
    await callback.answer("Статус обновлён")
    await premises_list(callback, state)


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


def _premises_status_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Свободно", callback_data="dpst:0"),
         InlineKeyboardButton(text="🔴 Занято", callback_data="dpst:1")],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="nav:home")],
    ])


@router.callback_query(PremisesFSM.area, F.data == "dsk:prem_area")
async def premises_skip_area(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(area=None)
    await state.set_state(PremisesFSM.status)
    await edit_or_send(callback.message, "Статус помещения:", reply_markup=_premises_status_kb())
    await callback.answer()


@router.message(PremisesFSM.area)
async def premises_area(message: Message, state: FSMContext) -> None:
    area = _parse_amount(message.text)
    if area is None or area <= 0:
        await message.answer("❌ Введите положительное число или пропустите:", reply_markup=_io_kb("dsk:prem_area"))
        return
    await state.update_data(area=str(area))
    await state.set_state(PremisesFSM.status)
    await message.answer("Статус помещения:", reply_markup=_premises_status_kb())


@router.callback_query(PremisesFSM.status, F.data.startswith("dpst:"))
async def premises_status(callback: CallbackQuery, state: FSMContext) -> None:
    is_occupied = bool(int(callback.data.split(":", 1)[1]))
    data = await state.get_data()
    area = Decimal(data["area"]) if data.get("area") else None
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        try:
            p = await directory_service.create_premises(
                session, landlord_id=lid, label=data["label"], address=data.get("address"),
                area=area, is_occupied=is_occupied,
            )
            await session.flush()
            pid = p.id
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            await state.clear()
            await edit_or_send(callback.message, f"❌ {exc}", reply_markup=main_menu_kb())
            await callback.answer()
            return
    await state.clear()
    await edit_or_send(callback.message, 
        f"✅ Помещение #{pid} «{data['label']}» добавлено ({_status_label(is_occupied)}).",
        reply_markup=_list_kb("dadd:premises"),
    )
    await callback.answer()


# --- Арендаторы ------------------------------------------------------------
@router.callback_query(F.data == "dir:tenants")
async def tenants_list(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        items = await directory_service.list_tenants(session, lid) if lid else []
    lines = ["<b>👤 Арендаторы:</b>"]
    if not items:
        lines.append("— пусто")
    for t in items:
        lines.append(f"#{t.id} · {t.name} · ИНН {t.inn}")
    await edit_or_send(callback.message, "\n".join(lines), reply_markup=_list_kb("dadd:tenants"))
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
    lines = ["<b>📄 Договоры:</b>"]
    if not items:
        lines.append("— пусто")
    for l in items:
        lines.append(f"#{l.id} · №{l.contract_no} · аренда {l.rent_amount} ₽ · статус {l.status.value}")
    await edit_or_send(callback.message, "\n".join(lines), reply_markup=_list_kb("dadd:leases"))
    await callback.answer()


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
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            await state.clear()
            await message.answer(f"❌ {exc}", reply_markup=main_menu_kb())
            return
    await state.clear()
    await message.answer(
        f"✅ Договор #{lease_id} №{data['contract_no']} создан (день оплаты {payment_day}).",
        reply_markup=_list_kb("dadd:leases"),
    )


# --- Счётчики --------------------------------------------------------------
@router.callback_query(F.data == "dir:meters")
async def meters_list(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        items = await directory_service.list_meters(session, lid) if lid else []
    lines = ["<b>🔌 Счётчики:</b>"]
    if not items:
        lines.append("— пусто")
    for m, prem in items:
        title = m.serial_no or m.label or f"счётчик {m.id}"
        coeff = f" · k={m.coefficient}" if m.coefficient is not None else ""
        lines.append(f"#{m.id} · {prem} · {title}{coeff}")
    await edit_or_send(callback.message, "\n".join(lines), reply_markup=_list_kb("dadd:meters"))
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
