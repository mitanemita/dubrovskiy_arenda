from aiogram import types, F
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from datetime import datetime
from src.utils.config import dp, MONEY_TOKEN
from src.utils.logger import logger
from aiogram import Bot
from aiogram.filters import StateFilter
from src.utils.database import get_db_connection
from src.utils.keyboards import *
from src.handlers.utils.states import ProfileStates
from datetime import timedelta
from aiogram.types import LabeledPrice, PreCheckoutQuery
import os
from PIL import Image
import io

async def get_user_profile(user_id: int):
    logger.debug(f"Getting profile for user {user_id}")
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Получение данных из user_details
            await cursor.execute(
                """SELECT name, type, inn, recw_inf
                FROM user_details WHERE host_id = %s""",
                (user_id,)
            )
            user_details = await cursor.fetchone()
            
            # Получение данных из users
            await cursor.execute(
                """SELECT stat, access_level, balance
                FROM users WHERE host_id = %s""",
                (user_id,)
            )
            user_data = await cursor.fetchone()
            
            # Получение статистики документов
            create_limits = {0: 10, 1: 30, 2: 70, 3: float('inf')}
            send_limits = {0: 2, 1: 15, 2: 50, 3: 200}
            await cursor.execute(
                """SELECT COUNT(*) as count FROM documents WHERE user_id = %s AND status != 'archived'""",
                (user_id,)
            )
            created_count = (await cursor.fetchone())["count"]
            await cursor.execute(
                """SELECT COUNT(*) as count FROM documents WHERE user_id = %s AND status = 'sent'""",
                (user_id,)
            )
            sent_count = (await cursor.fetchone())["count"]
            
            if not user_details or not user_data:
                logger.warning(f"Profile data not found for user {user_id}")
                return None
                
            return {
                **user_details,
                **user_data,
                "created_count": created_count,
                "sent_count": sent_count,
                "create_limit": create_limits.get(user_data["access_level"], float('inf')),
                "send_limit": send_limits.get(user_data["access_level"], float('inf'))
            }
    except Exception as e:
        logger.error(f"Error getting profile for user {user_id}: {str(e)}")
        return None
    finally:
        if conn is not None:
            await conn.ensure_closed()

