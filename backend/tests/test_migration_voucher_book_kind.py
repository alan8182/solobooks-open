# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""e9b3d7f2c5a8（book_kind 放宽 'voucher'）迁移测试。

防御点：
- 旧形态表插入 'voucher' 行被拦截、upgrade 后可行（CHECK 真的放宽了）；
- 重复 upgrade 幂等；
- downgrade：存在 voucher 行时拒绝执行（A2 禁止静默删数据），清理后可回滚；
- downgrade → upgrade 循环可行。
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
    / "e9b3d7f2c5a8_voucher_book_kind.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_e9b3d7f2c5a8", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_conn_with_old_table():
    """带旧 CHECK（monthly/yearly）与一行存量账簿册的内存库。

    列清单冻结为生产形态（d1f4a6c8b2e3 建表 + d2b5f8a1c4e7 补 pdf_* 后），
    与迁移里的重建清单一致。
    """
    engine = sa.create_engine("sqlite://", poolclass=StaticPool)
    conn = engine.connect()
    conn.execute(
        sa.text(
            "CREATE TABLE ledger_book_snapshots ("
            " id VARCHAR(128) PRIMARY KEY,"
            " company_id VARCHAR(128) NOT NULL,"
            " book_kind VARCHAR(16) NOT NULL DEFAULT 'monthly',"
            " period VARCHAR(7) NOT NULL,"
            " book_no VARCHAR(32) NOT NULL,"
            " status VARCHAR(16) NOT NULL DEFAULT 'issued',"
            " payload JSON NOT NULL,"
            " content_hash VARCHAR(64) NOT NULL,"
            " page_count INTEGER NOT NULL DEFAULT 1,"
            " line_count INTEGER NOT NULL DEFAULT 0,"
            " anchor JSON,"
            " generated_by VARCHAR(128),"
            " generated_at TIMESTAMP,"
            " void_reason TEXT,"
            " voided_by VARCHAR(128),"
            " voided_at TIMESTAMP,"
            " pdf_path TEXT,"
            " pdf_sha256 VARCHAR(64),"
            " pdf_size_bytes BIGINT,"
            " pdf_generated_at TIMESTAMP,"
            " CONSTRAINT ck_book_snapshot_status CHECK (status IN ('issued','void')),"
            " CONSTRAINT ck_book_snapshot_kind CHECK (book_kind IN ('monthly','yearly')),"
            " CONSTRAINT uq_book_snapshot_number UNIQUE (company_id, book_kind, period, book_no)"
            ")"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO ledger_book_snapshots (id, company_id, book_kind, period, book_no, status, payload, content_hash)"
            " VALUES ('seed-1', 'c1', 'monthly', '2026-10', '2026-10-0001', 'issued', '{}', 'a' * 64)"
        )
    )
    conn.commit()
    return engine, conn


def _run(monkeypatch, conn, direction: str) -> None:
    module = _load_migration()
    operations = Operations(MigrationContext.configure(conn))
    monkeypatch.setattr(module, "op", operations)
    getattr(module, direction)()
    conn.commit()


def _insert_voucher_row(conn, snapshot_id: str = "v-1") -> None:
    conn.execute(
        sa.text(
            "INSERT INTO ledger_book_snapshots (id, company_id, book_kind, period, book_no, status, payload, content_hash)"
            f" VALUES ('{snapshot_id}', 'c1', 'voucher', '2026-10', '2026-10-0001', 'issued', '{{}}', 'b' * 64)"
        )
    )
    conn.commit()


def test_voucher_kind_blocked_before_allowed_after(monkeypatch):
    _engine, conn = _make_conn_with_old_table()
    with pytest.raises(sa.exc.IntegrityError):
        _insert_voucher_row(conn)
    conn.execute(sa.text("ROLLBACK"))
    _run(monkeypatch, conn, "upgrade")
    _insert_voucher_row(conn)
    row = conn.execute(
        sa.text("SELECT book_kind FROM ledger_book_snapshots WHERE id = 'seed-1'")
    ).fetchone()
    assert row.book_kind == "monthly"


def test_upgrade_is_idempotent_on_rerun(monkeypatch):
    _engine, conn = _make_conn_with_old_table()
    _run(monkeypatch, conn, "upgrade")
    _run(monkeypatch, conn, "upgrade")
    _insert_voucher_row(conn)


def test_downgrade_refuses_with_voucher_rows(monkeypatch):
    """A2：存在 voucher 册时 downgrade 必须拒绝，不静默删数据。"""
    _engine, conn = _make_conn_with_old_table()
    _run(monkeypatch, conn, "upgrade")
    _insert_voucher_row(conn)
    with pytest.raises(RuntimeError, match="凭证册"):
        _run(monkeypatch, conn, "downgrade")


def test_downgrade_then_reupgrade_after_cleanup(monkeypatch):
    _engine, conn = _make_conn_with_old_table()
    _run(monkeypatch, conn, "upgrade")
    _insert_voucher_row(conn)
    conn.execute(sa.text("DELETE FROM ledger_book_snapshots WHERE book_kind = 'voucher'"))
    conn.commit()
    _run(monkeypatch, conn, "downgrade")
    with pytest.raises(sa.exc.IntegrityError):
        _insert_voucher_row(conn, "v-2")
    conn.execute(sa.text("ROLLBACK"))
    _run(monkeypatch, conn, "upgrade")
    _insert_voucher_row(conn, "v-3")
