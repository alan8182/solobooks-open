# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from __future__ import annotations

from uuid import uuid4
import sqlite3
from pathlib import Path

from deps.auth import AuthContext, require_auth
from main import app

TEST_CORE_DB = Path(__file__).resolve().parent.parent / "data" / ".pytest_accounting.db"

def test_account_tree_initializes_tenant_and_returns_rd_children(client, auth_headers):
    company_id = f"account-tree-{uuid4().hex}"
    app.dependency_overrides[require_auth] = lambda: AuthContext(
        user_id=f"owner-{company_id}", company_id=company_id,
    )
    try:
        response = client.get("/api/ledger/accounts/tree", headers=auth_headers)
    finally:
        app.dependency_overrides.pop(require_auth, None)
    assert response.status_code == 200, response.text
    roots = response.json()
    assert roots
    cost_group = next(node for node in roots if node["code"] == "成本类")
    rd_parent = next(node for node in cost_group["children"] if node["code"] == "4301")
    expensed = next(node for node in rd_parent["children"] if node["code"] == "4301001")
    assert {child["code"] for child in expensed["children"]} == {
        "430100101", "430100102", "430100103", "430100104", "430100105", "430100106", "430100199",
    }
    assert any(node["code"] == "4301002" for node in rd_parent["children"])


def test_account_tree_keeps_accounts_with_legacy_persisted_group_nodes(client, auth_headers):
    company_id = f"legacy-account-tree-{uuid4().hex}"
    user_id = f"owner-{company_id}"
    app.dependency_overrides[require_auth] = lambda: AuthContext(user_id=user_id, company_id=company_id)
    try:
        initial = client.get("/api/ledger/accounts/tree", headers=auth_headers)
        assert initial.status_code == 200, initial.text
        with sqlite3.connect(TEST_CORE_DB) as connection:
            categories = ["资产类", "负债类", "权益类", "成本类", "损益-收入类", "损益-费用类"]
            for index, category in enumerate(categories):
                connection.execute(
                    "INSERT INTO chart_accounts "
                    "(id, company_id, code, name, parent_code, level, category, type, normal_side, "
                    "is_leaf, is_system, is_active, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, NULL, 1, NULL, NULL, NULL, 0, 1, 1, ?, ?)",
                    (f"legacy-group-{index}-{company_id}", company_id, category, category, "2026-01-01", "2026-01-01"),
                )
                connection.execute(
                    "UPDATE chart_accounts SET parent_code = ? "
                    "WHERE company_id = ? AND level = 1 AND category = ?",
                    (category, company_id, category),
                )
            connection.commit()
        response = client.get("/api/ledger/accounts/tree", headers=auth_headers)
    finally:
        app.dependency_overrides.pop(require_auth, None)

    assert response.status_code == 200, response.text
    roots = response.json()
    account_count = 0
    pending = list(roots)
    while pending:
        node = pending.pop()
        if node["level"] > 0:
            account_count += 1
        pending.extend(node.get("children") or [])
    assert account_count >= 48
    cost_group = next(node for node in roots if node["code"] == "成本类")
    assert any(node["code"] == "4301" for node in cost_group["children"])
