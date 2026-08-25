from aiogram import types
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from aiogram.types import ReplyKeyboardRemove, BufferedInputFile
from src.utils.config import dp
from src.utils.keyboards import mass_send_keyboard, after_generation_keyboard, menu_keyboard
from src.services.generate_document import create_act
from src.services.email_sender import send_email
from src.services.excel_to_pdf import convert_excel_to_pdf
from src.utils.database import get_db_connection
from src.utils.logger import logger
import os
import aiomysql
from datetime import datetime
from src.handlers.utils.states import MassSendStates

async def check_document_limits(user_id: int, count: int, action: str) -> tuple[bool, str]:
    """
    Проверяет лимиты на создание или отправку документов для пользователя.

    :param user_id: ID пользователя (host_id в таблице users, user_id в таблице documents)
    :param count: Количество документов для создания или отправки
    :param action: 'create' или 'send' (действие для проверки)
    :return: (можно ли выполнить действие, сообщение об ошибке если нельзя)
    """
    logger.debug(f"Проверка лимитов для user_id={user_id}, count={count}, action={action}")
    conn = await get_db_connection()
    if conn is None:
        logger.error(f"Не удалось установить соединение с базой данных для user_id={user_id}")
        return False, "Ошибка подключения к базе данных."

    try:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # Получаем access_level пользователя
            logger.debug(f"Запрос access_level для user_id={user_id}")
            await cursor.execute("SELECT access_level FROM users WHERE host_id = %s", (user_id,))
            user = await cursor.fetchone()
            if not user:
                logger.warning(f"Пользователь user_id={user_id} не найден в базе данных")
                return False, "Пользователь не найден."

            access_level = user["access_level"]
            logger.debug(f"access_level для user_id={user_id}: {access_level}")

            # Лимиты для создания и отправки документов
            create_limits = {0: 10, 1: 30, 2: 70, 3: float('inf')}
            send_limits = {0: 2, 1: 15, 2: 50, 3: 200}

            if action == "create":
                # Подсчет созданных документов
                logger.debug(f"Подсчет созданных документов для user_id={user_id}")
                await cursor.execute("SELECT COUNT(*) as count FROM documents WHERE user_id = %s AND status != 'archived'", (user_id,))
                created_count = (await cursor.fetchone())["count"]
                logger.debug(f"Создано документов: {created_count}")

                # Проверка лимита создания
                limit = create_limits.get(access_level, float('inf'))
                remaining = limit - created_count
                if created_count + count > limit:
                    logger.info(f"Лимит создания документов будет превышен для user_id={user_id}, "
                               f"создано={created_count}, запрошено={count}, лимит={limit}")
                    return False, (f"Превышен лимит создания документов ({limit}). "
                                  f"Вы создали {created_count} документов, можете создать еще {remaining}.")
                return True, ""

            elif action == "send":
                # Подсчет отправленных документов
                logger.debug(f"Подсчет отправленных документов для user_id={user_id}")
                await cursor.execute("SELECT COUNT(*) as count FROM documents WHERE user_id = %s AND status = 'sent'", (user_id,))
                sent_count = (await cursor.fetchone())["count"]
                logger.debug(f"Отправлено документов: {sent_count}")

                # Проверка лимита отправки
                limit = send_limits.get(access_level, float('inf'))
                remaining = limit - sent_count
                if sent_count + count > limit:
                    logger.info(f"Лимит отправки документов будет превышен для user_id={user_id}, "
                               f"отправлено={sent_count}, запрошено={count}, лимит={limit}")
                    return False, (f"Превышен лимит отправки документов ({limit}). "
                                  f"Вы отправили {sent_count} документов, можете отправить еще {remaining}.")
                return True, ""

            logger.warning(f"Неизвестное действие: {action} для user_id={user_id}")
            return False, "Неизвестное действие."
    except Exception as e:
        logger.error(f"Ошибка при проверке лимитов для user_id={user_id}: {str(e)}")
        return False, "Ошибка при проверке лимитов."
    finally:
        if conn is not None:
            try:
                await conn.ensure_closed()
                logger.debug(f"Соединение с базой данных закрыто для user_id={user_id}")
            except Exception as e:
                logger.error(f"Ошибка при закрытии соединения для user_id={user_id}: {str(e)}")
        else:
            logger.warning(f"Соединение с базой данных не было установлено для user_id={user_id}")

