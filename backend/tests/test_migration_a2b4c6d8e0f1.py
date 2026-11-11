# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""a2b4c6d8e0f1（补住房公积金科目 221105/5602017）的可移植性与幂等性测试。

复用 test_migration_d3e5a7b9c1f4 的护栏口径：无 UNIQUE 约束的遗留 schema
上 upgrade 不能报错、不能重新启用用户停用的科目、重复执行幂等。
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
    / "a2b4c6d8e0f1_housing_fund_accounts.py"
)

# 复刻 accounts_service 的遗留 DDL：无 UNIQUE(company_id, code)
LEGACY_CHART_ACCOUNTS_DDL = """
CREATE TABLE chart_accounts (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL,
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  parent_code TEXT,
  level INTEGER NOT NULL DEFAULT 1,
  category TEXT,
  type TEXT,
  normal_side TEXT,
  is_leaf INTEGER NOT NULL DEFAULT 1,
  is_system INTEGER NOT NULL DEFAULT 1,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_a2b4c6d8e0f1", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_legacy_schema(conn) -> None:
    conn.execute(sa.text(LEGACY_CHART_ACCOUNTS_DDL))
    conn.commit()


def _seed_company(conn, company_id: str) -> None:
    """塞入迁移前的存量行：仅父级 2211/5602 存在，两个新码缺失。"""
    now = "2026-01-01T00:00:00+00:00"
    rows = [
        ("2211", "应付职工薪酬", "负债类", "流动负债", "credit", 0),
        ("5602", "管理费用", "损益", "费用", "debit", 1),
    ]
    for code, name, cat, typ, side, leaf in rows:
        conn.execute(
            sa.text(
                "INSERT INTO chart_accounts "
                "(id, company_id, code, name, parent_code, level, category, type, "
                " normal_side, is_leaf, is_system, is_active, created_at, updated_at) "
                "VALUES (:id, :cid, :code, :name, NULL, 1, :cat, :typ, "
                " :side, :leaf, 1, 1, :now, :now)"
            ),
            {
                "id": f"acct-{company_id}-{code}",
                "cid": company_id,
                "code": code,
                "name": name,
                "cat": cat,
                "typ": typ,
                "side": side,
                "leaf": leaf,
                "now": now,
            },
        )
    conn.commit()


def _accounts(conn, company_id: str) -> dict[str, sa.Row]:
    rows = conn.execute(
        sa.text(
            "SELECT code, name, parent_code, level, category, type, normal_side, "
            "is_leaf, is_system, is_active FROM chart_accounts WHERE company_id = :cid"
        ),
        {"cid": company_id},
    ).fetchall()
    return {row.code: row for row in rows}


def _run_upgrade(monkeypatch, conn) -> None:
    module = _load_migration()
    operations = Operations(MigrationContext.configure(conn))
    monkeypatch.setattr(module, "op", operations)
    module.upgrade()


@pytest.fixture
def legacy_conn():
    engine = sa.create_engine("sqlite://", poolclass=StaticPool)
    conn = engine.connect()
    try:
        _create_legacy_schema(conn)
        yield conn
    finally:
        conn.close()
        engine.dispose()


def test_upgrade_inserts_both_housing_fund_leaves(legacy_conn, monkeypatch):
    _seed_company(legacy_conn, "c-legacy")
    _run_upgrade(monkeypatch, legacy_conn)

    accounts = _accounts(legacy_conn, "c-legacy")
    hf = accounts["221105"]
    assert hf.name == "应付职工薪酬-住房公积金"
    assert hf.parent_code == "2211"
    assert hf.is_leaf == 1 and hf.is_system == 1 and hf.is_active == 1
    assert hf.normal_side == "credit"
    assert hf.level == 2

    exp = accounts["5602017"]
    assert exp.name == "管理费用-住房公积金"
    assert exp.parent_code == "5602"
    assert exp.is_leaf == 1 and exp.is_system == 1 and exp.is_active == 1
    assert exp.normal_side == "debit"

    # 父级不被触碰：5602 保持末级（4 位总账历史被直接记账），2211 保持容器
    assert accounts["5602"].is_leaf == 1
    assert accounts["2211"].is_leaf == 0


def test_upgrade_is_idempotent(legacy_conn, monkeypatch):
    _seed_company(legacy_conn, "c-legacy")
    _run_upgrade(monkeypatch, legacy_conn)
    before = _accounts(legacy_conn, "c-legacy")
    _run_upgrade(monkeypatch, legacy_conn)
    after = _accounts(legacy_conn, "c-legacy")
    assert set(before.keys()) == set(after.keys())
    for code, row in after.items():
        assert row.name == before[code].name
        assert row.is_active == before[code].is_active
