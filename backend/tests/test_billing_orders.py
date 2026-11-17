# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""支付宝收款地基回归测试。

重点防御的根因（对应 A 档不变量）：
- 通知验签失败绝不触碰订单/用户；
- 支付宝重复推送通知必须幂等（CAS），续费不因重复通知叠加顺延；
- 通知金额与订单金额不符绝不激活权益；
- 付费套餐到期后必须惰性回落 trial 口径（不能白嫖未到期前的无限额度）。
"""
from __future__ import annotations

import base64
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import config
from core.plans import sellable_plans
from services.alipay_client import sign_params
from services.quota_service import add_months
from services.token_service import create_access_token

from tests.conftest import TEST_CORE_DB

# 套餐到期/周期口径一律 UTC 日期（与 quota_service 一致），不能用本地时区日期
today = datetime.now(timezone.utc).date()


def _headers(user_id: str, company_id: str = "c1") -> dict:
    token = create_access_token({"sub": user_id, "company_id": company_id})
    return {"Authorization": f"Bearer {token}"}


def _ensure_user(user_id: str, plan_code: str = "trial", plan_expires_at: str | None = None) -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO auth_users ("
            " user_id, openid, phone, phone_verified_at, wechat_openid_bound_at,"
            " current_company_id, nickname, avatar, is_platform_admin, is_disabled,"
            " plan_code, plan_anchor_at, plan_expires_at, quota_bonus_tokens,"
            " created_at, last_login_at, updated_at"
            ") VALUES (?, '', '', '', '', 'c1', '', '', 0, 0, ?, NULL, ?, 0,"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', '')",
            (user_id, plan_code, plan_expires_at),
        )
        conn.execute(
            "INSERT OR IGNORE INTO company_members (company_id, user_id, role, status, created_at, updated_at)"
            " VALUES ('c1', ?, 'owner', 'active', '2026-01-01', '2026-01-01')",
            (user_id,),
        )
        conn.commit()
    finally:
        conn.close()


def _cleanup(user_ids: list[str]) -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        for uid in user_ids:
            orders = [row[0] for row in conn.execute(
                "SELECT out_trade_no FROM billing_orders WHERE user_id = ?", (uid,)
            ).fetchall()]
            for no in orders:
                conn.execute("DELETE FROM billing_notify_logs WHERE out_trade_no = ?", (no,))
            conn.execute("DELETE FROM billing_orders WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM billing_notify_logs WHERE out_trade_no LIKE 'SEED-%'")
            conn.execute("DELETE FROM user_quota_counters WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM company_members WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM auth_users WHERE user_id = ?", (uid,))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def alipay_keys(monkeypatch):
    """注入测试专用 RSA2 密钥对：签名与验签配对，测试间互不依赖 backend/.env。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    public_b64 = base64.b64encode(
        private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).decode("ascii")
    monkeypatch.setattr(config, "ALIPAY_APP_ID", "2021007120638329")
    monkeypatch.setattr(config, "ALIPAY_PRIVATE_KEY", private_pem)
    monkeypatch.setattr(config, "ALIPAY_PUBLIC_KEY", public_b64)
    return {"app_id": "2021007120638329"}


