# Система учёта аренды коммерческих помещений

Автоматизированный документооборот аренды: приём платежей, начисления, пеня,
формирование документов (**УПД 5.03**, счета, квитанции), напоминания и отправка
почты. Работает на сервере 24/7.

## Архитектура

```
                 ┌─────────┐   вебхуки (JSON)    ┌──────────────────────────┐
   почта  ─────► │   n8n   │ ──────────────────► │  Python-сервис (FastAPI) │
 (чеки,          │ приём + │  X-Webhook-Token    │  /webhook/incoming-payment│
  показания)     │ LLM     │                     │  /webhook/meter-reading   │
                 └─────────┘                     │                          │
                                                 │  бизнес-логика + БД      │
        ┌──────────────┐                         │  планировщик (APScheduler)│
        │ Telegram-бот │ ◄──────подтверждение────┤  генерация PDF (WeasyPrint)│
        │ (подтв./наст.)│        платежей         │  почта (aiosmtplib)      │
        └──────────────┘                         └────────────┬─────────────┘
                                                              │
                                                     ┌────────▼────────┐
                                                     │   PostgreSQL    │  (источник правды)
                                                     └─────────────────┘
```

- **PostgreSQL** — единый источник правды.
- **n8n** — ТОЛЬКО приём почты и распознавание (LLM вытаскивает поля из чеков/писем)
  и отправка готового JSON в наши вебхуки. В финансовые таблицы n8n не пишет.
- **Python-сервис** — вся бизнес-логика; единственный, кто пишет в деньги/начисления.

## Технологии

Python 3.11 · FastAPI · SQLAlchemy 2 (async, asyncpg) · Alembic · APScheduler ·
aiogram 3 · WeasyPrint · aiosmtplib · Pydantic v2.

## Структура

```
app/
  config.py           настройки из .env
  db/                 модели, enum, база, дефолты настроек
  domain/             чистые расчёты (деньги, начисления, разнос) — с тестами
  services/           бизнес-операции (единственные, кто пишет в деньги)
  api/                FastAPI-вебхуки для n8n
  scheduler/          APScheduler: начисления, напоминания, пеня
  bot/                Telegram-бот: подтверждение платежей, настройки, отчёты
  documents/          HTML-шаблоны + рендер PDF (УПД 5.03, квитанция)
  email/              отправка почты
  scripts/bootstrap.py  первичная инициализация
alembic/              миграции БД
tests/                82 теста (финансовая логика, реквизиты УПД, вебхуки, и т.д.)
```

## Развёртывание на Ubuntu (Docker)

1. Установить Docker и Docker Compose:
   ```bash
   sudo apt update && sudo apt install -y docker.io docker-compose-plugin
   ```
2. Клонировать репозиторий и подготовить окружение:
   ```bash
   git clone <repo> && cd dubrovskiy_arenda
   cp .env.example .env
   ```
3. Заполнить `.env` (обязательно):
   - `BOT_TOKEN` — токен бота от @BotFather
   - `ADMIN_IDS` — ваш Telegram ID (через запятую)
   - `DB_PASSWORD` — пароль PostgreSQL
   - `EMAIL_LOGIN` / `EMAIL_PASSWORD` — почта отправки (для Gmail — App Password)
   - `WEBHOOK_TOKEN` — общий секрет для n8n
   - `N8N_USER` / `N8N_PASSWORD` — доступ в веб-интерфейс n8n
4. Запустить (миграции применятся автоматически перед стартом):
   ```bash
   docker compose up -d --build
   ```
5. Первичная инициализация (арендодатель + владельцы + настройки), один раз:
   ```bash
   docker compose run --rm api python -m app.scripts.bootstrap
   ```
6. Проверка:
   - API: `curl http://localhost:8000/health` → `{"status":"ok"}`
   - Документация вебхуков: `http://localhost:8000/docs`
   - n8n: `http://localhost:5678`
   - Бот: напишите `/start` в Telegram.

### Сервисы docker-compose
- `db` — PostgreSQL 16
- `migrate` — одноразовое `alembic upgrade head`
- `api` — FastAPI (вебхуки) + планировщик, порт `8000`
- `bot` — Telegram-бот + доставка уведомлений (TG и email)
- `n8n` — приём почты и распознавание, порт `5678`

## Подключение n8n к вебхукам

n8n после распознавания письма отправляет HTTP-запрос в наш сервис:

- **Платёж** (чек арендатора):
  `POST http://api:8000/webhook/incoming-payment`
  Заголовок `X-Webhook-Token: <WEBHOOK_TOKEN>`
  Тело (пример):
  ```json
  {"amount": "50000.00", "contract_no": "17/2024-АР",
   "payment_date": "2026-04-04", "proof_file": "receipts/abc.jpg"}
  ```
  Для сопоставления достаточно любого из: `lease_id`, `contract_no`,
  `premises_id`, `premises_label`, `tenant_inn`.

- **Показания счётчика** (письмо электрика):
  `POST http://api:8000/webhook/meter-reading`
  Заголовок `X-Webhook-Token: <WEBHOOK_TOKEN>`
  ```json
  {"meter_serial": "М-100", "period": "2026-04-01", "curr_value": "15350"}
  ```
  `prev_value` можно не передавать — возьмётся из истории.

Оба вебхука возвращают `{"ok":..., "matched":...}`. Если сопоставить не удалось —
арендодателю приходит алерт в Telegram, данные требуют ручной обработки через бота.

## Документы

- **УПД 5.03**, статус 2 (АУСН, «Без НДС») — печатная форма PDF с подписью-картинкой.
- **Квитанция** — аренда + электричество, при просрочке добавляется пеня.

Формат утверждён приказом ФНС от 19.12.2023 № ЕД-7-26/970@ (ред. № ЕД-7-26/1032@).

## Разработка и тесты

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt aiosqlite
pytest -q          # 82 теста (SQLite in-memory, без внешних сервисов)
ruff check app     # статический анализ
```

## Безопасность

- Секреты только в `.env` (в git не попадают; см. `.gitignore`).
- Вебхуки защищены общим токеном (`X-Webhook-Token`, сравнение constant-time).
- В финансовые таблицы пишет только Python; n8n отдаёт лишь распознанный JSON.
