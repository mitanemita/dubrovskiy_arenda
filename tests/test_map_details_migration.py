"""Новые сведения карты не изменяют существующие данные."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_map_details_migration_keeps_existing_records(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    db = tmp_path / "records.sqlite"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db}"

    def migrate(*args):
        subprocess.run([sys.executable, "-m", "alembic", *args], cwd=repo, env=env,
                       check=True, capture_output=True, text=True)

    migrate("upgrade", "0009")
    tables = ("landlords", "tenants", "premises", "leases", "payments", "property_sites")
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO landlords (id,name,type,inn,tax_mode) VALUES (1,'Владелец','ip','123456789012','ausn')")
        conn.execute("INSERT INTO tenants (id,landlord_id,name,type,inn) VALUES (1,1,'Арендатор','ooo','1234567890')")
        conn.execute("INSERT INTO property_sites (id,landlord_id,name,template_key) VALUES (1,1,'Краснодар','krasnodar')")
        conn.execute("INSERT INTO premises (id,landlord_id,label,litera,site_id,area,is_occupied) "
                     "VALUES (1,1,'Склад','Д',1,200,1)")
        conn.execute("INSERT INTO leases (id,tenant_id,premises_id,contract_no,contract_date,rent_amount,"
                     "payment_day,penalty_rate,penalty_base,status) VALUES (1,1,1,'A','2026-01-01',10000,5,0.1,'full','active')")
        conn.execute("INSERT INTO payments (id,lease_id,amount,source,status) VALUES (1,1,5000,'manual','confirmed')")
        before = {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    migrate("upgrade", "head")
    with sqlite3.connect(db) as conn:
        for table in tables:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            assert ([r[:-2] for r in rows] if table == "premises" else rows) == before[table]
        assert conn.execute("SELECT asking_rate,readiness FROM premises").fetchall() == [(None, None)]
    migrate("downgrade", "0009")
    with sqlite3.connect(db) as conn:
        assert {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in tables} == before
