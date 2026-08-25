from aiogram import types
from aiogram.fsm.context import FSMContext
from src.utils import logger, config, database
from src.utils.keyboards import *
from src.handlers.utils.states import SurveyStates
from src.handlers.document_handlers import send_documents
from aiogram.types import ReplyKeyboardRemove
from aiogram.filters import StateFilter
from src.handlers.get_ten import *

async def change_tenant_data(user_id: int, column: str, value: any, tenant_id: int) -> bool:
    logger.debug(f"Updating tenant {tenant_id} by user {user_id}, field: {column}, value: {value}")
    conn = None
    try:
        conn = await database.get_db_connection()
        async with conn.cursor() as cursor:
            query = f"UPDATE tenants SET {column} = %s WHERE id = %s"
            await cursor.execute(query, (value, tenant_id))
            await conn.commit()
            return True
    except Exception as e:
        logger.error(f"Database error: {str(e)}")
        return False
    finally:
        if conn:
            await conn.ensure_closed()
        logger.debug(f"Update completed for tenant {tenant_id}")

@config.dp.message(lambda message: message.text and message.text.lower() == "выбрать арендатора")
async def manage_tenants_start(message: types.Message, state: FSMContext):
    logger.debug(f"User {message.from_user.id} started tenant management")
    await list_tenants(message, state)
    await message.answer("📋 Введите номер арендатора из списка:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(SurveyStates.SELECT_TENANT)

@config.dp.message(StateFilter(SurveyStates.SELECT_TENANT))
async def handle_tenant_id_input(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    input_number = message.text.strip()
    logger.debug(f"User {user_id} ввел номер: {input_number}")

    if not input_number.isdigit():
        await message.answer("❌ Номер должен быть числом! Повторите ввод:")
        return

    state_data = await state.get_data()
    tenant_ids = state_data.get("tenant_ids", [])

    try:
        index = int(input_number) - 1
        if index < 0 or index >= len(tenant_ids):
            raise ValueError
        tenant_id = tenant_ids[index]
    except (ValueError, IndexError):
        await message.answer("❌ Неверный номер! Повторите ввод:")
        return

    conn = await database.get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT id FROM tenants WHERE id = %s", (tenant_id,))
            result = await cursor.fetchone()
            
            if not result:
                await message.answer("❌ Арендатор не найден! Повторите ввод:")
                return
            
        await state.update_data(tenant_id=tenant_id)
        logger.debug(f"Выбран арендатор ID {tenant_id}")
        await message.answer("🔧 Выберите действие:", reply_markup=tenant_actions_keyboard())
        await state.set_state(SurveyStates.TENANT_ACTION)
        
    except Exception as e:
        logger.error(f"Ошибка при поиске арендатора: {str(e)}")
        await message.answer("⚠️ Произошла ошибка, попробуйте позже")
    finally:
        if conn:
            await conn.ensure_closed()

@config.dp.message(StateFilter(SurveyStates.TENANT_ACTION))
async def handle_tenant_action(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    action = message.text
    logger.debug(f"User {user_id} selected action: {action}")

    if action == "✏️ Изменить":
        logger.debug(f"User {user_id} entered edit mode")
        await message.answer("📝 Выберите поле для изменения:", reply_markup=edit_tenant_fields_keyboard())
        await state.set_state(SurveyStates.TENANT_ACTION1)
    elif action == "🗑️ Удалить":
        user_data = await state.get_data()
        tenant_id = user_data.get('tenant_id')
        logger.debug(f"User {user_id} initiated deletion of tenant {tenant_id}")
        await message.answer(
            f"⚠️ Вы уверены, что хотите удалить арендатора ID {tenant_id}?",
            reply_markup=confirm_delete_keyboard()
        )
        await state.set_state(SurveyStates.CONFIRM_DELETE)
    elif action == "📄 Сформировать документ":
        logger.debug(f"User {user_id} selected document generation")
        await message.answer("📂 Выберите тип документа:", reply_markup=document_types_keyboard())
        await state.set_state(SurveyStates.SELECT_DOCUMENT_TYPE)
    elif action == "↩️ Назад":
        logger.debug(f"User {user_id} returned to main menu")
        await list_tenants(message)
        await state.clear()
    else:
        logger.warning(f"User {user_id} sent unknown action: {message.text}")
        await message.answer("❌ Пожалуйста, используйте кнопки для выбора действия")

@config.dp.message(StateFilter(SurveyStates.CONFIRM_DELETE))
async def handle_delete_confirmation(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = await state.get_data()
    tenant_id = user_data.get('tenant_id')
    logger.debug(f"User {user_id} confirmation: {message.text} for tenant {tenant_id}")

    if message.text == "✅ Да, удалить":
        conn = await database.get_db_connection()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
                await conn.commit()
                logger.info(f"Tenant {tenant_id} deleted by user {user_id}")
                await message.answer(f"✅ Арендатор ID {tenant_id} успешно удален!", reply_markup=menu_keyboard())
        except Exception as e:
            logger.error(f"Delete error: {str(e)}")
            await message.answer("❌ Произошла ошибка при удалении")
        finally:
            await conn.ensure_closed()
            await state.clear()
    else:
        logger.debug(f"User {user_id} cancelled deletion")
        await message.answer("❌ Удаление отменено", reply_markup=tenant_actions_keyboard())
        await state.set_state(SurveyStates.TENANT_ACTION)