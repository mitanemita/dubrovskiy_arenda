# Локальный запуск на Windows и macOS

Есть два режима:

| Режим | Что запускается | Что нужно |
|---|---|---|
| **A. Полный (Docker)** — рекомендуется | PostgreSQL, API, бот, планировщик, n8n — как на сервере | Git, Docker Desktop |
| **B. Разработка без Docker** | тесты, карта `karta.py`, при желании бот на своей БД | Git, Python 3.11, (PostgreSQL) |

> ⚠️ **Отдельный тестовый бот.** Если локально запустить бота с тем же `BOT_TOKEN`, что и на
> сервере, оба процесса будут бороться за сообщения (`TelegramConflictError`). Создайте у
> @BotFather второго бота для локальной работы и пропишите его токен в локальный `.env`.

---

## 1. Установка программ

### Windows 10/11

1. **Git** — https://git-scm.com/download/win, установка «Далее → Далее».
   При вопросе о переносах строк выберите *Checkout as-is, commit Unix-style* (`core.autocrlf=input`).
2. **Docker Desktop** (для режима A) — https://www.docker.com/products/docker-desktop/
   - при установке оставьте галочку **Use WSL 2**;
   - если попросит — выполните в PowerShell от администратора `wsl --install` и перезагрузитесь;
   - запустите Docker Desktop и дождитесь зелёного статуса *Engine running*.
3. **Python 3.11** (для режима B) — https://www.python.org/downloads/release/python-3119/
   (*Windows installer 64-bit*). На первом экране поставьте галочку **Add python.exe to PATH**.
   > Именно 3.11: на Python 3.13 библиотека `asyncpg==0.29` не устанавливается.
