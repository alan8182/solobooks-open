# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""年度结账表：跨年账套连续性的锚点。"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8d2f6a0c4e8"
down_revision = "c1d3e5f7a9b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fiscal_year_closes",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="closed"),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("rule_versions", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("ledger_watermark", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("closed_by", sa.String(length=128), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reopened_by", sa.String(length=128), nullable=True),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopened_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"], ["auth_companies.company_id"], ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('closed','reopened')", name="ck_fiscal_year_close_status",
        ),
        sa.CheckConstraint(
            "fiscal_year >= 2000 AND fiscal_year <= 2999", name="ck_fiscal_year_close_year",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "fiscal_year", name="uq_fiscal_year_close_company_year"),
    )
    op.create_index(
        "ix_fiscal_year_closes_company", "fiscal_year_closes", ["company_id", "fiscal_year"],
    )


def downgrade() -> None:
    op.drop_index("ix_fiscal_year_closes_company", table_name="fiscal_year_closes")
    op.drop_table("fiscal_year_closes")
