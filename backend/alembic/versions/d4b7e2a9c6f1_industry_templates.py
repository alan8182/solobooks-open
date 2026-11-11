# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Add backend-maintained industry templates.

Revision ID: d4b7e2a9c6f1
Revises: c9a4f2e7b6d1
"""

from alembic import op
import sqlalchemy as sa


revision = "d4b7e2a9c6f1"
down_revision = "c9a4f2e7b6d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "industry_templates",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("code", sa.String(length=96), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("group_code", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("group_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("restricted_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_industry_templates_code"),
    )
    op.create_index(
        "ix_industry_templates_enabled_sort",
        "industry_templates",
        ["enabled", "sort_order"],
        unique=False,
    )
    op.create_index(
        "ix_industry_templates_group",
        "industry_templates",
        ["group_code", "sort_order"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_industry_templates_group", table_name="industry_templates")
    op.drop_index("ix_industry_templates_enabled_sort", table_name="industry_templates")
    op.drop_table("industry_templates")
