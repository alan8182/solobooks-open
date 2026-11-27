# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""微信开放平台网站应用扫码登录：协议、ticket、合并与身份隔离回归。

覆盖的根因：
- state 必须同时校验签名与 nonce（OAuth 登录 CSRF）；
- redirect 只信签名 cookie，query 参数永远不参与跳转（open redirect）；
- ticket 一次性、只存哈希、purpose 不串用；绑定失败不能烧 ticket；
- 未验证手机号前不发 JWT、不建账号；
- 合并老账号必须保留原账套/套餐/点数；同一微信不得搬到另一个账号；
- 网站 openid 与小程序 openid 严格隔离，小程序登录不得清空网站身份。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.conftest import TEST_CORE_DB

PENDING_COOKIE = "wx_web_pending"
STATE_COOKIE = "wx_web_state"
ACCEPTED_AT = "2026-11-26T10:00:00+08:00"


@pytest.fixture(autouse=True)
def _wx_config(monkeypatch):
    """开启扫码登录并让 cookie 走 http（TestClient 的 testserver 不是 https）。"""
    import config
    from routers import auth as auth_router
    from services import wechat_web_auth_service as svc

    monkeypatch.setattr(config, "WECHAT_WEB_APPID", "wx_test_appid")
    monkeypatch.setattr(config, "WECHAT_WEB_SECRET", "wx_test_secret")
    monkeypatch.setattr(svc, "WECHAT_WEB_APPID", "wx_test_appid")
    monkeypatch.setattr(svc, "WECHAT_WEB_SECRET", "wx_test_secret")
    http_callback = "http://testserver/api/auth/wechat/web/callback"
    monkeypatch.setattr(svc, "WECHAT_WEB_REDIRECT_URI", http_callback)
    monkeypatch.setattr(auth_router, "WECHAT_WEB_REDIRECT_URI", http_callback)
    from services import password_service

    password_service._FAILURES.clear()
    yield
    password_service._FAILURES.clear()


def _mock_wechat(monkeypatch, *, openid: str, unionid: str = "", nickname: str = ""):
    from routers import auth as auth_router

    async def _exchange(code, trace_id=""):
        return {"openid": openid, "unionid": unionid, "access_token": "at-test"}

    async def _userinfo(access_token, openid_arg, trace_id=""):
        return {"nickname": nickname, "avatar": ""}

    monkeypatch.setattr(auth_router, "exchange_code_for_token", _exchange)
    monkeypatch.setattr(auth_router, "fetch_userinfo", _userinfo)


def _seed_user(
    user_id: str,
    *,
    phone: str = "",
    phone_verified_at: str = "",
    openid: str = "",
    wechat_web_openid: str = "",
    wechat_unionid: str = "",
    is_disabled: int = 0,
    company_id: str = "",
    token_balance: int = 0,
    promo_token_balance: int = 0,
) -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        # INSERT OR REPLACE：TEST_CORE_DB 跨测试文件共享（如 test_admin.py 也种
        # u-disabled），裸 INSERT 会在全量跑时撞 auth_users.user_id 唯一键
        conn.execute(
            "INSERT OR REPLACE INTO auth_users ("
            "user_id, openid, phone, phone_verified_at, wechat_openid_bound_at,"
            "wechat_web_openid, wechat_unionid, wechat_web_bound_at,"
            "current_company_id, nickname, avatar, password_hash, password_updated_at,"
            "is_platform_admin, is_disabled, token_balance, promo_token_balance,"
            "created_at, last_login_at, updated_at"
            ") VALUES (?,?,?,?,'',?,?,'',?,'','','','',0,?,?,?, '2026-01-01','2026-01-01','')",
            (
                user_id,
                openid,
                phone,
                phone_verified_at,
                wechat_web_openid,
                wechat_unionid,
                company_id,
                is_disabled,
                token_balance,
                promo_token_balance,
            ),
        )
        if company_id:
            payload = {
                "company_id": company_id,
                "name": "原有账套",
                "owner_user_id": user_id,
                "onboardingType": "new",
            }
            conn.execute(
                "INSERT OR REPLACE INTO auth_companies "
                "(company_id, owner_user_id, payload, base_currency, accounting_standard,"
                " posting_policy, timezone, created_at, updated_at) "
                "VALUES (?,?,?,'CNY','small_enterprise_cn','owner_self_post','Asia/Shanghai',"
                "'2026-01-01','')",
                (company_id, user_id, json.dumps(payload, ensure_ascii=False)),
            )
            conn.execute(
                "INSERT OR REPLACE INTO company_members (company_id, user_id, role, status)"
                " VALUES (?, ?, 'owner', 'active')",
                (company_id, user_id),
            )
        conn.commit()
    finally:
        conn.close()


