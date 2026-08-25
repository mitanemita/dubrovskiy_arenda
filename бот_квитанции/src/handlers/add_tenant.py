from aiogram.types import ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from aiogram.types import Message
from src.utils.keyboards import *
from src.utils.config import dp
from src.utils.logger import logger
from src.utils.database import get_db_connection
import re
from datetime import datetime
from src.handlers.utils.states import AddTenant

async def start_adding_tenant(message: Message, state: FSMContext):
    user_id = message.from_user.id
    logger.info(f"[{user_id}] /add_tenant - Начало добавления арендатора")
    await state.set_state(AddTenant.name)
    await message.answer(
        "✨ <b>Добро пожаловать в систему регистрации арендатора!</b> ✨\n\n"
        "▫️ Введите полное наименование организации или ФИО:\n"
        "————————————————\n"
        "🏢 <i>Пример для ООО:</i> ТД Велес\n"
        "👤 <i>Пример для ИП:</i> Мелякин Иван Владимирович",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML"
    )

@dp.message(StateFilter(AddTenant.name))
async def process_name(message: Message, state: FSMContext):
    user_id = message.from_user.id
    name = message.text
    logger.debug(f"[{user_id}] /add_tenant - Введено имя: {name}")
    await state.update_data(name=name)
    await state.set_state(AddTenant.email)
    await message.answer(
        "📩 Введите <b>контактный email</b> арендатора:\n"
        "————————————————\n"
        "<i>Пример:</i> office@company.ru",
        parse_mode="HTML"
    )

@dp.message(StateFilter(AddTenant.email))
async def process_email(message: Message, state: FSMContext):
    user_id = message.from_user.id
    email = message.text
    if not validate_email(email):
        logger.warning(f"[{user_id}] /add_tenant - Невалидный email: {email}")
        await message.answer(
            "❌ <b>Неверный формат email!</b>\n"
            "Пожалуйста, введите в формате: name@example.com",
            parse_mode="HTML"
        )
        return
    
    logger.debug(f"[{user_id}] /add_tenant - Введен email: {email}")
    await state.update_data(email=email)
    await state.set_state(AddTenant.type)
    await message.answer(
        "📋 Выберите <b>тип арендатора</b>:",
        reply_markup=type_keyboard(),
        parse_mode="HTML"
    )

@dp.message(StateFilter(AddTenant.type))
async def process_type(message: Message, state: FSMContext):
    user_id = message.from_user.id
    tenant_type = message.text
    logger.debug(f"[{user_id}] /add_tenant - Выбран тип: {tenant_type}")
    await state.update_data(type=tenant_type)
    
    await state.set_state(AddTenant.rent)
    await message.answer(
        "💰 Введите <b>ежемесячную сумму аренды</b>:\n"
        "————————————————\n"
        "<i>Пример:</i> 15000.50",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML"
    )

