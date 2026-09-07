"""Админ-хендлеры бота: настройки, расходы, показания, корректировки, отчёты.

UI-обёртки над сервисами (settings/expense/reading/adjustment/report).
Бизнес-логика и расчёты — в сервисах и покрыты тестами.

Навигация построена на инлайн-кнопках (callback_data), поэтому переход в любой
раздел работает всегда — в том числе посреди незавершённого ввода: callback не
перехватывается обработчиками FSM-состояний и сбрасывает состояние. Ожидание
текстового сообщения (FSM) остаётся только там, где нужно ввести непредсказуемое
значение — сумму, показания, текст/дату задачи, причину корректировки.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from app.bot.keyboards import back_kb, cancel_kb, main_menu_kb
from app.db.base import async_session_factory
from app.db.enums import ChargeType, DataSource, ExpenseCategory, LeaseStatus
from app.db.models import Charge, Lease, Meter, Premises, Tenant, User
from app.services import (
    adjustment_service,
    billing_service,
    confirmation_service,
    expense_service,
    matching_service,
    payment_service,
    reading_service,
    report_service,
    settings_service,
    task_service,
)

router = Router()


# Лимит длины сообщения Telegram — 4096 символов; берём с запасом.
_TG_MAX_LEN = 4000


def _clip(text: str) -> str:
    """Обрезает текст до лимита Telegram, чтобы длинное сообщение не роняло бот."""
    if len(text) <= _TG_MAX_LEN:
        return text
    return text[: _TG_MAX_LEN - 1] + "…"


async def edit_or_send(message: Message, text: str, reply_markup=None) -> None:
    """Навигация без спама: правит текущее сообщение бота, иначе шлёт новое.

    Используется в callback-хендлерах, чтобы переход в другое меню заменял
    предыдущее сообщение, а не добавлял новое. Текст обрезается до лимита Telegram.
    """
    text = _clip(text)
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return
        await message.answer(text, reply_markup=reply_markup)
    except Exception:
        await message.answer(text, reply_markup=reply_markup)


# Настройки, доступные для правки через бота: ключ -> подпись
_EDITABLE_SETTINGS = {
    "electricity_tariff": "Тариф ₽/кВт·ч",
    "electricity_coeff": "Коэффициент электричества",
    "penalty_rate": "Ставка пени, %/день",
    "reminder_days_before": "Напоминание за N дней",
    "server_cost": "Серверная, ₽/мес",
    "salary_cost": "Зарплаты, ₽/мес",
}

_EXPENSE_CHOICES = {
    "travel": "Командировочные",
    "repair": "Текущий ремонт",
    "docs": "Документация",
    "taxes": "Налоги",
    "other": "Прочее",
}


class SettingFSM(StatesGroup):
    value = State()


class ExpenseFSM(StatesGroup):
    amount = State()


class AdjustFSM(StatesGroup):
    amount = State()
    reason = State()


class ReadingFSM(StatesGroup):
    value = State()


class TaskFSM(StatesGroup):
    add_title = State()
    add_priority = State()
    add_date = State()
    bulk_titles = State()
    edit_title = State()
    edit_priority = State()
    edit_date = State()


class PayFSM(StatesGroup):
    amount = State()


async def _landlord_id(session, tg_id: int) -> int | None:
    """landlord_id оператора: по пользователю, иначе единственный арендодатель."""
    result = await session.execute(select(User.landlord_id).where(User.tg_id == tg_id))
    lid = result.scalar_one_or_none()
    if lid is not None:
        return lid
    return await matching_service.get_default_landlord_id(session)


def _parse_amount(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ".").replace(" ", ""))
    except (InvalidOperation, ValueError):
        return None


def _parse_date(text: str) -> date | None:
    """Парсит дату ДД.ММ.ГГГГ."""
    try:
        return datetime.strptime(text.strip(), "%d.%m.%Y").date()
    except ValueError:
        return None


# --- Главное меню ----------------------------------------------------------
async def show_main_menu(message: Message, *, greet: bool = False, edit: bool = False) -> None:
    """Показывает главное меню (инлайн). greet — приветствие; edit — правит сообщение."""
    text = (
        "👋 Бот учёта аренды.\nВыберите раздел:"
        if greet
        else "Главное меню — выберите раздел:"
    )
    if edit:
        await edit_or_send(message, text, reply_markup=main_menu_kb())
    else:
        await message.answer(text, reply_markup=main_menu_kb())


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_main_menu(message)


@router.callback_query(F.data == "nav:home")
async def nav_home(callback: CallbackQuery, state: FSMContext) -> None:
    """Возврат в главное меню из любого места, сброс незавершённого ввода."""
    await state.clear()
    await show_main_menu(callback.message, edit=True)
    await callback.answer()


# --- Настройки -------------------------------------------------------------
@router.callback_query(F.data == "menu:settings")
async def settings_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    rows = [
        [InlineKeyboardButton(text=title, callback_data=f"set:{key}")]
        for key, title in _EDITABLE_SETTINGS.items()
    ]
    rows.append([InlineKeyboardButton(text="◀️ В меню", callback_data="nav:home")])
    await edit_or_send(callback.message, "Выберите параметр для изменения:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("set:"))
async def settings_pick(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":", 1)[1]
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        current = await settings_service.get_setting(session, lid, key) if lid else None
    await state.update_data(setting_key=key)
    await state.set_state(SettingFSM.value)
    await edit_or_send(callback.message, 
        f"{_EDITABLE_SETTINGS.get(key, key)} (текущее: {current}).\nВведите новое значение:",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(SettingFSM.value)
async def settings_save(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = data["setting_key"]
    if _parse_amount(message.text) is None:
        await message.answer("❌ Введите число. Повторите:", reply_markup=cancel_kb())
        return
    async with async_session_factory() as session:
        lid = await _landlord_id(session, message.from_user.id)
        await settings_service.set_setting(session, lid, key, message.text.replace(",", ".").strip())
        await session.commit()
    await state.clear()
    await message.answer("✅ Значение сохранено.", reply_markup=main_menu_kb())


# --- Расходы ---------------------------------------------------------------
# Русские названия всех категорий расходов (для списка и правки)
_EXPENSE_LABELS = {
    ExpenseCategory.server: "Серверная",
    ExpenseCategory.electricity: "Электричество",
    ExpenseCategory.salary: "Зарплаты",
    ExpenseCategory.travel: "Командировочные",
    ExpenseCategory.repair: "Текущий ремонт",
    ExpenseCategory.docs: "Документация",
    ExpenseCategory.taxes: "Налоги",
    ExpenseCategory.other: "Прочее",
}


def _expense_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить расход", callback_data="exp_add")],
        [InlineKeyboardButton(text="📋 Расходы месяца (правка)", callback_data="exp_list")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="nav:home")],
    ])


@router.callback_query(F.data == "menu:expense")
async def expense_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_send(callback.message, "<b>💸 Расходы</b> — что сделать?", reply_markup=_expense_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "exp_add")
async def expense_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    rows = [
        [InlineKeyboardButton(text=title, callback_data=f"exp:{code}")]
        for code, title in _EXPENSE_CHOICES.items()
    ]
    rows.append([InlineKeyboardButton(text="◀️ К расходам", callback_data="menu:expense")])
    await edit_or_send(callback.message, "Категория расхода:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("exp:"))
async def expense_pick(callback: CallbackQuery, state: FSMContext) -> None:
    code = callback.data.split(":", 1)[1]
    await state.update_data(expense_category=code)
    await state.set_state(ExpenseFSM.amount)
    await edit_or_send(callback.message, f"Расход «{_EXPENSE_CHOICES[code]}». Введите сумму, ₽:", reply_markup=cancel_kb())
    await callback.answer()


@router.message(ExpenseFSM.amount)
async def expense_save(message: Message, state: FSMContext) -> None:
    amount = _parse_amount(message.text)
    if amount is None or amount <= 0:
        await message.answer("❌ Введите положительную сумму. Повторите:", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    category = ExpenseCategory(data["expense_category"])
    async with async_session_factory() as session:
        lid = await _landlord_id(session, message.from_user.id)
        await expense_service.add_expense(
            session, landlord_id=lid, category=category, amount=amount, period=date.today()
        )
        await session.commit()
    await state.clear()
    await message.answer(f"✅ Расход {amount} ₽ добавлен.", reply_markup=_expense_menu_kb())


# Список расходов месяца + правка суммы (с аудитом) — вместо общей «Корректировки»
@router.callback_query(F.data == "exp_list")
async def expense_list(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        items = await expense_service.list_expenses(session, lid, date.today()) if lid else []
    lines = ["<b>💸 Расходы за текущий месяц</b> (нажмите № для правки суммы):"]
    if not items:
        lines.append("— пусто")
    for i, e in enumerate(items, start=1):
        lines.append(f"{i}. {_EXPENSE_LABELS.get(e.category, e.category.value)}: {e.amount} ₽")
    num_buttons = [InlineKeyboardButton(text=str(i), callback_data=f"ecorr:{e.id}") for i, e in enumerate(items, start=1)]
    rows = [num_buttons[i:i + 5] for i in range(0, len(num_buttons), 5)]
    rows.append([InlineKeyboardButton(text="◀️ К расходам", callback_data="menu:expense")])
    await edit_or_send(callback.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("ecorr:"))
async def expense_correct_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(entity_type="expense", entity_id=int(callback.data.split(":", 1)[1]))
    await state.set_state(AdjustFSM.amount)
    await edit_or_send(callback.message, "Введите новую сумму расхода, ₽:", reply_markup=cancel_kb())
    await callback.answer()


@router.message(AdjustFSM.amount)
async def adjust_amount(message: Message, state: FSMContext) -> None:
    amount = _parse_amount(message.text)
    if amount is None or amount < 0:
        await message.answer("❌ Введите неотрицательную сумму. Повторите:", reply_markup=cancel_kb())
        return
    await state.update_data(new_amount=str(amount))
    await state.set_state(AdjustFSM.reason)
    await message.answer("Укажите причину корректировки:", reply_markup=cancel_kb())


@router.message(AdjustFSM.reason)
async def adjust_save(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, message.from_user.id)
        user = (
            await session.execute(select(User).where(User.tg_id == message.from_user.id))
        ).scalar_one_or_none()
        try:
            await adjustment_service.correct_amount(
                session,
                landlord_id=lid,
                user_id=user.id if user else None,
                entity_type=data["entity_type"],
                entity_id=data["entity_id"],
                new_amount=Decimal(data["new_amount"]),
                reason=message.text,
            )
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            await state.clear()
            await message.answer(f"❌ {exc}", reply_markup=main_menu_kb())
            return
    await state.clear()
    await message.answer("✅ Сумма скорректирована (записано в аудит).", reply_markup=_expense_menu_kb())


# --- Показания счётчиков (ручной ввод / электричество) --------------------
@router.callback_query(F.data == "menu:readings")
async def readings_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        if lid is None:
            await edit_or_send(callback.message, "Нет арендодателя.", reply_markup=back_kb())
            await callback.answer()
            return
        rows = (
            await session.execute(
                select(Meter.id, Meter.serial_no, Meter.label, Premises.label)
                .join(Premises, Premises.id == Meter.premises_id)
                .where(Premises.landlord_id == lid)
            )
        ).all()
    if not rows:
        await edit_or_send(callback.message, "Счётчиков пока нет.", reply_markup=back_kb())
        await callback.answer()
        return
    kb_rows = [
        [InlineKeyboardButton(
            text=f"{prem} · {serial or label or ('счётчик ' + str(mid))}",
            callback_data=f"mr:{mid}",
        )]
        for mid, serial, label, prem in rows
    ]
    kb_rows.append([InlineKeyboardButton(text="◀️ В меню", callback_data="nav:home")])
    await edit_or_send(callback.message, "Выберите счётчик для ввода показаний:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await callback.answer()


@router.callback_query(F.data.startswith("mr:"))
async def reading_pick(callback: CallbackQuery, state: FSMContext) -> None:
    meter_id = int(callback.data.split(":", 1)[1])
    await state.update_data(meter_id=meter_id)
    await state.set_state(ReadingFSM.value)
    await edit_or_send(callback.message, 
        "Введите период и текущие показания в формате: ММ.ГГГГ значение\n"
        "Пример: 04.2026 15350",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(ReadingFSM.value)
async def reading_save(message: Message, state: FSMContext) -> None:
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("❌ Формат: ММ.ГГГГ значение. Повторите:", reply_markup=cancel_kb())
        return
    try:
        month, year = parts[0].split(".")
        period = date(int(year), int(month), 1)
    except (ValueError, IndexError):
        await message.answer("❌ Неверный период (ММ.ГГГГ). Повторите:", reply_markup=cancel_kb())
        return
    curr = _parse_amount(parts[1])
    if curr is None or curr < 0:
        await message.answer("❌ Неверное значение показаний. Повторите:", reply_markup=cancel_kb())
        return

    data = await state.get_data()
    async with async_session_factory() as session:
        meter = await session.get(Meter, data["meter_id"])
        if meter is None:
            await state.clear()
            await message.answer("❌ Счётчик не найден.", reply_markup=main_menu_kb())
            return
        try:
            reading = await reading_service.upsert_reading(
                session, meter, period=period, curr_value=curr, source=DataSource.manual
            )
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            await state.clear()
            await message.answer(f"❌ {exc}", reply_markup=main_menu_kb())
            return
    await state.clear()
    await message.answer(
        f"✅ Показания сохранены. Расход: {reading.consumption} кВт·ч.", reply_markup=main_menu_kb()
    )


# --- Менеджер задач --------------------------------------------------------
def _priority_kb(context: str, with_keep: bool = False, with_date: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура выбора приоритета/даты. context: add/bulk/edit."""
    rows = [[
        InlineKeyboardButton(text="🔴 1 (3 дня)", callback_data=f"tp:{context}:1"),
        InlineKeyboardButton(text="🟡 2 (7 дней)", callback_data=f"tp:{context}:2"),
        InlineKeyboardButton(text="🟢 3 (25 дней)", callback_data=f"tp:{context}:3"),
    ]]
    extra = []
    if with_date:
        extra.append(InlineKeyboardButton(text="📅 На дату", callback_data=f"tp:{context}:date"))
    if with_keep:
        extra.append(InlineKeyboardButton(text="↔️ Не менять", callback_data="tp:edit:keep"))
    if extra:
        rows.append(extra)
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _tasks_menu_kb() -> InlineKeyboardMarkup:
    """Компактное меню раздела «Задачи»."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Ближайшие", callback_data="tasks:recent"),
         InlineKeyboardButton(text="✏️ Редактировать", callback_data="tasks:edit")],
        [InlineKeyboardButton(text="➕ Задача", callback_data="task_add"),
         InlineKeyboardButton(text="➕ Списком", callback_data="task_bulk")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="nav:home")],
    ])


_PRIORITY_ICON = {
    task_service.TaskPriority.high: "🔴",
    task_service.TaskPriority.medium: "🟡",
    task_service.TaskPriority.low: "🟢",
}


def _due_human(due) -> str:
    """Человекочитаемый остаток до срока: реальные дни, а не «окно приоритета»."""
    if due is None:
        return "без срока"
    days = (due - date.today()).days
    if days > 0:
        return f"осталось {days} дн."
    if days == 0:
        return "сегодня"
    return f"просрочено на {-days} дн."


def _task_line(idx: int, t) -> str:
    due = t.due_date.strftime("%d.%m.%Y") if t.due_date else "—"
    icon = _PRIORITY_ICON.get(t.priority, "")
    return f"{idx}. {icon} {t.title} · до {due} ({_due_human(t.due_date)})"


@router.callback_query(F.data == "menu:tasks")
async def tasks_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_send(callback.message, "<b>📝 Задачи</b> — что открыть?", reply_markup=_tasks_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "tasks:recent")
async def tasks_recent(callback: CallbackQuery, state: FSMContext) -> None:
    """Первые 10 ближайших задач (без кнопок по каждой)."""
    await state.clear()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        tasks = await task_service.list_tasks(session, lid) if lid else []
    lines = ["<b>📋 Ближайшие задачи:</b>"]
    if not tasks:
        lines.append("— пусто")
    for i, t in enumerate(tasks[:10], start=1):
        lines.append(_task_line(i, t))
    if len(tasks) > 10:
        lines.append(f"… и ещё {len(tasks) - 10}. Для правки — «✏️ Редактировать».")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ К задачам", callback_data="menu:tasks")]])
    await edit_or_send(callback.message, "\n".join(lines), reply_markup=kb)
    await callback.answer()


# Сколько задач показываем на одной странице редактирования
_TASKS_PAGE = 20


@router.callback_query(F.data == "tasks:edit")
@router.callback_query(F.data.startswith("tasks:editp:"))
async def tasks_edit_list(callback: CallbackQuery, state: FSMContext) -> None:
    """Постраничный список задач + кнопки-номера для выбора конкретной задачи.

    Пагинация нужна, чтобы длинный список не превышал лимит сообщения Telegram.
    """
    await state.clear()
    offset = int(callback.data.split(":")[2]) if callback.data.startswith("tasks:editp:") else 0
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        tasks = await task_service.list_tasks(session, lid) if lid else []

    total = len(tasks)
    offset = max(0, min(offset, (total - 1) // _TASKS_PAGE * _TASKS_PAGE if total else 0))
    page = tasks[offset:offset + _TASKS_PAGE]

    lines = ["<b>✏️ Редактирование задач</b>"]
    if not tasks:
        lines.append("— пусто")
    else:
        page_no = offset // _TASKS_PAGE + 1
        pages_total = (total + _TASKS_PAGE - 1) // _TASKS_PAGE
        lines.append(f"Стр. {page_no}/{pages_total}. Выберите номер задачи:")
        for i, t in enumerate(page, start=offset + 1):
            lines.append(_task_line(i, t))

    # Кнопки-номера только для текущей страницы, по 5 в ряд
    num_buttons = [
        InlineKeyboardButton(text=str(offset + i), callback_data=f"tpick:{t.id}")
        for i, t in enumerate(page, start=1)
    ]
    rows = [num_buttons[i:i + 5] for i in range(0, len(num_buttons), 5)]
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="◀️ Пред.", callback_data=f"tasks:editp:{offset - _TASKS_PAGE}"))
    if offset + _TASKS_PAGE < total:
        nav.append(InlineKeyboardButton(text="След. ▶️", callback_data=f"tasks:editp:{offset + _TASKS_PAGE}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="◀️ К задачам", callback_data="menu:tasks")])
    await edit_or_send(callback.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("tpick:"))
async def task_pick(callback: CallbackQuery, state: FSMContext) -> None:
    """Карточка выбранной задачи с действиями и кнопкой «Назад»."""
    await state.clear()
    task_id = int(callback.data.split(":", 1)[1])
    async with async_session_factory() as session:
        t = await task_service.get_task(session, task_id)
    if t is None:
        await callback.answer("Задача не найдена", show_alert=True)
        return
    due = t.due_date.strftime("%d.%m.%Y") if t.due_date else "—"
    text = (
        f"<b>Задача #{t.id}</b>\n"
        f"{task_service.PRIORITY_LABEL.get(t.priority, '')} {t.title}\n"
        f"Срок: {due} · статус: {t.status.value}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"taskdone:{t.id}"),
         InlineKeyboardButton(text="🗑 Удалить", callback_data=f"taskdel:{t.id}")],
        [InlineKeyboardButton(text="🏷 Категория", callback_data=f"taskcat:{t.id}"),
         InlineKeyboardButton(text="📅 Дата", callback_data=f"taskdate:{t.id}")],
        [InlineKeyboardButton(text="◀️ К списку", callback_data="tasks:edit")],
    ])
    await edit_or_send(callback.message, text, reply_markup=kb)
    await callback.answer()


# Добавление одной задачи
@router.callback_query(F.data == "task_add")
async def task_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(TaskFSM.add_title)
    await edit_or_send(callback.message, "Введите текст задачи:", reply_markup=cancel_kb())
    await callback.answer()


@router.message(TaskFSM.add_title)
async def task_add_title(message: Message, state: FSMContext) -> None:
    title = message.text.strip()
    if not title:
        await message.answer("❌ Текст задачи не может быть пустым. Повторите:", reply_markup=cancel_kb())
        return
    await state.update_data(title=title)
    await state.set_state(TaskFSM.add_priority)
    await message.answer(
        "Выберите приоритет (задаёт срок) или «На дату»:",
        reply_markup=_priority_kb("add", with_date=True),
    )


async def _create_task_and_reply(callback_or_msg, state, *, priority, due_date, user_tg_id):
    """Создаёт задачу и отвечает пользователю (общий код для приоритета/даты)."""
    data = await state.get_data()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, user_tg_id)
        user = (await session.execute(select(User).where(User.tg_id == user_tg_id))).scalar_one_or_none()
        task = await task_service.create_task(
            session, landlord_id=lid, title=data["title"], priority=priority,
            due_date=due_date, created_by_id=user.id if user else None,
        )
        await session.flush()
        due_str = task.due_date.strftime("%d.%m.%Y")
        await session.commit()
    await state.clear()
    return due_str


@router.callback_query(TaskFSM.add_priority, F.data == "tp:add:date")
async def task_add_pick_date(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TaskFSM.add_date)
    await edit_or_send(callback.message, "Введите дату задачи в формате ДД.ММ.ГГГГ:", reply_markup=cancel_kb())
    await callback.answer()


@router.message(TaskFSM.add_date)
async def task_add_date(message: Message, state: FSMContext) -> None:
    due = _parse_date(message.text)
    if due is None:
        await message.answer("❌ Формат ДД.ММ.ГГГГ. Повторите:", reply_markup=cancel_kb())
        return
    due_str = await _create_task_and_reply(
        message, state, priority=task_service.TaskPriority.medium, due_date=due, user_tg_id=message.from_user.id
    )
    await message.answer(f"✅ Задача добавлена на {due_str}.", reply_markup=main_menu_kb())


@router.callback_query(TaskFSM.add_priority, F.data.startswith("tp:add:"))
async def task_add_priority(callback: CallbackQuery, state: FSMContext) -> None:
    priority = task_service.NUM_PRIORITY[int(callback.data.split(":")[2])]
    due_str = await _create_task_and_reply(
        callback, state, priority=priority, due_date=None, user_tg_id=callback.from_user.id
    )
    await edit_or_send(callback.message, f"✅ Задача добавлена. Срок: {due_str}.", reply_markup=main_menu_kb())
    await callback.answer()


# Добавление списком (приоритет/дата — в конце каждой строки)
@router.callback_query(F.data == "task_bulk")
async def task_bulk_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(TaskFSM.bulk_titles)
    await edit_or_send(callback.message, 
        "Пришлите задачи списком — по одной на строку.\n"
        "В конце строки укажите приоритет <b>1</b>/<b>2</b>/<b>3</b> или дату <b>ДД.ММ.ГГГГ</b>.\n"
        "Если ничего не указано — приоритет 2.\n\n"
        "Пример:\n"
        "<code>Позвонить электрику 1\n"
        "Уборка территории 3\n"
        "Вывоз камней литера А 15.12.2026</code>",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(TaskFSM.bulk_titles)
async def task_bulk_save(message: Message, state: FSMContext) -> None:
    lines = [ln for ln in message.text.splitlines() if ln.strip()]
    if not lines:
        await message.answer("❌ Пусто. Пришлите задачи по одной на строку:", reply_markup=cancel_kb())
        return
    async with async_session_factory() as session:
        lid = await _landlord_id(session, message.from_user.id)
        user = (await session.execute(select(User).where(User.tg_id == message.from_user.id))).scalar_one_or_none()
        created = await task_service.create_tasks_from_lines(
            session, landlord_id=lid, lines=lines,
            created_by_id=user.id if user else None,
        )
        await session.commit()
    await state.clear()
    await message.answer(f"✅ Добавлено задач: {len(created)}.", reply_markup=main_menu_kb())


# Редактирование
@router.callback_query(F.data.startswith("taskedit:"))
async def task_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    tid = int(callback.data.split(":", 1)[1])
    title = await _task_title(tid)
    await state.update_data(edit_id=tid)
    await state.set_state(TaskFSM.edit_title)
    await edit_or_send(
        callback.message,
        f"Задача: «{title}»\nНовый текст (или «-» чтобы оставить как есть):",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(TaskFSM.edit_title)
async def task_edit_title(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    await state.update_data(new_title=None if text == "-" else text)
    await state.set_state(TaskFSM.edit_priority)
    await message.answer(
        "Новый приоритет, дата или без изменений:",
        reply_markup=_priority_kb("edit", with_keep=True, with_date=True),
    )


async def _task_title(task_id: int) -> str:
    """Название задачи для подписи в мастерах редактирования."""
    async with async_session_factory() as session:
        t = await task_service.get_task(session, task_id)
    return t.title if t else f"#{task_id}"


# Смена категории из напоминания
@router.callback_query(F.data.startswith("taskcat:"))
async def task_reassign_category(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    tid = int(callback.data.split(":", 1)[1])
    title = await _task_title(tid)
    await state.update_data(edit_id=tid, new_title=None)
    await state.set_state(TaskFSM.edit_priority)
    await edit_or_send(
        callback.message,
        f"Задача: «{title}»\nНовая категория (приоритет/срок):",
        reply_markup=_priority_kb("edit", with_date=True),
    )
    await callback.answer()


# Перенос на дату из напоминания
@router.callback_query(F.data.startswith("taskdate:"))
async def task_reassign_date(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    tid = int(callback.data.split(":", 1)[1])
    title = await _task_title(tid)
    await state.update_data(edit_id=tid, new_title=None)
    await state.set_state(TaskFSM.edit_date)
    await edit_or_send(
        callback.message,
        f"Задача: «{title}»\nНовая дата в формате ДД.ММ.ГГГГ:",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.callback_query(TaskFSM.edit_priority, F.data == "tp:edit:date")
async def task_edit_pick_date(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TaskFSM.edit_date)
    await edit_or_send(callback.message, "Новая дата задачи в формате ДД.ММ.ГГГГ:", reply_markup=cancel_kb())
    await callback.answer()


@router.message(TaskFSM.edit_date)
async def task_edit_date(message: Message, state: FSMContext) -> None:
    due = _parse_date(message.text)
    if due is None:
        await message.answer("❌ Формат ДД.ММ.ГГГГ. Повторите:", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    async with async_session_factory() as session:
        if data.get("new_title"):
            await task_service.update_task(session, data["edit_id"], title=data["new_title"])
        await task_service.set_due_date(session, data["edit_id"], due)
        await session.commit()
    await state.clear()
    await message.answer(f"✅ Задача перенесена на {due.strftime('%d.%m.%Y')}.", reply_markup=main_menu_kb())


@router.callback_query(TaskFSM.edit_priority, F.data.startswith("tp:edit:"))
async def task_edit_priority(callback: CallbackQuery, state: FSMContext) -> None:
    raw = callback.data.split(":")[2]
    priority = None if raw == "keep" else task_service.NUM_PRIORITY[int(raw)]
    data = await state.get_data()
    async with async_session_factory() as session:
        await task_service.update_task(
            session, data["edit_id"], title=data.get("new_title"), priority=priority
        )
        await session.commit()
    await state.clear()
    await edit_or_send(callback.message, "✅ Задача изменена.", reply_markup=main_menu_kb())
    await callback.answer()


# Удаление и выполнение
@router.callback_query(F.data.startswith("taskdel:"))
async def task_delete(callback: CallbackQuery) -> None:
    task_id = int(callback.data.split(":", 1)[1])
    async with async_session_factory() as session:
        await task_service.delete_task(session, task_id)
        await session.commit()
    await callback.answer("Задача удалена")
    _back = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ К списку задач", callback_data="tasks:edit")]])
    try:
        await callback.message.edit_text("🗑 Задача удалена.", reply_markup=_back)
    except Exception:
        await edit_or_send(callback.message, "🗑 Задача удалена.", reply_markup=_back)


@router.callback_query(F.data.startswith("taskdone:"))
async def task_done(callback: CallbackQuery) -> None:
    task_id = int(callback.data.split(":", 1)[1])
    async with async_session_factory() as session:
        await task_service.mark_done(session, task_id)
        await session.commit()
    await callback.answer("Задача выполнена")
    _back = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ К списку задач", callback_data="tasks:edit")]])
    try:
        await callback.message.edit_text("✅ Задача отмечена выполненной.", reply_markup=_back)
    except Exception:
        await edit_or_send(callback.message, "✅ Задача отмечена выполненной.", reply_markup=_back)


# --- Ручная отметка оплаты от арендатора -----------------------------------
@router.callback_query(F.data == "menu:pay")
async def payment_manual_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        rows = (await session.execute(
            select(Lease.id, Lease.contract_no, Tenant.name)
            .join(Tenant, Tenant.id == Lease.tenant_id)
            .where(Tenant.landlord_id == lid, Lease.status == LeaseStatus.active)
        )).all() if lid else []
    if not rows:
        await edit_or_send(callback.message, "Активных договоров нет.", reply_markup=back_kb())
        await callback.answer()
        return
    kb_rows = [
        [InlineKeyboardButton(text=f"{name} · №{contract}", callback_data=f"pm:{lease_id}")]
        for lease_id, contract, name in rows
    ]
    kb_rows.append([InlineKeyboardButton(text="◀️ В меню", callback_data="nav:home")])
    await edit_or_send(callback.message, "Выберите договор для отметки оплаты:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await callback.answer()


_CHARGE_TYPE_LABEL = {
    ChargeType.rent: "Аренда",
    ChargeType.electricity: "Электричество",
    ChargeType.penalty: "Пеня",
    ChargeType.other: "Прочее",
}


@router.callback_query(F.data.startswith("pm:"))
async def payment_manual_pick(callback: CallbackQuery, state: FSMContext) -> None:
    """После выбора договора — что оплачиваем: конкретное начисление или всю сумму."""
    lease_id = int(callback.data.split(":", 1)[1])
    await state.update_data(lease_id=lease_id)
    period = billing_service.period_start(date.today())
    async with async_session_factory() as session:
        # До-начисляем аренду (и электричество, если есть показания) за текущий месяц,
        # чтобы их можно было отметить оплаченными, даже если начисления ещё не сгенерированы.
        lease = await session.get(Lease, lease_id)
        if lease is not None:
            await billing_service.create_rent_charge(session, lease, period)
            await billing_service.create_electricity_charge(session, lease, period)
            await session.commit()
        charges = (await session.execute(
            select(Charge).where(Charge.lease_id == lease_id).order_by(Charge.period, Charge.type)
        )).scalars().all()
    rows: list[list[InlineKeyboardButton]] = []
    for c in charges:
        outstanding = c.amount - c.paid_amount
        if outstanding <= 0:
            continue
        label = f"{_CHARGE_TYPE_LABEL.get(c.type, c.type.value)} {c.period.strftime('%m.%Y')}: {outstanding} ₽"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"payc:{c.id}")])
    rows.append([InlineKeyboardButton(text="💰 Произвольная сумма", callback_data=f"paylump:{lease_id}")])
    rows.append([InlineKeyboardButton(text="◀️ В меню", callback_data="nav:home")])
    hint = "Выберите начисление к оплате или «Произвольная сумма»:" if len(rows) > 2 else \
        "Открытых начислений нет. Можно отметить произвольную сумму:"
    await edit_or_send(callback.message, hint, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("paylump:"))
async def payment_lump_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(pay_mode="lump", lease_id=int(callback.data.split(":", 1)[1]))
    await state.set_state(PayFSM.amount)
    await edit_or_send(callback.message, "Введите сумму поступившей оплаты, ₽:", reply_markup=cancel_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("payc:"))
async def payment_charge_start(callback: CallbackQuery, state: FSMContext) -> None:
    charge_id = int(callback.data.split(":", 1)[1])
    async with async_session_factory() as session:
        charge = await session.get(Charge, charge_id)
    if charge is None:
        await callback.answer("Начисление не найдено", show_alert=True)
        return
    outstanding = charge.amount - charge.paid_amount
    await state.update_data(pay_mode="charge", charge_id=charge_id)
    await state.set_state(PayFSM.amount)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Оплатить полностью ({outstanding} ₽)", callback_data=f"payfull:{charge_id}")],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="nav:home")],
    ])
    label = _CHARGE_TYPE_LABEL.get(charge.type, charge.type.value)
    await edit_or_send(
        callback.message,
        f"Оплата «{label}» за {charge.period.strftime('%m.%Y')} (остаток {outstanding} ₽).\n"
        f"Введите сумму или нажмите «Оплатить полностью»:",
        reply_markup=kb,
    )
    await callback.answer()


async def _finish_charge_payment(message: Message, tg_id: int, state: FSMContext, charge_id: int, amount: Decimal) -> None:
    async with async_session_factory() as session:
        user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
        charge = await session.get(Charge, charge_id)
        if charge is None:
            await state.clear()
            await message.answer("❌ Начисление не найдено.", reply_markup=main_menu_kb())
            return
        label = _CHARGE_TYPE_LABEL.get(charge.type, charge.type.value)
        result = await payment_service.pay_charge(
            session, charge, amount, confirmed_by_id=user.id if user else None, today=date.today()
        )
        await session.commit()
    await state.clear()
    note = "закрыто полностью" if result["fully_paid"] else f"остаток {result['remaining']} ₽"
    await message.answer(f"✅ Оплата «{label}» {result['allocated']} ₽ отмечена ({note}).", reply_markup=main_menu_kb())


@router.callback_query(F.data.startswith("payfull:"))
async def payment_charge_full(callback: CallbackQuery, state: FSMContext) -> None:
    charge_id = int(callback.data.split(":", 1)[1])
    async with async_session_factory() as session:
        charge = await session.get(Charge, charge_id)
    if charge is None:
        await callback.answer("Начисление не найдено", show_alert=True)
        return
    outstanding = charge.amount - charge.paid_amount
    await _finish_charge_payment(callback.message, callback.from_user.id, state, charge_id, outstanding)
    await callback.answer()


@router.message(PayFSM.amount)
async def payment_manual_save(message: Message, state: FSMContext) -> None:
    amount = _parse_amount(message.text)
    if amount is None or amount <= 0:
        await message.answer("❌ Введите положительную сумму. Повторите:", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    if data.get("pay_mode") == "charge":
        await _finish_charge_payment(message, message.from_user.id, state, data["charge_id"], amount)
        return
    async with async_session_factory() as session:
        user = (await session.execute(select(User).where(User.tg_id == message.from_user.id))).scalar_one_or_none()
        payment = await payment_service.register_payment(
            session, data["lease_id"], amount, payment_date=date.today(), source=DataSource.manual
        )
        await session.flush()
        # арендодатель отмечает вручную -> сразу подтверждаем
        result = await confirmation_service.process_payment_decision(
            session, payment, approve=True, user_id=user.id if user else None, today=date.today()
        )
        await session.commit()
    await state.clear()
    if result.get("fully_paid"):
        note = "начисления закрыты полностью"
    else:
        note = f"частично, остаток {result.get('remaining_debt')} ₽"
    await message.answer(f"✅ Оплата {amount} ₽ отмечена ({note}). Арендатор уведомлён.", reply_markup=main_menu_kb())


# --- Отчёты ----------------------------------------------------------------
@router.callback_query(F.data == "menu:reports")
async def reports(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session_factory() as session:
        lid = await _landlord_id(session, callback.from_user.id)
        if lid is None:
            await edit_or_send(callback.message, "Нет данных.", reply_markup=back_kb())
            await callback.answer()
            return
        status = await report_service.tenant_payment_status(session, lid, date.today())
        by_prem = await report_service.payments_by_premises(session, lid)
        elec = await report_service.electricity_summary(session, lid, date.today())

    lines = [f"<b>💳 Оплата за текущий месяц: {status['paid']} из {status['total']} договоров</b>"]
    if not status["rows"]:
        lines.append("— активных договоров нет")
    for r in status["rows"]:
        if not r["has_charges"]:
            mark = "➖"
            tail = "нет начислений"
        elif r["is_paid"]:
            mark = "✅"
            tail = f"оплачено {r['paid']} ₽"
        else:
            mark = "❌"
            tail = f"долг {r['debt']} ₽ (оплачено {r['paid']} из {r['charged']} ₽)"
        lines.append(f"{mark} {r['tenant']} · {r['premises']}: {tail}")
    if status["total_debt"] > 0:
        lines.append(f"<b>Итого долг: {status['total_debt']} ₽</b>")

    lines.append("\n<b>Подтверждённые платежи по помещениям:</b>")
    lines += [f"• {r['premises']}: {r['confirmed_total']} ₽" for r in by_prem] or ["— нет"]
    lines.append("\n<b>Электричество за текущий месяц:</b>")
    lines += [
        f"• {r['premises']}: {r['consumption_kwh']} кВт·ч = {r['amount']} ₽" for r in elec
    ] or ["— нет"]
    await edit_or_send(callback.message, "\n".join(lines), reply_markup=back_kb())
    await callback.answer()
