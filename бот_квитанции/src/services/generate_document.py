import os
import re
import shutil
import locale
import asyncio
import openpyxl
from datetime import datetime
from openpyxl.styles import Font
from src.utils.logger import logger
from src.utils.database import get_db_connection
from num2words import num2words
from src.utils.config import BASE_DIR
import calendar

try:
    locale.setlocale(locale.LC_ALL, 'ru_RU.UTF-8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_ALL, 'Russian_Russia.1251')
    except locale.Error:
        logger.warning("Не удалось установить русскую локаль")

async def generate_document(
    data: dict,
    template_name: str,
    output_filename: str,
    template_dir: str = "data/templates",
    output_dir: str = "data/files"
) -> str:
    """Асинхронная генерация документа с поддержкой кириллицы"""
    logger.info(f"Начало генерации документа: {output_filename}")
    
    try:
        logger.debug(f"BASE_DIR = {BASE_DIR}")
        base_dir = BASE_DIR
        template_path = base_dir / template_dir / template_name
        output_path = base_dir / output_dir / output_filename

        if not os.path.exists(template_path):
            available_files = '\n'.join(os.listdir(os.path.dirname(template_path)))
            logger.error(f"Шаблон не найден. Доступные файлы:\n{available_files}")
            raise FileNotFoundError(f"Шаблон {template_name} не найден")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, shutil.copy2, template_path, output_path)
        
        await fill_template(output_path, data)
        
        logger.info(f"Документ создан: {output_path}")
        if not os.path.exists(output_path):
            logger.error(f"Файл не был создан: {output_path}")
        else:
            logger.debug(f"Файл действительно существует: {output_path}")

        return output_path

    except Exception as e:
        logger.error(f"Ошибка генерации: {str(e)}", exc_info=True)
        if output_path and os.path.exists(output_path):
            try:
                await loop.run_in_executor(None, os.remove, output_path)
            except Exception as cleanup_error:
                logger.error(f"Ошибка очистки: {cleanup_error}")
        raise

async def fill_template(file_path: str, data: dict):
    """Заполнение шаблона с сохранением форматирования"""
    try:
        loop = asyncio.get_running_loop()
        
        wb = await loop.run_in_executor(
            None,
            lambda: openpyxl.load_workbook(file_path)
        )
        
        total = 0
        not_replaced = set()

        for sheet in wb.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str):
                        original = cell.value
                        new_value = original
                        
                        original_font = Font(
                            name=cell.font.name,
                            size=cell.font.size,
                            bold=cell.font.bold,
                            italic=cell.font.italic,
                            underline=cell.font.underline,
                            strikethrough=cell.font.strikethrough,
                            color=cell.font.color
                        )

                        for key, value in data.items():
                            placeholder = f"{{{{{key}}}}}"
                            if placeholder in original:
                                new_value = new_value.replace(placeholder, str(value))
                                total += 1
                                
                        if new_value != original:
                            cell.value = new_value
                            cell.font = original_font
                            total += 1
                        elif "{{" in original and "}}" in original:
                            not_replaced.add(original)

        logger.debug(f"Произведено замен: {total}")
        if not_replaced:
            logger.warning(f"Не замененные плейсхолдеры: {', '.join(not_replaced)}")
        
        await loop.run_in_executor(
            None,
            lambda: wb.save(file_path)
        )
        
    except Exception as e:
        logger.error(f"Ошибка заполнения: {str(e)}")
        raise

async def get_tenant_data(tenant_id: int):
    """Получение данных с проверкой кодировки"""
    conn = None
    try:
        conn = await get_db_connection()
        async with conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT name, inn, dog_num, dog_dat, 
                       rent_amount, adr_tow, type, recw_inf 
                       FROM tenants WHERE id = %s""",
                    (tenant_id,)
                )
                result = await cursor.fetchone()
                if not result:
                    raise ValueError(f"Арендатор с ID {tenant_id} не найден")
                
                logger.debug(f"Данные из БД: {repr(result)}")
                return result
                
    except Exception as e:
        logger.error(f"Ошибка БД: {str(e)}")
        raise
    finally:
        if conn is not None and not conn.closed:
            await conn.close()

async def get_user_data(user_id: int):
    """Получение данных с проверкой кодировки"""
    conn = None
    try:
        conn = await get_db_connection()
        async with conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT name, inn, 
                       type, recw_inf 
                       FROM user_details WHERE host_id = %s""",
                    (user_id,)
                )
                result = await cursor.fetchone()
                if not result:
                    raise ValueError(f"Пользователь с ID {user_id} не найден")
                
                logger.debug(f"Данные из БД: {repr(result)}")
                return result
                
    except Exception as e:
        logger.error(f"Ошибка БД: {str(e)}")
        raise
    finally:
        if conn is not None and not conn.closed:
            await conn.close()

