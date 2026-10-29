# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""require_auth 成员归属校验：JWT 存续期内成员资格变动立即生效。

覆盖验收点：
- 成员行 active → 放行
- 成员行停用（disabled）→ 403，与 ensure_tenant / PAT 校验同口径
- 成员行删除且非 owner → 403
- 成员行删除但仍是公司 owner（auth_companies.owner_user_id）→ 放行（owner 兜底）
- 用户行不存在（开发兜底身份）→ 成员校验跳过，不影响既有兜底行为
"""
from __future__ import annotations

import sqlite3

from services.token_service import create_access_token

_COMPANY = "c-memb"


def _headers(user_id: str, company_id: str = _COMPANY) -> dict:
    token = create_access_token({"sub": user_id, "company_id": company_id})
    return {"Authorization": f"Bearer {token}"}


def _exec(sql: str, params: tuple = ()) -> None:
    from tests.conftest import TEST_CORE_DB

    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _seed() -> None:
    """播种 c-memb 公司、owner 与 active 成员（不动共享的 c1）。"""
    from tests.conftest import TEST_CORE_DB

    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.executemany(
            """
            INSERT OR REPLACE INTO auth_users (
                user_id, openid, phone, phone_verified_at, wechat_openid_bound_at,
                current_company_id, nickname, avatar, is_platform_admin, is_disabled,
                created_at, last_login_at, updated_at
            ) VALUES (?, '', '', '', '', ?, '', '', 0, 0, '2026-01-01', '2026-01-01', '')
            """,
            [("u-memb", _COMPANY), ("u-owner", _COMPANY)],
        )
        conn.execute(
            "INSERT OR REPLACE INTO auth_companies ("
            " company_id, owner_user_id, payload, base_currency, accounting_standard,"
            " posting_policy, timezone, created_at, updated_at"
            ") VALUES (?, 'u-owner', '{}', 'CNY', 'small_enterprise_cn',"
            " 'owner_self_post', 'Asia/Shanghai', '2026-01-01T00:00:00+00:00', '')",
            (_COMPANY,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO company_members (company_id, user_id, role, status)"
            " VALUES (?, 'u-memb', 'accountant', 'active')",
            (_COMPANY,),
        )
        conn.commit()
    finally:
        conn.close()

def test_membership_lifecycle_controls_access(client):
    _seed()
    # active 成员放行
    assert client.get("/api/agent/actions", headers=_headers("u-memb")).status_code == 200

    # 停用成员资格 → 立即 403（JWT 仍在有效期内）
    _exec(
        "UPDATE company_members SET status = 'disabled' WHERE company_id = ? AND user_id = 'u-memb'",
        (_COMPANY,),
    )
    resp = client.get("/api/agent/actions", headers=_headers("u-memb"))
    assert resp.status_code == 403
    assert "无权访问该企业账套" in resp.json()["detail"]

    # 删除成员行且非 owner → 403
    _exec(
        "DELETE FROM company_members WHERE company_id = ? AND user_id = 'u-memb'",
        (_COMPANY,),
    )
    assert client.get("/api/agent/actions", headers=_headers("u-memb")).status_code == 403

    # owner 无成员行 → 放行（has_company_access 的 owner 兜底，与 ensure_tenant 自动补建同口径）
    assert client.get("/api/agent/actions", headers=_headers("u-owner")).status_code == 200

    # 用户行不存在（开发兜底身份）→ 成员校验跳过
    assert client.get("/api/agent/actions", headers={"Authorization": "Bearer test-token-unit-test"}).status_code == 200
