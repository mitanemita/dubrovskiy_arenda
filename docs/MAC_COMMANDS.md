# Шпаргалка: бот на Mac

Все команды вводятся в Терминале. Если путь к папке у вас другой, поправьте его в шаге 1 —
остальные команды не меняются.

---

## 1. Перейти в папку проекта

```bash
cd ~/работа/dubrovskiy_arenda
```

Если путь не подходит: напишите `cd ` (с пробелом), перетащите мышкой папку
`dubrovskiy_arenda` из Finder в окно Терминала и нажмите Enter.

Проверить, что вы в нужной папке (в списке должен быть `docker-compose.yml`):
```bash
ls
```

> ⚠️ Саму папку `dubrovskiy_arenda` **не переименовывайте**: Docker привязывает базу данных
> к имени папки, и после переименования бот запустится с пустой базой. Переносить её в другое
> место можно.

---

## 2. Запуск

Сначала откройте Docker Desktop и дождитесь статуса *Engine running*:
```bash
open -a Docker
```

Запустить бота, API и базу:
```bash
docker compose up -d db migrate api bot
```

Проверить, что всё работает (`db`, `api`, `bot` — *running*; `migrate` — *exited (0)*, это нормально):
```bash
docker compose ps
```

В Telegram напишите тестовому боту `/menu`.

---

## 3. Остановка

Остановить всё (база данных сохраняется):
```bash
docker compose down
```

Остановить только бота:
```bash
docker compose stop bot
```

Запустить бота снова:
```bash
docker compose start bot
```

Перезапустить бота (если завис или поменяли `.env`):
```bash
docker compose restart bot
```

> ❌ Не используйте `docker compose down -v` — эта команда удаляет базу данных.

После остановки Docker Desktop можно закрыть: значок кита в верхней строке → *Quit Docker Desktop*.

---

## 4. Обновление до новой версии

Скачать изменения:
```bash
git pull
```

Пересобрать и перезапустить (новые миграции базы применятся сами):
```bash
docker compose up -d --build db migrate api bot
```

Проверить версию интерфейса бота:
```bash
docker compose exec bot python -c "from app.bot.handlers import BOT_UI_VERSION; print(BOT_UI_VERSION)"
```

Посмотреть, на какой ветке вы сейчас:
```bash
git branch --show-current
```

Перейти на другую ветку (если попросят), затем снова `git pull` и пересборка:
```bash
git checkout claude/affectionate-hawking-rhkwwa
```

---

## 5. Логи (что делает бот, ошибки)

Логи бота в реальном времени (выход — `Ctrl+C`):
```bash
docker compose logs -f --tail 100 bot
```

Логи API:
```bash
docker compose logs -f --tail 100 api
```

Логи миграций (если бот не стартует после обновления):
```bash
docker compose logs migrate
```

---

## 6. Проблемы со связью с Telegram

Ошибка в логах `Cannot connect to host api.telegram.org` — проверить связь с Мака:
```bash
curl -m 15 -sS -o /dev/null -w "%{http_code} за %{time_total} с\n" https://api.telegram.org
```

…и из контейнера бота:
```bash
docker compose exec bot python -c "import urllib.request,time;t=time.time();urllib.request.urlopen('https://api.telegram.org',timeout=15);print('ok',round(time.time()-t,1),'с')"
```

Если с Мака работает, а из контейнера нет — включите в VPN режим «для всей системы» (TUN) и:
```bash
docker compose restart bot
```

---

## 7. Резервная копия базы

Сделать копию (файл появится в папке проекта):
```bash
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > backup-$(date +%Y-%m-%d).sql
```

Восстановить из копии (⚠️ перезапишет текущие данные; подставьте имя файла):
```bash
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"' < backup-2026-11-13.sql
```

---

## 8. Карта базы

В боте: `/menu` → **📊 Отчёты** → **🗺 Карта базы**.

Открыть файл карты-шаблона на Маке:
```bash
open app/karta/Karta-krasnodar.html
```

Показать данные литеры (например, К5):
```bash
python3.11 app/karta/karta.py show app/karta/Karta-krasnodar.html К5
```

Проверить, что цвета всех литер соответствуют данным:
```bash
python3.11 app/karta/karta.py check app/karta/Karta-krasnodar.html
```

---

## 9. Настройки (`.env`)

Открыть файл настроек:
```bash
open -e .env
```

После изменения `.env` перезапустите:
```bash
docker compose up -d db migrate api bot
```

---

## Коротко

| Что | Команда |
|---|---|
| В папку | `cd ~/работа/dubrovskiy_arenda` |
| Запустить | `docker compose up -d db migrate api bot` |
| Статус | `docker compose ps` |
| Логи бота | `docker compose logs -f --tail 100 bot` |
| Обновить | `git pull` → `docker compose up -d --build db migrate api bot` |
| Перезапустить бота | `docker compose restart bot` |
| Остановить | `docker compose down` |
