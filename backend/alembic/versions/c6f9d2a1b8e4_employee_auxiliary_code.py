# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""职员辅助核算：employees 表补 code 列与非空唯一索引。

直接复用薪酬管理既有的 ``employees`` 表作为「职员」辅助核算档案主数据，
避免工资花名册与辅助核算档案两张表分裂。已有存量员工的 ``code`` 保持空串
不静默改写，允许通过辅助核算档案编辑补齐，新建自动分配 ``EMxxxx`` 编码。

Revision ID: c6f9d2a1b8e4
Revises: d2b5f8a1c4e7
Create Date: 2026-11-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c6f9d2a1b8e4"
down_revision = "d2b5f8a1c4e7"
branch_labels = None
depends_on = None

_INDEX_NAME = "uq_employees_company_code_nonempty"


def _column_names(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _index_names(bind, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("employees"):
        return
    if "code" not in _column_names(bind, "employees"):
        op.add_column(
            "employees",
            sa.Column("code", sa.Text(), nullable=False, server_default=""),
        )
    if _INDEX_NAME not in _index_names(bind, "employees"):
        op.create_index(
            _INDEX_NAME,
            "employees",
            ["company_id", "code"],
            unique=True,
            sqlite_where=sa.text("code != ''"),
            postgresql_where=sa.text("code != ''"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("employees"):
        return
    if _INDEX_NAME in _index_names(bind, "employees"):
        op.drop_index(_INDEX_NAME, table_name="employees")
    if "code" in _column_names(bind, "employees"):
        op.drop_column("employees", "code")
