# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""P1 WP-D：数量金额式核算——科目数量标志 + 凭证行数量/单价/单位。

- chart_accounts.quantity_enabled / unit：科目级数量核算开关与展示单位。
  用户可在科目管理里改；1403/1405/1408 由本迁移一次性默认开启（存量账套），
  新建账套由 seed_accounts 按同一清单（core/chart_policy.DEFAULT_QUANTITY_ACCOUNT_CODES）开启。
- journal_lines.quantity / unit_price / unit：三列全 nullable。数量不参与借贷
  平衡，amount 仍是唯一权威值；估价入账允许只填数量不填单价。
- 新列必须带 server_default：deploy 容器启动自动跑 alembic，且 SQLite 测试库
  靠 create_all 出表，裸 SQL 插入要能容忍省列。

Revision ID: e8a3c5f7b1d9
Revises: 547c64a1d455
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e8a3c5f7b1d9"
down_revision = "547c64a1d455"
branch_labels = None
depends_on = None

QUANTITY_FLAG_CODES = ("1403", "1405", "1408")


def _existing_columns(table: str) -> set[str]:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    chart_columns = _existing_columns("chart_accounts")
    if "quantity_enabled" not in chart_columns:
        op.add_column(
            "chart_accounts",
            sa.Column("quantity_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "unit" not in chart_columns:
        op.add_column("chart_accounts", sa.Column("unit", sa.String(16), nullable=True))

    line_columns = _existing_columns("journal_lines")
    if "quantity" not in line_columns:
        op.add_column("journal_lines", sa.Column("quantity", sa.Numeric(18, 4), nullable=True))
    if "unit_price" not in line_columns:
        op.add_column("journal_lines", sa.Column("unit_price", sa.Numeric(12, 6), nullable=True))
    if "unit" not in line_columns:
        op.add_column("journal_lines", sa.Column("unit", sa.String(16), nullable=True))

    # 存量账套的存货三科目默认开启数量核算（特性首次上线，不存在"用户主动关过"）
    op.execute(
        "UPDATE chart_accounts SET quantity_enabled = true WHERE code IN ('1403', '1405', '1408')"
    )


def downgrade() -> None:
    line_columns = _existing_columns("journal_lines")
    for name in ("unit", "unit_price", "quantity"):
        if name in line_columns:
            op.drop_column("journal_lines", name)
    chart_columns = _existing_columns("chart_accounts")
    if "unit" in chart_columns:
        op.drop_column("chart_accounts", "unit")
    if "quantity_enabled" in chart_columns:
        op.drop_column("chart_accounts", "quantity_enabled")
