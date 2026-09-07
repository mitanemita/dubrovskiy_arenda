# Хендовер: система учёта аренды (Telegram-бот + API)

Этот файл — краткий контекст для нового чата, чтобы не пересказывать всё заново.
Читай его первым, затем при необходимости смотри код по указанным путям.

## Что это за проект
Бэкенд учёта коммерческой аренды: помещения, арендаторы, договоры, счётчики,
начисления (аренда/электричество/пеня), платежи, расходы, задачи, документы
(УПД 5.03 и квитанция, PDF через WeasyPrint), уведомления (Telegram/email),
приём данных из n8n через вебхуки. Управление — через Telegram-бот.

Система однопользовательская по смыслу: один арендодатель (Landlord), несколько
операторов (User по tg_id из `ADMIN_IDS`). Всё живёт под одним landlord_id.

## Стек и запуск
- Python 3.11, aiogram 3.13, FastAPI, SQLAlchemy 2 (async, asyncpg), Alembic,
  APScheduler, WeasyPrint, PostgreSQL 16. Тесты: pytest + SQLite (in-memory).
- Docker Compose сервисы: `db`, `migrate` (alembic upgrade head), `api`
  (FastAPI+планировщик), `bot` (Telegram + доставка уведомлений), `n8n`.
- Запуск: `docker compose up -d --build`. Миграции применяются сервисом migrate.
- Первичная инициализация арендодателя/настроек — идемпотентно при старте бота
  (`app/scripts/bootstrap.py`), плюс есть ручной запуск.
- Тесты: `pip install -r requirements.txt httpx pytest pytest-asyncio aiosqlite`
  затем `python -m pytest -q` (сейчас ~137 тестов зелёные).

## Ветка и деплой
- Работаем в ветке `claude/migration-service-error-1bjg1w` (НЕ main).
- Деплой: `git fetch origin && git checkout <branch> && git pull &&
  docker compose up -d --build bot`.
- В `app/bot/handlers.py` есть `BOT_UI_VERSION` — метка версии UI. Видна в
  `/start` и `/version`. Поднимай её при каждом изменении бота, чтобы на сервере
  было видно, что приехала новая сборка (`docker compose exec bot python -c
  "from app.bot.handlers import BOT_UI_VERSION; print(BOT_UI_VERSION)"`).

## Структура (главное)
- `app/db/models.py` — ORM-модели (единый источник правды).
- `app/db/enums.py` — перечисления.
- `alembic/versions/` — миграции (последняя 0006: tasks.assignee; 0005:
  premises.is_occupied).
- `app/services/` — вся бизнес-логика, покрыта тестами, БЕЗ Telegram:
  - `directory_service` — CRUD помещений/арендаторов/договоров/счётчиков,
    авто-занятость помещений (`_sync_occupancy`, `active_occupants`),
    правка полей (`update_*_field`, `update_lease`), удаление.
  - `billing_service` — начисления (аренда/электричество/пеня).
  - `payment_service` — платежи, разнос по начислениям, `pay_charge`
    (адресная оплата одного начисления, напр. только электричество).
  - `report_service` — `payments_by_premises`, `electricity_summary`,
    `tenant_payment_status` (кто оплатил/должник + телефон + что не оплачено),
    `monthly_summary` (доход/расход/электричество/должники).
  - `expense_service`, `adjustment_service` (аудит корректировок сумм
    начислений/расходов), `settings_service`, `task_service`, `document_service`,
    `admin_service` (демо-данные и очистка БД — задачи НЕ трогает).
- `app/bot/` — слой Telegram (тонкие обёртки над сервисами):
  - `handlers.py` — /start, /version, оплата-решение, legacy reply-кнопки,
    `BOT_UI_VERSION`, `_is_allowed`.
  - `handlers_admin.py` — меню, настройки, расходы (+правка по месяцам),
    показания, задачи (адресат/выполненные), оплата, отчёты (сводка+подразделы+
    выбор месяца), правка доходов по месяцам. Тут же `edit_or_send`
    (навигация правит текущее сообщение, не спамит) и `_clip`.
  - `handlers_directory.py` — раздел «🗂 Справочники» (карточки, правка, удаление).
  - `handlers_admin_tools.py` — `/admin`: демо-данные, очистка, тест УПД/квитанции.
  - `runner.py` — сборка Dispatcher, глобальный error-handler, `FSMCleanupMiddleware`
    (удаляет сообщения пользователя во время мастеров), авто-bootstrap.
  - `keyboards.py` — инлайн-меню (`MENU_SECTIONS`, `main_menu_kb`, back/cancel).

## Ключевые UX-соглашения (важно соблюдать)
- Вся навигация — инлайн-кнопки под сообщением. Reply-клавиатуры НЕ используем.
- Навигация правит текущее сообщение через `edit_or_send` (не плодит сообщения).
- Ожидание ввода (FSM) — только для непредсказуемых значений (суммы, показания,
  ИНН, названия, даты). Всё выбираемое — кнопки.
- Нумерация в списках — сквозная 1..N (НЕ по id!). Кнопка хранит id, показываем №.
- Занятость помещения — автоматическая (договор → занято; удаление/смена → свободно).
  Ручной пометки статуса нет.
- При правках сумм — через `adjustment_service.correct_amount` (пишется аудит).
- Каждое изменение бота — поднимай `BOT_UI_VERSION`.

## Данные для аналитики (Power BI и т.п.)
Всё пишется в PostgreSQL, подключайся напрямую. Доходы = `charges`
(type: rent/electricity/penalty/other, period=1-е число месяца, amount,
paid_amount, status) + `payments` + `payment_allocations` (разнос платежа по
начислениям). Расходы = `expenses` (category, mode auto/manual, period, amount).
Показания = `meter_readings` (consumption). Корректировки = `adjustments` (аудит).
Связи: charge→lease→(tenant, premises)→landlord; expense→landlord. Периоды
нормализованы к 1-му числу месяца (см. `billing_service.period_start`).

## Что сделано в последних итерациях
Инлайн-меню, авто-инициализация, справочники с CRUD, авто-занятость, отчёты
(сводка/должники/по месяцам), задачи (адресат Митя/Алексей/текст/общая,
выполненные), оплата конкретного начисления (в т.ч. только электричество),
тест документов в /admin, правка доходов/расходов по месяцам, удаление
сообщений пользователя в мастерах, начисление аренды при создании договора.

## TODO (осознанно отложено)
1. **Мастера «в одном сообщении»**: сейчас middleware удаляет сообщения
   пользователя, но подсказки бота в многошаговых мастерах всё ещё идут
   отдельными сообщениями. Нужен «панельный» помощник (редактировать одно
   сообщение по сохранённому `_panel_id` в FSM-state) и перевод на него мастеров
   добавления/редактирования в `handlers_directory` и добавления задач.
2. **Корректировка конкретного платежа** (не только начисления): изменить/отменить
   уже проведённый платёж с пересчётом статусов начислений.
3. Возможные улучшения отчётов: экспорт (CSV/xlsx), графики.

## Как продолжать
- Меняешь логику → правь `app/services/*` и добавляй тесты в `tests/`.
- Меняешь UX → правь `app/bot/*`, поднимай `BOT_UI_VERSION`, прогоняй
  `python -m pytest -q`. Для проверки хендлеров без Telegram можно поднять
  Dispatcher с фейковым Bot (см. историю — переопределение `Bot.__call__`).
- Схему БД меняешь → новая миграция в `alembic/versions/` (цепочка down_revision)
  и поле в `app/db/models.py`.
