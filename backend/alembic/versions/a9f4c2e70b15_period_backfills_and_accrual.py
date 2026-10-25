# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""跨期补记台账 + 暂估凭证类型

- 新建 period_backfills：业务日期所属期间已关账时，凭证改记到后续未关账期间的
  留痕。依据《小企业会计准则》第 88 条未来适用法（差错在发现当期更正，不追溯）。
- journal_entries.entry_type 放开 'accrual'（暂估入账）。SQLite 无 ALTER
  CONSTRAINT，用 batch_alter_table 重建；PG 直接 DROP/ADD。
- 不回填数据：历史行 period_start 仍等于 occurred_on 所属月，语义未变。

Revision ID: a9f4c2e70b15
Revises: d3e5a7b9c1f4
Create Date: 2026-10-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a9f4c2e70b15"
down_revision = "d3e5a7b9c1f4"
branch_labels = None
depends_on = None

ENTRY_TYPES_WITH_ACCRUAL = "entry_type IN ('standard','opening','closing','reversal','accrual')"
ENTRY_TYPES_BEFORE = "entry_type IN ('standard','opening','closing','reversal')"


def _alter_entry_type_check(expression: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("journal_entries", recreate="always") as batch:
            batch.drop_constraint("ck_journal_entry_type", type_="check")
            batch.create_check_constraint("ck_journal_entry_type", expression)
    else:
        op.drop_constraint("ck_journal_entry_type", "journal_entries", type_="check")
        op.create_check_constraint("ck_journal_entry_type", "journal_entries", expression)


def upgrade() -> None:
    op.create_table(
        "period_backfills",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("journal_entry_id", sa.String(length=128), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("original_period", sa.Date(), nullable=False),
        sa.Column("posting_period", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(20, 2, asdecimal=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["auth_companies.company_id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["journal_entry_id"], ["journal_entries.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "journal_entry_id", name="uq_backfill_company_entry"),
        sa.CheckConstraint("status IN ('active','reversed')", name="ck_backfill_status"),
        sa.CheckConstraint("posting_period >= original_period", name="ck_backfill_period_order"),
    )
    op.create_index("ix_backfill_company_posting", "period_backfills", ["company_id", "posting_period"])
    op.create_index("ix_backfill_company_original", "period_backfills", ["company_id", "original_period"])
    op.create_index("ix_backfill_company_status", "period_backfills", ["company_id", "status"])

    _alter_entry_type_check(ENTRY_TYPES_WITH_ACCRUAL)


def downgrade() -> None:
    _alter_entry_type_check(ENTRY_TYPES_BEFORE)

    op.drop_index("ix_backfill_company_status", table_name="period_backfills")
    op.drop_index("ix_backfill_company_original", table_name="period_backfills")
    op.drop_index("ix_backfill_company_posting", table_name="period_backfills")
    op.drop_table("period_backfills")
