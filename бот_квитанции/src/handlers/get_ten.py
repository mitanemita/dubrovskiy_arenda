from src.utils.database import get_db_connection
from src.utils.logger import logger
from src.utils.keyboards import *
from aiogram import types


async def get_all_tenants(message, state):
    user_id = int(message.from_user.id)
    logger.debug(f'Пользователь {message.from_user.id} команда /get_all_tenants')
    try:
        conn = await get_db_connection()
        if not conn:
            logger.error("Ошибка подключения к базе данных.")
            return False
    
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT 
                    id, name, email, type, rent_amount, 
                    inn, dog_num, adr_tow, 
                    dog_dat, recw_inf
                FROM tenants WHERE host_id = %s
            """, (user_id,))
            results = await cursor.fetchall()
            tenant_ids = [tenant['id'] for tenant in results]
            if state:
                await state.update_data(tenant_ids=tenant_ids)
            if not results:
                await message.answer('Список арендаторов пуст.')
            else:
                response = "<b>📋 Полный список арендаторов</b>\n\n"
                for idx, record in enumerate(results, 1):
                    
                    response += (
                        f"<b>🔹№ {idx} - {record['name']}</b>\n"
                        f"├ Тип: {record['type']}\n"
                        f"├ Email: {record['email']}\n"
                        f"├ ИНН: {record['inn']}\n"
                        f"├ Договор №: {record['dog_num']}\n"
                        f"├ Дата договора: {record['dog_dat']}\n"
                        f"├ Реквизиты: {record['recw_inf']}\n"
                        f"├ Аренда: {record['rent_amount']} руб.\n"
                        f"├ Товарный адрес: {record['adr_tow']}\n"
                        f"\n{'-'*40}\n"
                    )
                
                await message.answer(
                    response,
                    parse_mode="HTML",
                    reply_markup=spis_keyboard(),
                    disable_web_page_preview=True
                )
    except Exception as e:
        logger.error(f'Ошибка в команде /get_all_tenants: {e}')
        await message.answer(
            "❌ Произошла внутренняя ошибка. Попробуйте позже.", 
            reply_markup=menu_keyboard()
        )
    finally:
        if conn:
            await conn.ensure_closed()

async def list_tenants(message: types.Message, state = None):
    user_id = message.from_user.id
    logger.info(f"Пользователь {message.from_user.id} запросил список арендаторов")
    
    try:
        conn = await get_db_connection()
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT id, name FROM tenants WHERE host_id = %s ORDER BY id", (user_id,))
            tenants = await cursor.fetchall()
            
            if not tenants:
                await message.answer("📭 Список арендаторов пуст")
                return
            tenant_ids = [tenant['id'] for tenant in tenants]
            if state:
                await state.update_data(tenant_ids=tenant_ids)
            response = ["<b>📃 Список арендаторов:</b>\n"]
            for idx, tenant in enumerate(tenants, 1):
                response.append(f"<b>{idx}.</b>  {tenant['name']}")
            
            await message.answer(
                '\n'.join(response),
                parse_mode="HTML",
                reply_markup=spis_keyboard()
            )
            
    except Exception as e:
        logger.error(f"Ошибка при получении списка: {e}")
        await message.answer("❌ Не удалось загрузить список арендаторов")
    finally:
        if conn:
            await conn.ensure_closed()