# Образ Python-сервиса (бот + планировщик, миграции)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Версия сборки (передаёт deploy.sh) — видна в /version и в логах бота
ARG GIT_COMMIT=unknown
ARG GIT_BRANCH=unknown
ARG BUILD_DATE=unknown
ENV GIT_COMMIT=$GIT_COMMIT GIT_BRANCH=$GIT_BRANCH BUILD_DATE=$BUILD_DATE

# Команда по умолчанию — бот (в docker-compose переопределяется для миграций)
CMD ["python", "-m", "app.bot.runner"]
