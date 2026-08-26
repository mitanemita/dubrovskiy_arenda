"""Запуск Telegram-бота: polling + фоновая доставка уведомлений из очереди."""
from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot import notifier
from app.bot.handlers import router
from app.config import get_settings
from app.db.base import async_session_factory
from app.utils.logger import logger

# Интервал проверки очереди TG-уведомлений, сек
DISPATCH_INTERVAL = 15


def build_bot() -> Bot:
    return Bot(
        token=get_settings().bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def _dispatch_loop(bot: Bot) -> None:
    """Периодически отправляет очередные TG-уведомления."""
    async def _send(chat_id: int, text: str, reply_markup=None) -> None:
        await bot.send_message(chat_id, text, reply_markup=reply_markup)

    while True:
        try:
            async with async_session_factory() as session:
                stats = await notifier.dispatch_telegram(session, _send)
                await session.commit()
                if stats["sent"] or stats["failed"]:
                    logger.info("TG-уведомления: %s", stats)
        except Exception:
            logger.exception("Ошибка цикла доставки TG-уведомлений")
        await asyncio.sleep(DISPATCH_INTERVAL)


async def run() -> None:
    """Точка входа бота (polling + фоновая доставка)."""
    bot = build_bot()
    dp = Dispatcher()
    dp.include_router(router)

    dispatch_task = asyncio.create_task(_dispatch_loop(bot))
    logger.info("Бот запущен (polling)")
    try:
        await dp.start_polling(bot)
    finally:
        dispatch_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run())
