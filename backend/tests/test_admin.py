# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""管理后台 M0：权限底座 + /overview。

覆盖验收点：
- 普通用户访问 /api/admin/overview → 403
- 被禁用用户全接口 → 401
- 管理员访问 /overview → 200 且字段齐全
- 生产配置（AUTH_DEV_FALLBACK=0）下垃圾 token 不再兜底放行
"""
from __future__ import annotations

import sqlite3

from services.token_service import create_access_token


def _seed_users() -> None:
    """直接写 SQLite 播种用户行（绕开 asyncio 连接池，避免与 TestClient 事件循环互扰）。"""
    from tests.conftest import TEST_CORE_DB

    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.executemany(
            """
            INSERT OR REPLACE INTO auth_users (
                user_id, openid, phone, phone_verified_at, wechat_openid_bound_at,
                current_company_id, nickname, avatar, is_platform_admin, is_disabled,
                created_at, last_login_at, updated_at
            ) VALUES (?, '', '', '', '', 'c1', ?, '', ?, ?, '2026-01-01', '2026-01-01', '')
            """,
            [
                ("u-admin", "管理员", 1, 0),
                ("u-admin-disabled", "被禁管理员", 1, 1),
                ("u-normal", "普通用户", 0, 0),
                ("u-disabled", "被禁用户", 0, 1),
            ],
        )
        # 生产登录流程必建成员关系；require_auth 按 ensure_tenant 同口径校验成员归属，
        # 播种需对齐（被禁用户的 401 先于成员 403，不受影响）。
        conn.executemany(
            "INSERT OR REPLACE INTO company_members (company_id, user_id, role, status)"
            " VALUES ('c1', ?, 'accountant', 'active')",
            [("u-admin",), ("u-admin-disabled",), ("u-normal",), ("u-disabled",)],
        )
        conn.commit()
    finally:
        conn.close()


def _headers(user_id: str, company_id: str = "c1") -> dict:
    token = create_access_token({"sub": user_id, "company_id": company_id})
    return {"Authorization": f"Bearer {token}"}


def test_overview_requires_admin(client):
    _seed_users()
    resp = client.get("/api/admin/overview", headers=_headers("u-normal"))
    assert resp.status_code == 403
    # 用户不存在（fail-open 于禁用检查，但不可能是管理员）
    stranger = _headers("u-nobody")
    assert client.get("/api/admin/overview", headers=stranger).status_code == 403


def test_overview_ok_for_admin(client):
    _seed_users()
    resp = client.get("/api/admin/overview", headers=_headers("u-admin"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["database"]["ok"] is True
    assert body["counts"]["users"] >= 4
    assert body["llm"]["circuitOpen"] is False
    assert body["agent"]["actionsToday"] >= 0
    assert body["usage"]["trackingEnabled"] is True  # M1 埋点已上线
    assert body["version"]


def test_disabled_user_blocked_everywhere(client):
    _seed_users()
    # 业务接口与管理接口一并 401
    assert client.get("/api/agent/actions", headers=_headers("u-disabled")).status_code == 401
    assert client.get("/api/admin/overview", headers=_headers("u-disabled")).status_code == 401
    # 被禁管理员同样被拦截（先管理员校验后禁用校验 → 401）
    assert client.get("/api/admin/overview", headers=_headers("u-admin-disabled")).status_code == 401
    # 正常用户不受影响
    assert client.get("/api/agent/actions", headers=_headers("u-normal")).status_code == 200


def test_dev_fallback_switch(client, monkeypatch):
    garbage = {"Authorization": "Bearer not-a-jwt-token"}
    # 默认（本地开发）：垃圾 token 兜底放行
    monkeypatch.setenv("AUTH_DEV_FALLBACK", "1")
    assert client.get("/api/agent/actions", headers=garbage).status_code == 200
    # 生产配置：兜底关闭，垃圾 token 401
    monkeypatch.setenv("AUTH_DEV_FALLBACK", "0")
    assert client.get("/api/agent/actions", headers=garbage).status_code == 401
    assert client.get("/api/admin/overview", headers=garbage).status_code == 401


def test_flags_query_failure_degrades_gracefully(client, monkeypatch):
    """迁移未执行（auth_users 缺标记列）时：业务请求降级放行，管理接口默认拒绝。"""

    def _boom(*_args, **_kwargs):
        raise RuntimeError("column is_platform_admin does not exist")

    import db.session

    monkeypatch.setattr(db.session, "session_scope", _boom)
    _seed_users()
    # 业务接口：禁用/管理员校验降级为放行，请求本身不受影响
    assert client.get("/api/agent/actions", headers=_headers("u-normal")).status_code == 200
    # 管理接口：查不到管理员标记 → 按非管理员拒绝（不会误放行）
    assert client.get("/api/admin/overview", headers=_headers("u-admin")).status_code == 403
