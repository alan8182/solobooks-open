"""密码登录：设置/修改/重置、防爆破、以及 _persist_user 不覆盖密码的回归。

覆盖验收点：
- 未设密码账号密码登录 → 提示走验证码登录；设密码后可登录且 /me 返回 has_password
- 错误密码与未注册手机号统一文案（防枚举）
- 已设密码后修改必须校验旧密码
- 13800000000 短信后门重置密码；未注册手机号 404
- 设密码后走一次验证码登录（触发 _persist_user 全量覆盖写）密码仍在 —— 关键回归
- 连续失败 5 次锁定，正确密码也 429
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from services.token_service import create_access_token


@pytest.fixture(autouse=True)
def _clean_password_throttle():
    """失败锁定是进程内全局缓存，测试前后各清一次，避免用例间与 IP 键互相污染。"""
    from services import password_service

    password_service._FAILURES.clear()
    yield
    password_service._FAILURES.clear()


def _seed_user(user_id: str, phone: str, password: str | None = None) -> None:
    from tests.conftest import TEST_CORE_DB
    from services.password_service import hash_password

    password_hash = asyncio.run(hash_password(password)) if password else ""
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO auth_users (
                user_id, openid, phone, phone_verified_at, wechat_openid_bound_at,
                current_company_id, nickname, avatar, password_hash, password_updated_at,
                is_platform_admin, is_disabled, created_at, last_login_at, updated_at
            ) VALUES (?, '', ?, '2026-01-01', '', 'c1', '', '', ?, '2026-01-01', 0, 0,
                      '2026-01-01', '2026-01-01', '')
            """,
            (user_id, phone, password_hash),
        )
        conn.commit()
    finally:
        conn.close()


def _headers(user_id: str, company_id: str = "c1") -> dict:
    token = create_access_token({"sub": user_id, "company_id": company_id})
    return {"Authorization": f"Bearer {token}"}


def test_set_then_login_and_me(client, monkeypatch):
    from routers import auth as auth_router

    async def _fake_check(phone, code, biz_id=None):
        return None

    monkeypatch.setattr(auth_router, "check_verify_code", _fake_check)
    _seed_user("u-pw1", "13911112222")
    # 未设密码：引导走验证码登录，不进入失败计数文案
    resp = client.post(
        "/api/auth/password/login", json={"phone": "13911112222", "password": "secret123"}
    )
    assert resp.status_code == 400
    assert "验证码登录" in resp.json()["detail"]

    # 验证码登录响应携带 has_password 标志，供前端弹"设置密码"引导
    resp = client.post(
        "/api/auth/phone/login", json={"phone": "13911112222", "code": "123456"}
    )
    assert resp.status_code == 200
    assert resp.json()["has_password"] is False

    resp = client.post("/api/auth/password/set", headers=_headers("u-pw1"),
                       json={"new_password": "secret123"})
    assert resp.status_code == 200
    assert resp.json()["has_password"] is True

    resp = client.post(
        "/api/auth/password/login", json={"phone": "13911112222", "password": "secret123"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["has_password"] is True

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["has_password"] is True


def test_wrong_password_and_unknown_phone_same_message(client):
    _seed_user("u-pw2", "13922223333", password="secret123")
    wrong = client.post(
        "/api/auth/password/login", json={"phone": "13922223333", "password": "wrong-pass"}
    )
    unknown = client.post(
        "/api/auth/password/login", json={"phone": "13999990000", "password": "whatever123"}
    )
    assert wrong.status_code == unknown.status_code == 400
    assert wrong.json()["detail"] == unknown.json()["detail"] == "手机号或密码错误"


def test_change_password_requires_old(client):
    _seed_user("u-pw3", "13933335555", password="secret123")
    headers = _headers("u-pw3")
    missing = client.post("/api/auth/password/set", headers=headers,
                          json={"new_password": "newpass123"})
    assert missing.status_code == 400
    assert "当前密码" in missing.json()["detail"]

    bad_old = client.post("/api/auth/password/set", headers=headers,
                          json={"old_password": "wrong-pass", "new_password": "newpass123"})
    assert bad_old.status_code == 400
    assert "当前密码错误" in bad_old.json()["detail"]

    ok = client.post("/api/auth/password/set", headers=headers,
                     json={"old_password": "secret123", "new_password": "newpass123"})
    assert ok.status_code == 200

    assert client.post(
        "/api/auth/password/login", json={"phone": "13933335555", "password": "secret123"}
    ).status_code == 400
    assert client.post(
        "/api/auth/password/login", json={"phone": "13933335555", "password": "newpass123"}
    ).status_code == 200


def test_reset_password_via_sms_backdoor(client):
    _seed_user("u-pw4", "13800000000", password="oldpass123")
    resp = client.post(
        "/api/auth/password/reset",
        json={"phone": "13800000000", "code": "123456", "new_password": "reseted123"},
    )
    assert resp.status_code == 200
    assert client.post(
        "/api/auth/password/login", json={"phone": "13800000000", "password": "oldpass123"}
    ).status_code == 400
    assert client.post(
        "/api/auth/password/login", json={"phone": "13800000000", "password": "reseted123"}
    ).status_code == 200


def test_reset_unregistered_phone(client, monkeypatch):
    from routers import auth as auth_router

    async def _fake_check(phone, code, biz_id=None):
        return None

    monkeypatch.setattr(auth_router, "check_verify_code", _fake_check)
    resp = client.post(
        "/api/auth/password/reset",
        json={"phone": "13988887777", "code": "654321", "new_password": "reseted123"},
    )
    assert resp.status_code == 404


def test_persist_user_keeps_password(client):
    """关键回归：_persist_user 全量覆盖写用户时不得抹掉密码列。"""
    _seed_user("u-pw5", "13800000000", password="secret123")
    # 触发一次验证码登录（走 get_or_create_user_by_phone → _persist_user）
    assert client.post(
        "/api/auth/phone/login", json={"phone": "13800000000", "code": "123456"}
    ).status_code == 200
    assert client.post(
        "/api/auth/password/login", json={"phone": "13800000000", "password": "secret123"}
    ).status_code == 200

    # 再触发资料更新路径（update_user_profile → _persist_user）
    _seed_user("u-pw6", "13966668888", password="secret123")
    assert client.post("/api/auth/profile", headers=_headers("u-pw6"),
                       json={"nickname": "昵称"}).status_code == 200
    assert client.post(
        "/api/auth/password/login", json={"phone": "13966668888", "password": "secret123"}
    ).status_code == 200


def test_lockout_after_max_failures(client):
    from services import password_service

    _seed_user("u-pw7", "13977779999", password="secret123")
    payload = {"phone": "13977779999", "password": "wrong-pass"}
    for _ in range(password_service.PASSWORD_LOGIN_MAX_FAILURES):
        resp = client.post("/api/auth/password/login", json=payload)
        assert resp.status_code == 400
    # 锁定期内正确密码同样 429
    locked = client.post(
        "/api/auth/password/login", json={"phone": "13977779999", "password": "secret123"}
    )
    assert locked.status_code == 429

    # 计数清零后恢复（对应进程重启/窗口过期的语义）
    password_service._FAILURES.clear()
    assert client.post(
        "/api/auth/password/login", json={"phone": "13977779999", "password": "secret123"}
    ).status_code == 200


def test_password_min_length(client):
    _seed_user("u-pw8", "13900001111")
    resp = client.post("/api/auth/password/set", headers=_headers("u-pw8"),
                       json={"new_password": "short"})
    assert resp.status_code == 400
    assert "密码长度" in resp.json()["detail"]