def generate_output_filename(template_name: str, db_data: dict) -> str:
    """Генерация имени файла с кириллицей"""
    clean_template = re.sub(r'\bШАБЛОН_?', '', template_name, flags=re.IGNORECASE)
    base_name = os.path.splitext(clean_template)[0].strip('_')

    clean_name = re.sub(r'[^a-zA-Zа-яА-ЯёЁ0-9_-]', '', db_data['name'])
    clean_name = clean_name.replace(' ', '_').replace('.', '')

    act_number = db_data['dog_num'].replace('/', '-')
    current_date = datetime.now().strftime("%Y%m%d")
    
    return (
        f"{base_name}_"
        f"{act_number}_"
        f"{clean_name}_"
        f"{current_date}"
        f".{template_name.split('.')[-1]}"
    )

MONTHS_GENITIVE = {
    'январь': 'января',
    'февраль': 'февраля',
    'март': 'марта',
    'апрель': 'апреля',
    'май': 'мая',
    'июнь': 'июня',
    'июль': 'июля',
    'август': 'августа',
    'сентябрь': 'сентября',
    'октябрь': 'октября',
    'ноябрь': 'ноября',
    'декабрь': 'декабря'
}

def format_date(date: datetime) -> str:
    """Форматирование даты с правильным склонением месяца"""
    date_str = date.strftime("%d %B %Y г.").lower()
    for nom, gen in MONTHS_GENITIVE.items():
        if nom in date_str:
            return date_str.replace(nom, gen).capitalize()
    return date_str

def convert_amount(amount: float) -> str:
    """Конвертация суммы в пропись с правильным склонением"""
    rub = int(amount)
    kop = int(round((amount - rub) * 100))
    
    rub_word = num2words(
        rub, 
        lang='ru',
        to='cardinal'
    ).capitalize()
    
    if rub % 100 in (11, 12, 13, 14) or rub % 10 in (0, 5, 6, 7, 8, 9):
        rub_word += " рублей"
    elif rub % 10 == 1:
        rub_word += " рубль"
    else:
        rub_word += " рубля"

    kop_str = f"{kop:02d}"
    kop_word = "копеек" if kop % 10 in (0, 5, 6, 7, 8, 9) or 11 <= kop % 100 <= 14 \
        else "копейки" if 2 <= kop % 10 <=4 else "копейка"

    return f"{rub_word} {kop_str} {kop_word}"

