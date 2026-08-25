from src.utils.config import DB_HOST, DB_NAME, DB_USER, DB_PASSWORD
import aiomysql
import traceback 
from src.utils.logger import logger

async def get_db_connection():
    logger.debug('Подключение к базе данных...')
    try:
        conn = await aiomysql.connect(
            host=DB_HOST, 
            user=DB_USER,
            password=DB_PASSWORD,
            db=DB_NAME,
            cursorclass=aiomysql.DictCursor
        )
        logger.debug('Подключение к базе данных прошло успешно')
        return conn
    except Exception as e:
        logger.error(f"Ошибка подключения к базе данных: {e}")
        return None 
    
async def reset_expired_subscriptions():
    logger.info("Запуск задания по сбросу подписок")
    conn = None  
    try:
        conn = await get_db_connection()
        if not conn:
            logger.error("❌ Не удалось подключиться к базе данных")
            return

        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE users "
                "SET access_level = 0, stat = NULL "
                "WHERE stat < CURRENT_DATE"
            )
            await conn.commit()
            logger.info(f"✅ Обновлено записей: {cursor.rowcount}")

    except Exception as e:
        logger.error(f"🔥 Ошибка при сбросе: {traceback.format_exc()}")
    finally:
        if conn: await conn.ensure_closed()