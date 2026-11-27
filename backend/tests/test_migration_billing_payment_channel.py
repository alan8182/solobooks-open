# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""d3f7a9c1e5b2（billing_orders.payment_channel 渠道维度）迁移测试。

防御点：
- 存量订单（全是支付宝单）必须被 server_default 'alipay' 一次性回填，
  不允许出现 NULL / 空渠道的历史行（退款分发与通知路由按渠道判断，NULL 会走错分支）；
- 护栏幂等：重复执行不报错、不重复加列；
- downgrade 可回滚且回滚后可重新 upgrade（batch 模式兼容 SQLite）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.pool import StaticPool

MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "d3f7a9c1e5b2_billing_payment_channel.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_d3f7a9c1e5b2", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_conn_with_orders_table():
    """带 billing_orders 存量行的内存库（模拟已上线、未加渠道列的生产形态）。"""
    engine = sa.create_engine("sqlite://", poolclass=StaticPool)
    conn = engine.connect()
    conn.execute(
        sa.text(
            "CREATE TABLE billing_orders ("
            " id VARCHAR(128) PRIMARY KEY,"
            " out_trade_no VARCHAR(32) NOT NULL UNIQUE,"
            " user_id VARCHAR(128) NOT NULL,"
            " plan_code VARCHAR(32) NOT NULL,"
            " period VARCHAR(16) NOT NULL,"
            " amount NUMERIC(20, 2) NOT NULL,"
            " status VARCHAR(16) NOT NULL,"
            " subject VARCHAR(128) NOT NULL"
            ")"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO billing_orders (id, out_trade_no, user_id, plan_code, period,"
            " amount, status, subject)"
            " VALUES ('seed-1', 'SB2611130001', 'u1', 'standard', 'annual', 598.00, 'activated', '存量支付宝单')"
        )
    )
    conn.commit()
    return engine, conn


def _run_upgrade(monkeypatch, conn) -> None:
    module = _load_migration()
    operations = Operations(MigrationContext.configure(conn))
    monkeypatch.setattr(module, "op", operations)
    module.upgrade()
    conn.commit()


def _run_downgrade(monkeypatch, conn) -> None:
    module = _load_migration()
    operations = Operations(MigrationContext.configure(conn))
    monkeypatch.setattr(module, "op", operations)
    module.downgrade()
    conn.commit()


def test_existing_rows_backfilled_alipay(monkeypatch):
    """存量行必须回填 alipay：新增列 NOT NULL server_default，不允许 NULL 渠道。"""
    _engine, conn = _make_conn_with_orders_table()
    _run_upgrade(monkeypatch, conn)
    row = conn.execute(
        sa.text("SELECT payment_channel FROM billing_orders WHERE out_trade_no = 'SB2611130001'")
    ).fetchone()
    assert row.payment_channel == "alipay"


def test_new_rows_default_alipay_and_explicit_wechat(monkeypatch):
    """新行不传渠道默认 alipay；显式传 wechat 落 wechat（渠道分发的前提）。"""
    _engine, conn = _make_conn_with_orders_table()
    _run_upgrade(monkeypatch, conn)
    conn.execute(
        sa.text(
            "INSERT INTO billing_orders (id, out_trade_no, user_id, plan_code, period,"
            " amount, status, subject)"
            " VALUES ('seed-2', 'SB2611190002', 'u1', 'pro', 'annual', 2980.00, 'created', '不传渠道')"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO billing_orders (id, out_trade_no, user_id, plan_code, period,"
            " amount, status, subject, payment_channel)"
            " VALUES ('seed-3', 'SB2611190003', 'u1', 'pro', 'annual', 2980.00, 'created', '微信单', 'wechat')"
        )
    )
    conn.commit()
    rows = conn.execute(
        sa.text("SELECT out_trade_no, payment_channel FROM billing_orders ORDER BY out_trade_no")
    ).fetchall()
    channels = {r.out_trade_no: r.payment_channel for r in rows}
    assert channels["SB2611190002"] == "alipay"
    assert channels["SB2611190003"] == "wechat"


def test_upgrade_is_idempotent_on_rerun(monkeypatch):
    _engine, conn = _make_conn_with_orders_table()
    _run_upgrade(monkeypatch, conn)
    _run_upgrade(monkeypatch, conn)
    assert "payment_channel" in {
        col["name"] for col in sa.inspect(conn).get_columns("billing_orders")
    }


def test_downgrade_then_reupgrade(monkeypatch):
    _engine, conn = _make_conn_with_orders_table()
    _run_upgrade(monkeypatch, conn)
    _run_downgrade(monkeypatch, conn)
    assert "payment_channel" not in {
        col["name"] for col in sa.inspect(conn).get_columns("billing_orders")
    }
    _run_upgrade(monkeypatch, conn)
    row = conn.execute(
        sa.text("SELECT payment_channel FROM billing_orders WHERE out_trade_no = 'SB2611130001'")
    ).fetchone()
    assert row.payment_channel == "alipay"
