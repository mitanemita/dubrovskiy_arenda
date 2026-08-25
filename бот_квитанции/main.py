from aiogram import Bot, types, F
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from src.utils.init import *
from apscheduler.schedulers.asyncio import AsyncIOScheduler



#инициализация
bot = Bot(token=BOT_TOKEN)


async def on_startup():
    try:
        logger.info("Инициализация планировщика")
        scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        scheduler.add_job(
            reset_expired_subscriptions,
            'cron',
            hour=0,
            minute=0  
        )
        scheduler.start()
        logger.info("Планировщик успешно запущен")
    except Exception as e:
        logger.error(f"Ошибка планировщика: {e}")


help_text = """
<b>🆘 Помощь по боту RentalMaster</b>

<u>🌐 Основная навигация:</u>

<b>📁 Главное меню:</b>
┌ <b>Профиль</b> → Управление подпиской, балансом и данными
│ ├ Изменить подпись: <i>Профиль → "Фото подписи" → "Загрузить новую"</i>
│ └ Пополнить баланс: <i>Профиль → "Подписка" → "Пополнить"</i>
│
└ <b>Арендаторы</b> → Полное управление базой
  ├ Добавить нового: <i>Главное меню → кнопка "Добавить арендатора"</i>
  ├ Список названий: <i>Главное меню → "Список арендаторов (названия)"</i>
  └ Полная база: <i>Главное меню → "Список арендаторов (полный)"</i>

<u>📄 Работа с документами:</u>
• Выберите арендатора → кнопка "📝 Создать документ"
• Для массовой рассылки: <i>"Список арендаторов" → "📤 Массовая рассылка"</i>
• Быстрые шаблоны: 
  ─ Акт аренды
  ─ Счет за аренду
  ─ Акт электроэнергия

<code>⚡ Массовая генерация доступна для:</code>
✅ Документов без персональных дополнений
✅ Шаблонов с предустановленными данными

💎 <b>Тарифные планы</b> (Профиль → "Моя подписка"):

<code>🆓 ПРОБНЫЙ</code>
┌ Генерация: 10 док.
└ Отправка: 2 док.

<code>🟢 ПРОДВИНУТЫЙ</code>
┌ Генерация: 30 док./мес
└ Отправка: 15 док./мес

<code>💎 VIP</code>
┌ Генерация: 70 док./мес
└ Отправка: 50 док./мес

<code>🌟 PREMIUM</code>
┌ Генерация: ∞
└ Отправка: 200 док./мес

<u>🛠️ Техническая поддержка:</u>
• Разработчик: @hacker588 (все вопросы и предложения)
• Бот в активной разработке 🚧 
• Принимаем ваши шаблоны: 
  <i>Напишите в поддержку → Прикрепите .xlsx файл → Опишите логику полей</i>

<code>📌 Советы:</code>
• Используйте иконку 🖊️ для редактирования
• Список арендатора → Выбрать арендатора → расширенные опции
• Статус подписки отображается в профиле
"""
@dp.message(Command("force_reset"))
async def force_reset(message: types.Message):
    if message.from_user.id not in ALLOWED_IDS:
        return
    await reset_expired_subscriptions()
    await message.answer("Сброс подписок выполнен вручную")
    
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    logger.info(f"Пользователь {message.from_user.id} команда - /start")
    user = message.from_user
    conn = None

    try:
        conn = await get_db_connection()
        if not conn:
            await message.answer("❌ Ошибка подключения к базе данных ")
            return

        async with conn.cursor() as cursor:
            await cursor.execute("SELECT id FROM users WHERE host_id = %s", (user.id,))
            result = await cursor.fetchone()
            logger.debug(f"Результат проверки пользователя: {result}")

            if not result:
                await start_adding_user1(message, state)
            else:
                await message.answer("👋 С возвращением!", reply_markup=menu_keyboard())

    except Exception as e:
        logger.fatal(f"Критическая ошибка: {e} после /start", exc_info=True)
        await message.answer("❌ Произошла внутренняя ошибка. Попробуйте позже.")
    finally:
        if conn:
            try:
                if not conn.closed:
                    await conn.ensure_closed()
            except Exception as close_error:
                logger.error(f"Ошибка при закрытии соединения: {close_error}")


async def cmd_help(message):
    await message.answer(help_text, parse_mode="HTML")

BUTTON_HANDLERS = {
    "sdg": cmd_start,
    "Помощь": cmd_help
}

BUTTON_HANDLERS_WITH_STATE = {
    "Список арендаторов (полный)": get_all_tenants,
    "Список арендаторов (названия)": list_tenants,
    "Добавить арендатора": start_adding_tenant,
    "Меню": cmd_start
}

@dp.message(F.text.in_(BUTTON_HANDLERS))
async def handle_buttons(message: types.Message):
    logger.debug(f"Пользователь {message.from_user.id} обработка кнопки {message.text}")
    handler = BUTTON_HANDLERS[message.text]
    await handler(message)

@dp.message(F.text.in_(BUTTON_HANDLERS_WITH_STATE))
async def handle_buttons_with_state(message: types.Message, state: FSMContext):
    logger.debug(f"Пользователь {message.from_user.id} обработка кнопки {message.text}")
    handler = BUTTON_HANDLERS_WITH_STATE[message.text]
    await handler(message, state)

@dp.message(F.text)
async def handle_unknown_message(message: types.Message):
    logger.warning(f"Нераспознанное сообщение от {message.from_user.id}: {message.text}")
    await message.answer(
        "⚠️ Команда не распознана\n"
        "Используйте кнопки меню или введите /help для списка команд",
        reply_markup=menu_keyboard()
    ) 
    

if __name__ == "__main__":
    dp.startup.register(on_startup)
    logger.info("Starting bot...")
    dp.run_polling(bot)