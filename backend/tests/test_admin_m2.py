# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""管理后台 M2：用户/公司管理 + 操作留痕。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from services.token_service import create_access_token

from tests.conftest import TEST_CORE_DB


def _seed_users() -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.executemany(
            """
            INSERT OR REPLACE INTO auth_users (
                user_id, openid, phone, phone_verified_at, wechat_openid_bound_at,
                current_company_id, nickname, avatar, is_platform_admin, is_disabled,
                created_at, last_login_at, updated_at
            ) VALUES (?, '', ?, '', '', 'c1', ?, '', ?, ?, '2026-01-01', '2026-01-01', '')
            """,
            [
                ("u-admin", "13800000001", "管理员", 1, 0),
                ("u-normal", "13800000002", "普通用户", 0, 0),
                ("u-target", "13900000003", "目标用户", 0, 0),
            ],
        )
        # 生产登录流程必建成员关系；require_auth 按 ensure_tenant 同口径校验成员
        # 归属（_headers 默认 company_id=c1），播种需对齐。c-m2 的归属由
        # _seed_companies 负责。
        conn.executemany(
            "INSERT OR REPLACE INTO company_members (company_id, user_id, role, status)"
            " VALUES ('c1', ?, 'accountant', 'active')",
            [("u-admin",), ("u-normal",), ("u-target",)],
        )
        conn.commit()
    finally:
        conn.close()


