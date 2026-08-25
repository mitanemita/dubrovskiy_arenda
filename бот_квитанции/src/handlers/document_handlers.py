import os
from aiogram.types import ReplyKeyboardRemove
from aiogram import types
from datetime import datetime
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile
from src.handlers.utils.states import SurveyStates
from src.services import generate_document, email_sender, excel_to_pdf
from src.utils import logger, config, database
from src.utils.keyboards import *
from aiogram.filters import StateFilter
import aiomysql
from src.utils.database import get_db_connection

async def send_documents(message: types.Message, act_path: str, invoice_path: str):
    with open(act_path, "rb") as f:
        await message.answer_document(
            BufferedInputFile(f.read(), filename=os.path.basename(act_path)),
            caption="✅ Акт коммунальных услуг"
        )
    with open(invoice_path, "rb") as f:
        await message.answer_document(
            BufferedInputFile(f.read(), filename=os.path.basename(invoice_path)),
            caption="✅ Счет за электричество"
        )

async def check_document_limits(user_id: int, action: str) -> tuple[bool, str]:
    """
    Проверяет лимиты на создание или отправку документов для пользователя.
    
    :param user_id: ID пользователя
    :param action: 'create' или 'send' (действие для проверки)
    :return: (можно ли выполнить действие, сообщение об ошибке если нельзя)
    """
    logger.debug(f"Проверка лимитов для user_id={user_id}, action={action}")
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

            # Лимиты для создания документов
            create_limits = {0: 10, 1: 30, 2: 70, 3: float('inf')}
            # Лимиты для отправки документов
            send_limits = {0: 2, 1: 15, 2: 50, 3: 200}

            # Подсчет созданных документов
            logger.debug(f"Подсчет созданных документов для user_id={user_id}")
            await cursor.execute("SELECT COUNT(*) as count FROM documents WHERE user_id = %s AND status != 'archived'", (user_id,))
            created_count = (await cursor.fetchone())["count"]
            logger.debug(f"Создано документов: {created_count}")

            if action == "create":
                limit = create_limits.get(access_level, float('inf'))
                if created_count >= limit:
                    logger.info(f"Лимит создания документов превышен для user_id={user_id}, limit={limit}")
                    return False, f"Превышен лимит создания документов ({limit}) для вашего уровня доступа."
                return True, ""

            # Подсчет отправленных документов
            logger.debug(f"Подсчет отправленных документов для user_id={user_id}")
            await cursor.execute("SELECT COUNT(*) as count FROM documents WHERE user_id = %s AND status = 'sent'", (user_id,))
            sent_count = (await cursor.fetchone())["count"]
            logger.debug(f"Отправлено документов: {sent_count}")

            if action == "send":
                limit = send_limits.get(access_level, float('inf'))
                if sent_count >= limit:
                    logger.info(f"Лимит отправки документов превышен для user_id={user_id}, limit={limit}")
                    return False, f"Превышен лимит отправки документов ({limit}) для вашего уровня доступа."
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
            logger.warning(f"Соединение с базой данных не было установлено для user_id={user_id}, пропуск закрытия.")

@config.dp.message(StateFilter(SurveyStates.SELECT_METER_COUNT))
async def handle_meter_count(message: types.Message, state: FSMContext):
    if message.text.isdigit() and 1 <= int(message.text) <= 5:
        await state.update_data(
            meter_count=int(message.text),
            current_meter=1,
            meters=[]
        )
        await message.answer(f"📝 Введите данные для счетчика 1 (формат: 'предыдущие -> текущие'):")
        await state.set_state(SurveyStates.ENTER_METER_DATA)
    else:
        await message.answer("❌ Введите число от 1 до 5.")

