# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""billing_orders 增加支付渠道维度 payment_channel（微信支付接入）。

背景：平台在支付宝之外新增微信支付（Native 扫码）作为第二收款渠道，
同一企业主体、同一记账口径。订单必须知道自己走了哪条渠道，否则
退款分发（alipay.trade.refund vs v3/refund）、通知路由、管理后台
展示都无从判断。

- 存量订单全是支付宝单：server_default 'alipay' 一次性回填，不做数据脚本；
- 不加 CheckConstraint 枚举：渠道白名单在应用层校验（create_order），
  未来扩展渠道（如 H5 归入 wechat）不动表结构。

幂等/回滚：列追加有存在性护栏可重跑；回滚 drop_column 用 batch 模式
兼容 SQLite（测试库）与 PostgreSQL（生产）。

Revision ID: d3f7a9c1e5b2
Revises: c5e8b2d4a7f1
Create Date: 2026-11-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "d3f7a9c1e5b2"
down_revision = "c5e8b2d4a7f1"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {col["name"] for col in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "billing_orders", "payment_channel"):
        op.add_column(
            "billing_orders",
            sa.Column("payment_channel", sa.String(16), nullable=False, server_default="alipay"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "billing_orders", "payment_channel"):
        with op.batch_alter_table("billing_orders") as batch:
            batch.drop_column("payment_channel")
