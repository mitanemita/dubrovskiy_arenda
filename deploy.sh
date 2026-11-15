#!/usr/bin/env bash
# Обновление до самой свежей версии и перезапуск (сервер и Mac).
#
#   ./deploy.sh                    — все сервисы (на сервере: db, api, bot, n8n)
#   ./deploy.sh db migrate api bot — только перечисленные (на Mac без n8n)
#
# Всегда берёт ветку main с GitHub (другую — DEPLOY_BRANCH=имя ./deploy.sh),
# пересобирает образы с номером версии и проверяет, что запущен именно этот коммит.
# .env и база данных не трогаются.
set -euo pipefail

cd "$(dirname "$0")"
BRANCH="${DEPLOY_BRANCH:-main}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[31m❌ %s\033[0m\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "Docker не установлен."
docker info >/dev/null 2>&1 || die "Docker не запущен (на Mac: open -a Docker, подождать и повторить)."
[ -f .env ] || die "Нет файла .env (скопируйте: cp .env.example .env и заполните)."

say "Скачиваю обновления с GitHub (ветка $BRANCH)"
git fetch origin --prune

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  git status --short --untracked-files=no
  die "В папке есть изменённые файлы (выше). Сохраните их или отмените: git stash"
fi

current="$(git rev-parse --abbrev-ref HEAD)"
if [ "$current" != "$BRANCH" ]; then
  say "Переключаюсь с ветки «$current» на «$BRANCH»"
  if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git checkout "$BRANCH"
  else
    git checkout -b "$BRANCH" --track "origin/$BRANCH"
  fi
fi
git merge --ff-only "origin/$BRANCH" \
  || die "Локальная ветка $BRANCH разошлась с GitHub. Сброс к версии с GitHub: git reset --hard origin/$BRANCH"

GIT_COMMIT="$(git rev-parse --short HEAD)"
GIT_BRANCH="$BRANCH"
BUILD_DATE="$(date '+%d.%m.%Y %H:%M')"
export GIT_COMMIT GIT_BRANCH BUILD_DATE
say "Версия: $GIT_BRANCH@$GIT_COMMIT — $(git log -1 --format='%s')"

# ${1+"$@"} — пустой список аргументов безопасен и в старом bash 3.2 (macOS) при set -u
say "Собираю и запускаю${1+: $*}"
docker compose up -d --build --remove-orphans ${1+"$@"}

say "Состояние сервисов"
docker compose ps

# Если бот запущен — убеждаемся, что в нём именно этот коммит
if docker compose ps --status running --services 2>/dev/null | grep -qx bot; then
  running="$(docker compose exec -T bot python -c 'from app.version import version_line; print(version_line())' 2>/dev/null || true)"
  case "$running" in
    *"@$GIT_COMMIT "*) printf '\n\033[32m✅ Запущена актуальная версия: %s\033[0m\n' "$running" ;;
    *) die "Бот запущен, но версия не совпала (в боте: «${running:-нет ответа}», ожидалась $GIT_COMMIT). Логи: docker compose logs --tail 50 bot" ;;
  esac
  echo "В Telegram: /version — должно быть то же самое."
fi