def _row(sql: str, params: tuple = ()) -> dict | None:
    conn = sqlite3.connect(TEST_CORE_DB)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _start_authorize(client, redirect: str = "/") -> str:
    resp = client.get(
        "/api/auth/wechat/web/authorize-url",
        params={"redirect": redirect, "privacy_accepted_at": ACCEPTED_AT},
    )
    assert resp.status_code == 200, resp.text
    url = resp.json()["authorize_url"]
    return parse_qs(urlsplit(url).query)["state"][0]


def _callback(client, state: str, code: str = "wx-code"):
    return client.get(
        "/api/auth/wechat/web/callback",
        params={"code": code, "state": state},
        follow_redirects=False,
    )


def _full_wechat_login(client, monkeypatch, *, openid: str, unionid: str = "") -> str:
    """执行 authorize + callback，返回 pending cookie 里的 ticket 明文。"""
    _mock_wechat(monkeypatch, openid=openid, unionid=unionid)
    state = _start_authorize(client)
    resp = _callback(client, state)
    assert resp.status_code == 302, resp.text
    assert "status=ready" in resp.headers["location"]
    token = client.cookies.get(PENDING_COOKIE)
    assert token
    return token


# ── 配置与入口 ────────────────────────────────────────────────────────────


def test_config_enabled_only_when_appid_and_secret_present(client):
    assert client.get("/api/auth/wechat/web/config").json() == {"enabled": True}

    import config

    original = config.WECHAT_WEB_SECRET
    try:
        config.WECHAT_WEB_SECRET = ""
        assert client.get("/api/auth/wechat/web/config").json() == {"enabled": False}
    finally:
        config.WECHAT_WEB_SECRET = original


def test_authorize_url_requires_consent(client):
    resp = client.get("/api/auth/wechat/web/authorize-url", params={"redirect": "/"})
    assert resp.status_code == 400


# ── state 安全 ────────────────────────────────────────────────────────────


def test_authorize_url_returns_qrconnect_and_signed_state_cookie(client):
    from services.wechat_web_auth_service import parse_state_cookie

    resp = client.get(
        "/api/auth/wechat/web/authorize-url",
        params={"redirect": "/ledger?tab=1", "privacy_accepted_at": ACCEPTED_AT},
    )
    assert resp.status_code == 200, resp.text
    url = resp.json()["authorize_url"]
    query = parse_qs(urlsplit(url).query)
    state = query["state"][0]
    assert state
    assert query["href"][0].startswith("data:text/css;base64,")
    payload = parse_state_cookie(client.cookies.get(STATE_COOKIE))
    assert payload["n"] == state
    assert payload["r"] == "/ledger?tab=1"
    assert payload["p"] == ACCEPTED_AT


def test_authorize_url_sanitizes_external_redirect_into_state(client):
    from services.wechat_web_auth_service import parse_state_cookie

    _start_authorize(client, redirect="https://evil.example/phish")
    payload = parse_state_cookie(client.cookies.get(STATE_COOKIE))
    assert payload["r"] == "/"

    _start_authorize(client, redirect="//evil.example/phish")
    payload = parse_state_cookie(client.cookies.get(STATE_COOKIE))
    assert payload["r"] == "/"


def test_state_nonce_mismatch_rejected(client, monkeypatch):
    _mock_wechat(monkeypatch, openid="wxweb_state_mismatch")
    _start_authorize(client)
    resp = _callback(client, state="not-the-nonce")
    assert resp.status_code == 302
    assert "wechat_error=state_invalid" in resp.headers["location"]
    assert PENDING_COOKIE not in resp.cookies


def test_state_tampered_hmac_rejected(client, monkeypatch):
    _mock_wechat(monkeypatch, openid="wxweb_state_tamper")
    state = _start_authorize(client)
    # 先删服务端下发的原始 cookie 再 set 篡改值：jar 里同名两条目时，
    # httpx 的发送顺序在部分环境下不同（2026-11-27 开源库实测相反），
    # 服务端"取最后一个同名值"的解析会拿到原始值而非篡改值，测试变 flaky
    original_cookie = client.cookies.get(STATE_COOKIE)
    client.cookies.delete(STATE_COOKIE)
    tampered = original_cookie + "tampered"
    client.cookies.set(STATE_COOKIE, tampered, path="/api/auth/wechat/web")
    resp = _callback(client, state)
    assert resp.status_code == 302
    assert "wechat_error=state_invalid" in resp.headers["location"]