@dp.message(StateFilter(None), F.text == "Профиль")
async def show_profile(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    logger.info(f"User {user_id} requested profile")
    
    try:
        profile_data = await get_user_profile(user_id)
        if not profile_data:
            logger.warning(f"Profile not found for user {user_id}")
            await message.answer("❌ Профиль не найден")
            return

        sub_date = "не активна"
        if profile_data['stat']:
            try:
                sub_date = datetime.strptime(str(profile_data['stat']), "%Y-%m-%d").strftime("%d.%m.%Y")
                logger.debug(f"Subscription date parsed for user {user_id}")
            except Exception as e:
                logger.error(f"Date parsing error for user {user_id}: {str(e)}")

        access_levels = {
            0: 'Пробный',
            1: 'Продвинутый',
            2: 'VIP',
            3: 'PREMIUM'
        }

        response = (
            "🌟 <b>Ваш профиль</b> 🌟\n\n"
            "▫️ 🏢 <b>Организация:</b> {0}\n"
            "▫️ 🏛 <b>Тип:</b> {1}\n"
            "▫️ 🔢 <b>ИНН:</b> {2}\n"
            "▫️ 🏦 <b>Реквизиты:</b>\n{3}\n\n"
            "✨ <b>Подписка</b> ✨\n"
            "▫️ 🚀 <b>Уровень:</b> {4}\n"
            "▫️ 📅 <b>Действует до:</b> {5}\n\n"
            "▫️ 💰 <b>Баланс:</b> {6}\n\n"
            "📑 <b>Документы</b> 📑\n"
            "▫️ 📝 <b>Создано:</b> {7} (осталось: {8})\n"
            "▫️ 📤 <b>Отправлено:</b> {9} (осталось: {10})"
        ).format(
            profile_data['name'], 
            profile_data['type'],  
            profile_data['inn'],  
            profile_data['recw_inf'],  
            access_levels[profile_data['access_level']],
            sub_date,
            profile_data['balance'],
            profile_data['created_count'],
            profile_data['create_limit'] - profile_data['created_count'] if profile_data['create_limit'] != float('inf') else "∞",
            profile_data['sent_count'],
            profile_data['send_limit'] - profile_data['sent_count'] if profile_data['send_limit'] != float('inf') else "∞"
        )
        
        await message.answer(response, reply_markup=profile_menu_keyboard(), parse_mode="HTML")
        await state.set_state(ProfileStates.PROFILE_VIEW)
        logger.info(f"Profile displayed for user {user_id}")

    except Exception as e:
        logger.error(f"Error showing profile for user {user_id}: {str(e)}")
        await message.answer("❌ Ошибка при загрузке профиля")

@dp.message(ProfileStates.PROFILE_VIEW, F.text.in_(["Изменить", "Подписка", "Фото подписи", "↩️ Назад"]))
async def handle_profile_menu(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    logger.info(f"User {user_id} selected menu option: {message.text}")
    
    try:
        if message.text == "↩️ Назад":
            await state.clear()
            await message.answer("Главное меню:", reply_markup=menu_keyboard())
            logger.info(f"User {user_id} returned to main menu")
            return
        
        if message.text == "Подписка":
            logger.info(f"User {user_id} accessed subscription menu")
    
            text = (
                "💎 <b>Меню подписки</b>\n\n"
                "Здесь вы можете:\n"
                "• Пополнить баланс\n"
                "• Купить/продлить подписку\n"
                "• Управлять тарифным планом\n\n"
                f"📍 Текущий курс: 1 RUB = 1 балл"
            )
    
            await message.answer(text, reply_markup=subscription_keyboard, parse_mode="HTML")
            await state.set_state(ProfileStates.SUBSCRIPTION_MENU)
        
        if message.text == "Фото подписи":
            photo_path = f"data/photos/{user_id}.png"
            kb = types.ReplyKeyboardMarkup(
                keyboard=[
                    [types.KeyboardButton(text="📷 Изменить")],
                    [types.KeyboardButton(text="↩️ Назад")]
                ],
                resize_keyboard=True
            )

            if os.path.exists(photo_path):
                logger.debug(f"Signature photo exists for user {user_id}")
                try:
                    await message.answer_photo(
                        types.FSInputFile(photo_path),
                        caption="📌 Ваша текущая подпись.\nХотите заменить её?",
                        reply_markup=kb
                    )
                except Exception as e:
                    logger.error(f"Error sending photo for user {user_id}: {str(e)}")
                    await message.answer("❌ Ошибка загрузки фото")                              
            else:
                logger.debug(f"No signature photo for user {user_id}")
                await message.answer(
                    "📌 У вас пока нет загруженной подписи.\nПришлите фото подписи в ответ на это сообщение.",
                    reply_markup=kb
                )

            await state.set_state(ProfileStates.PHOTO_SIGNATURE)
            logger.info(f"User {user_id} entered photo signature section")
            return
        
        if message.text == "Изменить":
            await message.answer("Выберите поле для изменения:", reply_markup=profile_edit_keyboard())
            await state.set_state(ProfileStates.EDIT_MENU)
            logger.info(f"User {user_id} entered edit menu")

    except Exception as e:
        logger.error(f"Error handling profile menu for user {user_id}: {str(e)}")
        await message.answer("❌ Произошла ошибка при обработке запроса")

@dp.message(ProfileStates.EDIT_MENU, F.text.startswith("✏️"))
async def handle_edit_selection(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        _, field = message.text.split(maxsplit=1)
        handlers = {
            "Название": edit_name_handler,
            "Тип": edit_type_handler,
            "ИНН": edit_inn_handler,
            "Реквизиты": edit_recw_inf_handler
        }
        
        logger.info(f"User {user_id} selected edit field: {field}")
        await handlers[field](message, state)
        
    except KeyError:
        logger.warning(f"User {user_id} tried to edit invalid field: {field}")
        await message.answer("⚠️ Это поле нельзя изменить!")
    except Exception as e:
        logger.error(f"Error handling edit selection for user {user_id}: {str(e)}")
        await message.answer("❌ Ошибка выбора поля")

@dp.message(ProfileStates.EDIT_MENU, F.text == "↩️ Назад")
async def handle_edit_back(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    logger.info(f"User {user_id} returned from edit menu")
    await show_profile(message, state)
    await state.set_state(ProfileStates.PROFILE_VIEW)

async def edit_name_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    logger.info(f"User {user_id} started editing name")
    await state.update_data(column="name", validator=lambda x: x.strip())
    await message.answer("🏢 Введите новое название организации:", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(ProfileStates.EDIT_PROFILE)

async def edit_type_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    logger.info(f"User {user_id} started editing type")
    await state.update_data(
        column="type",
        validator=lambda x: x if True else False
    )
    await message.answer("🏛 Выберите тип организации:", reply_markup=type_keyboard())
    await state.set_state(ProfileStates.EDIT_PROFILE)

async def edit_inn_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    logger.info(f"User {user_id} started editing INN")
    await state.update_data(
        column="inn",
        validator=lambda x: x if len(x) in (10, 12) and x.isdigit() else False
    )
    await message.answer("🔢 Введите ИНН (10 или 12 цифр):", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(ProfileStates.EDIT_PROFILE)

async def edit_recw_inf_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    logger.info(f"User {user_id} started editing requisites")
    await state.update_data(column="recw_inf", validator=lambda x: x.strip())
    await message.answer("🏦 Введите новые реквизиты:", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(ProfileStates.EDIT_PROFILE)

@dp.message(ProfileStates.EDIT_PROFILE)
async def save_profile_changes(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    
    try:
        logger.info(f"User {user_id} attempting to save changes for {data['column']}")
        value = data['validator'](message.text)
        if not value:
            logger.warning(f"Invalid format for {data['column']} from user {user_id}")
            raise ValueError

        conn = await get_db_connection()
        async with conn.cursor() as cursor:
            await cursor.execute(
                f"UPDATE user_details SET {data['column']} = %s WHERE host_id = %s",
                (value, user_id)
            )
            await conn.commit()

        logger.info(f"Profile updated for user {user_id} in field {data['column']}")
        await message.answer("✅ Изменения успешно сохранены!")
        await show_profile(message, state)

    except ValueError:
        logger.warning(f"Validation failed for user {user_id} input: {message.text}")
        await message.answer("❌ Некорректный формат! Повторите ввод:")
    except Exception as e:
        logger.error(f"Error updating profile for user {user_id}: {str(e)}")
        await message.answer("❌ Ошибка сохранения! Обратитесь в поддержку")
        await state.clear()

from PIL import Image
import io

@dp.message(ProfileStates.PHOTO_SIGNATURE, F.photo)
async def handle_signature_photo(message: types.Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    try:
        logger.info(f"User {user_id} uploading signature photo")
        os.makedirs("data/photos", exist_ok=True)
        photo_path = f"data/photos/{user_id}.png"

        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)

        # Скачиваем файл в память, чтобы проверить размеры
        file_io = io.BytesIO()
        await bot.download_file(file.file_path, destination=file_io)
        file_io.seek(0)

        # Открываем изображение с помощью Pillow
        image = Image.open(file_io)
        width, height = image.size

        # Проверка размеров и соотношения сторон
        min_width, max_width = 1000, 1400
        min_height, max_height = 800, 1100
        min_ratio, max_ratio = 1, 2  # Соотношение сторон (ширина/высота)

        ratio = width / height if height > 0 else 0

        if not (min_width <= width <= max_width):
            logger.warning(f"Signature photo for user {user_id} has invalid width: {width}px")
            await message.answer(
                f"⚠️ Ширина изображения должна быть от {min_width} до {max_width} пикселей. "
                f"Текущая ширина: {width}px.\nРекомендуемый размер: ~500x100 пикселей."
            )
            return

        if not (min_height <= height <= max_height):
            logger.warning(f"Signature photo for user {user_id} has invalid height: {height}px")
            await message.answer(
                f"⚠️ Высота изображения должна быть от {min_height} до {max_height} пикселей. "
                f"Текущая высота: {height}px.\nРекомендуемый размер: ~500x100 пикселей."
            )
            return

        if not (min_ratio <= ratio <= max_ratio):
            logger.warning(f"Signature photo for user {user_id} has invalid aspect ratio: {ratio}")
            await message.answer(
                f"⚠️ Соотношение сторон должно быть примерно 5:1 (от {min_ratio}:1 до {max_ratio}:1). "
                f"Текущее соотношение: {ratio:.1f}:1.\nРекомендуемый размер: ~500x100 пикселей."
            )
            return

        # Если размеры подходят, сохраняем файл
        file_io.seek(0)  # Возвращаемся в начало файла
        with open(photo_path, "wb") as f:
            f.write(file_io.read())

        logger.info(f"Signature photo saved for user {user_id}")
        await message.answer("✅ Фото подписи сохранено!", reply_markup=profile_menu_keyboard())
        await state.set_state(ProfileStates.PROFILE_VIEW)

    except Exception as e:
        logger.error(f"Error saving signature photo for user {user_id}: {str(e)}")
        await message.answer("❌ Ошибка сохранения фото!")

@dp.message(ProfileStates.PHOTO_SIGNATURE)
async def handle_signature_buttons(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        if message.text == "↩️ Назад":
            logger.info(f"User {user_id} returned from photo signature section")
            await show_profile(message, state)
            await state.set_state(ProfileStates.PROFILE_VIEW)
        elif message.text == "📷 Изменить":
            logger.info(f"User {user_id} requested photo change")
            await message.answer("📸 Пришлите новое фото подписи.")
        else:
            logger.warning(f"User {user_id} sent invalid input in photo section: {message.text}")
            await message.answer("📎 Пожалуйста, отправьте фото или выберите действие с клавиатуры.")
    except Exception as e:
        logger.error(f"Error handling photo signature for user {user_id}: {str(e)}")
        await message.answer("❌ Произошла ошибка при обработке запроса")

SUBSCRIPTION_PRICES = {
    1: 490,  # Продвинутый (в рублях)
    2: 990,  # VIP
    3: 1490  # 🌟 PREMIUM
}

SUBSCRIPTION_NAMES = {
    1: '🟢 ПРОДВИНУТЫЙ (1 месяц)',
    2: '💎 VIP (1 месяц)',
    3: '🌟 PREMIUM (1 месяц)'
}

ACCESS_LEVELS = {
    0: 'Пробный',
    1: 'Продвинутый',
    2: 'VIP',
    3: 'PREMIUM'
}

async def save_payment_record(user_id: int, amount: float, payment_type: str, conn):
    """Save a payment record to the payments table."""
    try:
        logger.debug(f"Saving payment record for user {user_id}, type: {payment_type}, amount: {amount}")
        async with conn.cursor() as cursor:
            period = datetime.now().replace(day=1)
            due_date = datetime.now().date()
            paid_date = datetime.now().date()
            status = 'paid'
            penalty_added = 0

            await cursor.execute(
                """
                INSERT INTO payments (user_id, type, amount, period, due_date, paid_date, status, penalty_added)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (user_id, payment_type, amount, period, due_date, paid_date, status, penalty_added)
            )
            await conn.commit()
            logger.info(f"Payment record saved for user {user_id}: {payment_type} of {amount} RUB")
    except Exception as e:
        if str(e).startswith("(1265,"):
            logger.error(f"Invalid payment type '{payment_type}' for user {user_id}. Update database schema to include this type.")
            raise ValueError("Invalid payment type. Please contact support.")
        logger.error(f"Error saving payment record for user {user_id}: {str(e)}")
        raise ValueError("Failed to save payment record. Please contact support.")

@dp.message(ProfileStates.SUBSCRIPTION_MENU, F.text.in_(["💰 Пополнить баланс", "🚀 Купить подписку", "↩️ Назад"]))
async def handle_subscription_actions(message: types.Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    
    if message.text == "↩️ Назад":
        await show_profile(message, state)
        return
    
    if message.text == "💰 Пополнить баланс":
        await message.answer(
            "💵 Введите сумму пополнения в RUB (мин. 100 RUB):",
            reply_markup=no_balance_keyboard()
        )
        await state.set_state(ProfileStates.ENTER_AMOUNT)
    
    elif message.text == "🚀 Купить подписку":
        profile_data = await get_user_profile(user_id)
        if not profile_data:
            await message.answer("❌ Не удалось загрузить данные профиля")
            return

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🚀 Продвинутый - 490 RUB", callback_data="sub_1")],
            [types.InlineKeyboardButton(text="💎 VIP - 990 RUB", callback_data="sub_2")],
            [types.InlineKeyboardButton(text="🌟 PREMIUM - 1490 RUB", callback_data="sub_3")],
            [types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_sub")]
        ])
        
        await message.answer(
            f"📦 Выберите тарифный план:\n\nТекущий баланс: {profile_data['balance']} RUB",
            reply_markup=keyboard
        )
        await state.set_state(ProfileStates.SELECT_SUBSCRIPTION)

@dp.message(ProfileStates.ENTER_AMOUNT, F.text)
async def handle_amount_input(message: types.Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    try:
        # Сначала присваиваем amount значение из message.text
        amount = message.text
        
        # Обработка кнопки "Назад"
        if amount == "↩️ Назад":
            logger.info(f"User {user_id} accessed subscription menu")
            text = (
                "💎 <b>Меню подписки</b>\n\n"
                "Здесь вы можете:\n"
                "• Пополнить баланс\n"
                "• Купить/продлить подписку\n"
                "• Управлять тарифным планом\n\n"
                f"📍 Текущий курс: 1 RUB = 1 балл"
            )
            await message.answer(text, reply_markup=subscription_keyboard, parse_mode="HTML")
            await state.set_state(ProfileStates.SUBSCRIPTION_MENU)
            return
        
        # Проверка, является ли ввод числом
        if not amount.isdigit():
            raise ValueError("Input must be a number")
        
        # Преобразование в целое число
        amount = int(amount)
        
        # Проверка диапазона суммы
        if amount < 100:
            await message.answer("❌ Минимальная сумма 100 RUB!")
            return
        if amount > 100000:
            await message.answer("❌ Максимальная сумма 100 000 RUB!")
            return

        # Создание счёта
        prices = [LabeledPrice(label="Пополнение баланса", amount=amount * 100)]
        await bot.send_invoice(
            chat_id=message.chat.id,
            title="Пополнение баланса",
            description=f"Пополнение баланса на {amount} RUB",
            payload=f"balance_{user_id}_{amount}",
            provider_token=MONEY_TOKEN,
            currency="RUB",
            prices=prices,
            start_parameter="balance-topup",
            need_email=True
        )
        logger.info(f"User {user_id} initiated balance topup of {amount} RUB")
        await state.set_state(ProfileStates.PROCESS_PAYMENT)

    except ValueError:
        logger.warning(f"Invalid amount input from user {user_id}: {message.text}")
        await message.answer("❌ Введите целое число в RUB (от 100 до 100000):")

@dp.message(ProfileStates.ENTER_AMOUNT)
async def handle_non_text_input(message: types.Message):
    user_id = message.from_user.id
    logger.warning(f"User {user_id} sent non-text input in ENTER_AMOUNT state")
    await message.answer("⚠️ Пожалуйста, введите сумму цифрами!\nПример: 1000")

@dp.callback_query(F.data.startswith("sub_"))
async def process_subscription_selection(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    level = int(callback.data.split("_")[1])
    
    if level not in SUBSCRIPTION_PRICES:
        await callback.message.answer("❌ Неверный уровень подписки!", reply_markup=subscription_keyboard)
        await state.set_state(ProfileStates.SUBSCRIPTION_MENU)
        await callback.answer()
        return
    
    profile_data = await get_user_profile(user_id)
    if not profile_data:
        await callback.message.answer("❌ Не удалось загрузить данные профиля", reply_markup=subscription_keyboard)
        await state.set_state(ProfileStates.SUBSCRIPTION_MENU)
        await callback.answer()
        return

    required_amount = SUBSCRIPTION_PRICES[level]
    current_balance = profile_data['balance']

    if current_balance < required_amount:
        await callback.message.answer(
            f"❌ Недостаточно средств!\nТребуется: {required_amount} RUB\nВаш баланс: {current_balance} RUB\nПожалуйста, пополните баланс.",
            reply_markup=subscription_keyboard
        )
        await state.set_state(ProfileStates.SUBSCRIPTION_MENU)
        logger.info(f"User {user_id} has insufficient balance ({current_balance} RUB) for subscription level {level} ({required_amount} RUB)")
        await callback.answer()
        return

    try:
        conn = await get_db_connection()
        async with conn.cursor() as cursor:
            # Deduct balance
            await cursor.execute(
                "UPDATE users SET balance = balance - %s WHERE host_id = %s",
                (required_amount, user_id)
            )
            # Update subscription
            new_expiry = datetime.now() + timedelta(days=30)
            new_expiry_str = new_expiry.strftime('%Y-%m-%d')
            await cursor.execute(
                "UPDATE users SET access_level = %s, stat = %s WHERE host_id = %s",
                (level, new_expiry_str, user_id)
            )
            # Delete all user documents
            await cursor.execute(
                "UPDATE documents SET status = 'archived' WHERE user_id = %s",
                (user_id,)
            )
            # Save payment record for subscription
            await save_payment_record(user_id, required_amount, 'subscription', conn)
            await conn.commit()

        await callback.message.answer(
            f"🎉 Вы успешно оформили подписку: {ACCESS_LEVELS[level]} до {new_expiry.strftime('%d.%m.%Y')}\n"
            f"Списано: {required_amount} RUB\nОстаток баланса: {current_balance - required_amount} RUB\n"
            f"📑 Все ваши лимиты обновлены.",
            reply_markup=profile_menu_keyboard()
        )
        await state.set_state(ProfileStates.PROFILE_VIEW)
        logger.info(f"User {user_id} purchased subscription level {level} for {required_amount} RUB and cleared documents")
        await callback.answer()

    except ValueError as ve:
        logger.error(f"Error processing subscription for user {user_id}: {str(ve)}")
        await callback.message.answer(f"❌ {str(ve)}", reply_markup=subscription_keyboard)
        await state.set_state(ProfileStates.SUBSCRIPTION_MENU)
        await callback.answer()
    except Exception as e:
        logger.error(f"Error processing subscription for user {user_id}: {str(e)}")
        await callback.message.answer("❌ Ошибка при оформлении подписки. Обратитесь в поддержку.", reply_markup=subscription_keyboard)
        await state.set_state(ProfileStates.SUBSCRIPTION_MENU)
        await callback.answer()
    finally:
        if conn is not None:
            await conn.ensure_closed()

@dp.callback_query(F.data == "cancel_sub")
async def cancel_subscription_selection(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    await callback.message.answer("Выбор подписки отменён.", reply_markup=subscription_keyboard)
    await state.set_state(ProfileStates.SUBSCRIPTION_MENU)
    logger.info(f"User {user_id} cancelled subscription selection")
    await callback.answer()

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    payment = message.successful_payment
    
    try:
        conn = await get_db_connection()
        async with conn.cursor() as cursor:
            if payment.invoice_payload.startswith("balance_"):
                _, _, amount = payment.invoice_payload.split("_")
                amount = int(amount)
                await cursor.execute(
                    "UPDATE users SET balance = balance + %s WHERE host_id = %s",
                    (amount, user_id)
                )
                await save_payment_record(user_id, amount, 'topup', conn)
                await conn.commit()
                
                text = f"✅ Баланс успешно пополнен на {amount} RUB"
                await message.answer(text, reply_markup=subscription_keyboard)
                await state.set_state(ProfileStates.SUBSCRIPTION_MENU)
                logger.info(f"User {user_id} successfully topped up balance by {amount} RUB")
            else:
                logger.warning(f"Unexpected invoice payload for user {user_id}: {payment.invoice_payload}")
                await message.answer("❌ Неизвестный тип платежа. Обратитесь в поддержку.")
            
    except ValueError as ve:
        logger.error(f"Payment processing error for user {user_id}: {str(ve)}")
        await message.answer(f"❌ {str(ve)}")
    except Exception as e:
        logger.error(f"Payment processing error for user {user_id}: {str(e)}")
        await message.answer("❌ Ошибка при обработке оплаты. Обратитесь в поддержку.")
    finally:
        if conn is not None:
            await conn.ensure_closed()