# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""通用归集（管理口径）：维度 / 归集对象 / 分配记录 / 自动归集规则

- 研发费用归集走「重写凭证行」（rd_journal_line_details），因为加计扣除申报
  要科目级证据；管理口径归集不动凭证行，分配记录独立成表、可重算、可跨维度。
  两者物理隔离，不在同一张表里用 policy 字段混着写。
- RATE = Numeric(12,6)，按比例分摊必然尾差（100 × 0.333333 × 3 = 99.99990），
  所以 amount 是唯一权威值，allocation_rate 仅作留痕。
- 不回填数据：新建表，历史凭证无分配记录，全部落在「未归集」态。

Revision ID: b7e2f4a1c3d9
Revises: a9f4c2e70b15
Create Date: 2026-10-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7e2f4a1c3d9"
down_revision = "a9f4c2e70b15"
branch_labels = None
depends_on = None


ALLOC_METHODS = "'direct','timesheet','usage','area','quantity','revenue','headcount','other'"


def upgrade() -> None:
    op.create_table(
        "accounting_dimensions",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False, server_default="custom"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["auth_companies.company_id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "code", name="uq_agg_dimension_company_code"),
        sa.UniqueConstraint("company_id", "id", name="uq_agg_dimension_company_id"),
        sa.CheckConstraint(
            "kind IN ('project','department','customer','supplier','custom')",
            name="ck_agg_dimension_kind",
        ),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_agg_dimension_status"),
    )
    op.create_index("ix_agg_dimension_company_status", "accounting_dimensions", ["company_id", "status"])

    op.create_table(
        "accounting_dimension_members",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("dimension_id", sa.String(length=128), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id", "dimension_id"],
            ["accounting_dimensions.company_id", "accounting_dimensions.id"],
            name="fk_agg_member_company_dimension",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "dimension_id", "code", name="uq_agg_member_company_dimension_code"),
        sa.UniqueConstraint("company_id", "id", name="uq_agg_member_company_id"),
        sa.CheckConstraint(
            "status IN ('draft','active','completed','archived')",
            name="ck_agg_member_status",
        ),
        sa.CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_agg_member_dates"),
    )
    op.create_index(
        "ix_agg_member_company_dimension_status",
        "accounting_dimension_members",
        ["company_id", "dimension_id", "status"],
    )

    op.create_table(
        "journal_line_allocations",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("journal_line_id", sa.String(length=128), nullable=False),
        sa.Column("dimension_id", sa.String(length=128), nullable=False),
        sa.Column("member_id", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.Numeric(20, 2, asdecimal=True), nullable=False),
        sa.Column("allocation_rate", sa.Numeric(12, 6, asdecimal=True), nullable=False, server_default="1"),
        sa.Column("allocation_method", sa.String(length=24), nullable=False, server_default="direct"),
        sa.Column("allocation_basis", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("rule_id", sa.String(length=128), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["journal_line_id"], ["journal_lines.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "dimension_id"],
            ["accounting_dimensions.company_id", "accounting_dimensions.id"],
            name="fk_agg_alloc_company_dimension",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "member_id"],
            ["accounting_dimension_members.company_id", "accounting_dimension_members.id"],
            name="fk_agg_alloc_company_member",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("journal_line_id", "dimension_id", "member_id", name="uq_agg_alloc_line_dimension_member"),
        sa.CheckConstraint("amount > 0", name="ck_agg_alloc_amount_positive"),
        sa.CheckConstraint("allocation_rate > 0 AND allocation_rate <= 1", name="ck_agg_alloc_rate"),
        sa.CheckConstraint(f"allocation_method IN ({ALLOC_METHODS})", name="ck_agg_alloc_method"),
        sa.CheckConstraint("source IN ('manual','rule')", name="ck_agg_alloc_source"),
    )
    op.create_index("ix_agg_alloc_company_member", "journal_line_allocations", ["company_id", "member_id"])
    op.create_index("ix_agg_alloc_company_dimension", "journal_line_allocations", ["company_id", "dimension_id"])
    op.create_index("ix_agg_alloc_line", "journal_line_allocations", ["journal_line_id"])

    op.create_table(
        "aggregation_rules",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("dimension_id", sa.String(length=128), nullable=False),
        sa.Column("member_id", sa.String(length=128), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("account_prefix", sa.String(length=32), nullable=True),
        sa.Column("direction", sa.String(length=8), nullable=True),
        sa.Column("keyword", sa.String(length=255), nullable=True),
        sa.Column("counterparty", sa.String(length=255), nullable=True),
        sa.Column("rate", sa.Numeric(12, 6, asdecimal=True), nullable=False, server_default="1"),
        sa.Column("method", sa.String(length=24), nullable=False, server_default="direct"),
        sa.Column("basis", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id", "dimension_id"],
            ["accounting_dimensions.company_id", "accounting_dimensions.id"],
            name="fk_agg_rule_company_dimension",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "member_id"],
            ["accounting_dimension_members.company_id", "accounting_dimension_members.id"],
            name="fk_agg_rule_company_member",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("direction IS NULL OR direction IN ('debit','credit')", name="ck_agg_rule_direction"),
        sa.CheckConstraint("rate > 0 AND rate <= 1", name="ck_agg_rule_rate"),
        sa.CheckConstraint(f"method IN ({ALLOC_METHODS})", name="ck_agg_rule_method"),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_agg_rule_status"),
    )
    op.create_index(
        "ix_agg_rule_company_dimension_priority",
        "aggregation_rules",
        ["company_id", "dimension_id", "priority"],
    )


def downgrade() -> None:
    op.drop_index("ix_agg_rule_company_dimension_priority", table_name="aggregation_rules")
    op.drop_table("aggregation_rules")

    op.drop_index("ix_agg_alloc_line", table_name="journal_line_allocations")
    op.drop_index("ix_agg_alloc_company_dimension", table_name="journal_line_allocations")
    op.drop_index("ix_agg_alloc_company_member", table_name="journal_line_allocations")
    op.drop_table("journal_line_allocations")

    op.drop_index("ix_agg_member_company_dimension_status", table_name="accounting_dimension_members")
    op.drop_table("accounting_dimension_members")

    op.drop_index("ix_agg_dimension_company_status", table_name="accounting_dimensions")
    op.drop_table("accounting_dimensions")
