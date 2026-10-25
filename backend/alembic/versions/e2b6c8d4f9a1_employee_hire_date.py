# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""员工名册补入职日期：employees.hire_date。

背景：salary_service._ensure_schema() 的 ALTER TABLE 在 PostgreSQL 上被
PostgresCompatConnection 短路，运行时新增列必须由迁移落地（同
d9f5b7e2c4a1）。入职日期用于把工资表补人限制在入职当月起，避免离职前/
入职前的月份被回溯计提。

Revision ID: e2b6c8d4f9a1
Revises: d9f5b7e2c4a1
"""

from alembic import op
import sqlalchemy as sa


revision = "e2b6c8d4f9a1"
down_revision = "d9f5b7e2c4a1"
branch_labels = None
depends_on = None


def _column_names(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("employees"):
        return
    if "hire_date" not in _column_names(bind, "employees"):
        op.add_column("employees", sa.Column("hire_date", sa.Text, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("employees"):
        return
    if "hire_date" in _column_names(bind, "employees"):
        op.drop_column("employees", "hire_date")
