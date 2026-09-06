"""notifications.related_task_id (кнопки действий в напоминании по задаче)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-06
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        batch.add_column(sa.Column("related_task_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_notifications_related_task_id",
            "tasks", ["related_task_id"], ["id"], ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("fk_notifications_related_task_id", type_="foreignkey")
        batch.drop_column("related_task_id")
