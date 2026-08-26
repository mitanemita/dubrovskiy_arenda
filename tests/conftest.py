"""Общие фикстуры тестов. Для тестов используем SQLite, чтобы не требовать PostgreSQL."""
import os

# ВАЖНО: задаём тестовый DSN до импорта app.db.base (движок создаётся при импорте).
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
