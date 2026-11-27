# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""b8e2d4c6a1f3（attachments 表 + invoices 查验字段）迁移测试。

防御点：
- 建表幂等：重复 upgrade 不报错、不重复建表/加列；
- invoices 存量行回填 verify_status='unverified'，不允许出现 NULL 状态行；
- downgrade 可回滚且回滚后可重新 upgrade（SQLite batch 模式兼容）；
- attachments 约束生效：status / document_type CHECK 与 (company_id, sha256) 唯一。
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
    / "b8e2d4c6a1f3_attachments_einvoice_xml.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_b8e2d4c6a1f3", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_conn_with_invoices_table():
    """带 invoices 存量行的内存库（模拟已上线、未加查验列/附件表的生产形态）。"""
    engine = sa.create_engine("sqlite://", poolclass=StaticPool)
    conn = engine.connect()
    conn.execute(
        sa.text(
            "CREATE TABLE auth_companies ("
            " company_id VARCHAR(128) PRIMARY KEY,"
            " owner_user_id VARCHAR(128) NOT NULL"
            ")"
        )
    )
    conn.execute(
        sa.text(
            "CREATE TABLE invoices ("
            " id VARCHAR(128) PRIMARY KEY,"
            " company_id VARCHAR(128) NOT NULL,"
            " direction VARCHAR(16) NOT NULL,"
            " invoice_number VARCHAR(64) NOT NULL"
            ")"
        )
    )
    conn.execute(sa.text("INSERT INTO auth_companies (company_id, owner_user_id) VALUES ('c1', 'u1')"))
    conn.execute(
        sa.text(
            "INSERT INTO invoices (id, company_id, direction, invoice_number)"
            " VALUES ('inv-seed-1', 'c1', 'expense', '26337000000000000001')"
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


def test_seed_rows_get_unverified_default(monkeypatch):
    """存量发票必须一次性回填 unverified：查验状态不允许 NULL 历史行。"""
    _engine, conn = _make_conn_with_invoices_table()
    _run(monkeypatch, conn, "upgrade")
    row = conn.execute(sa.text("SELECT verify_status FROM invoices WHERE id = 'inv-seed-1'")).fetchone()
    assert row.verify_status == "unverified"
    columns = {col["name"] for col in sa.inspect(conn).get_columns("attachments")}
    assert {"invoice_id", "content_sha256", "stored_name", "status"} <= columns


def test_upgrade_is_idempotent_on_rerun(monkeypatch):
    _engine, conn = _make_conn_with_invoices_table()
    _run(monkeypatch, conn, "upgrade")
    _run(monkeypatch, conn, "upgrade")
    assert "verify_status" in {col["name"] for col in sa.inspect(conn).get_columns("invoices")}
    assert sa.inspect(conn).has_table("attachments")


def test_attachment_constraints_and_unique_sha(monkeypatch):
    """status/document_type CHECK 与 (company_id, content_sha256) 唯一必须生效。"""
    _engine, conn = _make_conn_with_invoices_table()
    _run(monkeypatch, conn, "upgrade")

    def _insert(attachment_id: str, **overrides):
        values = {
            "id": attachment_id,
            "company_id": "c1",
            "invoice_id": "inv-seed-1",
            "document_type": "einvoice_xml",
            "file_name": "dzfp.xml",
            "content_sha256": "a" * 64,
            "stored_name": f"{attachment_id}.xml",
            "status": "active",
        }
        values.update(overrides)
        columns = ", ".join(values.keys())
        placeholders = ", ".join(f":{key}" for key in values)
        conn.execute(sa.text(f"INSERT INTO attachments ({columns}) VALUES ({placeholders})"), values)

    _insert("att-1")
    conn.commit()
    # 同公司同 sha 重复 → 唯一约束拦截
    with pytest.raises(sa.exc.IntegrityError):
        _insert("att-2")
        conn.commit()
    conn.execute(sa.text("ROLLBACK"))
    # 非法状态 → CHECK 拦截
    with pytest.raises(sa.exc.IntegrityError):
        _insert("att-3", content_sha256="b" * 64, status="deleted")
        conn.commit()
    conn.execute(sa.text("ROLLBACK"))
    # 非法类型 → CHECK 拦截
    with pytest.raises(sa.exc.IntegrityError):
        _insert("att-4", content_sha256="c" * 64, document_type="photo")
        conn.commit()


def test_downgrade_then_reupgrade(monkeypatch):
    _engine, conn = _make_conn_with_invoices_table()
    _run(monkeypatch, conn, "upgrade")
    _run(monkeypatch, conn, "downgrade")
    assert "verify_status" not in {col["name"] for col in sa.inspect(conn).get_columns("invoices")}
    assert not sa.inspect(conn).has_table("attachments")
    # 原表存量行完好
    row = conn.execute(sa.text("SELECT invoice_number FROM invoices WHERE id = 'inv-seed-1'")).fetchone()
    assert row.invoice_number == "26337000000000000001"
    _run(monkeypatch, conn, "upgrade")
    row = conn.execute(sa.text("SELECT verify_status FROM invoices WHERE id = 'inv-seed-1'")).fetchone()
    assert row.verify_status == "unverified"
