# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""报表项映射规则：把科目余额按声明口径归集到报表行（P2 双轨校验用）

- patterns 是声明式数组而非表达式：每个 pattern 自带 mode/value/sign(+1/-1)，
  能表达「前缀 + 前缀」「前缀 − 前缀」「多科目精确」，但**没有求值器**，无注入面。
- match_mode='prefix' 时 value 不得短于 4 位。本项目吃过两次亏：
  ``_sum_prefix(balances,"4")`` 把 4301 连同所有 4xxx 一起算进来；
  ``_sum_codes(["2211"])`` 精确码求和又漏掉 221102 子级，应付职工薪酬静默归零。
- 本表**不直接驱动**对外报表。规则口径先与现有硬编码口径逐行双轨比对、
  连续 N 期零差异才允许切换。报表现被 400+ 测试锁定，硬切是自杀。
- 不回填数据：新建表，历史报表行为完全不变。

Revision ID: c4d91a6b2e07
Revises: b7e2f4a1c3d9
Create Date: 2026-10-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4d91a6b2e07"
down_revision = "b7e2f4a1c3d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_line_rules",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("report", sa.String(length=32), nullable=False),
        sa.Column("line_code", sa.String(length=64), nullable=False),
        sa.Column("line_name", sa.String(length=255), nullable=False),
        sa.Column("patterns", sa.JSON(), nullable=False),
        sa.Column("sign", sa.String(length=8), nullable=False, server_default="net"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["auth_companies.company_id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "report", "line_code", name="uq_report_line_rule_line"),
        sa.CheckConstraint("report IN ('profit','balance')", name="ck_report_line_rule_report"),
        sa.CheckConstraint("sign IN ('debit','credit','net')", name="ck_report_line_rule_sign"),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_report_line_rule_status"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_report_line_rule_effective",
        ),
    )
    op.create_index(
        "ix_report_line_rule_company_report",
        "report_line_rules",
        ["company_id", "report", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_report_line_rule_company_report", table_name="report_line_rules")
    op.drop_table("report_line_rules")
