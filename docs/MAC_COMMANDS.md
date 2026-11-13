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

Бот работает внутри Docker, поэтому **сначала всегда запускается Docker Desktop**, потом бот.

### Одной командой (рекомендуется)
Запускает Docker, ждёт, пока он будет готов, и запускает бота:
```bash
open -a Docker; until docker info >/dev/null 2>&1; do sleep 2; done; docker compose up -d db migrate api bot
```

### По шагам
Запустить Docker Desktop:
```bash
open -a Docker
```

Подождать 20–60 секунд и проверить, что Docker готов (должен вывести номер версии, например `28.x.x`):
```bash
docker info --format '{{.ServerVersion}}'
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

Закрыть Docker Desktop (освобождает память Мака; сначала остановите бота командой выше):
```bash
osascript -e 'quit app "Docker"'
```
или значок кита в верхней строке → *Quit Docker Desktop*.

После закрытия Docker бот не работает. Для следующего запуска — раздел 2.

### Остановить всё и закрыть Docker одной командой
```bash
docker compose down && osascript -e 'quit app "Docker"'
```

---

## 4. Обновление до новой версии

Docker должен быть запущен (раздел 2).

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

## 6. Частые ошибки

### `failed to connect to the docker API … docker.sock` / `Cannot connect to the Docker daemon`
Docker Desktop не запущен. Запустите и дождитесь готовности:
```bash
open -a Docker; until docker info >/dev/null 2>&1; do sleep 2; done; echo "Docker готов"
```
Затем снова команду запуска бота.

### `zsh: command not found: docker`
Docker Desktop не установлен или ещё ни разу не открывался. Установить:
```bash
brew install --cask docker-desktop
```
Потом `open -a Docker`, принять соглашение, открыть новое окно Терминала.

### Автозапуск Docker при включении Мака
Docker Desktop → ⚙️ *Settings* → *General* → **Start Docker Desktop when you sign in to your computer**.
Бот сам при этом не запускается — после включения Мака выполните команду запуска (раздел 2).
Если бот был запущен, когда Мак выключили, он поднимется сам вместе с Docker
(у сервисов стоит `restart: unless-stopped`); после `docker compose down` — нет.

### Связь с Telegram

Ошибка в логах бота `Cannot connect to host api.telegram.org` — проверить связь с Мака:
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
| Запустить Docker | `open -a Docker` (подождать, пока кит перестанет мигать) |
| Docker готов? | `docker info --format '{{.ServerVersion}}'` |
| Запустить бота | `docker compose up -d db migrate api bot` |
| Статус | `docker compose ps` |
| Логи бота | `docker compose logs -f --tail 100 bot` |
| Обновить | `git pull` → `docker compose up -d --build db migrate api bot` |
| Перезапустить бота | `docker compose restart bot` |
| Остановить бота | `docker compose down` |
| Закрыть Docker | `osascript -e 'quit app "Docker"'` |
| Ошибка `docker.sock` | Docker не запущен → `open -a Docker` |
