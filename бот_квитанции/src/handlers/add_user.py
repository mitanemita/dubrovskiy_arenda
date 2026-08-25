from aiogram.types import ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from aiogram.types import Message
from src.utils.keyboards import *
from src.utils.config import dp
from src.utils.logger import logger
from src.utils.database import get_db_connection
from src.handlers.utils.states import AddUser
welcome_text = """
<b>🏠 Добро пожаловать в RentalMaster! 🌟</b>

<u>Ваш цифровой помощник для управления арендной документацией</u>

🚀 <b>Что вы сможете делать:</b>
✅ Генерировать документы за 3 клика
✅ Отправлять PDF напрямую арендаторам
✅ Использовать электронную подпись
✅ Работать с группой объектов
✅ Автоматизировать рутину

✨ <b>Главные преимущества:</b>
▫️ <i>Мгновенная конвертация</i> в PDF с водяным знаком
▫️ <i>Умное хранилище</i> для всех ваших документов
▫️ <i>Шаблоны-заготовки</i> для разных типов договоров
▫️ <i>Напоминания</i> об оплате и продлении

📌 <b>С чего начать?</b>
1. <b>Заполните профиль</b>
   (ваши реквизиты и контакты)
2. <b>Добавьте арендаторов</b> — добавить арендатора
   (реквизиты и контакты арендатора)
3. <b>Настройте подпись</b> - профиль - фото подписи
   (фото)

📬 <b>Система отправки документов:</b>
• Авто-проверка email арендаторов
• В случае если у арендатора нет почты\n
  укажите свой email для получения готовых документов
• История отправленных документов

💎 <b>Тарифные планы:</b>

<code>🆓 ПРОБНЫЙ</code>
┌ Генерация: 10 док.
└ Отправка: 2 док.

<code>🟢 ПРОДВИНУТЫЙ</code>
┌ Генерация: 30 док.
└ Отправка: 15 док.

<code>💎 VIP</code>
┌ Генерация: 70 док.
└ Отправка: 50 док.

<code>🌟 PREMIUM</code>
┌ Генерация: ∞
└ Отправка: 200 док.

🔍 <b>Важно знать:</b>
• Лимиты обновляются при покупке подписки
• Можно сменить тариф в любой момент
• Все данные хранятся в зашифрованном виде
• Техподдержка @dmitriydubr

📌 <b>Первые шаги:</b>
1. Заполните ваши данные
2. Добавьте арендатора
3. Выберите его из списка
4. Выберите тип документа из списка

👉 <i>Нужна помощь?</i> Нажмите "Помощь" в меню!
"""

async def start_adding_user1(message: Message, state: FSMContext):
    await message.answer(welcome_text, parse_mode='HTML')
    await start_adding_user(message, state)
async def start_adding_user(message: Message, state: FSMContext):
    user_id = message.from_user.id
    logger.info(f"User {user_id} initiated tenant addition process")
    await state.set_state(AddUser.name)
    await message.answer(
        "✨ <b>Добро пожаловать в систему регистрации арендодателя!</b> ✨\n\n"
        "▫️ Для начала работы укажите:\n"
        "▫️ <b>Для юридического лица</b> - полное наименование организации\n"
        "▫️ <b>Для ИП</b> - ваши ФИО полностью\n\n"
        "📌 Примеры оформления:\n"
        "————————————————\n"
        "🏛 <i>«Торговый Дом Велес»</i>\n"
        "👤 <i>Мелякин Иван Владимирович</i>",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML"
    )

@dp.message(StateFilter(AddUser.name))
async def process_name(message: Message, state: FSMContext):
    user_id = message.from_user.id
    name = message.text
    logger.debug(f"User {user_id} entered tenant name: {name}")
    await state.update_data(name=name)
    await state.set_state(AddUser.type)
    await message.answer(
        "📌 <b>Выберите организационную форму:</b>\n"
        "————————————————",
        reply_markup=type_keyboard(),
        parse_mode="HTML"
    )

@dp.message(StateFilter(AddUser.type))
async def process_type(message: Message, state: FSMContext):
    user_id = message.from_user.id
    tenant_type = message.text
    logger.debug(f"User {user_id} selected tenant type: {tenant_type}")
    await state.update_data(type=tenant_type)
    await state.set_state(AddUser.inn)
    await message.answer(
        "🔢 <b>Введите ИНН:</b>\n\n"
        "▫️ Для <b>юридического лица</b> — 10 цифр\n"
        "▫️ Для <b>индивидуального предпринимателя</b> — 12 цифр\n\n"
        "📌 Пример:\n"
        "————————————————\n"
        "📟 <i>1234567890</i> — для ООО\n"
        "📠 <i>123456789012</i> — для ИП",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML"
    )

@dp.message(StateFilter(AddUser.inn))
async def process_inn(message: Message, state: FSMContext):
    user_id = message.from_user.id
    inn = message.text
    data = await state.get_data()
    
    if not 10 <= len(message.text) <= 12 or not message.text.isdigit():
        await message.answer("ИНН Должен содержать от 10 до 12 цифр!")
        return

    logger.debug(f"User {user_id} provided valid INN: {inn}")
    await state.update_data(inn=inn)
    await state.set_state(AddUser.recw_inf)
    await message.answer(
        "🏢 <b>Введите расчетный счет:</b>\n\n"
        "Укажите полные реквизиты в формате:\n"
        "————————————————\n"
        "р/с 40702810611010583455 БИК 044525058 в Филиал 'БИЗНЕС'ПАО 'СОВКОМБАНК' к/с 30101810045250000058\n\n",
        parse_mode="HTML"
    )

@dp.message(StateFilter(AddUser.recw_inf))
async def process_recw_inf(message: Message, state: FSMContext):
    user_id = message.from_user.id
    payment_details = message.text
    logger.debug(f"User {user_id} entered payment details: {payment_details}")
    await state.update_data(recw_inf=payment_details)
    data = await state.get_data()
    
    try:
        conn = await get_db_connection()
        async with conn.cursor() as cursor:
            await cursor.execute(
                """INSERT INTO users
                (host_id)
                VALUES (%s)""",(user_id,)
            )
            await conn.commit()
            await cursor.execute(
                """INSERT INTO user_details 
                (name, type, inn, recw_inf, host_id) 
                VALUES (%s, %s, %s, %s, %s)""",
                (
                    data['name'], data['type'],
                    data['inn'], 
                    data['recw_inf'], user_id
                )
            )
            await conn.commit()
        
        logger.info(f"User {user_id} successfully added tenant: {data}")
        await message.answer(
            "✅ Регистрация успешно завершена!\n\n"
            "Благодарим за предоставленную информацию. Ваш профиль готов к работе.\n"
            "Для продолжения работы воспользуйтесь меню ниже.",
            reply_markup=menu_keyboard()
        )
    except Exception as e:
        logger.error(f"Error adding tenant by user {user_id}: {str(e)}")
        await message.answer(
            "⚠️ Внимание!\n"
            "Произошла техническая ошибка при сохранении данных. "
            "Пожалуйста, повторите попытку позже или обратитесь "
            "в техническую поддержку.",
            reply_markup=menu_keyboard()
        )
    finally:
        await state.clear()
        if conn: await conn.ensure_closed()