async def create_act(user_id: int, tenant_id: int, template_name: str, meters_data: list = [{'curr': 1, 'prev': 1}], custom_date: datetime = None, peni_days: int = 0) -> str:
    """Основной процесс создания акта"""
    conn = None
    try:
        db_data_ten = await get_tenant_data(tenant_id)
        db_data_user = await get_user_data(user_id)
        logger.debug(f"Получены данные: {db_data_ten}")
        
        mnozh = 14
        
        now = custom_date or datetime.now()
        
        amount_peni = int(db_data_ten["rent_amount"])*0.005
        amount_peni_sum = amount_peni*peni_days
        
        total_kwh = sum(m['curr'] - m['prev'] for m in meters_data)
        total_amount = total_kwh * mnozh
        total_amount_with_tax = total_amount * 0.93
        last_day_of_month = datetime(
            year=now.year,
            month=now.month,
            day=calendar.monthrange(now.year, now.month)[1]
        )
        recw_info = db_data_user['recw_inf'].split()
        
        if db_data_ten.get('type') == 'Индивидуальный предприниматель':
            krat = f"ИП {db_data_ten['name'].split()[0]} {db_data_ten['name'].split()[1][0]}. {db_data_ten['name'].split()[2][0]}."
        elif db_data_ten.get('type') == 'Физ. лицо':
            krat = f"{db_data_ten['name'].split()[0]} {db_data_ten['name'].split()[1][0]}. {db_data_ten['name'].split()[2][0]}."
        else:
            krat = f"{db_data_ten.get('type', '')} {db_data_ten.get('name', '')}".strip()
            
        
        act_data = {
            "Тип1": db_data_user['type'],
            "Множитель": mnozh,
            "Название": db_data_ten['name'],
            "Название_П": db_data_user['name'],
            "Тип": db_data_ten['type'],
            "Тип_П": db_data_user['type'],
            "Краткое_Имя": (
                f"ИП {db_data_ten['name'].split()[0]} {db_data_ten['name'].split()[1][0]}. {db_data_ten['name'].split()[2][0]}."
                if db_data_ten.get('type') == 'Индивидуальный предприниматель' 
                else f"{db_data_ten.get('type', '')} {db_data_ten.get('name', '')}".strip()
            ),
            "Краткое_Имя_П": (
                f"ИП {db_data_user['name'].split()[0]} {db_data_user['name'].split()[1][0]}. {db_data_user['name'].split()[2][0]}."
                if db_data_user.get('type') == 'Индивидуальный предприниматель' 
                else f"{db_data_user.get('type', '')} {db_data_user.get('name', '')}".strip()
            ),
            "Сч1": recw_info[-1],
            "Сч2": recw_info[1],
            "БИК": recw_info[3],
            "БАНК": ' '.join(recw_info[5:-2]),
            "ИНН_П": db_data_user['inn'],
            "ИНН": db_data_ten['inn'],
            "Сумма": total_amount_with_tax,
            "Сумма_прописью": convert_amount(total_amount_with_tax),
            "Счет": db_data_ten['recw_inf'],
            "Счет_П": db_data_user['recw_inf'],
            "Договор": db_data_ten['dog_num'],
            "Дата_договора": format_date(db_data_ten['dog_dat']),
            "Аренда": f"{db_data_ten['rent_amount']:,.2f}".replace(',', ' '),
            "Аренда_прописью": convert_amount(db_data_ten['rent_amount']),
            "Дней_просрочки": peni_days,
            "Цена_просрочка": amount_peni,
            "Сумма_просрочка": amount_peni_sum,
            "Сумма_просрочка_прописью": convert_amount(amount_peni_sum),
            "Товарный_адрес": db_data_ten['adr_tow'],
            "Электро": total_amount,
            "Электро_прописью": convert_amount(total_amount),
            "Дата_посл": last_day_of_month.strftime("%d %B %Y г."),
            "Дата": now.strftime("%d %B %Y г."),
            "Дата1": now.strftime("%B %Y г.").lower().capitalize(),
            "Акт": f"{db_data_ten['dog_num'].split('/')[0]}/{now.strftime('%m-%y')}" if '/' in db_data_ten['dog_num'] else f"{db_data_ten['dog_num']}/{now.strftime('%m-%y')}",
            **{f"Расх{i+1}_стоимость": round((m['curr'] - m['prev']) * mnozh, 2) for i, m in enumerate(meters_data)},
            **{f"Расх{i+1}_знач": round((m['curr'] - m['prev']) * mnozh * 0.93, 2) for i, m in enumerate(meters_data)},
            **{f"Пред{i+1}": m["prev"] for i, m in enumerate(meters_data)},
            **{f"Нас{i+1}": m["curr"] for i, m in enumerate(meters_data)},
            **{f"Расх{i+1}": (m["curr"] - m["prev"]) for i, m in enumerate(meters_data)},
        }
        
        output_filename = generate_output_filename(template_name, db_data_ten)
        logger.info(f"Сформировано имя файла: {output_filename}")
        
        output_path = await generate_document(
            data=act_data,
            template_name=template_name,
            output_filename=output_filename
        )
        conn = await get_db_connection()

        async with conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """INSERT INTO documents 
                    (user_id, tenant_id, document_name, status, file_path)
                    VALUES (%s, %s, %s, %s, %s)""",
                    (
                        user_id, 
                        tenant_id,
                        act_data.get("Акт", "Без названия"),
                        'draft',
                        output_path
                    )
                )
                await conn.commit()
        
        logger.info(f"Документ записан в базу данных. ID: {cursor.lastrowid}")
        return output_path
    
    except Exception as e:
        logger.error(f"Ошибка создания акта: {str(e)}", exc_info=True)
        raise
    finally:
        if conn and not conn.closed:
            await conn.close()



if __name__ == "__main__":
    async def main():
        try:
            tenant_id = int(input("Введите ID арендатора: "))
            template_name = input("Введите название шаблона: ").strip()
            
            result = await create_act(tenant_id, template_name)
            print(f"\nДокумент успешно создан:\n{result}")
            
        except Exception as e:
            print(f"\nОшибка: {str(e)}")

    asyncio.run(main())