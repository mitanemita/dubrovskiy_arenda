from src.utils.database import get_db_connection
from src.utils.logger import logger

async def check_subscription(user_id: int) -> bool:
    """Проверяет активную подписку пользователя"""
    conn = None
    try:
        conn = await get_db_connection()
        if not conn:
            return False
            
        async with conn.transaction():
            result = await conn.fetchrow(
                """SELECT stat FROM users
                WHERE host_id = %s AND stat >= CURRENT_DATE""",
                user_id
            )
            
            return bool(result)

    except Exception as e:
        logger.error(f"Ошибка проверки подписки: {str(e)}")
        return False
        
    finally:
        if conn and not conn.is_closed():
            await conn.close()