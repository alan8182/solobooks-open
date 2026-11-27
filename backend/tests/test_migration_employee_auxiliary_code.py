# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""c6f9d2a1b8e4（employees.code 职员辅助核算编码）迁移测试。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "c6f9d2a1b8e4_employee_auxiliary_code.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_c6f9d2a1b8e4", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_conn_with_employees_table():
    engine = sa.create_engine("sqlite://", poolclass=StaticPool)
    conn = engine.connect()
    conn.execute(
        sa.text(
            "CREATE TABLE employees ("
            " id TEXT PRIMARY KEY,"
            " company_id TEXT NOT NULL,"
            " name TEXT NOT NULL,"
            " department TEXT NOT NULL DEFAULT '',"
            " is_active INTEGER NOT NULL DEFAULT 1,"
            " created_at TEXT NOT NULL,"
            " updated_at TEXT NOT NULL"
            ")"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO employees (id, company_id, name, department, is_active, created_at, updated_at)"
            " VALUES ('e1', 'c1', '老员工甲', '研发部', 1, '2026-01-01', '2026-01-01'),"
            "        ('e2', 'c1', '老员工乙', '市场部', 1, '2026-01-01', '2026-01-01')"
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


def test_existing_rows_default_empty_and_partial_unique_index(monkeypatch):
    """存量多名员工 code 默认为空串且互不冲突；非空 code 在同公司内必须唯一。"""
    _engine, conn = _make_conn_with_employees_table()
    _run_upgrade(monkeypatch, conn)
    codes = conn.execute(sa.text("SELECT id, code FROM employees ORDER BY id")).fetchall()
    assert [(r.id, r.code) for r in codes] == [("e1", ""), ("e2", "")]

    conn.execute(sa.text("UPDATE employees SET code = 'EM0001' WHERE id = 'e1'"))
    conn.commit()
    with pytest.raises(IntegrityError):
        conn.execute(sa.text("UPDATE employees SET code = 'EM0001' WHERE id = 'e2'"))
    conn.rollback()


def test_upgrade_idempotent_and_downgrade_reversible(monkeypatch):
    _engine, conn = _make_conn_with_employees_table()
    _run_upgrade(monkeypatch, conn)
    _run_upgrade(monkeypatch, conn)
    assert "code" in {col["name"] for col in sa.inspect(conn).get_columns("employees")}

    _run_downgrade(monkeypatch, conn)
    assert "code" not in {col["name"] for col in sa.inspect(conn).get_columns("employees")}
    assert "uq_employees_company_code_nonempty" not in {
        idx["name"] for idx in sa.inspect(conn).get_indexes("employees")
    }

    _run_upgrade(monkeypatch, conn)
    assert "code" in {col["name"] for col in sa.inspect(conn).get_columns("employees")}
