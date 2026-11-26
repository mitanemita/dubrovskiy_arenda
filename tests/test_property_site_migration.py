"""Миграция площадок не перераспределяет и не удаляет данные учёта."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_site_migration_preserves_existing_business_data(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    db = tmp_path / "before.sqlite"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db}"

    def migrate(*args):
        subprocess.run([sys.executable, "-m", "alembic", *args], cwd=repo, env=env,
                       check=True, text=True, capture_output=True)

    migrate("upgrade", "0008")
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO landlords (id,name,type,inn,tax_mode) VALUES (1,'Владелец','ip','123456789012','ausn')")
        conn.execute("INSERT INTO tenants (id,landlord_id,name,type,inn) VALUES (1,1,'Арендатор','ooo','1234567890')")
        conn.execute("INSERT INTO premises (id,landlord_id,label,litera,is_occupied) VALUES (1,1,'Склад','Д',1)")
        conn.execute("INSERT INTO leases (id,tenant_id,premises_id,contract_no,contract_date,rent_amount,"
                     "payment_day,penalty_rate,penalty_base,status) VALUES (1,1,1,'A','2026-01-01',10000,5,0.1,'full','active')")
        conn.execute("INSERT INTO payments (id,lease_id,amount,source,status) VALUES (1,1,5000,'manual','confirmed')")
        before = [conn.execute(f"SELECT * FROM {t}").fetchall() for t in ("landlords", "tenants", "leases", "payments")]
        premises = conn.execute("SELECT * FROM premises").fetchall()
    migrate("upgrade", "head")
    with sqlite3.connect(db) as conn:
        after = [conn.execute(f"SELECT * FROM {t}").fetchall() for t in ("landlords", "tenants", "leases", "payments")]
        assert after == before
        assert [row[:-1] for row in conn.execute("SELECT * FROM premises").fetchall()] == premises
        assert conn.execute("SELECT site_id FROM premises").fetchall() == [(None,)]
        assert conn.execute("SELECT * FROM property_sites").fetchall() == []
