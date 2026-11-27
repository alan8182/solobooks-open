# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""新人首月 AI 体验包（¥1 买 80 万 Token，30 天有效，每账号限购 1 次）。

在 ``auth_users`` 上新增三列（均带 server_default / nullable=True 以兼容裸 SQL 测试插入）：
- ``promo_token_balance``：剩余特惠额度（先套餐、再特惠、后永久余额）；
- ``promo_expires_at``：到期 ISO 日期串，惰性到期归零（无需 cron）；
- ``promo_purchased_at``：首次购入时间戳（退款收回额度时保留，防退款重刷）。

同时将 PostgreSQL ``billing_orders.order_type`` 从 ``VARCHAR(16)`` 扩展为
``VARCHAR(32)``（'first_month_promo' 为 17 字符）。

Revision ID: e5f1a9c3b7d2
Revises: a3c7e9b2d4f1
Create Date: 2026-11-24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "e5f1a9c3b7d2"
down_revision = "a3c7e9b2d4f1"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {col["name"] for col in sa.inspect(bind).get_columns(table)}


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "auth_users", "promo_token_balance"):
        op.add_column(
            "auth_users",
            sa.Column("promo_token_balance", sa.Integer(), nullable=False, server_default="0"),
        )
    if not _has_column(bind, "auth_users", "promo_expires_at"):
        op.add_column(
            "auth_users",
            sa.Column("promo_expires_at", sa.Text(), nullable=True),
        )
    if not _has_column(bind, "auth_users", "promo_purchased_at"):
        op.add_column(
            "auth_users",
            sa.Column("promo_purchased_at", sa.Text(), nullable=True),
        )

    if _has_table(bind, "billing_orders") and _has_column(bind, "billing_orders", "order_type"):
        if bind.dialect.name != "sqlite":
            op.alter_column(
                "billing_orders",
                "order_type",
                existing_type=sa.String(16),
                type_=sa.String(32),
                existing_nullable=False,
                existing_server_default="plan",
            )


def downgrade() -> None:
    bind = op.get_bind()

    if _has_table(bind, "billing_orders") and _has_column(bind, "billing_orders", "order_type"):
        promo_orders = bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM billing_orders "
                "WHERE order_type = 'first_month_promo' AND status IN ('paid', 'activated')"
            )
        ).scalar()
        if promo_orders:
            raise RuntimeError(
                f"存在 {promo_orders} 条已支付的新人首月体验包订单，拒绝降级；请先人工对账并归档"
            )
        if bind.dialect.name != "sqlite":
            too_long = bind.execute(
                sa.text("SELECT COUNT(*) FROM billing_orders WHERE LENGTH(order_type) > 16")
            ).scalar()
            if not too_long:
                op.alter_column(
                    "billing_orders",
                    "order_type",
                    existing_type=sa.String(32),
                    type_=sa.String(16),
                    existing_nullable=False,
                    existing_server_default="plan",
                )

    cols = [c for c in ("promo_purchased_at", "promo_expires_at", "promo_token_balance") if _has_column(bind, "auth_users", c)]
    if not cols:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("auth_users") as batch:
            for col in cols:
                batch.drop_column(col)
    else:
        for col in cols:
            op.drop_column("auth_users", col)
