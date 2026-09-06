"""tasks: два флага напоминаний вместо одного

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-06
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("remind_pre_sent", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("remind_due_sent", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.drop_column("remind_sent")


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("remind_sent", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.drop_column("remind_due_sent")
        batch.drop_column("remind_pre_sent")
