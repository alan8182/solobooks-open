# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""员工花名册新增独立公积金基数列 housing_fund_base（NULL 表示跟随社保基数）。

Revision ID: f1c4e8a2b9d6
Revises: e9b3d7f2c5a8
Create Date: 2026-11-22
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "f1c4e8a2b9d6"
down_revision = "e9b3d7f2c5a8"
branch_labels = None
depends_on = None


def _column_names(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("employees"):
        return
    if "housing_fund_base" not in _column_names(bind, "employees"):
        op.add_column(
            "employees",
            sa.Column("housing_fund_base", sa.Numeric(20, 2), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("employees"):
        return
    if "housing_fund_base" in _column_names(bind, "employees"):
        op.drop_column("employees", "housing_fund_base")
