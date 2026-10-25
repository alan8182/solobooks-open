# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""工资模块补丁：salary_sheets 补齐公积金/生成代次等运行时增量列。

背景：salary_service._ensure_schema() 里的 ALTER TABLE ADD COLUMN 在
PostgreSQL 下被 services/database.PostgresCompatConnection.execute 短路
（DDL 一律返回空 cursor），所以这些列从未落到生产库，导致
get_or_create_salary_sheet → _housing_fund_totals 的 UPDATE 报
UndefinedColumnError。与 employees.is_owner（d3e5a7b9c1f4 已补齐）同因。

Revision ID: d9f5b7e2c4a1
Revises: d1f4a6c8b2e3
"""

from alembic import op
import sqlalchemy as sa


revision = "d9f5b7e2c4a1"
down_revision = "d1f4a6c8b2e3"
branch_labels = None
depends_on = None


# (列名, Column) — 与 salary_service._ensure_schema() 的增量列 DDL 对齐。
_COLUMNS = [
    ("hf_paid_voucher_id", sa.Column("hf_paid_voucher_id", sa.Text, nullable=True)),
    ("ss_payment_scope", sa.Column("ss_payment_scope", sa.Text, nullable=False, server_default="combined")),
    ("accrual_generation", sa.Column("accrual_generation", sa.Integer, nullable=False, server_default="0")),
    ("payment_generation", sa.Column("payment_generation", sa.Integer, nullable=False, server_default="0")),
    ("ss_generation", sa.Column("ss_generation", sa.Integer, nullable=False, server_default="0")),
    ("tax_generation", sa.Column("tax_generation", sa.Integer, nullable=False, server_default="0")),
    ("hf_generation", sa.Column("hf_generation", sa.Integer, nullable=False, server_default="0")),
    ("personal_housing_fund_total", sa.Column("personal_housing_fund_total", sa.Numeric(20, 2), nullable=False, server_default="0.0")),
    ("company_housing_fund_total", sa.Column("company_housing_fund_total", sa.Numeric(20, 2), nullable=False, server_default="0.0")),
]


def _column_names(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("salary_sheets"):
        return
    existing = _column_names(bind, "salary_sheets")
    for name, column in _COLUMNS:
        if name not in existing:
            op.add_column("salary_sheets", column)


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("salary_sheets"):
        return
    existing = _column_names(bind, "salary_sheets")
    for name, _ in reversed(_COLUMNS):
        if name in existing:
            op.drop_column("salary_sheets", name)
