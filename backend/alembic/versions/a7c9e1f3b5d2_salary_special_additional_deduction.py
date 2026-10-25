# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""员工名册补专项附加扣除：employees.special_additional_deduction。

背景：工资表「个税代扣」原本纯手填，本次加入累计预扣法试算，需要按员工记住
月度专项附加扣除合计（子女教育/继续教育/房贷利息/住房租金/赡养老人/婴幼儿照护）。
与 e2b6c8d4f9a1 同因：salary_service._ensure_schema() 的 ALTER TABLE 在
PostgreSQL 下被 PostgresCompatConnection 短路（DDL 一律返回空 cursor），
运行时新增列必须由迁移落地，否则 PostgreSQL 上试算会读到不存在的列。

Revision ID: a7c9e1f3b5d2
Revises: e2b6c8d4f9a1
"""

from alembic import op
import sqlalchemy as sa


revision = "a7c9e1f3b5d2"
down_revision = "e2b6c8d4f9a1"
branch_labels = None
depends_on = None


_COLUMNS = [
    (
        "special_additional_deduction",
        sa.Column(
            "special_additional_deduction",
            sa.Numeric(20, 2),
            nullable=False,
            server_default="0.0",
        ),
    ),
]


def _column_names(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("employees"):
        return
    existing = _column_names(bind, "employees")
    for name, column in _COLUMNS:
        if name not in existing:
            op.add_column("employees", column)


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("employees"):
        return
    existing = _column_names(bind, "employees")
    for name, _ in reversed(_COLUMNS):
        if name in existing:
            op.drop_column("employees", name)