@dp.message(lambda msg: msg.text == "📤 Массовая рассылка")
async def start_mass_send(message: types.Message, state: FSMContext):
    logger.info("Пользователь начал массовую рассылку")
    await message.answer("👥 Кому отправить документы?", reply_markup=mass_send_keyboard())
    await state.set_state(MassSendStates.CHOOSE_RECIPIENTS)

@dp.message(StateFilter(MassSendStates.CHOOSE_RECIPIENTS))
async def handle_recipient_choice(message: types.Message, state: FSMContext):
    choice = message.text.strip()
    user_id = message.from_user.id
    if choice == "📧 Всем арендаторам":
        conn = await get_db_connection()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute("SELECT id FROM tenants WHERE host_id = %s", (user_id,))
                result = await cursor.fetchall()
                tenant_ids = [tenant['id'] for tenant in result]
            logger.info(f"Выбраны арендаторы: {tenant_ids}")
            await state.update_data(selected_tenants=tenant_ids, document_date=None)
            await message.answer("📂 Выберите тип документа:", reply_markup=mass_send_keyboard(template=True))
            await state.set_state(MassSendStates.SELECT_TEMPLATE)
        finally:
            if conn is not None:
                await conn.ensure_closed()

    elif choice == "📝 Выбрать по ID":
        conn = await get_db_connection()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute("SELECT id, name FROM tenants WHERE host_id = %s", (message.from_user.id,))
                tenants = await cursor.fetchall()
                if not tenants:
                    await message.answer("📭 Список арендаторов пуст")
                    return
                tenant_ids = [tenant["id"] for tenant in tenants]
                await state.update_data(tenant_ids=tenant_ids)
                response = ["<b>📃 Список арендаторов:</b>\n"]
                for idx, tenant in enumerate(tenants, 1):
                    response.append(f"{idx}. {tenant['name']}")
                await message.answer('\n'.join(response), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка при получении арендаторов: {e}")
            await message.answer("❌ Ошибка загрузки списка")
        finally:
            if conn is not None:
                await conn.ensure_closed()
            await message.answer("👥 Введите номера арендаторов через запятую (например: 1,3,5):", reply_markup=ReplyKeyboardRemove())
            await state.set_state(MassSendStates.ENTER_IDS)
    elif choice == "↩️ Назад":
        await message.answer("Главное меню:", reply_markup=menu_keyboard())
        await state.clear()
        return

@dp.message(StateFilter(MassSendStates.ENTER_IDS))
async def handle_ids_input(message: types.Message, state: FSMContext):
    state_data = await state.get_data()
    tenant_ids = state_data.get("tenant_ids", [])
    try:
        input_numbers = [int(n.strip()) for n in message.text.split(",")]
        selected_indices = [n-1 for n in input_numbers]
        valid_indices = [idx for idx in selected_indices if 0 <= idx < len(tenant_ids)]
        if not valid_indices:
            raise ValueError
        tenant_numbers = {tenant_ids[idx]: f"№{n}" for idx, n in zip(valid_indices, input_numbers)}
        selected_ids = [tenant_ids[idx] for idx in valid_indices]
        await state.update_data(
            selected_tenants=selected_ids,
            tenant_numbers=tenant_numbers
        )
        await message.answer("📂 Выберите тип документа:", reply_markup=mass_send_keyboard(template=True))
        await state.set_state(MassSendStates.SELECT_TEMPLATE)
    except (ValueError, IndexError):
        await message.answer("❌ Некорректные номера. Пример: 1,3,5")

@dp.message(StateFilter(MassSendStates.SELECT_TEMPLATE))
async def handle_template_selection(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    if message.text.strip() == "Счет за аренду" and not user_data.get("document_date"):
        await state.update_data(pending_template="Счет за аренду")
        logger.debug("Ожидание ввода даты для Счета за аренду")
        await message.answer("📅 Введите дату формирования счета (в формате ДД.ММ.ГГГГ):", reply_markup=ReplyKeyboardRemove())
        await state.set_state(MassSendStates.CONFIRM_SEND)
        return
    await generate_documents(message, state)

@dp.message(StateFilter(MassSendStates.CONFIRM_SEND))
async def handle_rent_date_or_send(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    user_id = message.from_user.id

    if not user_data.get("document_date") and user_data.get("pending_template") == "Счет за аренду":
        try:
            input_date = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
            await state.update_data(document_date=input_date)
            await generate_documents(message, state)
        except ValueError:
            await message.answer("❌ Неверный формат даты. Введите в формате ДД.ММ.ГГГГ")
        return

    if message.text != "✅ Да, отправить":
        await message.answer("❌ Отправка отменена.", reply_markup=menu_keyboard())
        await state.clear()
        return

    files = user_data.get("generated_files", [])
    tenant_numbers = user_data.get("tenant_numbers", {})

    # Проверка лимита отправки документов
    can_send, error_message = await check_document_limits(user_id, len(files), "send")
    if not can_send:
        await message.answer(f"❌ {error_message}", reply_markup=menu_keyboard())
        await state.clear()
        return

    report = []
    conn = None

    try:
        conn = await get_db_connection()
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # Получаем имя арендодателя
            await cursor.execute("SELECT name FROM user_details WHERE host_id = %s", (user_id,))
            landlord_data = await cursor.fetchone()
            landlord_name = landlord_data["name"] if landlord_data else "Арендодатель"

            for idx, (tenant_id, xlsx_path) in enumerate(files, 1):
                display_id = tenant_numbers.get(tenant_id, f"№{idx}")
                if not xlsx_path or not os.path.exists(xlsx_path):
                    report.append(f"{display_id}: ❌ файл не найден")
                    continue

                try:
                    # Получаем email и имя арендатора
                    await cursor.execute("SELECT email, name FROM tenants WHERE id = %s", (tenant_id,))
                    tenant_data = await cursor.fetchone()
                    tenant_email = tenant_data["email"] if tenant_data else None
                    tenant_name = tenant_data["name"] if tenant_data else "Арендатор"

                    if not tenant_email:
                        report.append(f"{display_id}: ❌ email не найден")
                        continue

                    pdf_path = str(xlsx_path).replace(".xlsx", ".pdf")
                    convert_excel_to_pdf(xlsx_path, pdf_path, user_id)
                    await cursor.execute(
                        """UPDATE documents 
                        SET status = 'sent', file_path = %s 
                        WHERE file_path = %s AND user_id = %s AND status = 'draft' LIMIT 1""",
                        (pdf_path, xlsx_path, user_id)
                    )
                    await conn.commit()

                    with open(pdf_path, "rb") as f:
                        pdf_data = f.read()

                    # Формирование текста письма в зависимости от шаблона
                    pdf_filename = os.path.basename(pdf_path)
                    if "Акт_аренда" in pdf_filename:
                        subject = "Акт аренды"
                        body = (
                            f"Уважаемый(ая) {tenant_name},\n\n"
                            f"Арендодатель {landlord_name} направил Вам акт аренды. "
                            f"Документ во вложении.\n\n"
                            f"Пожалуйста, ознакомьтесь с документом и свяжитесь с арендодателем при необходимости.\n\n"
                            f"Это сообщение сформировано автоматически ботом компании."
                        )
                    elif "Счет_за_аренду" in pdf_filename:
                        subject = "Счет за аренду"
                        body = (
                            f"Уважаемый(ая) {tenant_name},\n\n"
                            f"Арендодатель {landlord_name} направил Вам счет за аренду. "
                            f"Документ во вложении.\n\n"
                            f"Пожалуйста, оплатите счет в установленные сроки.\n\n"
                            f"Это сообщение сформировано автоматически ботом компании."
                        )
                    else:
                        subject = "Документ по аренде"
                        body = (
                            f"Уважаемый(ая) {tenant_name},\n\n"
                            f"Арендодатель {landlord_name} направил Вам документ. "
                            f"Документ во вложении.\n\n"
                            f"Пожалуйста, ознакомьтесь с документом и свяжитесь с арендодателем при необходимости.\n\n"
                            f"Это сообщение сформировано автоматически ботом компании."
                        )

                    # Отправка письма
                    success = await send_email(
                        to_email=tenant_email,
                        subject=subject,
                        body=body,
                        file_data=pdf_data,
                        filename=pdf_filename
                    )
                    report.append(f"{display_id}: {'✅ отправлено' if success else '❌ ошибка'}")

                except Exception as e:
                    logger.error(f"Ошибка для ID {tenant_id}: {str(e)}", exc_info=True)
                    report.append(f"{display_id}: ❌ ошибка обработки")
                    await conn.rollback()

        await message.answer("\n".join(report), reply_markup=menu_keyboard())

    except Exception as e:
        logger.error(f"Общая ошибка: {str(e)}", exc_info=True)
        await message.answer("⚠️ Произошла внутренняя ошибка")

    finally:
        if conn and not conn.closed:
            await conn.ensure_closed()
        await state.clear()

async def generate_documents(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = await state.get_data()
    doc_type = user_data.get("pending_template") or message.text.strip()
    template_map = {
        "Акт аренда": "ШАБЛОН_Акт_аренда.xlsx",
        "Счет за аренду": "ШАБЛОН_Счет_за_аренду.xlsx"
    }
    template = template_map.get(doc_type)
    if not template:
        await message.answer("❌ Неизвестный тип документа")
        return

    tenant_ids = user_data.get("selected_tenants")
    tenant_numbers = user_data.get("tenant_numbers", {})
    
    # Проверка лимита создания документов
    can_create, error_message = await check_document_limits(user_id, len(tenant_ids), "create")
    if not can_create:
        await message.answer(f"❌ {error_message}", reply_markup=menu_keyboard())
        await state.clear()
        return

    custom_date = user_data.get("document_date")
    generated_files = []

    conn = await get_db_connection()
    try:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT id, name FROM tenants WHERE id IN %s", (tuple(tenant_ids),))
            tenants = {tenant['id']: tenant['name'] for tenant in await cursor.fetchall()}
    finally:
        if conn is not None:
            await conn.ensure_closed()

    for idx, tenant_id in enumerate(tenant_ids, 1):
        try:
            display_name = tenant_numbers.get(tenant_id, f"№{idx}")
            tenant_name = tenants.get(tenant_id, f"Арендатор {display_name}")

            logger.info(f"Генерация документа для {display_name} ({tenant_id})...")
            xlsx_path = await create_act(user_id, tenant_id, template, custom_date=custom_date)
            generated_files.append((tenant_id, xlsx_path))

            with open(xlsx_path, "rb") as f:
                xlsx_data = f.read()

            await message.answer_document(
                document=BufferedInputFile(xlsx_data, filename=os.path.basename(xlsx_path)),
                caption=f"🔹 {display_name} ({tenant_name})\n✅ Документ сформирован"
            )
        except Exception as e:
            logger.exception(f"Ошибка генерации для {tenant_id}")
            generated_files.append((tenant_id, None))
            await message.answer(f"❌ Ошибка формирования документа для {display_name}")

    await state.update_data(generated_files=generated_files)
    await message.answer("📄 Все документы отправлены в чат. Отправить их арендаторам по email?",
                         reply_markup=after_generation_keyboard())
    await state.set_state(MassSendStates.CONFIRM_SEND)