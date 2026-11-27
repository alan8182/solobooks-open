# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""b4e1a7c9d2f3（AI 点数充值）迁移测试。

防御点（SQLite batch 重建的真实踩坑）：
- 重建 billing_orders 时匿名 UNIQUE（out_trade_no 幂等键）不会被反射回来，
  迁移必须显式补唯一索引，否则同一订单号可重复入账；
- 存量订单回填 order_type='plan'、token_balance=0，不允许 NULL；
- period 检查放开 one_time，且可重复执行；
- 有充值订单/余额流水时 downgrade 必须拒绝（资金凭证不可静默丢失）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.pool import StaticPool

MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "b4e1a7c9d2f3_ai_token_topup.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_b4e1a7c9d2f3", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_conn_with_legacy_tables():
    """迁移前的线上形态：auth_users 无 token_balance，billing_orders 无充值列。"""
    engine = sa.create_engine("sqlite://", poolclass=StaticPool)
    conn = engine.connect()
    conn.exec_driver_sql(
        """
        CREATE TABLE auth_users (
          user_id VARCHAR(128) PRIMARY KEY,
          plan_code VARCHAR(32) NOT NULL DEFAULT 'trial',
          quota_bonus_tokens INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    # 每条命名约束单独成行：SQLAlchemy 的 SQLite 反射按行解析 CHECK，
    # 挤成一行会导致 batch 重建报 No such constraint（与真实 Alembic 建表格式一致）。
    conn.exec_driver_sql(
        """
        CREATE TABLE billing_orders (
          id VARCHAR(128) PRIMARY KEY,
          out_trade_no VARCHAR(32) NOT NULL UNIQUE,
          user_id VARCHAR(128) NOT NULL,
          plan_code VARCHAR(32) NOT NULL,
          period VARCHAR(16) NOT NULL DEFAULT 'annual',
          amount NUMERIC(20, 2) NOT NULL,
          status VARCHAR(16) NOT NULL DEFAULT 'created',
          subject VARCHAR(128) NOT NULL,
          payment_channel VARCHAR(16) NOT NULL DEFAULT 'alipay',
          CONSTRAINT ck_billing_order_status CHECK (status IN ('created','paid','activated','closed','failed')),
          CONSTRAINT ck_billing_order_period CHECK (period IN ('annual')),
          CONSTRAINT ck_billing_order_amount_positive CHECK (amount > 0)
        )
        """
    )
    conn.execute(
        sa.text(
            "INSERT INTO auth_users (user_id) VALUES ('u1')"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO billing_orders (id, out_trade_no, user_id, plan_code, period,"
            " amount, status, subject)"
            " VALUES ('seed-1', 'SB2611130001', 'u1', 'standard', 'annual', 598.00,"
            " 'activated', '存量套餐单')"
        )
    )
    conn.commit()
    return engine, conn


def _run(method: str, monkeypatch, conn) -> None:
    module = _load_migration()
    operations = Operations(MigrationContext.configure(conn))
    monkeypatch.setattr(module, "op", operations)
    getattr(module, method)()
    conn.commit()


def _columns(conn, table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(conn).get_columns(table)}


def test_upgrade_backfills_columns_and_preserves_rows(monkeypatch):
    _engine, conn = _make_conn_with_legacy_tables()
    _run("upgrade", monkeypatch, conn)

    assert {"order_type", "token_amount"} <= _columns(conn, "billing_orders")
    assert "token_balance" in _columns(conn, "auth_users")
    assert "ai_token_ledger" in sa.inspect(conn).get_table_names()

    order = conn.execute(
        sa.text(
            "SELECT period, order_type, token_amount, amount, subject FROM billing_orders"
            " WHERE out_trade_no = 'SB2611130001'"
        )
    ).fetchone()
    assert order.period == "annual"
    assert order.order_type == "plan"
    assert order.token_amount is None
    assert str(order.amount) == "598"
    assert order.subject == "存量套餐单"
    assert conn.execute(sa.text("SELECT token_balance FROM auth_users")).scalar() == 0


def test_upgrade_keeps_out_trade_no_unique_after_batch_rebuild(monkeypatch):
    """batch 重建会丢匿名 UNIQUE：迁移后重复订单号必须仍被数据库拒绝。"""
    _engine, conn = _make_conn_with_legacy_tables()
    _run("upgrade", monkeypatch, conn)

    with pytest.raises(sa.exc.IntegrityError):
        conn.execute(
            sa.text(
                "INSERT INTO billing_orders (id, out_trade_no, user_id, plan_code, period,"
                " amount, status, subject)"
                " VALUES ('dup', 'SB2611130001', 'u1', 'pro', 'annual', 2980.00, 'created', '重复单')"
            )
        )


def test_upgrade_allows_one_time_period_and_is_idempotent(monkeypatch):
    _engine, conn = _make_conn_with_legacy_tables()
    _run("upgrade", monkeypatch, conn)
    _run("upgrade", monkeypatch, conn)

    conn.execute(
        sa.text(
            "INSERT INTO billing_orders (id, out_trade_no, user_id, plan_code, period,"
            " amount, status, subject, order_type, token_amount)"
            " VALUES ('t1', 'SB2611190001', 'u1', 'token_topup', 'one_time', 1.00,"
            " 'created', 'AI 点数充值 10000 点', 'token_topup', 10000)"
        )
    )
    conn.commit()
    assert conn.execute(
        sa.text("SELECT COUNT(*) FROM billing_orders WHERE period = 'one_time'")
    ).scalar() == 1


def test_downgrade_refuses_when_topup_or_ledger_rows_exist(monkeypatch):
    _engine, conn = _make_conn_with_legacy_tables()
    _run("upgrade", monkeypatch, conn)
    conn.execute(
        sa.text(
            "INSERT INTO billing_orders (id, out_trade_no, user_id, plan_code, period,"
            " amount, status, subject, order_type, token_amount)"
            " VALUES ('t1', 'SB2611190002', 'u1', 'token_topup', 'one_time', 1.00,"
            " 'activated', 'AI 点数充值 10000 点', 'token_topup', 10000)"
        )
    )
    conn.commit()
    with pytest.raises(RuntimeError, match="拒绝降级"):
        _run("downgrade", monkeypatch, conn)

    conn.execute(sa.text("DELETE FROM billing_orders WHERE order_type = 'token_topup'"))
    conn.execute(
        sa.text(
            "INSERT INTO ai_token_ledger (user_id, kind, delta_tokens, balance_after)"
            " VALUES ('u1', 'topup', 10000, 10000)"
        )
    )
    conn.commit()
    with pytest.raises(RuntimeError, match="拒绝降级"):
        _run("downgrade", monkeypatch, conn)


def test_downgrade_then_reupgrade_on_empty_topup_tables(monkeypatch):
    _engine, conn = _make_conn_with_legacy_tables()
    _run("upgrade", monkeypatch, conn)
    _run("downgrade", monkeypatch, conn)

    assert "ai_token_ledger" not in sa.inspect(conn).get_table_names()
    assert "order_type" not in _columns(conn, "billing_orders")
    assert "token_balance" not in _columns(conn, "auth_users")

    _run("upgrade", monkeypatch, conn)
    assert conn.execute(
        sa.text("SELECT order_type FROM billing_orders WHERE out_trade_no = 'SB2611130001'")
    ).scalar() == "plan"