4. **Редактор** — VS Code (https://code.visualstudio.com/) или любой другой.
5. Проверка в PowerShell:
   ```powershell
   git --version
   docker --version
   docker compose version
   py -3.11 --version
   ```

### macOS (Intel и Apple Silicon)

1. **Homebrew** (если нет):
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```
2. **Git и Python 3.11**:
   ```bash
   brew install git python@3.11
   ```
3. **Docker Desktop** (для режима A) — https://www.docker.com/products/docker-desktop/
   (версия для *Apple chip* или *Intel chip*), запустить и дождаться *Engine running*.
4. **Для генерации PDF без Docker** (режим B, только если нужны УПД/квитанции):
   ```bash
   brew install pango
   ```
5. Проверка:
   ```bash
   git --version && docker --version && docker compose version && python3.11 --version
   ```

---

## 2. Получение проекта

Windows (PowerShell) и macOS (Терминал) одинаково:
```bash
git clone https://github.com/mitanemita/dubrovskiy_arenda.git
cd dubrovskiy_arenda
git checkout claude/migration-service-error-1bjg1w
```
> Актуальный бот (инлайн-меню, одно сообщение-панель, справочники) пока живёт в ветке
> `claude/migration-service-error-1bjg1w`, а не в `main`. Карта и эта инструкция — в ветке
> `claude/affectionate-hawking-rhkwwa` (она построена поверх рабочей); после слияния
> берите рабочую ветку.

Создать `.env` из примера:
- Windows: `copy .env.example .env`
- macOS: `cp .env.example .env`

Открыть `.env` в редакторе (сохранять в UTF-8) и заполнить минимум:

```ini
BOT_TOKEN=<токен ТЕСТОВОГО бота>
ADMIN_IDS=<ваш Telegram ID>        # узнать у @userinfobot
DB_USER=postgres
DB_PASSWORD=localpass               # любой пароль для локальной БД
DB_NAME=arenda
WEBHOOK_TOKEN=local-secret
EMAIL_PROVIDER=smtp                 # почта локально не нужна — письма просто не уйдут
EMAIL_LOGIN=
EMAIL_PASSWORD=
```

---

## 3. Режим A — всё в Docker

```bash
docker compose up -d --build
```
Первая сборка 3–10 минут. Миграции БД применяются автоматически (сервис `migrate`).

Первичная инициализация (арендодатель, настройки) выполняется автоматически при старте
бота. Ручной запуск при необходимости:
```bash
docker compose run --rm api python -m app.scripts.bootstrap
```

Проверка:
- API: http://localhost:8000/health → `{"status":"ok"}`
- Документация вебхуков: http://localhost:8000/docs
- n8n: http://localhost:5678 (логин/пароль из `N8N_USER` / `N8N_PASSWORD`)
- Бот: напишите тестовому боту `/menu` — откроется инлайн-меню в одном сообщении.
- Демо-данные для проб: `/admin` → «🧪 Заполнить тестовыми данными».

Полезное:
```bash
docker compose logs -f bot
```
```bash
docker compose ps
```
```bash
docker compose down
```
(`down` останавливает, данные БД сохраняются в томе `pg_data`; `down -v` — удалит и данные.)

Без n8n (быстрее и легче):
```bash
docker compose up -d --build db migrate api bot
```

Если порт занят (`port is already allocated`) — у вас уже работает PostgreSQL/другой сервис на
5432/8000/5678: остановите его или поменяйте левую часть в `ports:` в `docker-compose.yml`.

---

## 4. Режим B — без Docker (тесты, карта, разработка)

### Виртуальное окружение

Windows (PowerShell):
```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
```
Если PowerShell ругается на политику выполнения — один раз:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

macOS:
```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

Далее одинаково:
```bash
pip install -r requirements.txt
```

### Тесты (без БД и внешних сервисов — SQLite в памяти)
```bash
pytest -q
```
```bash
ruff check app
```
> На Windows тесты PDF-документов могут падать без GTK (WeasyPrint). Для работы с PDF
> используйте режим A — в Docker-образе всё уже установлено.

### Бот и API на локальной машине (по желанию)
Нужен PostgreSQL. Проще всего поднять только базу в Docker:
```bash
docker compose up -d db
```
В `.env` оставить `DB_HOST=localhost`, затем:
```bash
alembic upgrade head
```
```bash
python -m app.scripts.bootstrap
```
```bash
uvicorn app.main:app --reload --port 8000
```
и во втором окне терминала (с активированным `.venv`):
```bash
python -m app.bot.runner
```

---

## 5. Интерактивная карта базы локально

Карте не нужен ни сервер, ни Docker — только Python (подойдёт любой 3.9+), без сторонних библиотек.

Файлы: `app/karta/Karta-krasnodar.html` (страница) и `app/karta/karta.py` (скрипт данных).

Посмотреть карту — открыть HTML двойным щелчком в браузере (Chrome, Edge, Safari 15.4+, Firefox 121+).

Команды (Windows: `py -3.11` вместо `python3`):
```bash
python3 app/karta/karta.py show app/karta/Karta-krasnodar.html К5
```
```bash
python3 app/karta/karta.py set app/karta/Karta-krasnodar.html Е6 tenant="Иванов" rent_fact=20000
```
```bash
python3 app/karta/karta.py check app/karta/Karta-krasnodar.html
```
`check` проверяет, что цвет каждой литеры соответствует арендатору/готовности.

Скриншоты для проверки (необязательно):
```bash
pip install playwright
```
```bash
playwright install chromium
```

---

## 6. Частые проблемы

| Симптом | Причина / решение |
|---|---|
| `TelegramConflictError: terminated by other getUpdates request` | Тот же токен бота запущен на сервере — используйте тестового бота |
| `Failed building wheel for asyncpg` | Python 3.12/3.13 — поставьте 3.11 |
| `ModuleNotFoundError: httpx` при `pytest` | Старый `requirements.txt` — `git pull` и `pip install -r requirements.txt` |
| Docker: `WSL 2 installation is incomplete` | PowerShell от администратора: `wsl --install`, перезагрузка |
| `password authentication failed` | Поменяли `DB_PASSWORD` после первого запуска — `docker compose down -v` (удалит локальную БД) и запустить заново |
| Кракозябры в `.env` / названиях | Сохраните `.env` в кодировке UTF-8 |