def _seed_companies() -> None:
    payload = json.dumps({"name": "测试公司甲"}, ensure_ascii=False)
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO auth_companies ("
            " company_id, owner_user_id, payload, base_currency, accounting_standard,"
            " posting_policy, timezone, created_at, updated_at"
            ") VALUES ('c-m2', 'u-normal', ?, 'CNY', 'small_enterprise_cn',"
            " 'owner_self_post', 'Asia/Shanghai', '2026-02-01T00:00:00+00:00', '')",
            (payload,),
        )
        conn.executemany(
            "INSERT OR REPLACE INTO company_members (company_id, user_id, role, status)"
            " VALUES (?, ?, ?, 'active')",
            [
                ("c-m2", "u-normal", "owner"),
                ("c-m2", "u-target", "accountant"),
            ],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO journal_entries ("
            " id, company_id, occurred_on, period_start, summary, status, entry_type,"
            " currency, created_by, rule_version, metadata, version, created_at, updated_at"
            ") VALUES (?, 'c-m2', '2026-02-01', '2026-02-01', '测试凭证', 'posted',"
            " 'standard', 'CNY', 'u-normal', 'account-mapping-v1', '{}', 1, ?, ?)",
            [
                (f"je-m2-{i}", "2026-02-01 0%d:00:00.000000" % i, "2026-02-01 0%d:00:00.000000" % i)
                for i in (1, 2)
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _cleanup() -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute(
            "DELETE FROM journal_entries WHERE id LIKE 'je-m2-%' OR company_id = 'c-m2'"
        )
        conn.execute("DELETE FROM company_members WHERE company_id = 'c-m2'")
        conn.execute("DELETE FROM auth_companies WHERE company_id = 'c-m2'")
        conn.execute("DELETE FROM admin_audit_logs WHERE target_id LIKE 'u-%' OR operator_id = 'u-admin'")
        conn.execute("DELETE FROM auth_users WHERE user_id IN ('u-admin', 'u-normal', 'u-target')")
        conn.commit()
    finally:
        conn.close()


def _headers(user_id: str, company_id: str = "c1") -> dict:
    token = create_access_token({"sub": user_id, "company_id": company_id})
    return {"Authorization": f"Bearer {token}"}


def test_users_list_search_and_pagination(client):
    _seed_users()
    try:
        body = client.get("/api/admin/users", headers=_headers("u-admin")).json()
        ids = [u["userId"] for u in body["items"]]
        assert "u-admin" in ids and "u-normal" in ids
        assert body["total"] >= 3

        by_id = {u["userId"]: u for u in body["items"]}
        assert by_id["u-admin"]["isPlatformAdmin"] is True
        assert by_id["u-normal"]["isPlatformAdmin"] is False

        # 手机号模糊搜索
        body = client.get(
            "/api/admin/users", params={"query": "13900000003"}, headers=_headers("u-admin")
        ).json()
        assert [u["userId"] for u in body["items"]] == ["u-target"]
        assert body["items"][0]["nickname"] == "目标用户"

        # 分页
        body = client.get(
            "/api/admin/users",
            params={"page": 1, "pageSize": 2},
            headers=_headers("u-admin"),
        ).json()
        assert len(body["items"]) <= 2
    finally:
        _cleanup()


def test_patch_user_disable_enable_with_audit(client):
    _seed_users()
    try:
        # 禁用 → 业务接口立即 401（require_auth 每请求查库）
        resp = client.patch(
            "/api/admin/users/u-normal",
            json={"isDisabled": True},
            headers=_headers("u-admin"),
        )
        assert resp.status_code == 200
        assert resp.json()["user"]["isDisabled"] is True
        assert (
            client.get("/api/agent/actions", headers=_headers("u-normal")).status_code == 401
        )

        # 启用恢复
        resp = client.patch(
            "/api/admin/users/u-normal",
            json={"isDisabled": False},
            headers=_headers("u-admin"),
        )
        assert resp.status_code == 200
        assert (
            client.get("/api/agent/actions", headers=_headers("u-normal")).status_code == 200
        )

        # 审计留痕
        logs = client.get("/api/admin/audit-logs", headers=_headers("u-admin")).json()["items"]
        actions = [log["action"] for log in logs if log["targetId"] == "u-normal"]
        assert "disable_user" in actions and "enable_user" in actions
        detail = next(log for log in logs if log["action"] == "disable_user")["detail"]
        assert detail["before"]["isDisabled"] is False and detail["after"]["isDisabled"] is True
    finally:
        _cleanup()


def test_patch_user_grant_revoke_admin(client):
    _seed_users()
    try:
        # 授予管理员 → overview 可访问
        assert (
            client.patch(
                "/api/admin/users/u-normal",
                json={"isPlatformAdmin": True},
                headers=_headers("u-admin"),
            ).status_code
            == 200
        )
        assert (
            client.get("/api/admin/overview", headers=_headers("u-normal")).status_code == 200
        )
        # 撤销 → 恢复 403
        assert (
            client.patch(
                "/api/admin/users/u-normal",
                json={"isPlatformAdmin": False},
                headers=_headers("u-admin"),
            ).status_code
            == 200
        )
        assert (
            client.get("/api/admin/overview", headers=_headers("u-normal")).status_code == 403
        )
        # 审计
        logs = client.get("/api/admin/audit-logs", headers=_headers("u-admin")).json()["items"]
        actions = [log["action"] for log in logs if log["targetId"] == "u-normal"]
        assert "grant_admin" in actions and "revoke_admin" in actions
    finally:
        _cleanup()


def test_patch_self_and_missing_user(client):
    _seed_users()
    try:
        # 不能修改自己
        assert (
            client.patch(
                "/api/admin/users/u-admin",
                json={"isDisabled": True},
                headers=_headers("u-admin"),
            ).status_code
            == 422
        )
        # 目标不存在
        assert (
            client.patch(
                "/api/admin/users/u-nobody",
                json={"isDisabled": True},
                headers=_headers("u-admin"),
            ).status_code
            == 404
        )
        # 空请求
        assert (
            client.patch(
                "/api/admin/users/u-normal", json={}, headers=_headers("u-admin")
            ).status_code
            == 422
        )
    finally:
        _cleanup()


def test_companies_list(client):
    _seed_users()
    _seed_companies()
    try:
        body = client.get("/api/admin/companies", headers=_headers("u-admin")).json()
        assert body["total"] >= 1
        target = next(c for c in body["items"] if c["companyId"] == "c-m2")
        assert target["name"] == "测试公司甲"
        assert target["memberCount"] == 2
        assert target["entryCount"] == 2
        assert target["lastActivityAt"]
        assert target["ownerUserId"] == "u-normal"

        # 按公司名搜索
        body = client.get(
            "/api/admin/companies", params={"query": "测试公司甲"}, headers=_headers("u-admin")
        ).json()
        assert [c["companyId"] for c in body["items"]] == ["c-m2"]
    finally:
        _cleanup()
