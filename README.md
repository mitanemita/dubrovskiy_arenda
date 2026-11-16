# Система учёта аренды коммерческих помещений

Telegram-бот учёта аренды: справочники (помещения, арендаторы, договоры, счётчики),
начисления (аренда, электричество, пеня), ручная отметка оплат, расходы, отчёты,
карта базы и задачи. Работает на сервере 24/7.

## Архитектура

```
  Telegram  ◄────►  bot (aiogram + планировщик APScheduler)  ◄────►  PostgreSQL
```

- **PostgreSQL** — единый источник правды.
- **bot** — один Python-процесс: Telegram-бот, планировщик (начисления 1-го числа,
  ежедневно пеня и напоминания по задачам) и доставка Telegram-уведомлений.

## Меню бота

- **🗂 Справочники** — помещения, арендаторы, договоры (с правкой начислений), счётчики,
  реквизиты арендодателя.
- **💼 Учёт и финансы** — 💸 Расходы · 🔢 Показания · 💰 Отметить оплату · 📊 Отчёты ·
  🗺 Карта базы.
- **📝 Задачи** — список, добавление (по одной и списком), адресат, уведомления о сроках.
- **⚙️ Настройки** — тариф и коэффициент электричества, ставка пени, фиксированные расходы.

## Технологии

Python 3.11 · aiogram 3 · SQLAlchemy 2 (async, asyncpg) · Alembic · APScheduler · Pydantic v2.

## Структура

```
app/
  config.py           настройки из .env
  db/                 модели, enum, база, дефолты настроек
  domain/             чистые расчёты (деньги, начисления, разнос) — с тестами
  services/           бизнес-операции (единственные, кто пишет в деньги)
  scheduler/          APScheduler: начисления, пеня, напоминания по задачам
  bot/                Telegram-бот: меню, справочники, учёт и финансы, задачи
  karta/              карта базы: HTML-шаблон и скрипт данных литер
  scripts/bootstrap.py  первичная инициализация
alembic/              миграции БД
tests/                тесты (SQLite in-memory)
```

## Развёртывание на Ubuntu (Docker)

1. Установить Docker (официальные пакеты Docker CE) и Compose-плагин:
   ```bash
   # официальный репозиторий Docker
   sudo apt update
   sudo apt install -y ca-certificates curl
   sudo install -m 0755 -d /etc/apt/keyrings
   sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
   sudo chmod a+r /etc/apt/keyrings/docker.asc
   echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
     https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
     sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
   sudo apt update
   sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
   sudo systemctl enable --now docker
   ```
   > Не ставьте пакет Ubuntu `docker.io` вместе с репозиторием Docker: его
   > `containerd` конфликтует с `containerd.io`. Используйте только `docker-ce`.
2. Клонировать репозиторий и подготовить окружение:
   ```bash
   git clone <repo> && cd dubrovskiy_arenda
   cp .env.example .env
   ```
3. Заполнить `.env` (обязательно):
   - `BOT_TOKEN` — токен бота от @BotFather
   - `ADMIN_IDS` — ваш Telegram ID (через запятую)
   - `DB_USER` / `DB_PASSWORD` / `DB_NAME` — доступ к PostgreSQL
4. Запустить (миграции применятся автоматически перед стартом):
   ```bash
   docker compose up -d --build
   ```
5. Первичная инициализация (арендодатель + владельцы + настройки) выполняется
   **автоматически** при старте бота (идемпотентно).
   Чтобы у оператора был доступ и не было ошибки «Нет арендодателя», укажите свой
   Telegram ID в `ADMIN_IDS` в `.env` **до** первого запуска.
6. Проверка: напишите боту `/start` в Telegram.
7. Заполнение данных через бота (раздел **🗂 Справочники**): **Помещение** (с литерой —
   по ней помещение попадает на карту базы), затем **Арендатор**, потом **Договор**,
   при необходимости — **Счётчик**. Занятость помещения определяется автоматически
   по активному договору.

### Сервисы docker-compose
- `db` — PostgreSQL 16 (данные в томе `pg_data`)
- `migrate` — одноразовое `alembic upgrade head`
- `bot` — Telegram-бот + планировщик

### Обновление до новой версии
Актуальная версия — всегда ветка **`main`**. Обновление одной командой:
```bash
cd dubrovskiy_arenda
./deploy.sh
```
Скрипт переключает папку на `main`, скачивает свежую версию с GitHub, пересобирает образы
(миграции применит сервис `migrate`) и проверяет, что бот запустился именно с этим коммитом.
Версию видно в боте по `/version`: `inline-menu-… · main@<коммит> · собрано <дата>`.
Данные в PostgreSQL сохраняются (том `pg_data`), `.env` не трогается.

## Инструкции для пользователей

- [1. Какие данные нужны от бухгалтера](docs/instructions/1_dannye_ot_buhgaltera.md)
- [2. Первичный ввод данных и запуск](docs/instructions/2_pervichnyy_vvod.md)
- [3. Ежемесячная работа](docs/instructions/3_ezhemesyachnaya_rabota.md)
- [4. Изменения, исправления, отчёты](docs/instructions/4_izmeneniya_i_otchety.md)

## Локальный запуск (Windows / macOS)

Пошагово: [docs/LOCAL_SETUP.md](docs/LOCAL_SETUP.md). Шпаргалка команд для Mac: [docs/MAC_COMMANDS.md](docs/MAC_COMMANDS.md).

## Карта базы

`app/karta/Karta-krasnodar.html` — шаблон интерактивной карты (без JS), `app/karta/karta.py` —
правка данных литер и пересборка. В боте: **💼 Учёт и финансы → 🗺 Карта базы** — бот
накладывает на шаблон актуальные данные БД (арендатор и аренда по активному договору,
сопоставление по литере помещения) и присылает HTML-файл. Цвет литеры всегда вычисляется
из данных: есть арендатор → «Арендовано».

## Разработка и тесты

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q          # SQLite in-memory, без внешних сервисов
```

## Безопасность

- Секреты только в `.env` (в git не попадают; см. `.gitignore`).
- Доступ к боту — только Telegram ID из `ADMIN_IDS` и активным пользователям в БД;
  проверяется перед каждым сообщением и нажатием кнопки (`app/bot/access.py`).
- PostgreSQL наружу не публикуется (порт доступен только внутри Docker-сети).
- Резервные копии базы (`*.sql`) содержат данные арендаторов — в git не попадают.
