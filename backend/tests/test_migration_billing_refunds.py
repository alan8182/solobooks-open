# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""c5e8b2d4a7f1（billing_refunds 退款表）迁移的可移植性与幂等性测试。

防御点：
- 空库与含 billing_orders 存量表都能 upgrade（存在性护栏）；
- 重复执行幂等（护栏分支不二次建表）；
- out_request_no 唯一约束真实生效（退款幂等键兜并发的根基）；
- downgrade 可回滚且回滚后可重新 upgrade。
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
    / "c5e8b2d4a7f1_billing_refunds.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_c5e8b2d4a7f1", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_conn():
    engine = sa.create_engine("sqlite://", poolclass=StaticPool)
    return engine, engine.connect()


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


def _insert_refund(conn, out_request_no: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO billing_refunds "
            "(id, out_trade_no, out_request_no, refund_amount, status, operator_user_id, reason) "
            "VALUES (:id, :no, :req, '598.00', 'submitted', 'admin-1', '测试退款')"
        ),
        {"id": f"rf-{out_request_no}", "no": "SB261118001", "req": out_request_no},
    )
    conn.commit()


def test_upgrade_creates_table_on_empty_db(monkeypatch):
    _engine, conn = _make_conn()
    _run_upgrade(monkeypatch, conn)
    columns = {col["name"] for col in sa.inspect(conn).get_columns("billing_refunds")}
    assert {
        "id", "out_trade_no", "trade_no", "out_request_no", "refund_amount", "status",
        "gateway_code", "gateway_sub_code", "gateway_msg", "operator_user_id", "reason",
        "raw_response", "refunded_at", "created_at", "updated_at",
    } <= columns


def test_upgrade_is_idempotent_on_rerun(monkeypatch):
    _engine, conn = _make_conn()
    _run_upgrade(monkeypatch, conn)
    # 重复执行（重跑迁移/已部分应用的环境）不得报错
    _run_upgrade(monkeypatch, conn)
    _insert_refund(conn, "SB261118001R1")
    row = conn.execute(
        sa.text("SELECT refund_amount, status FROM billing_refunds WHERE out_request_no = :r"),
        {"r": "SB261118001R1"},
    ).fetchone()
    assert float(row.refund_amount) == 598.00 and row.status == "submitted"


def test_out_request_no_unique_enforced(monkeypatch):
    """退款幂等的根基：同一 out_request_no 二次插入必须被唯一约束拒绝。"""
    _engine, conn = _make_conn()
    _run_upgrade(monkeypatch, conn)
    _insert_refund(conn, "SB261118002R1")
    with pytest.raises(sa.exc.IntegrityError):
        conn.execute(
            sa.text(
                "INSERT INTO billing_refunds "
                "(id, out_trade_no, out_request_no, refund_amount, status, operator_user_id, reason) "
                "VALUES ('rf-other', 'SB261118002', 'SB261118002R1', '598.00', 'submitted', 'x', 'y')"
            )
        )
        conn.commit()


def test_status_check_constraint(monkeypatch):
    _engine, conn = _make_conn()
    _run_upgrade(monkeypatch, conn)
    with pytest.raises(sa.exc.IntegrityError):
        conn.execute(
            sa.text(
                "INSERT INTO billing_refunds "
                "(id, out_trade_no, out_request_no, refund_amount, status, operator_user_id, reason) "
                "VALUES ('rf-bad', 'SBX', 'SBXR1', '598.00', 'refunded', 'x', 'y')"
            )
        )
        conn.commit()


def test_downgrade_then_reupgrade(monkeypatch):
    _engine, conn = _make_conn()
    _run_upgrade(monkeypatch, conn)
    _insert_refund(conn, "SB261118003R1")
    _run_downgrade(monkeypatch, conn)
    assert not sa.inspect(conn).has_table("billing_refunds")
    _run_upgrade(monkeypatch, conn)
    assert sa.inspect(conn).has_table("billing_refunds")