@config.dp.message(StateFilter(SurveyStates.ENTER_METER_DATA))
async def handle_meter_data(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    try:
        prev, curr = map(float, message.text.split("->"))
        meters = user_data["meters"] + [{"prev": prev, "curr": curr}]
        current_meter = user_data["current_meter"] + 1

        if current_meter > user_data["meter_count"]:
            await state.update_data(meters=meters)
            await message.answer("📅 Введите дату формирования документов (ДД.ММ.ГГГГ):")
            await state.set_state(SurveyStates.ENTER_RENT_DATE)
        else:
            await state.update_data(meters=meters, current_meter=current_meter)
            await message.answer(f"📝 Введите данные для счетчика {current_meter}:")
    except:
        await message.answer("❌ Неверный формат. Используйте: '100.5 -> 200.3'")

@config.dp.message(StateFilter(SurveyStates.ENTER_PENI))
async def handle_rent_peni_input(message: types.Message, state: FSMContext):
    if message.text.isdigit():
        await state.update_data(day_peni=int(message.text))
        await message.answer("📅 Введите дату формирования счета (ДД.ММ.ГГГГ):")
        await state.set_state(SurveyStates.PENI_DATE)
    else:
        await message.answer("Введите положительное число")

@config.dp.message(StateFilter(SurveyStates.PENI_DATE))
async def handle_date_peni_input(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    can_create, error_message = await check_document_limits(user_id, "create")
    if not can_create:
        await message.answer(f"❌ {error_message}", reply_markup=tenant_actions_keyboard())
        await state.set_state(SurveyStates.TENANT_ACTION)
        return

    try:
        date = datetime.strptime(message.text.strip(), "%d.%m.%Y")
        user_data = await state.get_data()
        tenant_id = user_data["tenant_id"]
        peni_days = user_data["day_peni"]

        pdf_path = await generate_document.create_act(
            user_id,
            tenant_id,
            template_name=f"ШАБЛОН_Счет_за_просрочку.xlsx",
            peni_days=peni_days,
            custom_date=date
        )
        with open(pdf_path, 'rb') as f:
            pdf_data = f.read()

        await state.update_data(
            pdf_data=pdf_data,
            pdf_filename=os.path.basename(pdf_path),
            tenant_id=tenant_id
        )
        await message.answer_document(
            document=BufferedInputFile(pdf_data, filename=os.path.basename(pdf_path)),
            caption="✅ Документ успешно сформирован"
        )

        await message.answer("Отправить документ на email арендатора?", reply_markup=after_generation_keyboard())
        await state.set_state(SurveyStates.SEND_CONFIRMATION)

    except ValueError:
        await message.answer("❌ Неверный формат даты. Введите ДД.ММ.ГГГГ:")
    except Exception as e:
        logger.error(f"Ошибка генерации документов: {str(e)}")
        await message.answer("⚠️ Произошла ошибка при формировании документов")

@config.dp.message(StateFilter(SurveyStates.ENTER_RENT_DATE))
async def handle_rent_date_input(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    can_create, error_message = await check_document_limits(user_id, "create")
    if not can_create:
        await message.answer(f"❌ {error_message}", reply_markup=tenant_actions_keyboard())
        await state.set_state(SurveyStates.TENANT_ACTION)
        return

    try:
        date = datetime.strptime(message.text.strip(), "%d.%m.%Y")
        user_data = await state.get_data()
        tenant_id = user_data["tenant_id"]
        meters = user_data["meters"]

        act_path = await generate_document.create_act(
            user_id,
            tenant_id,
            template_name=f"ШАБЛОН_Акт_ком_услуги{len(meters)}.xlsx",
            meters_data=meters,
            custom_date=date
        )

        invoice_path = await generate_document.create_act(
            user_id,
            tenant_id,
            template_name="ШАБЛОН_Счет_за_электричество.xlsx",
            meters_data=meters,
            custom_date=date
        )

        await state.update_data(
            pdf_data=[act_path, invoice_path],
            pdf_filename=[
                os.path.basename(act_path),
                os.path.basename(invoice_path)
            ]
        )

        await send_documents(message, act_path, invoice_path)
        await message.answer("Отправить документы на email арендатора?", reply_markup=after_generation_keyboard())
        await state.set_state(SurveyStates.SEND_CONFIRMATION)

    except ValueError:
        await message.answer("❌ Неверный формат даты. Введите ДД.ММ.ГГГГ:")
    except Exception as e:
        logger.error(f"Ошибка генерации документов: {str(e)}")
        await message.answer("⚠️ Произошла ошибка при формировании документов")

@config.dp.message(StateFilter(SurveyStates.ENTER_RENT_DATE1))
async def handle_rent_date1(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    can_create, error_message = await check_document_limits(user_id, "create")
    if not can_create:
        await message.answer(f"❌ {error_message}", reply_markup=tenant_actions_keyboard())
        await state.set_state(SurveyStates.TENANT_ACTION)
        return

    try:
        date = datetime.strptime(message.text.strip(), "%d.%m.%Y")
        user_data = await state.get_data()
        tenant_id = user_data["tenant_id"]

        template_name = "ШАБЛОН_Счет_за_аренду.xlsx"
        pdf_path = await generate_document.create_act(user_id, tenant_id, template_name, custom_date=date)

        with open(pdf_path, 'rb') as f:
            pdf_data = f.read()

        await state.update_data(
            pdf_data=pdf_data,
            pdf_filename=os.path.basename(pdf_path),
            tenant_id=tenant_id
        )

        await message.answer_document(
            document=BufferedInputFile(pdf_data, filename=os.path.basename(pdf_path)),
            caption="✅ Документ успешно сформирован"
        )

        await message.answer("Отправить документ на email арендатора?", reply_markup=after_generation_keyboard())
        await state.set_state(SurveyStates.SEND_CONFIRMATION)

    except ValueError:
        await message.answer("❌ Неверный формат даты. Введите ДД.ММ.ГГГГ:")
    except Exception as e:
        logger.error(f"Ошибка генерации документов: {str(e)}")
        await message.answer("⚠️ Произошла ошибка при формировании документов")

@config.dp.message(StateFilter(SurveyStates.SELECT_DOCUMENT_TYPE))
async def handle_document_selection(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    doc_type = message.text
    user_data = await state.get_data()
    tenant_id = user_data.get('tenant_id')

    if doc_type == "↩️ Назад":
        await state.set_state(SurveyStates.TENANT_ACTION)
        await message.answer("🔧 Выберите действие:", reply_markup=tenant_actions_keyboard())
        return

    # Проверка лимита на создание документов
    can_create, error_message = await check_document_limits(user_id, "create")
    if not can_create:
        await message.answer(f"❌ {error_message}", reply_markup=tenant_actions_keyboard())
        await state.set_state(SurveyStates.TENANT_ACTION)
        return

    try:
        templates = {
            "Акт аренда": "ШАБЛОН_Акт_аренда.xlsx",
            "Счет за аренду": None,
            "Счет и акт электричество": None,
            "Счет пени": None
        }
        if doc_type == "Счет пени":
            await message.answer("Сколько дней просрочки у арендатора?", reply_markup=ReplyKeyboardRemove())
            await state.set_state(SurveyStates.ENTER_PENI)
            return

        if doc_type == "Счет и акт электричество":
            await message.answer("🔢 Сколько счетчиков у арендатора? (1-5)", reply_markup=ReplyKeyboardRemove())
            await state.set_state(SurveyStates.SELECT_METER_COUNT)
            return

        if doc_type == "Счет за аренду":
            await state.update_data(selected_template=templates[doc_type])
            await message.answer("📅 Введите дату формирования счета (ДД.ММ.ГГГГ):", reply_markup=ReplyKeyboardRemove())
            await state.set_state(SurveyStates.ENTER_RENT_DATE1)
            return

        template_name = templates[doc_type]
        pdf_path = await generate_document.create_act(user_id, tenant_id, template_name)

        with open(pdf_path, 'rb') as f:
            pdf_data = f.read()

        await state.update_data(
            pdf_data=pdf_data,
            pdf_filename=os.path.basename(pdf_path),
            tenant_id=tenant_id
        )

        await message.answer_document(
            document=BufferedInputFile(pdf_data, filename=os.path.basename(pdf_path)),
            caption="✅ Документ успешно сформирован"
        )

        await message.answer("Отправить документ на email арендатора?", reply_markup=after_generation_keyboard())
        await state.set_state(SurveyStates.SEND_CONFIRMATION)

    except Exception as e:
        logger.error(f"Document generation error: {str(e)}")
        await message.answer("❌ Ошибка при формировании документа")
        await state.set_state(SurveyStates.TENANT_ACTION)

@config.dp.message(StateFilter(SurveyStates.SEND_CONFIRMATION))
async def handle_send_confirmation(message: types.Message, state: FSMContext):
    if message.text == "✅ Да, отправить":
        user_id = message.from_user.id

        # Проверка лимита на отправку
        can_send, error_message = await check_document_limits(user_id, "send")
        if not can_send:
            await message.answer(f"❌ {error_message}", reply_markup=tenant_actions_keyboard())
            await state.set_state(SurveyStates.TENANT_ACTION)
            return

        conn = await get_db_connection()
        try:
            user_data = await state.get_data()
            filenames = user_data.get("pdf_filename", [])
            tenant_id = user_data.get("tenant_id")
            if isinstance(filenames, str):
                filenames = [filenames]

            # Получение данных арендатора и арендодателя
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                # Получаем email и имя арендатора
                await cursor.execute("SELECT email, name FROM tenants WHERE id = %s", (tenant_id,))
                tenant_data = await cursor.fetchone()
                tenant_email = tenant_data["email"] if tenant_data else None
                tenant_name = tenant_data["name"] if tenant_data else "Арендатор"

                # Получаем имя арендодателя
                await cursor.execute("SELECT name FROM user_details WHERE host_id = %s", (user_id,))
                landlord_data = await cursor.fetchone()
                landlord_name = landlord_data["name"] if landlord_data else "Арендодатель"

            if not tenant_email:
                await message.answer("❌ Email арендатора не найден")
                return

            files_data = []
            for filename in filenames:
                xlsx_path = os.path.join(config.FILES_DIR, filename)
                pdf_filename = filename.replace(".xlsx", ".pdf")
                pdf_path = os.path.join(config.FILES_DIR, pdf_filename)

                # Проверка подписи и конвертация в PDF
                photo_path = f"data/photos/{message.from_user.id}.png"
                if os.path.exists(photo_path):
                    excel_to_pdf.convert_excel_to_pdf(xlsx_path, pdf_path, message.from_user.id)
                else:
                    await message.answer("❌ Подпись не найдена, отправка отменена.")
                    return

                # Обновление статуса документа
                async with conn.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute(
                        """UPDATE documents 
                        SET status = 'sent', file_path = %s 
                        WHERE file_path = %s AND user_id = %s AND status = 'draft' LIMIT 1""",
                        (pdf_path, xlsx_path, user_id)
                    )
                    await conn.commit()

                with open(pdf_path, "rb") as f:
                    files_data.append((f.read(), pdf_filename))

            # Формирование текста письма в зависимости от шаблона
            success_count = 0
            for file_data, filename in files_data:
                # Определяем тип документа по имени файла
                if "Акт_ком_услуги" in filename:
                    subject = "Акт коммунальных услуг"
                    body = (
                        f"Уважаемый(ая) {tenant_name},\n\n"
                        f"Арендодатель {landlord_name} направил Вам акт коммунальных услуг. "
                        f"Документ во вложении.\n\n"
                        f"Пожалуйста, ознакомьтесь с документом и свяжитесь с арендодателем при необходимости.\n\n"
                        f"Это сообщение сформировано автоматически телеграмм ботом @RentalMaster_bot. Создатель @hacker588."
                    )
                elif "Счет_за_электричество" in filename:
                    subject = "Счет за электроэнергию"
                    body = (
                        f"Уважаемый(ая) {tenant_name},\n\n"
                        f"Арендодатель {landlord_name} направил Вам счет за электроэнергию. "
                        f"Документ во вложении.\n\n"
                        f"Пожалуйста, оплатите счет в установленные сроки.\n\n"
                        f"Это сообщение сформировано автоматически телеграмм ботом @RentalMaster_bot. Создатель @hacker588."
                    )
                elif "Счет_за_аренду" in filename:
                    subject = "Счет за аренду"
                    body = (
                        f"Уважаемый(ая) {tenant_name},\n\n"
                        f"Арендодатель {landlord_name} направил Вам счет за аренду. "
                        f"Документ во вложении.\n\n"
                        f"Пожалуйста, оплатите счет в установленные сроки.\n\n"
                        f"Это сообщение сформировано автоматически телеграмм ботом @RentalMaster_bot. Создатель @hacker588."
                    )
                elif "Счет_за_просрочку" in filename:
                    subject = "Счет за просрочку платежа"
                    body = (
                        f"Уважаемый(ая) {tenant_name},\n\n"
                        f"Арендодатель {landlord_name} направил Вам счет за просрочку платежа. "
                        f"Документ во вложении.\n\n"
                        f"Пожалуйста, погасите задолженность в кратчайшие сроки.\n\n"
                        f"Это сообщение сформировано автоматически телеграмм ботом @RentalMaster_bot. Создатель @hacker588."
                    )
                elif "Акт_аренда" in filename:
                    subject = "Акт аренды"
                    body = (
                        f"Уважаемый(ая) {tenant_name},\n\n"
                        f"Арендодатель {landlord_name} направил Вам акт аренды. "
                        f"Документ во вложении.\n\n"
                        f"Пожалуйста, ознакомьтесь с документом и свяжитесь с арендодателем при необходимости.\n\n"
                        f"Это сообщение сформировано автоматически телеграмм ботом @RentalMaster_bot. Создатель @hacker588."
                    )
                else:
                    subject = "Документ по аренде"
                    body = (
                        f"Уважаемый(ая) {tenant_name},\n\n"
                        f"Арендодатель {landlord_name} направил Вам документ. "
                        f"Документ во вложении.\n\n"
                        f"Пожалуйста, ознакомьтесь с документом и свяжитесь с арендодателем при необходимости.\n\n"
                        f"Это сообщение сформировано автоматически телеграмм ботом @RentalMaster_bot. Создатель @hacker588."
                    )

                # Отправка письма
                success = await email_sender.send_email(
                    to_email=tenant_email,
                    subject=subject,
                    body=body,
                    file_data=file_data,
                    filename=filename
                )
                if success:
                    success_count += 1

            if success_count == len(files_data):
                await message.answer("✅ Все документы успешно отправлены!", reply_markup=tenant_actions_keyboard())
                await state.set_state(SurveyStates.TENANT_ACTION)
            else:
                await message.answer("⚠️ Не все документы были отправлены")

        except Exception as e:
            logger.exception(f"Ошибка отправки: {e}")
            await message.answer("🚫 Произошла ошибка при отправке")
        finally:
            await conn.ensure_closed()
    else:
        await message.answer("❌ Документы не отправлены!", reply_markup=tenant_actions_keyboard())
        await state.set_state(SurveyStates.TENANT_ACTION)