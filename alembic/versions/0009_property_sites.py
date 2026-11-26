"""Площадки помещений; существующие помещения остаются без привязки.

Revision ID: 0009
Revises: 0008
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "property_sites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("landlord_id", sa.Integer(), sa.ForeignKey("landlords.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("template_key", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("landlord_id", "name", name="uq_property_site_name"),
    )
    with op.batch_alter_table("premises") as batch:
        batch.add_column(sa.Column("site_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_premises_property_site", "property_sites", ["site_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("premises") as batch:
        batch.drop_constraint("fk_premises_property_site", type_="foreignkey")
        batch.drop_column("site_id")
    op.drop_table("property_sites")
