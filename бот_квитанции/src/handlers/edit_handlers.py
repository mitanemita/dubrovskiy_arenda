import re
from datetime import datetime
from aiogram import types
from aiogram.fsm.context import FSMContext
from src.handlers.utils.states import SurveyStates
from src.utils import logger
from src.handlers.tenent_handlers import change_tenant_data
from src.utils.keyboards import *
from src.utils.config import *
from aiogram.types import ReplyKeyboardRemove
from aiogram.filters import StateFilter


@dp.message(StateFilter(SurveyStates.TENANT_ACTION1), lambda message: message.text.startswith("✏️") or message.text == "↩️ Назад")
async def handle_edit_selection(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    logger.debug(f"User {user_id} clicked button: '{message.text}'")

    if message.text == "↩️ Назад":
        await state.set_state(SurveyStates.TENANT_ACTION)
        await message.answer("🔧 Выберите действие:", reply_markup=tenant_actions_keyboard())
        return

    try:
        _, field = message.text.split(maxsplit=1)
        field = field.strip()
        logger.debug(f"Processing field: '{field}'")
    except Exception as e:
        logger.error(f"Error parsing button text: {str(e)}")
        await message.answer("❌ Ошибка обработки команды")
        return

    handlers = {
        "Название": edit_tenant_name_handler,
        "Email": edit_tenant_email_handler,
        "Тип": edit_tenant_type_handler,
        "Сумма аренды": edit_rent_amount_handler,
        "ИНН": edit_inn_handler,
        "Номер договора": edit_dog_num_handler,
        "Дата договора": edit_dog_date_handler,
        "Реквизиты": edit_recw_inf_handler
    }

    handler = handlers.get(field)
    
    if handler:
        logger.debug(f"Handler found for field: {field}")
        await handler(message, state)
    else:
        logger.warning(f"Unknown field '{field}' selected by user {user_id}")
        await message.answer("❌ Неизвестное поле для редактирования")

@dp.message(StateFilter(SurveyStates.EDIT_TENANT))
async def handle_tenant_edit(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = await state.get_data()
    tenant_id = user_data.get('tenant_id')
    column = user_data.get('column')
    logger.debug(f"User {user_id} editing {column} for tenant {tenant_id}")

    try:
        validator = user_data.get('validator')
        value = validator(message.text) if validator else None
        
        if not value:
            logger.warning(f"Validation failed for user {user_id}, input: {message.text}")
            raise ValueError
            
        success = await change_tenant_data(user_id, column, value, tenant_id)

        if success:
            logger.info(f"Tenant {tenant_id} updated by user {user_id}, field: {column}")
            await message.answer("✅ Данные арендатора успешно обновлены!", reply_markup=tenant_actions_keyboard())
            await state.set_state(SurveyStates.TENANT_ACTION)
        else:
            logger.error(f"Update failed for tenant {tenant_id} by user {user_id}")
            await message.answer("❌ Ошибка при обновлении!", reply_markup=tenant_actions_keyboard())

    except ValueError:
        logger.warning(f"Invalid input from user {user_id}: {message.text}")
        await message.answer("❌ Некорректный формат данных! Повторите ввод:")
    except Exception as e:
        logger.error(f"Error during edit: {str(e)}")
        await message.answer("⚠️ Произошла ошибка, попробуйте позже")
        await state.clear()

async def edit_tenant_name_handler(message: types.Message, state: FSMContext):
    await state.update_data(column="name", validator=lambda x: x.strip() if x.strip() else False)
    await message.answer("🏢 Введите новое название организации:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(SurveyStates.EDIT_TENANT)

async def edit_tenant_email_handler(message: types.Message, state: FSMContext):
    await state.update_data(
        column="email",
        validator=lambda x: x if re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', x) else False
    )
    await message.answer("📧 Введите новый email:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(SurveyStates.EDIT_TENANT)

async def edit_tenant_type_handler(message: types.Message, state: FSMContext):
    await state.update_data(
        column="type",
        validator=lambda x: x if x in ['ИП', 'ООО', 'АО', 'Физ. лицо'] else False
    )
    await message.answer("🏛️ Выберите тип организации:", reply_markup=type_keyboard())
    await state.set_state(SurveyStates.EDIT_TENANT)

async def edit_rent_amount_handler(message: types.Message, state: FSMContext):
    await state.update_data(
        column="rent_amount",
        validator=lambda x: float(x.replace(',', '.')) if re.match(r'^\d+([.,]\d{1,2})?$', x) else False
    )
    await message.answer("💰 Введите новую сумму аренды:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(SurveyStates.EDIT_TENANT)

async def edit_inn_handler(message: types.Message, state: FSMContext):
    await state.update_data(
        column="inn",
        validator=lambda x: x if re.match(r'^\d{10,12}$', x) else False
    )
    await message.answer("📄 Введите ИНН (10 или 12 цифр):", reply_markup=ReplyKeyboardRemove())
    await state.set_state(SurveyStates.EDIT_TENANT)

async def edit_dog_num_handler(message: types.Message, state: FSMContext):
    await state.update_data(
        column="dog_num",
        validator=lambda x: x.strip() if x.strip() else False
    )
    await message.answer("📝 Введите новый номер договора:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(SurveyStates.EDIT_TENANT)

async def edit_dog_date_handler(message: types.Message, state: FSMContext):
    await state.update_data(
        column="dog_dat",
        validator=lambda x: datetime.strptime(x, "%d.%m.%Y").strftime("%Y-%m-%d") if re.match(r'\d{2}\.\d{2}\.\d{4}', x) else False
    )
    await message.answer("📅 Введите новую дату договора (ДД.ММ.ГГГГ):", reply_markup=ReplyKeyboardRemove())
    await state.set_state(SurveyStates.EDIT_TENANT)

async def edit_recw_inf_handler(message: types.Message, state: FSMContext):
    await state.update_data(
        column="recw_inf",
        validator=lambda x: x.strip() if x.strip() else False
    )
    await message.answer("🏦 Введите новые реквизиты:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(SurveyStates.EDIT_TENANT)