"""Проверка обновления схемы без потери существующих задач."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_migration_preserves_existing_completed_tasks(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    db = tmp_path / "migration.sqlite"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db}"

    def alembic(*args):
        subprocess.run([sys.executable, "-m", "alembic", *args], cwd=repo, env=env,
                       check=True, capture_output=True, text=True)

    alembic("upgrade", "0007")
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO tasks (id, landlord_id, title, priority, status, updated_at) "
                     "VALUES (1, 1, 'Старая задача', 'medium', 'done', '2026-11-20 10:00:00')")
    alembic("upgrade", "head")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT title, status, updated_at, completed_at FROM tasks").fetchone() == (
            "Старая задача", "done", "2026-11-20 10:00:00", None)
    alembic("downgrade", "0007")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT title, status FROM tasks").fetchone() == ("Старая задача", "done")
