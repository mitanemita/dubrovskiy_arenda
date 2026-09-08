"""premises: статус занятости (свободно/занято)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-07
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("premises") as batch:
        batch.add_column(
            sa.Column("is_occupied", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("premises") as batch:
        batch.drop_column("is_occupied")