def _create_order(client, headers: dict, plan_code: str = "standard") -> dict:
    resp = client.post("/api/billing/orders", json={"planCode": plan_code}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _signed_notify_body(order_no: str, amount: str, *, status: str = "TRADE_SUCCESS",
                        app_id: str | None = None, tamper: bool = False) -> dict:
    params = {
        "app_id": app_id or config.ALIPAY_APP_ID,
        "trade_no": f"TEST{now_stamp()}",
        "out_trade_no": order_no,
        "trade_status": status,
        "total_amount": amount,
        "buyer_logon_id": "test****@alipay.com",
        "sign_type": "RSA2",
        "charset": "utf-8",
        "notify_type": "trade_status_sync",
    }
    params["sign"] = sign_params(params)
    if tamper:
        params["sign"] = "AA" + params["sign"][2:]
    return params


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")


def _seed_order(order_no: str, user_id: str, *, status: str = "created", amount: str = "598.00",
                expires_in_hours: float = 2.0) -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        # SQLAlchemy 的 SQLite 方言存 DateTime 用空格分隔无时区格式；裸 SQL 播种必须同格式，
        # 否则与 SQLAlchemy 绑定参数的字典序比较失效（'T' > ' '，惰性关单条件恒假）
        expires = (datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )
        conn.execute(
            "INSERT OR REPLACE INTO billing_orders (id, out_trade_no, user_id, plan_code, period,"
            " amount, status, subject, trade_no, buyer_logon_id, expires_at, created_at, updated_at)"
            " VALUES (?, ?, ?, 'standard', 'annual', ?, ?, '云端标准版（年付）', '', '', ?,"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (f"seed-{order_no}", order_no, user_id, amount, status, expires),
        )
        conn.commit()
    finally:
        conn.close()


def _user_row(user_id: str) -> dict:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        row = conn.execute(
            "SELECT plan_code, plan_anchor_at, plan_expires_at FROM auth_users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return {"plan_code": row[0], "plan_anchor_at": row[1], "plan_expires_at": row[2]}
    finally:
        conn.close()


def _order_row(order_no: str) -> dict:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        row = conn.execute(
            "SELECT status, trade_no FROM billing_orders WHERE out_trade_no = ?", (order_no,)
        ).fetchone()
        return {"status": row[0], "trade_no": row[1]}
    finally:
        conn.close()


def _post_notify(client, params: dict):
    return client.post("/api/billing/alipay/notify", data=params)


# ---------------------------------------------------------------------------
# 售卖目录与下单
# ---------------------------------------------------------------------------

def test_pricing_catalog_single_source(client, auth_headers, alipay_keys):
    """定价目录必须来自 core/plans.py 唯一真相，且年付价格两位小数字符串。"""
    resp = client.get("/api/billing/pricing", headers=auth_headers)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["alipayEnabled"] is True
    by_code = {item["planCode"]: item for item in payload["items"]}
    assert by_code["standard"]["priceAnnual"] == "598.00"
    assert by_code["pro"]["priceAnnual"] == "2980.00"
    assert by_code["standard"]["period"] == "annual"
    # trial / plus 不在目录
    assert set(by_code) == {plan.code for plan in sellable_plans()}


def test_create_order_uses_catalog_price_and_unique_trade_no(client, auth_headers, alipay_keys):
    first = _create_order(client, _headers("billing_create"), "standard")
    second = _create_order(client, _headers("billing_create"), "standard")
    assert first["amount"] == "598.00"
    assert first["orderNo"].startswith("SB") and len(first["orderNo"]) <= 32
    assert first["orderNo"] != second["orderNo"]
    # 收银台 URL 走配置网关 + page.pay 方法
    assert "method=alipay.trade.page.pay" in first["payUrl"]
    assert first["payUrl"].startswith(config.ALIPAY_GATEWAY)
    _cleanup(["billing_create"])


def test_create_order_rejects_non_sellable_plan(client, auth_headers, alipay_keys):
    resp = client.post("/api/billing/orders", json={"planCode": "trial"}, headers=_headers("billing_sell"))
    assert resp.status_code == 422
    _cleanup(["billing_sell"])


def test_orders_endpoint_requires_auth(client):
    assert client.get("/api/billing/pricing").status_code == 401
    assert client.post("/api/billing/orders", json={"planCode": "standard"}).status_code == 401


# ---------------------------------------------------------------------------
# 异步通知：验签 / 幂等 / 金额
# ---------------------------------------------------------------------------

def test_notify_bad_signature_never_touches_order(client, auth_headers, alipay_keys):
    """根因防御：伪造/篡改通知不得改变订单与套餐状态（验签是回调的唯一身份）。"""
    user_id = "billing_bad_sign"
    _ensure_user(user_id)
    order = _create_order(client, _headers(user_id), "standard")
    resp = _post_notify(client, _signed_notify_body(order["orderNo"], "598.00", tamper=True))
    assert resp.status_code == 200 and resp.text == "failure"
    assert _order_row(order["orderNo"])["status"] == "created"
    assert _user_row(user_id)["plan_code"] == "trial"
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        verified = conn.execute(
            "SELECT sign_verified, result FROM billing_notify_logs WHERE out_trade_no = ?",
            (order["orderNo"],),
        ).fetchone()
        assert verified == (0, "rejected_bad_sign")
    finally:
        conn.close()
    _cleanup([user_id])


def test_notify_success_activates_and_replay_is_idempotent(client, auth_headers, alipay_keys):
    """根因防御：TRADE_SUCCESS 激活权益；支付宝重复推送不得二次顺延到期日（CAS 幂等）。"""
    user_id = "billing_idempotent"
    _ensure_user(user_id)
    order = _create_order(client, _headers(user_id), "standard")

    first = _post_notify(client, _signed_notify_body(order["orderNo"], "598.00"))
    assert first.text == "success"
    row = _user_row(user_id)
    assert row["plan_code"] == "standard"
    assert row["plan_expires_at"] == add_months(today, 12).isoformat()
    assert row["plan_anchor_at"] == today.isoformat()
    assert _order_row(order["orderNo"])["status"] == "activated"

    # 重复推送（支付宝最多重推 8 次）：仍回 success，但到期日不得叠加
    replay = _post_notify(client, _signed_notify_body(order["orderNo"], "598.00"))
    assert replay.text == "success"
    assert _user_row(user_id)["plan_expires_at"] == row["plan_expires_at"]
    assert _order_row(order["orderNo"])["status"] == "activated"
    _cleanup([user_id])


def test_notify_amount_mismatch_never_activates(client, auth_headers, alipay_keys):
    """根因防御：通知金额与订单不符（部分支付/篡改）绝不激活，留档人工处理。"""
    user_id = "billing_amount"
    _ensure_user(user_id)
    order = _create_order(client, _headers(user_id), "standard")
    resp = _post_notify(client, _signed_notify_body(order["orderNo"], "1.00"))
    assert resp.text == "failure"
    assert _order_row(order["orderNo"])["status"] == "created"
    assert _user_row(user_id)["plan_code"] == "trial"
    _cleanup([user_id])


def test_notify_wrong_app_id_rejected(client, auth_headers, alipay_keys):
    user_id = "billing_appid"
    _ensure_user(user_id)
    order = _create_order(client, _headers(user_id), "standard")
    resp = _post_notify(
        client, _signed_notify_body(order["orderNo"], "598.00", app_id="2021000000000000")
    )
    assert resp.text == "failure"
    assert _order_row(order["orderNo"])["status"] == "created"
    _cleanup([user_id])


def test_notify_trade_closed_closes_created_order(client, auth_headers, alipay_keys):
    user_id = "billing_closed"
    _ensure_user(user_id)
    order = _create_order(client, _headers(user_id), "standard")
    resp = _post_notify(
        client, _signed_notify_body(order["orderNo"], "598.00", status="TRADE_CLOSED")
    )
    assert resp.text == "success"
    assert _order_row(order["orderNo"])["status"] == "closed"
    assert _user_row(user_id)["plan_code"] == "trial"
    _cleanup([user_id])


def test_notify_unknown_order_logged_as_not_found(client, alipay_keys):
    resp = _post_notify(client, _signed_notify_body("SB00000000000000", "598.00"))
    assert resp.text == "failure"
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        result = conn.execute(
            "SELECT result FROM billing_notify_logs WHERE out_trade_no = 'SB00000000000000'"
        ).fetchone()
        assert result is not None and result[0] == "order_not_found"
        conn.execute("DELETE FROM billing_notify_logs WHERE out_trade_no = 'SB00000000000000'")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 续费 / 惰性降级 / 惰性关单
# ---------------------------------------------------------------------------

def test_renewal_extends_from_existing_expiry_keeps_anchor(client, auth_headers, alipay_keys):
    """根因防御：同套餐续费从原到期日顺延（不吞用户未到期天数），周期锚点不动。"""
    user_id = "billing_renew"
    anchor = "2026-03-15"
    future = add_months(today, 3)
    _ensure_user(user_id, plan_code="standard", plan_expires_at=future.isoformat())
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute("UPDATE auth_users SET plan_anchor_at = ? WHERE user_id = ?", (anchor, user_id))
        conn.commit()
    finally:
        conn.close()

    order = _create_order(client, _headers(user_id), "standard")
    resp = _post_notify(client, _signed_notify_body(order["orderNo"], "598.00"))
    assert resp.text == "success"
    row = _user_row(user_id)
    assert row["plan_expires_at"] == add_months(future, 12).isoformat()
    assert row["plan_anchor_at"] == anchor
    _cleanup([user_id])


def test_expired_plan_falls_back_to_trial_in_quota_state(client, auth_headers):
    """根因防御：付费套餐到期后必须回落 trial 口径（plan_expires_at 过期 ≠ 永久解锁）。"""
    user_id = "billing_expired"
    _ensure_user(user_id, plan_code="standard", plan_expires_at=(today - timedelta(days=1)).isoformat())
    resp = client.get("/api/usage/me", headers=_headers(user_id))
    assert resp.status_code == 200
    quota = resp.json().get("quota") or {}
    assert quota.get("planCode") == "trial"
    assert quota.get("planExpiresAt") == (today - timedelta(days=1)).isoformat()
    _cleanup([user_id])


def test_active_plan_exposes_plan_expires_at(client, auth_headers):
    user_id = "billing_active"
    _ensure_user(user_id, plan_code="standard", plan_expires_at=add_months(today, 6).isoformat())
    resp = client.get("/api/usage/me", headers=_headers(user_id))
    quota = resp.json().get("quota") or {}
    assert quota.get("planCode") == "standard"
    assert quota.get("planExpiresAt") == add_months(today, 6).isoformat()
    _cleanup([user_id])


def test_lazy_close_expires_stale_created_orders(client, auth_headers, alipay_keys):
    """根因防御：过了支付有效期的 created 订单不得再被成功通知激活为新权益。"""
    user_id = "billing_lazy"
    _ensure_user(user_id)
    stale_no = "SEED-STALE-01"
    _seed_order(stale_no, user_id, status="created", expires_in_hours=-1.0)
    resp = client.get("/api/billing/orders", headers=_headers(user_id))
    assert resp.status_code == 200
    statuses = {item["orderNo"]: item["status"] for item in resp.json()["items"]}
    assert statuses[stale_no] == "closed"
    _cleanup([user_id])


# ---------------------------------------------------------------------------
# 同步回跳与管理端
# ---------------------------------------------------------------------------

def test_return_redirects_to_mine_with_order_no(client):
    resp = client.get("/api/billing/alipay/return", params={"out_trade_no": "SB261113000000"}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("/mine?orderNo=SB261113000000")


def test_admin_patch_user_plan_expires_at(client, auth_headers):
    """管理端可设置/清空套餐到期日（客服场景），且必须校验日期格式。"""
    user_id = "billing_admin"
    _ensure_user(user_id)
    admin_conn = sqlite3.connect(TEST_CORE_DB)
    try:
        admin_conn.execute(
            "INSERT OR IGNORE INTO auth_users (user_id, openid, phone, phone_verified_at,"
            " wechat_openid_bound_at, current_company_id, nickname, avatar, is_platform_admin,"
            " is_disabled, plan_code, plan_anchor_at, quota_bonus_tokens, created_at, last_login_at, updated_at)"
            " VALUES ('admin_billing', '', '', '', '', 'c1', '', '', 1, 0, 'trial', NULL, 0,"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', '')"
        )
        admin_conn.commit()
    finally:
        admin_conn.close()

    resp = client.patch(
        "/api/admin/users/billing_admin",
        json={"planCode": "standard", "planExpiresAt": "2027-11-13"},
        headers=_headers("admin_billing"),
    )
    assert resp.status_code == 200, resp.text
    assert _user_row(user_id)["plan_expires_at"] == "2027-11-13"

    bad = client.patch(
        "/api/admin/users/billing_admin",
        json={"planExpiresAt": "not-a-date"},
        headers=_headers("admin_billing"),
    )
    assert bad.status_code == 422
    _cleanup([user_id])
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute("DELETE FROM auth_users WHERE user_id = 'admin_billing'")
        conn.commit()
    finally:
        conn.close()
