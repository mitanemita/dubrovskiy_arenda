import os
from pathlib import Path
from aiogram import Dispatcher
from pathlib import Path
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

ALLOWED_IDS = [1374296012, 651116138]
BOT_TOKEN = "8128273016:AAEumOvms7wDwrIcfeqtFZgnqE8wqxfrbu8"
MONEY_TOKEN = '1744374395:TEST:bb8fe74c20b5c9d3bebb'
DB_HOST = "localhost"
DB_USER = "root"
DB_PASSWORD = "1Mita.rubl1"
DB_NAME = "kwit_bot"


EMAIL_CONFIG = {
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 465,
    "email": "mitanemita4@gmail.com",
    "password": "heau jdgx qaei knnj"
}