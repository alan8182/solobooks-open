# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""支付宝收款地基：billing_orders / billing_notify_logs 两表 + auth_users.plan_expires_at。

背景：产品开始用支付宝「电脑网站支付」售卖自身 SaaS 套餐（standard/pro 年付）。
此前零计费基础——无订单表、无支付流水、auth_users 无套餐到期字段。

- billing_orders：平台自营收款订单，状态机 created → paid → activated，
  与用户账套账务层（journal_entries 等）完全隔离；
- billing_notify_logs：支付宝异步通知只增不改流水（A2「审计只增」），
  验签失败/金额不符的通知也必须留档；
- plan_expires_at：付费套餐到期日（ISO 日期串），quota_service 惰性降级为 trial。

幂等/回滚：两表均为新建（可 drop 回滚），列追加有存在性护栏，迁移可重跑。
JSON 列用 JSON().with_variant(JSONB(), "postgresql")，SQLite 测试库与 PG 通用。

Revision ID: b8d4f2a6c9e1
Revises: e5a8c3d1f7b2
Create Date: 2026-11-13
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "b8d4f2a6c9e1"
down_revision = "e5a8c3d1f7b2"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(JSONB(), "postgresql")


def _has_table(bind, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def _has_column(bind, table: str, column: str) -> bool:
    return column in {col["name"] for col in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "auth_users", "plan_expires_at"):
        op.add_column(
            "auth_users",
            sa.Column("plan_expires_at", sa.Text(), nullable=True),
        )

    if not _has_table(bind, "billing_orders"):
        op.create_table(
            "billing_orders",
            sa.Column("id", sa.String(128), primary_key=True),
            sa.Column("out_trade_no", sa.String(32), nullable=False, unique=True),
            sa.Column(
                "user_id",
                sa.String(128),
                sa.ForeignKey("auth_users.user_id"),
                nullable=False,
            ),
            sa.Column("plan_code", sa.String(32), nullable=False),
            sa.Column("period", sa.String(16), nullable=False, server_default="annual"),
            sa.Column("amount", sa.Numeric(20, 2), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="created"),
            sa.Column("subject", sa.String(128), nullable=False),
            sa.Column("trade_no", sa.String(64), nullable=False, server_default=""),
            sa.Column("buyer_logon_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("notify_payload", _json_type(), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "status IN ('created','paid','activated','closed','failed')",
                name="ck_billing_order_status",
            ),
            sa.CheckConstraint("period IN ('annual')", name="ck_billing_order_period"),
            sa.CheckConstraint("amount > 0", name="ck_billing_order_amount_positive"),
        )
        op.create_index(
            "ix_billing_orders_user_created", "billing_orders", ["user_id", "created_at"]
        )
        op.create_index("ix_billing_orders_trade_no", "billing_orders", ["trade_no"])

    if not _has_table(bind, "billing_notify_logs"):
        op.create_table(
            "billing_notify_logs",
            sa.Column("id", sa.String(128), primary_key=True),
            sa.Column("out_trade_no", sa.String(32), nullable=False, server_default=""),
            sa.Column("trade_no", sa.String(64), nullable=False, server_default=""),
            sa.Column("trade_status", sa.String(32), nullable=False, server_default=""),
            sa.Column(
                "sign_verified", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("result", sa.String(32), nullable=False, server_default=""),
            sa.Column("payload", _json_type(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_billing_notify_logs_out_trade_no", "billing_notify_logs", ["out_trade_no"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "billing_notify_logs"):
        op.drop_table("billing_notify_logs")
    if _has_table(bind, "billing_orders"):
        op.drop_table("billing_orders")
    if _has_column(bind, "auth_users", "plan_expires_at"):
        op.drop_column("auth_users", "plan_expires_at")
