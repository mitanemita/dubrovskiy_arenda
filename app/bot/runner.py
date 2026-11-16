"""Запуск Telegram-бота: polling + планировщик + фоновая доставка уведомлений из очереди."""
from __future__ import annotations

import asyncio

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import ErrorEvent

from app.bot import notifier
from app.bot.access import AccessMiddleware
from app.bot.handlers import router
from app.bot.handlers_admin import router as admin_router
from app.bot.handlers_directory import router as directory_router
from app.config import get_settings
from app.db.base import async_session_factory
from app.scheduler.runner import build_scheduler
from app.utils.logger import logger
from app.version import version_line

# Интервал проверки очереди TG-уведомлений, сек
DISPATCH_INTERVAL = 15


class FSMCleanupMiddleware(BaseMiddleware):
    """Удаляет сообщение пользователя, если оно было вводом внутри мастера (FSM).

    В связке с мастерами «в одном сообщении» (wiz_show) это убирает спам:
    и подсказки бота (правятся на месте), и введённые пользователем значения.
    """

    async def __call__(self, handler, event, data):
        state = data.get("state")
        active = bool(state and await state.get_state())
        result = await handler(event, data)
        if active:
            try:
                await event.delete()
            except Exception:
                pass
        return result


def build_bot() -> Bot:
    return Bot(
        token=get_settings().bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def _dispatch_loop(bot: Bot) -> None:
    """Периодически отправляет очередные TG-уведомления."""
    async def _send(chat_id: int, text: str) -> None:
        await bot.send_message(chat_id, text)

    while True:
        try:
            async with async_session_factory() as session:
                tg_stats = await notifier.dispatch_telegram(session, _send)
                await session.commit()
                if any(tg_stats.values()):
                    logger.info("TG-уведомления: %s", tg_stats)
        except Exception:
            logger.exception("Ошибка цикла доставки уведомлений")
        await asyncio.sleep(DISPATCH_INTERVAL)


async def run() -> None:
    """Точка входа бота (polling + планировщик + фоновая доставка)."""
    # Идемпотентная первичная инициализация: арендодатель + владельцы + настройки.
    # Гарантирует, что разделы бота не падают с «Нет арендодателя» на чистой БД.
    try:
        from app.scripts.bootstrap import bootstrap

        await bootstrap()
    except Exception:
        logger.exception("Не удалось выполнить первичную инициализацию (bootstrap)")

    bot = build_bot()
    dp = Dispatcher()
    # Доступ проверяется до фильтров и хендлеров — и для сообщений, и для кнопок.
    dp.message.outer_middleware(AccessMiddleware())
    dp.callback_query.outer_middleware(AccessMiddleware())
    dp.message.middleware(FSMCleanupMiddleware())
    dp.include_router(router)
    dp.include_router(admin_router)
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

    # Планировщик: начисления 1-го числа, ежедневно — пеня и напоминания по задачам.
    scheduler = build_scheduler()
    scheduler.start()
    dispatch_task = asyncio.create_task(_dispatch_loop(bot))
    logger.info("Бот и планировщик запущены (polling), версия: %s", version_line())
    try:
        # drop_pending_updates — чистим backlog (в т.ч. после сбоя/двойного запуска).
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        scheduler.shutdown(wait=False)
        dispatch_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run())
