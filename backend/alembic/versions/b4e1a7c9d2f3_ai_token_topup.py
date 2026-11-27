# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""AI 点数充值：用户永久余额 + 只增流水 + 充值订单类型。

背景：充值是与订阅套餐独立的商品（1 元 = 10,000 点，1 点 = 1 token），
余额永久有效，不随套餐周期重置。现有 ``auth_users.quota_bonus_tokens`` 是
按周期重复叠加的赠送额度，不能当永久余额使用，因此新增独立列与流水表。

- ``auth_users.token_balance``：充值余额快照（允许为负，表示额度边界上的一次
  调用超额，待下次充值抵扣）；真实变动以 ``ai_token_ledger`` 为准。
- ``ai_token_ledger``：充值/超额消耗/退款/管理调整只增流水，``order_no + kind``
  唯一约束兜住同一订单重复入账或重复退款。
- ``billing_orders.order_type / token_amount``：区分套餐订单与充值订单；
  ``period`` 放开 one_time。

回滚：充值订单或流水一旦存在，降级会拒绝执行（避免丢失资金凭证）；只有
空表状态下才允许回滚。SQLite 的约束变更走 batch 重建，PostgreSQL 走原生
DROP/ADD CONSTRAINT。

Revision ID: b4e1a7c9d2f3
Revises: d3f7a9c1e5b2
Create Date: 2026-11-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "b4e1a7c9d2f3"
down_revision = "d3f7a9c1e5b2"
branch_labels = None
depends_on = None

_LEDGER_KINDS = "'topup','consume','refund','admin_grant','admin_deduct'"


def _has_column(bind, table: str, column: str) -> bool:
    return column in {col["name"] for col in sa.inspect(bind).get_columns(table)}


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def _has_unique_on(bind, table: str, column: str) -> bool:
    """table.column 是否已有唯一约束（唯一约束或唯一索引任一种）。"""
    insp = sa.inspect(bind)
    try:
        for uc in insp.get_unique_constraints(table):
            if list(uc.get("column_names") or []) == [column]:
                return True
    except Exception:  # noqa: BLE001 — 方言不支持反射唯一约束
        pass
    try:
        for ix in insp.get_indexes(table):
            if ix.get("unique") and list(ix.get("column_names") or []) == [column]:
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _period_check_sql(bind) -> str:
    try:
        checks = sa.inspect(bind).get_check_constraints("billing_orders")
    except Exception:  # noqa: BLE001 — 老库方言可能不支持反射，按不存在处理
        return ""
    for check in checks:
        if check.get("name") == "ck_billing_order_period":
            return str(check.get("sqltext") or check.get("sql") or "")
    return ""


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "auth_users", "token_balance"):
        op.add_column(
            "auth_users",
            sa.Column("token_balance", sa.Integer(), nullable=False, server_default="0"),
        )

    if not _has_table(bind, "ai_token_ledger"):
        op.create_table(
            "ai_token_ledger",
            sa.Column(
                "id",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                primary_key=True,
                autoincrement=True,
            ),
            sa.Column("user_id", sa.String(128), nullable=False),
            sa.Column("kind", sa.String(24), nullable=False),
            sa.Column("delta_tokens", sa.Integer(), nullable=False),
            sa.Column("balance_after", sa.Integer(), nullable=False),
            sa.Column("order_no", sa.String(32), nullable=True),
            sa.Column("note", sa.String(255), nullable=False, server_default=""),
            sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.ForeignKeyConstraint(
                ["user_id"], ["auth_users.user_id"], ondelete="CASCADE"
            ),
            sa.CheckConstraint(
                f"kind IN ({_LEDGER_KINDS})", name="ck_ai_token_ledger_kind"
            ),
            sa.UniqueConstraint(
                "order_no", "kind", name="uq_ai_token_ledger_order_kind"
            ),
        )
        op.create_index(
            "ix_ai_token_ledger_user_created",
            "ai_token_ledger",
            ["user_id", "created_at"],
        )

    if not _has_column(bind, "billing_orders", "order_type"):
        op.add_column(
            "billing_orders",
            sa.Column("order_type", sa.String(16), nullable=False, server_default="plan"),
        )
    if not _has_column(bind, "billing_orders", "token_amount"):
        op.add_column(
            "billing_orders",
            sa.Column("token_amount", sa.Integer(), nullable=True),
        )

    # period 约束放开 one_time。先检查当前定义，已放开则跳过（可重跑）。
    if "one_time" not in _period_check_sql(bind):
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("billing_orders", recreate="always") as batch:
                batch.drop_constraint("ck_billing_order_period", type_="check")
                batch.create_check_constraint(
                    "ck_billing_order_period", "period IN ('annual','one_time')"
                )
            # SQLite 的 batch 重建按反射复制表，匿名 UNIQUE（out_trade_no 幂等键）
            # 不会被反射回来，必须显式补一个唯一索引，否则订单号可重复入账。
            if not _has_unique_on(bind, "billing_orders", "out_trade_no"):
                op.create_index(
                    "uq_billing_orders_out_trade_no",
                    "billing_orders",
                    ["out_trade_no"],
                    unique=True,
                )
        else:
            op.drop_constraint("ck_billing_order_period", "billing_orders", type_="check")
            op.create_check_constraint(
                "ck_billing_order_period",
                "billing_orders",
                "period IN ('annual','one_time')",
            )


def downgrade() -> None:
    bind = op.get_bind()

    # 资金凭证安全闸：有充值订单或余额流水时拒绝降级，先人工归档/对账。
    if _has_table(bind, "billing_orders"):
        topup_orders = bind.execute(
            sa.text("SELECT COUNT(*) FROM billing_orders WHERE period = 'one_time'")
        ).scalar()
        if topup_orders:
            raise RuntimeError(
                f"存在 {topup_orders} 条 AI 点数充值订单，拒绝降级；请先人工对账并归档"
            )
    if _has_table(bind, "ai_token_ledger"):
        ledger_rows = bind.execute(sa.text("SELECT COUNT(*) FROM ai_token_ledger")).scalar()
        if ledger_rows:
            raise RuntimeError(
                f"存在 {ledger_rows} 条 AI 点数流水，拒绝降级；请先人工对账并归档"
            )

    if _has_table(bind, "ai_token_ledger"):
        op.drop_index("ix_ai_token_ledger_user_created", table_name="ai_token_ledger")
        op.drop_table("ai_token_ledger")

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("billing_orders", recreate="always") as batch:
            batch.drop_constraint("ck_billing_order_period", type_="check")
            batch.create_check_constraint(
                "ck_billing_order_period", "period IN ('annual')"
            )
            if _has_column(bind, "billing_orders", "token_amount"):
                batch.drop_column("token_amount")
            if _has_column(bind, "billing_orders", "order_type"):
                batch.drop_column("order_type")
        if _has_column(bind, "auth_users", "token_balance"):
            with op.batch_alter_table("auth_users") as batch:
                batch.drop_column("token_balance")
    else:
        op.drop_constraint("ck_billing_order_period", "billing_orders", type_="check")
        op.create_check_constraint(
            "ck_billing_order_period", "billing_orders", "period IN ('annual')"
        )
        if _has_column(bind, "billing_orders", "token_amount"):
            op.drop_column("billing_orders", "token_amount")
        if _has_column(bind, "billing_orders", "order_type"):
            op.drop_column("billing_orders", "order_type")
        if _has_column(bind, "auth_users", "token_balance"):
            op.drop_column("auth_users", "token_balance")
