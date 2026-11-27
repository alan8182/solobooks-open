# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""管理后台订单管理地基：billing_refunds 退款表（全额退款 + 幂等）。

背景：/console 订单管理上线，支持管理端对 paid/activated 订单发起全额退款
（alipay.trade.refund 同步结果为准）。

- billing_refunds：退款记录。out_request_no 唯一 = 幂等键（全额退款固定
  {out_trade_no}R1，并发双击由唯一约束兜底）；status submitted → success/failed。
- 刻意不改 billing_orders 的状态枚举（不加 refunded）：SQLite 改 Check 约束
  需要整表重建，风险不成比例；订单是否已退由本表 join 派生。

幂等/回滚：纯新建表（可 drop 回滚），upgrade 带存在性护栏可重跑。
JSON 列用 JSON().with_variant(JSONB(), "postgresql")，SQLite 测试库与 PG 通用。

Revision ID: c5e8b2d4a7f1
Revises: b8d4f2a6c9e1
Create Date: 2026-11-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "c5e8b2d4a7f1"
down_revision = "b8d4f2a6c9e1"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(JSONB(), "postgresql")


def _has_table(bind, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "billing_refunds"):
        op.create_table(
            "billing_refunds",
            sa.Column("id", sa.String(128), primary_key=True),
            sa.Column("out_trade_no", sa.String(32), nullable=False),
            sa.Column("trade_no", sa.String(64), nullable=False, server_default=""),
            sa.Column("out_request_no", sa.String(64), nullable=False, unique=True),
            sa.Column("refund_amount", sa.Numeric(20, 2), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="submitted"),
            sa.Column("gateway_code", sa.String(16), nullable=False, server_default=""),
            sa.Column("gateway_sub_code", sa.String(64), nullable=False, server_default=""),
            sa.Column("gateway_msg", sa.String(255), nullable=False, server_default=""),
            sa.Column("operator_user_id", sa.String(128), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("raw_response", _json_type(), nullable=True),
            sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
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
                "status IN ('submitted','success','failed')",
                name="ck_billing_refund_status",
            ),
            sa.CheckConstraint("refund_amount > 0", name="ck_billing_refund_amount_positive"),
        )
        op.create_index("ix_billing_refunds_out_trade_no", "billing_refunds", ["out_trade_no"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "billing_refunds"):
        op.drop_table("billing_refunds")
