# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""bank_category_rules 规则管理迁移回归测试。"""

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
    / "c9a4f2e7b6d1_bank_category_rule_management.py"
)

LEGACY_DDL = """
CREATE TABLE bank_category_rules (
  company_id TEXT NOT NULL,
  direction TEXT NOT NULL,
  match_pattern TEXT NOT NULL,
  category TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (company_id, direction, match_pattern)
)
"""


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_c9a4f2e7b6d1", MIGRATION_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_preserves_and_backfills_legacy_rules(monkeypatch):
    engine = sa.create_engine("sqlite://", poolclass=StaticPool)
    conn = engine.connect()
    try:
        conn.execute(sa.text(LEGACY_DDL))
        conn.execute(
            sa.text(
                "INSERT INTO bank_category_rules "
                "(company_id, direction, match_pattern, category, updated_at) "
                "VALUES ('c-legacy', 'expense', '旧供应商', '办公费', "
                "'2026-01-01T00:00:00+00:00')"
            )
        )
        conn.commit()

        module = _load_migration()
        operations = Operations(MigrationContext.configure(conn))
        monkeypatch.setattr(module, "op", operations)
        module.upgrade()

        columns = {
            column["name"]
            for column in sa.inspect(conn).get_columns("bank_category_rules")
        }
        assert {
            "id",
            "counterparty_key",
            "purpose_key",
            "enabled",
            "hit_count",
            "last_hit_at",
            "amount_min",
            "amount_max",
            "created_at",
        }.issubset(columns)

        row = conn.execute(
            sa.text("SELECT * FROM bank_category_rules")
        ).mappings().one()
        assert row["id"].startswith("legacy|")
        assert row["counterparty_key"] == "旧供应商"
        assert row["purpose_key"] == ""
        assert row["enabled"] == 1
        assert row["hit_count"] == 0
        assert row["created_at"] == "2026-01-01T00:00:00+00:00"
    finally:
        conn.close()
        engine.dispose()
