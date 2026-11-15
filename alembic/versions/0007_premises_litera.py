"""premises.litera: литера помещения на территории базы (К1, Д, Е6 …)

Свободный текст: у другой базы литеры могут быть свои, со списком карты не сверяется.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-13
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("premises") as batch:
        batch.add_column(sa.Column("litera", sa.String(length=32), nullable=True))
    op.create_index("ix_premises_landlord_litera", "premises", ["landlord_id", "litera"])


def downgrade() -> None:
    op.drop_index("ix_premises_landlord_litera", table_name="premises")
    with op.batch_alter_table("premises") as batch:
        batch.drop_column("litera")
