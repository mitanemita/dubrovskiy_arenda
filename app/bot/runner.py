"""Запуск Telegram-бота: polling + фоновая доставка уведомлений из очереди."""
from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import ErrorEvent

from app.bot import notifier
from app.bot.handlers import router
from app.bot.handlers_admin import router as admin_router
from app.bot.handlers_admin_tools import router as admin_tools_router
from app.bot.handlers_directory import router as directory_router
from app.config import get_settings
from app.db.base import async_session_factory
from app.email.sender import send_email
from app.services import email_service
from app.utils.logger import logger

# Интервал проверки очереди TG-уведомлений, сек
DISPATCH_INTERVAL = 15


def _ui_version() -> str:
    from app.bot.handlers import BOT_UI_VERSION

    return BOT_UI_VERSION


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
                tg_stats = await notifier.dispatch_telegram(session, _send)
                email_stats = await email_service.dispatch_email(session, send_email)
                await session.commit()
                if any(tg_stats.values()):
                    logger.info("TG-уведомления: %s", tg_stats)
                if any(email_stats.values()):
                    logger.info("Email-уведомления: %s", email_stats)
        except Exception:
            logger.exception("Ошибка цикла доставки уведомлений")
        await asyncio.sleep(DISPATCH_INTERVAL)


async def run() -> None:
    """Точка входа бота (polling + фоновая доставка)."""
    # Идемпотентная первичная инициализация: арендодатель + владельцы + настройки.
    # Гарантирует, что разделы бота не падают с «Нет арендодателя» на чистой БД.
    try:
        from app.scripts.bootstrap import bootstrap

        await bootstrap()
    except Exception:
        logger.exception("Не удалось выполнить первичную инициализацию (bootstrap)")

    bot = build_bot()
    dp = Dispatcher()
    dp.include_router(router)
    dp.include_router(admin_router)
    dp.include_router(admin_tools_router)
    dp.include_router(directory_router)

    @dp.error()
    async def on_error(event: ErrorEvent) -> None:
        """Глобальный обработчик: логируем ошибку и не оставляем чат «немым».

        Сбрасываем зависшее FSM-состояние и сообщаем пользователю, чтобы бот
        всегда отвечал, даже если конкретный хендлер упал.
        """
        logger.exception("Необработанная ошибка в хендлере: %s", event.exception)
        upd = event.update
        msg = upd.message or (upd.callback_query.message if upd.callback_query else None)
        frm = upd.message.from_user if upd.message else (
            upd.callback_query.from_user if upd.callback_query else None
        )
        if msg is None or frm is None:
            return
        try:
            key = StorageKey(bot_id=bot.id, chat_id=msg.chat.id, user_id=frm.id)
            await FSMContext(storage=dp.storage, key=key).clear()
        except Exception:
            logger.exception("Не удалось сбросить состояние после ошибки")
        try:
            await msg.answer("⚠️ Произошла ошибка. Действие отменено, попробуйте снова: /start")
        except Exception:
            logger.exception("Не удалось уведомить пользователя об ошибке")

    dispatch_task = asyncio.create_task(_dispatch_loop(bot))
    logger.info("Бот запущен (polling), версия UI: %s", _ui_version())
    try:
        # drop_pending_updates — чистим backlog (в т.ч. после сбоя/двойного запуска).
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        dispatch_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run())
