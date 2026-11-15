"""Версия запущенной сборки: метка UI + git-коммит, ветка и время сборки образа.

GIT_COMMIT / GIT_BRANCH / BUILD_DATE передаёт `deploy.sh` при сборке Docker-образа
(build args -> ENV). По ним в /version и в логе сразу видно, какой коммит запущен.
"""
from __future__ import annotations

import os

# Метка версии интерфейса бота — поднимай при каждом изменении бота.
BOT_UI_VERSION = "inline-menu-2026.11.15.1"


def build_info() -> dict[str, str]:
    return {
        "ui": BOT_UI_VERSION,
        "commit": os.environ.get("GIT_COMMIT") or "unknown",
        "branch": os.environ.get("GIT_BRANCH") or "unknown",
        "built": os.environ.get("BUILD_DATE") or "unknown",
    }


def version_line() -> str:
    """Одна строка: «inline-menu-… · main@abc1234 · собрано 15.11.2026 10:00»."""
    i = build_info()
    return f"{i['ui']} · {i['branch']}@{i['commit']} · собрано {i['built']}"
