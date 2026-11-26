"""Готовность и предполагаемая ставка помещений.

Revision ID: 0010
Revises: 0009
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("premises", sa.Column("asking_rate", sa.Numeric(12, 2), nullable=True))
    op.add_column("premises", sa.Column("readiness", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("premises", "readiness")
    op.drop_column("premises", "asking_rate")
