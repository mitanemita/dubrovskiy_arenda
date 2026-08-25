import logging
import os
import logging.config
import yaml
from colorama import Fore, Style, init
from pathlib import Path
from src.utils.config import LOGS_SETTINGS_PATH, LOGS_DIR

init(autoreset=True)

class ColoredFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: Fore.BLUE,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT
    }

    def format(self, record):
        color = self.COLORS.get(record.levelno, Fore.WHITE)
        record.levelname = f"{color}{record.levelname}{Style.RESET_ALL}"
        record.msg = f"{color}{record.msg}{Style.RESET_ALL}"
        return super().format(record)


def setup_logging():
    """Настройка логирования через YAML или базовая конфигурация."""
    logger = logging.getLogger('my_bot')

    # Проверка наличия директории логов
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    if LOGS_SETTINGS_PATH.exists():
        with open(LOGS_SETTINGS_PATH, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        config['handlers']['info_file']['filename'] = str(LOGS_DIR / 'info.log')
        config['handlers']['error_file']['filename'] = str(LOGS_DIR / 'error.log')

        logging.config.dictConfig(config)
    else:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(ColoredFormatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s - [%(filename)s:%(lineno)d]'
        ))
        logging.basicConfig(
            level=logging.INFO,
            handlers=[console_handler]
        )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ColoredFormatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s - [%(filename)s:%(lineno)d]'
    ))
    console_handler.setLevel(logging.DEBUG)
    logger.addHandler(console_handler)
    logger.propagate = False


setup_logging()
logger = logging.getLogger('my_bot')