@dp.message(StateFilter(AddTenant.rent))
async def process_rent(message: Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        rent_amount = float(message.text)
        logger.debug(f"[{user_id}] /add_tenant - Введена сумма: {rent_amount}")
    except ValueError:
        logger.warning(f"[{user_id}] /add_tenant - Неверный формат суммы: {message.text}")
        await message.answer(
            "❌ <b>Неверный формат суммы!</b>\n"
            "Используйте числовой формат с точкой для копеек\n"
            "<i>Пример:</i> 12345.67",
            parse_mode="HTML"
        )
        return
    
    await state.update_data(rent=rent_amount)
    await state.set_state(AddTenant.inn)
    await message.answer(
        "🔢 Введите <b>ИНН арендатора</b>:\n"
        "————————————————\n"
        "🏢 Для Юр.лица — 10 цифр\n"
        "👤 Для Физ.лица — 12 цифр",
        parse_mode="HTML"
    )

@dp.message(StateFilter(AddTenant.inn))
async def process_inn(message: Message, state: FSMContext):
    user_id = message.from_user.id
    inn = message.text
    data = await state.get_data()
    
    if data['type'] == 2 and (len(inn) != 10 or not inn.isdigit()):
        await message.answer(
            "❌ <b>Некорректный ИНН!</b>\n"
            "Для Юр.лица должно быть ровно 10 цифр",
            parse_mode="HTML"
        )
        return
    elif data['type'] == 1 and (len(inn) != 12 or not inn.isdigit()):
        await message.answer(
            "❌ <b>Некорректный ИНН!</b>\n"
            "Для Физ.лица должно быть ровно 12 цифр",
            parse_mode="HTML"
        )
        return
    
    await state.update_data(inn=inn)
    await state.set_state(AddTenant.dog_num)
    await message.answer(
        "📄 Введите <b>номер договора</b>:\n"
        "————————————————\n"
        "<i>Пример:</i> №123/2023-АР",
        parse_mode="HTML"
    )

@dp.message(StateFilter(AddTenant.dog_num))
async def process_dog_num(message: Message, state: FSMContext):
    await state.update_data(dog_num=message.text)
    await state.set_state(AddTenant.adr_tow)
    await message.answer(
        "🏭 Введите <b>адрес размещения товара</b>:\n"
        "————————————————\n"
        "<i>Пример:</i> г. Москва, ул. Промышленная, д. 15, склад №3",
        parse_mode="HTML"
    )

@dp.message(StateFilter(AddTenant.adr_tow))
async def process_adr_tow(message: Message, state: FSMContext):
    await state.update_data(adr_tow=message.text)
    await state.set_state(AddTenant.dog_dat)
    await message.answer(
        "📅 Введите <b>дату заключения договора</b>:\n"
        "————————————————\n"
        "<i>Формат:</i> ДД.ММ.ГГГГ\n"
        "<i>Пример:</i> 15.01.2023",
        parse_mode="HTML"
    )

@dp.message(StateFilter(AddTenant.dog_dat))
async def process_dog_dat(message: Message, state: FSMContext):
    user_date = message.text
    
    if not re.match(r'\d{2}\.\d{2}\.\d{4}', user_date):
        await message.answer(
            "❌ <b>Неверный формат даты!</b>\n"
            "Используйте формат: ДД.ММ.ГГГГ\n"
            "<i>Пример:</i> 01.01.2023",
            parse_mode="HTML"
        )
        return

    try:
        date_obj = datetime.strptime(user_date, "%d.%m.%Y")
        mysql_date = date_obj.strftime("%Y-%m-%d")
    except ValueError:
        await message.answer(
            "❌ <b>Некорректная дата!</b>\n"
            "Проверьте правильность введенной даты",
            parse_mode="HTML"
        )
        return

    await state.update_data(dog_dat=mysql_date)
    await state.set_state(AddTenant.recw_inf)
    await message.answer(
        "💳 Введите <b>платежные реквизиты</b>:\n"
        "————————————————\n"
        "🏦 <i>Пример для Юр.лица:</i>\n"
        "р/с 40702810611010583455 БИК 044525058 в Филиал 'БИЗНЕС'ПАО 'СОВКОМБАНК' к/с 30101810045250000058\n"
        "💼 <i>Пример для Физ.лица:</i>\n"
        "ул. Образцова, д. 3, кв. 15",
        parse_mode="HTML"
    )

@dp.message(StateFilter(AddTenant.recw_inf))
async def process_recw_inf(message: Message, state: FSMContext):
    user_id = message.from_user.id
    await state.update_data(recw_inf=message.text)
    data = await state.get_data()
    
    try:
        conn = await get_db_connection()
        async with conn.cursor() as cursor:
            await cursor.execute(
                """INSERT INTO tenants 
                (name, email, type, rent_amount, inn, 
                 dog_num, adr_tow, dog_dat, recw_inf, host_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    data['name'], data['email'], data['type'], data['rent'],
                    data['inn'], data['dog_num'], 
                    data['adr_tow'], data['dog_dat'], data['recw_inf'], user_id
                )
            )
            await conn.commit()
            
        await message.answer(
            "✅ <b>Арендатор успешно зарегистрирован!</b>\n\n"
            "Все данные сохранены в системе.\n"
            "Для продолжения работы воспользуйтесь меню ниже.",
            reply_markup=menu_keyboard(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"[{user_id}] Ошибка добавления: {str(e)}")
        await message.answer(
            "❌ <b>Ошибка сохранения данных!</b>\n"
            "Попробуйте повторить операцию позже.",
            reply_markup=menu_keyboard(),
            parse_mode="HTML"
        )
    finally:
        await state.clear()
        if conn: await conn.ensure_closed()

def validate_email(email: str) -> bool:
    result = re.match(r"[^@]+@[^@]+\.[^@]+", email) is not None
    if not result:
        logger.debug(f"Валидация email не пройдена: {email}")
    return result