def test_callback_ignores_query_redirect(client, monkeypatch):
    """query 里的 redirect 永远不参与跳转，只信签名 state。"""
    _mock_wechat(monkeypatch, openid="wxweb_open_redirect")
    state = _start_authorize(client, redirect="/")
    resp = client.get(
        "/api/auth/wechat/web/callback",
        params={"code": "c", "state": state, "redirect": "https://evil.example"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "evil.example" not in resp.headers["location"]


# ── 首次必须绑手机 ────────────────────────────────────────────────────────


def test_new_user_callback_requires_phone_before_any_token(client, monkeypatch):
    _full_wechat_login(client, monkeypatch, openid="wxweb_new_user")

    resp = client.post("/api/auth/wechat/web/exchange")
    assert resp.status_code == 200
    assert resp.json() == {"phone_required": True}
    assert "access_token" not in resp.json()
    # 未验证手机号前不建账号
    assert _row("SELECT user_id FROM auth_users WHERE wechat_web_openid = ?", ("wxweb_new_user",)) is None


def test_bind_phone_creates_account_and_second_login_skips_binding(client, monkeypatch):
    from routers import auth as auth_router

    async def _ok(phone, code, biz_id=None):
        return None

    monkeypatch.setattr(auth_router, "check_verify_code", _ok)

    _full_wechat_login(client, monkeypatch, openid="wxweb_bind_new")
    bind = client.post(
        "/api/auth/wechat/web/bind-phone",
        json={"phone": "13700001111", "code": "123456"},
    )
    assert bind.status_code == 200, bind.text
    body = bind.json()
    assert body["access_token"] and body["company_id"]
    assert body["is_new"] is True and body["merged"] is False

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200, me.text
    assert me.json()["wechat_web_bound"] is True
    assert me.json()["phone"] == "13700001111"

    # 再次扫码：已有手机号 → login ticket，不再要求绑定
    _mock_wechat(monkeypatch, openid="wxweb_bind_new")
    state = _start_authorize(client)
    resp = _callback(client, state)
    assert resp.status_code == 302
    again = client.post("/api/auth/wechat/web/exchange")
    assert again.status_code == 200, again.text
    assert again.json()["phone_required"] is False
    assert again.json()["access_token"]


def test_unionid_match_without_phone_still_requires_bind(client, monkeypatch):
    _seed_user("u-union-no-phone", wechat_web_openid="wxweb_other", wechat_unionid="union-no-phone")
    _full_wechat_login(client, monkeypatch, openid="wxweb_union_new", unionid="union-no-phone")
    resp = client.post("/api/auth/wechat/web/exchange")
    assert resp.status_code == 200
    assert resp.json() == {"phone_required": True}


def test_bind_ticket_not_burned_on_sms_failure(client, monkeypatch):
    from fastapi import HTTPException
    from routers import auth as auth_router

    async def _bad(phone, code, biz_id=None):
        raise HTTPException(status_code=400, detail="验证码错误")

    monkeypatch.setattr(auth_router, "check_verify_code", _bad)
    token = _full_wechat_login(client, monkeypatch, openid="wxweb_sms_fail")

    resp = client.post(
        "/api/auth/wechat/web/bind-phone", json={"phone": "13700002222", "code": "000000"}
    )
    assert resp.status_code == 400

    from services.account_service import get_wechat_login_ticket

    ticket = asyncio.run(get_wechat_login_ticket(token, purpose="bind"))
    assert ticket is not None, "短信失败不应烧掉 bind ticket"


def test_exchange_rejects_bind_ticket_without_consuming_it(client, monkeypatch):
    token = _full_wechat_login(client, monkeypatch, openid="wxweb_purpose")
    resp = client.post("/api/auth/wechat/web/exchange")
    assert resp.json() == {"phone_required": True}

    from services.account_service import get_wechat_login_ticket

    assert asyncio.run(get_wechat_login_ticket(token, purpose="bind")) is not None


def test_ticket_single_use_only_one_exchange_succeeds(client, monkeypatch):
    from services.account_service import (
        create_wechat_login_ticket,
        exchange_wechat_login_ticket,
    )

    _seed_user("u-single-use", phone="13700003333", phone_verified_at="2026-01-01",
               wechat_web_openid="wxweb_single", company_id="c-single")
    token = asyncio.run(
        create_wechat_login_ticket(
            openid="wxweb_single", purpose="login", user_id="u-single-use"
        )
    )

    async def _attempt():
        try:
            await exchange_wechat_login_ticket(token)
            return True
        except Exception:  # noqa: BLE001 - SQLite 并发可能抛锁错误，同样视为失败
            return False

    async def _both():
        return await asyncio.gather(_attempt(), _attempt())

    results = asyncio.run(_both())
    assert sum(1 for ok in results if ok) == 1


# ── 合并老账号 ────────────────────────────────────────────────────────────


def test_merge_preserves_company_plan_and_tokens(client, monkeypatch):
    from routers import auth as auth_router

    async def _ok(phone, code, biz_id=None):
        return None

    monkeypatch.setattr(auth_router, "check_verify_code", _ok)
    _seed_user(
        "u-merge",
        phone="13700004444",
        phone_verified_at="2026-01-01",
        company_id="c-merge",
        token_balance=4321,
        promo_token_balance=99,
    )
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute("UPDATE auth_users SET plan_code = 'pro' WHERE user_id = 'u-merge'")
        conn.commit()
    finally:
        conn.close()

    _full_wechat_login(client, monkeypatch, openid="wxweb_merge")
    resp = client.post(
        "/api/auth/wechat/web/bind-phone",
        json={"phone": "13700004444", "code": "123456"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["merged"] is True and body["is_new"] is False
    assert body["user_id"] == "u-merge"
    assert body["company_id"] == "c-merge"

    row = _row(
        "SELECT plan_code, token_balance, promo_token_balance, wechat_web_openid "
        "FROM auth_users WHERE user_id = 'u-merge'"
    )
    assert row["plan_code"] == "pro"
    assert row["token_balance"] == 4321
    assert row["promo_token_balance"] == 99
    assert row["wechat_web_openid"] == "wxweb_merge"


def test_bind_conflict_when_phone_owned_by_other_web_openid(client, monkeypatch):
    from routers import auth as auth_router

    async def _ok(phone, code, biz_id=None):
        return None

    monkeypatch.setattr(auth_router, "check_verify_code", _ok)
    _seed_user(
        "u-occupied",
        phone="13700005555",
        phone_verified_at="2026-01-01",
        company_id="c-occupied",
        wechat_web_openid="wxweb_other_owner",
    )

    _full_wechat_login(client, monkeypatch, openid="wxweb_conflict")
    resp = client.post(
        "/api/auth/wechat/web/bind-phone",
        json={"phone": "13700005555", "code": "123456"},
    )
    assert resp.status_code == 409
    row = _row("SELECT wechat_web_openid FROM auth_users WHERE user_id = 'u-occupied'")
    assert row["wechat_web_openid"] == "wxweb_other_owner", "冲突时绝不允许覆盖已有绑定"


def test_disabled_old_account_rejected_before_ticket(client, monkeypatch):
    _seed_user(
        "u-disabled",
        phone="13700006666",
        phone_verified_at="2026-01-01",
        wechat_web_openid="wxweb_disabled",
        is_disabled=1,
    )
    _mock_wechat(monkeypatch, openid="wxweb_disabled")
    state = _start_authorize(client)
    resp = _callback(client, state)
    assert resp.status_code == 302
    assert "wechat_error=account_disabled" in resp.headers["location"]
    assert PENDING_COOKIE not in resp.cookies


# ── ticket 存储与身份隔离 ─────────────────────────────────────────────────


def test_ticket_table_stores_hash_only(client, monkeypatch):
    token = _full_wechat_login(client, monkeypatch, openid="wxweb_hash_only")
    rows = _row("SELECT * FROM wechat_login_tickets ORDER BY created_at DESC LIMIT 1")
    assert token not in json.dumps(rows, ensure_ascii=False)
    assert len(rows["ticket_hash"]) == 64


def test_miniprogram_login_preserves_wechat_web_identity(client):
    from services.account_service import get_or_create_user

    _seed_user(
        "u-mini",
        openid="mini-openid-1",
        wechat_web_openid="wxweb_keep",
        wechat_unionid="union-keep",
        company_id="c-mini",
    )
    user, _company, is_new = asyncio.run(get_or_create_user("mini-openid-1"))
    assert is_new is False
    assert user["wechat_web_openid"] == "wxweb_keep"
    row = _row("SELECT wechat_web_openid, wechat_unionid FROM auth_users WHERE user_id = 'u-mini'")
    assert row["wechat_web_openid"] == "wxweb_keep"
    assert row["wechat_unionid"] == "union-keep"


def test_privacy_consent_recorded_on_bind(client, monkeypatch):
    from routers import auth as auth_router

    async def _ok(phone, code, biz_id=None):
        return None

    monkeypatch.setattr(auth_router, "check_verify_code", _ok)
    _full_wechat_login(client, monkeypatch, openid="wxweb_consent")
    resp = client.post(
        "/api/auth/wechat/web/bind-phone",
        json={"phone": "13700007777", "code": "123456"},
    )
    assert resp.status_code == 200, resp.text
    consent = _row(
        "SELECT privacy_accepted_at, tos_version, privacy_version FROM auth_consents "
        "WHERE user_id = ?",
        (resp.json()["user_id"],),
    )
    assert consent["privacy_accepted_at"] == ACCEPTED_AT
    assert consent["tos_version"] == "v1.0"


def test_sqlite_auth_users_has_wechat_web_columns(client):
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(auth_users)")}
    finally:
        conn.close()
    assert {"wechat_web_openid", "wechat_unionid", "wechat_web_bound_at"} <= columns
