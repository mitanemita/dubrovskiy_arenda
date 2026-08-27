"""Конфигурация приложения. Все секреты и параметры — только из окружения/.env."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки сервиса. Значения берутся из переменных окружения (или .env)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Telegram ---
    bot_token: str = Field(default="", alias="BOT_TOKEN")
    admin_ids_raw: str = Field(default="", alias="ADMIN_IDS")

    # --- PostgreSQL ---
    # Можно задать целиком DATABASE_URL, иначе собирается из частей ниже.
    database_url: str = Field(default="", alias="DATABASE_URL")
    db_host: str = Field(default="localhost", alias="DB_HOST")
    db_port: int = Field(default=5432, alias="DB_PORT")
    db_user: str = Field(default="postgres", alias="DB_USER")
    db_password: str = Field(default="", alias="DB_PASSWORD")
    db_name: str = Field(default="arenda", alias="DB_NAME")

    # --- Почта (SMTP для отправки, IMAP при необходимости приёма) ---
    smtp_server: str = Field(default="smtp.gmail.com", alias="SMTP_SERVER")
    smtp_port: int = Field(default=465, alias="SMTP_PORT")
    email_login: str = Field(default="", alias="EMAIL_LOGIN")
    email_password: str = Field(default="", alias="EMAIL_PASSWORD")

    # --- Вебхуки n8n (общий секрет для авторизации входящих запросов) ---
    webhook_token: str = Field(default="", alias="WEBHOOK_TOKEN")

    # --- Приложение ---
    timezone: str = Field(default="Europe/Moscow", alias="TZ")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def admin_ids(self) -> list[int]:
        """Список Telegram ID администраторов из строки '123,456'."""
        return [int(x) for x in self.admin_ids_raw.replace(" ", "").split(",") if x.isdigit()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_dsn(self) -> str:
        """DSN для asyncpg-движка SQLAlchemy."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache
def get_settings() -> Settings:
    """Кэшированный доступ к настройкам (единый экземпляр на процесс)."""
    return Settings()
