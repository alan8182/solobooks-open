# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""工资模块补丁：salary_records 增加个人与单位公积金独立字段。

背景：此前 salary_records 仅有 personal_social_security 与
company_social_security 合并列，公积金与社保通过比例倒推拆分。
现增补 personal_housing_fund 与 company_housing_fund 独立列，
实现社保与公积金明细独立录入与回填，彻底杜绝比例盲拆导致的失真。

Revision ID: e5a8c3d1f7b2
Revises: b2d6f8a1c4e7
Create Date: 2026-11-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e5a8c3d1f7b2"
down_revision = "b2d6f8a1c4e7"
branch_labels = None
depends_on = None


_COLUMNS = [
    ("personal_housing_fund", sa.Column("personal_housing_fund", sa.Numeric(20, 2), nullable=True)),
    ("company_housing_fund", sa.Column("company_housing_fund", sa.Numeric(20, 2), nullable=True)),
]


def _column_names(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("salary_records"):
        return
    existing = _column_names(bind, "salary_records")
    for name, column in _COLUMNS:
        if name not in existing:
            op.add_column("salary_records", column)


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("salary_records"):
        return
    existing = _column_names(bind, "salary_records")
    for name, _ in reversed(_COLUMNS):
        if name in existing:
            op.drop_column("salary_records", name)
