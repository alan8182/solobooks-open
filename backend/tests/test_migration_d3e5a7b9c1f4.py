# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""d3e5a7b9c1f4 迁移的可移植性与幂等性测试。

背景：迁移曾用 ``ON CONFLICT (company_id, code)`` upsert，但遗留 SQLite 库
（accounts_service 原始 DDL、早期版本）的 chart_accounts 没有该唯一约束，
``alembic upgrade`` 直接报 "no unique constraint matching" 全量中止。
改为先 SELECT 再 INSERT/UPDATE 后，无约束与有约束两种 schema 都必须能跑，
且不能重新启用被用户停用的科目。
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
    / "d3e5a7b9c1f4_salary_payroll_accounting_refactor.py"
)

# 迁移目标科目：容器父级 + 新增子级 + 既有末级刷新
EXPECTED_CODES = (
    "1221", "122101", "122102", "122103",
    "2211", "221101", "221102", "221103", "221104",
    "2221012",
    "5602001", "5602014", "5602016",
)
CONTAINER_CODES = ("1221", "2211")

# 复刻 accounts_service 的遗留 DDL：无 UNIQUE(company_id, code)，
# 这正是 ON CONFLICT 写法在存量库上炸掉的根因。
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

LEGACY_EMPLOYEES_DDL = """
CREATE TABLE employees (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL,
  name TEXT NOT NULL,
  department TEXT,
  base_salary TEXT,
  social_security_base TEXT,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_d3e5a7b9c1f4", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_conn():
    engine = sa.create_engine("sqlite://", poolclass=StaticPool)
    return engine, engine.connect()


def _create_legacy_schema(conn) -> None:
    conn.execute(sa.text(LEGACY_CHART_ACCOUNTS_DDL))
    conn.execute(sa.text(LEGACY_EMPLOYEES_DDL))
    conn.commit()


def _seed_company(conn, company_id: str, *, stale_parent: bool = False) -> None:
    """塞入迁移前的存量行：父级名称过期、is_leaf=1，5602001 被用户停用。"""
    now = "2026-01-01T00:00:00+00:00"
    rows = [
        ("2211", "应付职工薪酬（旧名）" if stale_parent else "应付职工薪酬", 1, 1),
        ("1221", "其他应收款", 1, 1),
        # 用户手工停用的科目：迁移后必须保持 is_active=0
        ("5602001", "管理人员职工薪酬", 1, 0),
        ("5602014", "管理费用-职工福利费", 1, 1),
    ]
    for code, name, leaf, active in rows:
        conn.execute(
            sa.text(
                "INSERT INTO chart_accounts "
                "(id, company_id, code, name, parent_code, level, category, type, "
                " normal_side, is_leaf, is_system, is_active, created_at, updated_at) "
                "VALUES (:id, :cid, :code, :name, NULL, 1, '损益', '费用', "
                " 'debit', :leaf, 1, :active, :now, :now)"
            ),
            {
                "id": f"acct-{company_id}-{code}",
                "cid": company_id,
                "code": code,
                "name": name,
                "leaf": leaf,
                "active": active,
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


def _columns(conn, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(conn).get_columns(table)}


@pytest.fixture
def legacy_conn():
    engine, conn = _make_conn()
    try:
        _create_legacy_schema(conn)
        yield conn
    finally:
        conn.close()
        engine.dispose()


def test_upgrade_runs_on_schema_without_unique_constraint(legacy_conn, monkeypatch):
    """遗留 schema（无 UNIQUE(company_id, code)）上 upgrade 不报错——ON CONFLICT 写法的回归护栏。"""
    _seed_company(legacy_conn, "c-legacy")
    _run_upgrade(monkeypatch, legacy_conn)  # 旧写法在此处抛 OperationalError

    accounts = _accounts(legacy_conn, "c-legacy")
    for code in EXPECTED_CODES:
        assert code in accounts, f"{code} 应已被迁移补齐"


def test_upgrade_flips_containers_and_adds_sub_accounts(legacy_conn, monkeypatch):
    _seed_company(legacy_conn, "c-legacy", stale_parent=True)
    _run_upgrade(monkeypatch, legacy_conn)

    accounts = _accounts(legacy_conn, "c-legacy")
    for code in CONTAINER_CODES:
        assert accounts[code].is_leaf == 0, f"{code} 应翻转为非末级容器"
    for code in ("122101", "122102", "122103", "221101", "221102", "221103", "221104", "2221012", "5602016"):
        assert accounts[code].is_leaf == 1, f"{code} 应是末级明细"
        assert accounts[code].is_active == 1
    # 4 位总账保持末级
    for code in ("5602001", "5602014"):
        assert accounts[code].is_leaf == 1, f"4 位总账 {code} 必须保持末级"
    # 存量行的过期名称/方向被刷新（UPDATE 分支）
    assert accounts["2211"].name == "应付职工薪酬"
    assert accounts["2211"].normal_side == "credit"
    assert accounts["5602016"].parent_code == "5602"
    assert accounts["2221012"].parent_code == "2221"
    assert accounts["221101"].parent_code == "2211"


def test_upgrade_preserves_user_disabled_account(legacy_conn, monkeypatch):
    """用户停用的科目不能被迁移重新启用。"""
    _seed_company(legacy_conn, "c-legacy")
    _run_upgrade(monkeypatch, legacy_conn)

    accounts = _accounts(legacy_conn, "c-legacy")
    assert accounts["5602001"].is_active == 0, "用户停用的 5602001 必须保持停用"


def test_upgrade_adds_missing_is_owner_column(legacy_conn, monkeypatch):
    assert "is_owner" not in _columns(legacy_conn, "employees")
    _seed_company(legacy_conn, "c-legacy")
    _run_upgrade(monkeypatch, legacy_conn)
    assert "is_owner" in _columns(legacy_conn, "employees")


def test_upgrade_is_idempotent(legacy_conn, monkeypatch):
    _seed_company(legacy_conn, "c-legacy")
    _run_upgrade(monkeypatch, legacy_conn)
    before = _accounts(legacy_conn, "c-legacy")

    _run_upgrade(monkeypatch, legacy_conn)
    after = _accounts(legacy_conn, "c-legacy")

    assert set(before) == set(after) == set(EXPECTED_CODES)
    for code in EXPECTED_CODES:
        assert before[code].name == after[code].name
        assert before[code].is_leaf == after[code].is_leaf
        assert before[code].is_active == after[code].is_active, "重复执行不得改变启用状态"


def test_upgrade_seeds_company_added_after_first_run(legacy_conn, monkeypatch):
    _seed_company(legacy_conn, "c-first")
    _run_upgrade(monkeypatch, legacy_conn)

    # 第一个租户升级后新建的租户，再次执行迁移也要补齐
    _seed_company(legacy_conn, "c-second")
    _run_upgrade(monkeypatch, legacy_conn)

    second = _accounts(legacy_conn, "c-second")
    for code in EXPECTED_CODES:
        assert code in second, f"后加入的租户缺少 {code}"
