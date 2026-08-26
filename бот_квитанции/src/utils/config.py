import os
from pathlib import Path
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

storage = MemoryStorage()
dp = Dispatcher(storage=storage)
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = DATA_DIR / "logs"
TEMPLATES_DIR = DATA_DIR / "templates"

LOGS_SETTINGS_PATH = LOGS_DIR / "logs_settings.yaml"
FILES_DIR = DATA_DIR / "files"

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(DATA_DIR / 'users_data', exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)


def _load_dotenv() -> None:
    """Лёгкий загрузчик .env без внешних зависимостей.

    Ищет файл .env в корне проекта и подгружает переменные,
    не перезатирая уже заданные в окружении (приоритет у реального
    окружения — это важно для Docker/сервера).
    """
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Реальное окружение имеет приоритет над .env
        os.environ.setdefault(key, value)


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    """Читает переменную окружения (секреты больше не хранятся в коде)."""
    return os.environ.get(name, default)


# --- Секреты и настройки (значения берутся ТОЛЬКО из окружения/.env) ---
# Список Telegram ID администраторов, через запятую: ADMIN_IDS=123,456
ALLOWED_IDS = [
    int(x) for x in _env("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()
]

BOT_TOKEN = _env("BOT_TOKEN")
MONEY_TOKEN = _env("MONEY_TOKEN")

DB_HOST = _env("DB_HOST", "localhost")
DB_USER = _env("DB_USER", "root")
DB_PASSWORD = _env("DB_PASSWORD")
DB_NAME = _env("DB_NAME", "kwit_bot")

EMAIL_CONFIG = {
    "smtp_server": _env("SMTP_SERVER", "smtp.gmail.com"),
    "smtp_port": int(_env("SMTP_PORT", "465")),
    "email": _env("EMAIL_LOGIN"),
    "password": _env("EMAIL_PASSWORD"),
}
