# Базовый образ Python-сервиса (API + бот + планировщик)
FROM python:3.11-slim

# Системные зависимости WeasyPrint (Pango/Cairo/GDK-Pixbuf) + шрифты с кириллицей
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        shared-mime-info \
        fonts-dejavu \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Каталог для сгенерированных документов
RUN mkdir -p data/documents

EXPOSE 8000

# Команда по умолчанию — API (переопределяется в docker-compose для бота/миграций)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
