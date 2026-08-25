from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from src.utils.logger import logger
from aiogram import types

def menu_keyboard() -> ReplyKeyboardMarkup:
    logger.debug('Инициализация главного меню')
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="Список арендаторов (полный)"),
        KeyboardButton(text="Список арендаторов (названия)"),
        KeyboardButton(text="Добавить арендатора"),
        KeyboardButton(text="Помощь"),
        KeyboardButton(text="Профиль")
    )
    builder.adjust(2, 1, 2)
    return builder.as_markup(
        resize_keyboard=True,
        input_field_placeholder="Выберите команду..."
    )

def spis_keyboard() -> ReplyKeyboardMarkup:
    logger.debug('Инициализация меню управления')
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="Меню"))
    builder.row(
        KeyboardButton(text="Выбрать арендатора"),
        KeyboardButton(text="📤 Массовая рассылка")
    )
    return builder.as_markup(
        resize_keyboard=True,
        input_field_placeholder="Выберите действие..."
    )

def type_keyboard() -> ReplyKeyboardMarkup:
    logger.debug('Инициализация клавиатуры типа')
    builder = ReplyKeyboardBuilder()
    builder.add(
        KeyboardButton(text="Индивидуальный предприниматель"),
        KeyboardButton(text="ООО"),
        KeyboardButton(text="Физ. лицо")
    )
    builder.adjust(2)
    return builder.as_markup(
        resize_keyboard=True,
        one_time_keyboard=True
    )

def edit_tenant_fields_keyboard() -> ReplyKeyboardMarkup:
    logger.debug('Инициализация клавиатуры редактирования')
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="✏️ Название"))
    builder.row(KeyboardButton(text="✏️ Email"))
    builder.row(KeyboardButton(text="✏️ Тип"))
    builder.row(KeyboardButton(text="✏️ Сумма аренды"))
    builder.row(KeyboardButton(text="✏️ ИНН"))
    builder.row(KeyboardButton(text="✏️ Номер договора"))
    builder.row(KeyboardButton(text="✏️ Дата договора"))
    builder.row(KeyboardButton(text="✏️ Реквизиты"))
    builder.row(KeyboardButton(text="↩️ Назад"))
    return builder.as_markup(
        resize_keyboard=True,
        one_time_keyboard=True
    )

def tenant_actions_keyboard() -> ReplyKeyboardMarkup:
    logger.debug('Инициализация клавиатуры действий')
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="✏️ Изменить"),
        KeyboardButton(text="🗑️ Удалить"),
        KeyboardButton(text="📄 Сформировать документ")
    )
    builder.row(KeyboardButton(text="↩️ Назад"))
    return builder.as_markup(
        resize_keyboard=True,
        one_time_keyboard=True
    )

def confirm_delete_keyboard() -> ReplyKeyboardMarkup:
    logger.debug('Инициализация клавиатуры подтверждения')
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="✅ Да, удалить"),
        KeyboardButton(text="❌ Отмена")
    )
    return builder.as_markup(
        resize_keyboard=True,
        one_time_keyboard=True
    )

def document_types_keyboard() -> ReplyKeyboardMarkup:
    logger.debug('Инициализация клавиатуры подтверждения')
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="Акт аренда"),
        KeyboardButton(text="Счет за аренду"),
        KeyboardButton(text="Счет и акт электричество"),
        KeyboardButton(text="Счет пени"),
        KeyboardButton(text="↩️ Назад")
    )
    builder.adjust(2, 3)
    return builder.as_markup(
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
def after_generation_keyboard() -> ReplyKeyboardMarkup:
    logger.debug('Инициализация клавиатуры подтверждения')
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="✅ Да, отправить"),
        KeyboardButton(text="↩️ Назад")
    )
    return builder.as_markup(
        resize_keyboard=True,
        one_time_keyboard=True
    )

def mass_send_keyboard(template: bool = False):
    if template:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Акт аренда"), KeyboardButton(text="Счет за аренду")]
            ],
            resize_keyboard=True
        )
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📧 Всем арендаторам")],
            [KeyboardButton(text="📝 Выбрать по ID")],
            [KeyboardButton(text="↩️ Назад")]
        ],
        resize_keyboard=True
    )

def profile_edit_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✏️ Название")],
            [KeyboardButton(text="✏️ Тип")],
            [KeyboardButton(text="✏️ ИНН")],
            [KeyboardButton(text="✏️ Реквизиты")],
            [KeyboardButton(text="↩️ Назад")]
        ],
        resize_keyboard=True
    )

def profile_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Изменить")],
            [
                KeyboardButton(text="Подписка"),
                KeyboardButton(text="Фото подписи")
            ],
            [KeyboardButton(text="↩️ Назад")]
        ],
        resize_keyboard=True
    )

subscription_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="💰 Пополнить баланс")],
        [types.KeyboardButton(text="🚀 Купить подписку")],
        [types.KeyboardButton(text="↩️ Назад")]
    ],
    resize_keyboard=True
)

def no_balance_keyboard():
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="↩️ Назад")]
            ],
            resize_keyboard=True
        )