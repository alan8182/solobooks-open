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
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import config
from core.plans import sellable_plans
from services.alipay_client import (
    _normalize_public_key,
    _sign_content,
    gateway_post,
    query_trade,
    sign_params,
    verify_notify_signature,
)
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
                conn.execute("DELETE FROM billing_refunds WHERE out_trade_no = ?", (no,))
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


def _notify_sign(params: dict) -> str:
    """按通知口径（剔除 sign/sign_type，对应 rsaCheckV1）签名——与 verify_notify_signature 同规则。

    请求签名（sign_params）与通知验签口径不同，不能混用。
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    from services.alipay_client import _load_private_key

    key = _load_private_key(config.ALIPAY_PRIVATE_KEY)
    signature = key.sign(
        _sign_content(params, keep_sign_type=False).encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


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
    params["sign"] = _notify_sign(params)
    if tamper:
        params["sign"] = "AA" + params["sign"][2:]
    return params


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")


def _seed_order(order_no: str, user_id: str, *, status: str = "created", amount: str = "598.00",
                expires_in_hours: float = 2.0, channel: str = "alipay") -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        # SQLAlchemy 的 SQLite 方言存 DateTime 用空格分隔无时区格式；裸 SQL 播种必须同格式，
        # 否则与 SQLAlchemy 绑定参数的字典序比较失效（'T' > ' '，惰性关单条件恒假）
        expires = (datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )
        conn.execute(
            "INSERT OR REPLACE INTO billing_orders (id, out_trade_no, user_id, plan_code, period,"
            " payment_channel, amount, status, subject, trade_no, buyer_logon_id, expires_at,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, 'standard', 'annual', ?, ?, ?, '云端标准版（年付）', '', '', ?,"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (f"seed-{order_no}", order_no, user_id, channel, amount, status, expires),
        )
        conn.commit()
    finally:
        conn.close()


def _user_row(user_id: str) -> dict:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        row = conn.execute(
            "SELECT plan_code, plan_anchor_at, plan_expires_at, token_balance FROM auth_users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return {
            "plan_code": row[0],
            "plan_anchor_at": row[1],
            "plan_expires_at": row[2],
            "token_balance": row[3],
        }
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
# 签名口径回归：请求含 sign_type / 通知验签剔除（2026-11-18 线上 invalid-signature 根因）
# ---------------------------------------------------------------------------

def test_request_signature_includes_sign_type(alipay_keys):
    """支付宝规则不对称：请求待签串包含 sign_type（官方 SDK getSignatureContent），
    网关按含 sign_type 的串验签；按通知口径（剔除 sign_type）签名会在收银台
    报 invalid-signature（2026-11-18 线上实测）。负向断言防退回旧实现。"""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    params = {
        "app_id": config.ALIPAY_APP_ID,
        "biz_content": '{"out_trade_no":"SBTESTSIG1","total_amount":"598.00"}',
        "charset": "utf-8",
        "method": "alipay.trade.page.pay",
        "sign_type": "RSA2",
        "timestamp": "2026-11-18 16:44:50",
        "version": "1.0",
    }
    sign = base64.b64decode(sign_params(params))
    public_key = serialization.load_pem_public_key(
        _normalize_public_key(config.ALIPAY_PUBLIC_KEY)
    )
    # 网关口径：含 sign_type 的待签串必须验证通过
    public_key.verify(
        sign, _sign_content(params, keep_sign_type=True).encode("utf-8"),
        padding.PKCS1v15(), hashes.SHA256(),
    )
    # 旧实现口径（剔除 sign_type）必须验证不过——防回归
    with pytest.raises(InvalidSignature):
        public_key.verify(
            sign, _sign_content(params, keep_sign_type=False).encode("utf-8"),
            padding.PKCS1v15(), hashes.SHA256(),
        )


def test_notify_verification_excludes_sign_type(alipay_keys):
    """通知口径（rsaCheckV1）：剔除 sign 与 sign_type 后验签通过。"""
    params = {
        "app_id": config.ALIPAY_APP_ID,
        "out_trade_no": "SBTESTSIG2",
        "trade_status": "TRADE_SUCCESS",
        "total_amount": "598.00",
        "sign_type": "RSA2",
    }
    params["sign"] = _notify_sign(params)
    assert verify_notify_signature(params) is True


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


# ---------------------------------------------------------------------------
# 管理端：订单列表 / 详情 / 全额退款 / 网关只读校准
# ---------------------------------------------------------------------------

def _ensure_admin(admin_id: str = "admin_billing_ops") -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO auth_users (user_id, openid, phone, phone_verified_at,"
            " wechat_openid_bound_at, current_company_id, nickname, avatar, is_platform_admin,"
            " is_disabled, plan_code, plan_anchor_at, quota_bonus_tokens, created_at,"
            " last_login_at, updated_at)"
            " VALUES (?, '', '', '', '', 'c1', '', '', 1, 0, 'trial', NULL, 0,"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', '')",
            (admin_id,),
        )
        conn.commit()
    finally:
        conn.close()


def _cleanup_admin(admin_id: str = "admin_billing_ops") -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute("DELETE FROM admin_audit_logs WHERE operator_id = ?", (admin_id,))
        conn.execute("DELETE FROM auth_users WHERE user_id = ?", (admin_id,))
        conn.commit()
    finally:
        conn.close()


def _refund_success_node(out_trade_no: str) -> dict:
    """alipay.trade.refund 同步成功响应的业务节点（服务层 monkeypatch 用）。"""
    return {
        "code": "10000",
        "msg": "Success",
        "out_trade_no": out_trade_no,
        "trade_no": f"T{now_stamp()}",
        "fund_change": "Y",
        "refund_fee": "598.00",
        "gmtRefundPay": "2026-11-18 17:10:00",
    }


def _refund_row(order_no: str) -> dict:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        row = conn.execute(
            "SELECT status, gateway_sub_code, gateway_msg FROM billing_refunds"
            " WHERE out_trade_no = ? ORDER BY created_at DESC LIMIT 1",
            (order_no,),
        ).fetchone()
        return {"status": row[0], "subCode": row[1], "msg": row[2]} if row else {}
    finally:
        conn.close()


def test_admin_orders_requires_admin(client):
    """订单管理端点：未登录 401，非平台管理员 403。"""
    _ensure_user("billing_notadmin")
    assert client.get("/api/admin/billing/orders").status_code == 401
    resp = client.get("/api/admin/billing/orders", headers=_headers("billing_notadmin"))
    assert resp.status_code == 403
    _cleanup(["billing_notadmin"])


def test_admin_refund_success_revokes_plan_and_replay_is_idempotent(
    client, alipay_keys, monkeypatch
):
    """退款成功：权益立即收回（trial/清空到期日）；重复提交回放同一结果，网关只调一次。"""
    user_id = "billing_refund_ok"
    _ensure_user(user_id, plan_code="standard", plan_expires_at="2027-11-18")
    order_no = "SEED-REFUND-01"
    _seed_order(order_no, user_id, status="activated")

    calls: list[str] = []

    def fake_refund(out_trade_no, refund_amount, *, out_request_no, refund_reason):
        calls.append(out_request_no)
        assert refund_amount == "598.00" and out_request_no == f"{out_trade_no}R1"
        return _refund_success_node(out_trade_no)

    monkeypatch.setattr("services.alipay_client.refund", fake_refund)
    _ensure_admin()
    try:
        resp = client.post(
            f"/api/admin/billing/orders/{order_no}/refund",
            json={"reason": "客户误购，协商退款"},
            headers=_headers("admin_billing_ops"),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["refunds"][0]["status"] == "success"
        assert _refund_row(order_no)["status"] == "success"
        user = _user_row(user_id)
        assert user["plan_code"] == "trial"
        assert user["plan_expires_at"] is None

        # 幂等重放：第二次提交不再调网关，返回同一成功结果
        resp2 = client.post(
            f"/api/admin/billing/orders/{order_no}/refund",
            json={"reason": "客户误购，协商退款"},
            headers=_headers("admin_billing_ops"),
        )
        assert resp2.status_code == 200
        assert resp2.json()["refunds"][0]["status"] == "success"
        assert len(calls) == 1
    finally:
        _cleanup([user_id])
        _cleanup_admin()


def test_admin_refund_rejects_reason_missing_and_bad_status(client, alipay_keys, monkeypatch):
    """缺原因 422 REFUND_REASON_REQUIRED；未支付/已关单 422 ORDER_NOT_REFUNDABLE；网关绝不被调。"""
    user_id = "billing_refund_rej"
    _ensure_user(user_id)
    _seed_order("SEED-REFUND-10", user_id, status="created")
    _seed_order("SEED-REFUND-11", user_id, status="closed")

    def fail_refund(*args, **kwargs):
        raise AssertionError("不可退路径不得调用网关")

    monkeypatch.setattr("services.alipay_client.refund", fail_refund)
    _ensure_admin()
    try:
        missing = client.post(
            "/api/admin/billing/orders/SEED-REFUND-10/refund",
            json={"reason": "  "},
            headers=_headers("admin_billing_ops"),
        )
        assert missing.status_code == 422
        assert missing.json()["detail"]["code"] == "REFUND_REASON_REQUIRED"

        unpaid = client.post(
            "/api/admin/billing/orders/SEED-REFUND-10/refund",
            json={"reason": "试退未支付单"},
            headers=_headers("admin_billing_ops"),
        )
        assert unpaid.status_code == 422
        assert unpaid.json()["detail"]["code"] == "ORDER_NOT_REFUNDABLE"

        closed = client.post(
            "/api/admin/billing/orders/SEED-REFUND-11/refund",
            json={"reason": "试退已关单"},
            headers=_headers("admin_billing_ops"),
        )
        assert closed.status_code == 422
        assert closed.json()["detail"]["code"] == "ORDER_NOT_REFUNDABLE"
    finally:
        _cleanup([user_id])
        _cleanup_admin()


def test_admin_refund_blocks_when_other_active_orders(client, alipay_keys, monkeypatch):
    """用户存在其他有效订单时拒绝退款（权益自动收回的唯一性前提），不留退款行。"""
    user_id = "billing_refund_multi"
    _ensure_user(user_id)
    _seed_order("SEED-REFUND-20", user_id, status="activated")
    _seed_order("SEED-REFUND-21", user_id, status="activated")

    def fail_refund(*args, **kwargs):
        raise AssertionError("多单拒绝路径不得调用网关")

    monkeypatch.setattr("services.alipay_client.refund", fail_refund)
    _ensure_admin()
    try:
        resp = client.post(
            "/api/admin/billing/orders/SEED-REFUND-20/refund",
            json={"reason": "多单测试"},
            headers=_headers("admin_billing_ops"),
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "OTHER_ACTIVE_ORDERS"
        assert _refund_row("SEED-REFUND-20") == {}
        assert _order_row("SEED-REFUND-20")["status"] == "activated"
    finally:
        _cleanup([user_id])
        _cleanup_admin()


def test_admin_refund_plan_ignores_token_topup_orders(client, alipay_keys, monkeypatch):
    """点数充值单不得拦套餐退款（2026-11-19 生产死锁案例）。

    用户先买 standard 套餐（activated）又充 ¥5 点数（activated），退套餐被
    「其他有效订单」拦死；而充值点数已耗尽无法先退、手工调套餐又不改订单
    状态，退款通道永久死锁。守卫与权益收回只应数其他**套餐**订单——收回
    只动 plan_code/expires/anchor，与 token_balance 正交；退款后点数余额不动。
    """
    user_id = "billing_refund_topup"
    _ensure_user(user_id, plan_code="standard", plan_expires_at="2027-11-18")
    order_no = "SEED-REFUND-40"
    _seed_order(order_no, user_id, status="activated")
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        # 充值单（activated，50000 点）+ 用户已耗尽的点数余额
        conn.execute(
            "INSERT OR REPLACE INTO billing_orders (id, out_trade_no, user_id, plan_code,"
            " order_type, period, payment_channel, amount, token_amount, status, subject,"
            " trade_no, buyer_logon_id, expires_at, created_at, updated_at)"
            " VALUES ('seed-SEED-REFUND-41', 'SEED-REFUND-41', ?, 'token_topup',"
            " 'token_topup', 'one_time', 'alipay', '5.00', 50000, 'activated',"
            " 'AI 点数充值包', '', '', ?, '2026-01-01T00:00:00+00:00',"
            " '2026-01-01T00:00:00+00:00')",
            (user_id, (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S.%f")),
        )
        conn.execute(
            "UPDATE auth_users SET token_balance = 12300 WHERE user_id = ?", (user_id,)
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        "services.alipay_client.refund",
        lambda *args, **kwargs: _refund_success_node(order_no),
    )
    _ensure_admin()
    try:
        resp = client.post(
            f"/api/admin/billing/orders/{order_no}/refund",
            json={"reason": "套餐退款演练（存在点数充值单）"},
            headers=_headers("admin_billing_ops"),
        )
        assert resp.status_code == 200, resp.text
        assert _refund_row(order_no)["status"] == "success"
        assert _user_row(user_id)["plan_code"] == "trial"
        assert _user_row(user_id)["plan_expires_at"] in (None, "")
        # 点数余额不受套餐退款影响（充值单继续有效）
        assert _user_row(user_id)["token_balance"] == 12300
    finally:
        _cleanup([user_id])
        _cleanup_admin()


def test_admin_refund_gateway_failure_marks_failed_then_retry_success(
    client, alipay_keys, monkeypatch
):
    """网关失败：退款行落 failed（带 subCode）、订单与权益不动；重试复用同一行成功。"""
    from services.alipay_client import AlipayGatewayError

    user_id = "billing_refund_fail"
    _ensure_user(user_id, plan_code="standard", plan_expires_at="2027-11-18")
    order_no = "SEED-REFUND-30"
    _seed_order(order_no, user_id, status="paid")

    state = {"calls": 0}

    def flaky_refund(out_trade_no, refund_amount, *, out_request_no, refund_reason):
        state["calls"] += 1
        if state["calls"] == 1:
            raise AlipayGatewayError(
                code="40004",
                sub_code="ACQ.REFUND_AMT_INCONSISTENT",
                gateway_msg="Business Failed",
                sub_msg="退款金额不一致",
            )
        return _refund_success_node(out_trade_no)

    monkeypatch.setattr("services.alipay_client.refund", flaky_refund)
    _ensure_admin()
    try:
        resp = client.post(
            f"/api/admin/billing/orders/{order_no}/refund",
            json={"reason": "网关失败演练"},
            headers=_headers("admin_billing_ops"),
        )
        assert resp.status_code == 502, resp.text
        assert resp.json()["detail"]["code"] == "REFUND_FAILED"
        assert "退款金额不一致" in resp.json()["detail"]["message"]
        assert _refund_row(order_no)["status"] == "failed"
        assert _refund_row(order_no)["subCode"] == "ACQ.REFUND_AMT_INCONSISTENT"
        assert "退款金额不一致" in _refund_row(order_no)["msg"]
        assert _order_row(order_no)["status"] == "paid"
        assert _user_row(user_id)["plan_code"] == "standard"

        retry = client.post(
            f"/api/admin/billing/orders/{order_no}/refund",
            json={"reason": "网关失败演练"},
            headers=_headers("admin_billing_ops"),
        )
        assert retry.status_code == 200
        assert _refund_row(order_no)["status"] == "success"
        assert _user_row(user_id)["plan_code"] == "trial"
        assert state["calls"] == 2
    finally:
        _cleanup([user_id])
        _cleanup_admin()


def test_admin_query_order_is_readonly(client, alipay_keys, monkeypatch):
    """网关查询只读校准：返回网关真实状态（含 TRADE_NOT_EXIST→None），绝不改本地数据。"""
    user_id = "billing_query_ro"
    _ensure_user(user_id)
    order_no = "SEED-QUERY-01"
    _seed_order(order_no, user_id, status="activated")

    monkeypatch.setattr(
        "services.alipay_client.query_trade",
        lambda out_trade_no: {"tradeStatus": "TRADE_SUCCESS", "sendPayDate": "2026-11-18 10:00:00"},
    )
    _ensure_admin()
    try:
        resp = client.post(
            f"/api/admin/billing/orders/{order_no}/query",
            headers=_headers("admin_billing_ops"),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["gatewayTrade"]["tradeStatus"] == "TRADE_SUCCESS"
        assert _order_row(order_no)["status"] == "activated"

        monkeypatch.setattr("services.alipay_client.query_trade", lambda out_trade_no: None)
        resp2 = client.post(
            f"/api/admin/billing/orders/{order_no}/query",
            headers=_headers("admin_billing_ops"),
        )
        assert resp2.status_code == 200
        assert resp2.json()["gatewayTrade"] is None
        assert _order_row(order_no)["status"] == "activated"
    finally:
        _cleanup([user_id])
        _cleanup_admin()


def test_admin_list_orders_search_and_refund_flag(client, alipay_keys, monkeypatch):
    """列表按订单号片段搜索命中；退款后列表行带退款标记。"""
    user_id = "billing_list"
    _ensure_user(user_id)
    _seed_order("SEED-LIST-01", user_id, status="activated")
    _seed_order("SEED-LIST-02", user_id, status="created")

    monkeypatch.setattr(
        "services.alipay_client.refund",
        lambda out_trade_no, refund_amount, *, out_request_no, refund_reason: _refund_success_node(out_trade_no),
    )
    _ensure_admin()
    try:
        resp = client.get(
            "/api/admin/billing/orders?query=SEED-LIST-01",
            headers=_headers("admin_billing_ops"),
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        hit = [i for i in items if i["orderNo"] == "SEED-LIST-01"][0]
        assert hit["userId"] == user_id
        assert hit["refund"] is None  # 未退款

        client.post(
            "/api/admin/billing/orders/SEED-LIST-01/refund",
            json={"reason": "列表标记演练"},
            headers=_headers("admin_billing_ops"),
        )
        resp2 = client.get(
            "/api/admin/billing/orders?query=SEED-LIST-01",
            headers=_headers("admin_billing_ops"),
        )
        hit2 = [i for i in resp2.json()["items"] if i["orderNo"] == "SEED-LIST-01"][0]
        assert hit2["refund"]["status"] == "success"
    finally:
        _cleanup([user_id])
        _cleanup_admin()


# ---------------------------------------------------------------------------
# 协议层：同步网关调用（gateway_post / refund / query_trade）
# ---------------------------------------------------------------------------

class _FakeGatewayResponse:
    def __init__(self, text: str, status: int = 200):
        self.text = text
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _sign_response_node(node_raw: str) -> str:
    """同步响应签名口径：RSA2 覆盖节点 JSON 原文（与请求参数排序无关）。"""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    from services.alipay_client import _load_private_key

    key = _load_private_key(config.ALIPAY_PRIVATE_KEY)
    signature = key.sign(node_raw.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode("ascii")


def _fake_gateway_body(node: dict) -> str:
    """按网关返回形态拼响应体：先放占位 sign，再对原文节点签名替换占位。"""
    import json as _json

    node_raw = _json.dumps(node, ensure_ascii=False, separators=(",", ":"))
    body = f'{{"alipay_trade_refund_response":{node_raw},"sign":"SIGN_PLACEHOLDER"}}'
    raw_in_body = body.split('"alipay_trade_refund_response":')[1].rsplit(',"sign"', 1)[0]
    sign = _sign_response_node(raw_in_body)
    return body.replace('"SIGN_PLACEHOLDER"', f'"{sign}"')


def test_gateway_post_verifies_response_sign(alipay_keys, monkeypatch):
    """同步响应验签：sign 覆盖节点原文，验签通过才返回业务节点；请求口径含 sign_type。"""
    import httpx as _httpx

    captured: dict = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["data"] = data
        return _FakeGatewayResponse(
            _fake_gateway_body({"code": "10000", "msg": "Success", "fund_change": "Y"})
        )

    monkeypatch.setattr(_httpx, "post", fake_post)
    result = gateway_post(
        "alipay.trade.refund", {"out_trade_no": "SBX", "refund_amount": "598.00"}
    )
    assert result["fund_change"] == "Y"
    assert captured["data"]["sign_type"] == "RSA2"
    assert captured["data"]["method"] == "alipay.trade.refund"


def test_gateway_post_rejects_tampered_response(alipay_keys, monkeypatch):
    """响应节点被篡改（与 sign 不匹配）必须抛协议错误，不返回任何业务数据。"""
    import httpx as _httpx

    from services.alipay_client import AlipayProtocolError

    body = _fake_gateway_body({"code": "10000", "msg": "Success", "fund_change": "Y"})
    tampered = body.replace('"msg":"Success"', '"msg":"Tampered"')
    monkeypatch.setattr(_httpx, "post", lambda *a, **k: _FakeGatewayResponse(tampered))
    with pytest.raises(AlipayProtocolError, match="验签失败"):
        gateway_post("alipay.trade.refund", {"out_trade_no": "SBX"})


def test_gateway_post_reads_snake_case_sub_code_from_business_failure(alipay_keys, monkeypatch):
    """40004 的真实原因在 sub_code/sub_msg（支付宝报文是 snake_case），不能被吞成空串。"""
    import httpx as _httpx

    from services.alipay_client import AlipayGatewayError

    body = (
        '{"alipay_trade_refund_response":{"code":"40004","msg":"Business Failed",'
        '"sub_code":"ACQ.SELLER_BALANCE_NOT_ENOUGH","sub_msg":"卖家余额不足"},"sign":""}'
    )
    monkeypatch.setattr(_httpx, "post", lambda *a, **k: _FakeGatewayResponse(body))
    with pytest.raises(AlipayGatewayError) as exc_info:
        gateway_post("alipay.trade.refund", {"out_trade_no": "SBX"})
    assert exc_info.value.sub_code == "ACQ.SELLER_BALANCE_NOT_ENOUGH"
    assert exc_info.value.sub_msg == "卖家余额不足"
    assert "卖家余额不足" in str(exc_info.value)


def test_query_trade_maps_trade_not_exist_to_none(alipay_keys, monkeypatch):
    """ACQ.TRADE_NOT_EXIST 是正常业务态（订单未在网关出现），返回 None 而非报错。"""
    import httpx as _httpx

    body = (
        '{"alipay_trade_query_response":{"code":"40004","msg":"Business Failed",'
        '"sub_code":"ACQ.TRADE_NOT_EXIST","sub_msg":"交易不存在"}}'
    )
    monkeypatch.setattr(_httpx, "post", lambda *a, **k: _FakeGatewayResponse(body))
    assert query_trade("SB-NOT-EXIST") is None


# ---------------------------------------------------------------------------
# 微信支付（Native 扫码，第二渠道）
#
# 重点防御的根因：
# - 回调必须验签 + 解密（A2 闸门），失败回非 200（v3 语义，失败回 200 会静默丢通知）；
# - 金额（分）与订单金额严格比对，不符绝不激活；
# - 重复通知幂等（CAS），续费不叠加；
# - 回调丢失时查单兜底能救回已付订单（含被惰性关单的补结算）；
# - 微信退款 PROCESSING → 退款通知终态，成功收回权益。
# ---------------------------------------------------------------------------

_WECHAT_MCH_ID = "1900000001"
_WECHAT_APP_ID = "wxtestappid1234"
_WECHAT_API_V3_KEY = "0123456789abcdef0123456789abcdef"
_WECHAT_KEY_ID = "PUB_KEY_ID_TEST0001"


@pytest.fixture
def wechat_keys(monkeypatch):
    """微信支付测试配置与密钥对（请求签名/回调验签/AESGCM 解密全配对）。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    monkeypatch.setattr(config, "WECHAT_PAY_MCH_ID", _WECHAT_MCH_ID)
    monkeypatch.setattr(config, "WECHAT_PAY_APP_ID", _WECHAT_APP_ID)
    monkeypatch.setattr(config, "WECHAT_PAY_API_V3_KEY", _WECHAT_API_V3_KEY)
    monkeypatch.setattr(config, "WECHAT_PAY_SERIAL_NO", "TESTSERIAL0001")
    monkeypatch.setattr(config, "WECHAT_PAY_PRIVATE_KEY", private_pem)
    monkeypatch.setattr(config, "WECHAT_PAY_PUBLIC_KEY", public_pem)
    monkeypatch.setattr(config, "WECHAT_PAY_PUBLIC_KEY_ID", _WECHAT_KEY_ID)
    monkeypatch.setattr(
        config, "WECHAT_PAY_NOTIFY_URL", "https://solobooks.cn/api/billing/wechat/notify"
    )
    return {"private_key": private_key, "public_pem": public_pem}


class _FakeWechatHttpResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.content = json.dumps(payload).encode("utf-8")

    def json(self) -> dict:
        return self._payload


def _patch_wechat_gateway(monkeypatch, payload: dict, *, capture: dict | None = None) -> None:
    """把 wechat_pay_client 的同步网关调用打到内存假响应（capture 收请求体断言用）。"""
    import httpx as _httpx

    def fake_request(method, url, headers=None, content=None, timeout=None, **kwargs):
        if capture is not None:
            capture["method"] = method
            capture["url"] = url
            capture["body"] = json.loads(content.decode("utf-8") if content else "{}")
        return _FakeWechatHttpResponse(payload)

    monkeypatch.setattr(_httpx, "request", fake_request)


def _wechat_sign_headers(body: str, *, tamper: bool = False) -> dict:
    """按 v3 通知验签口径（ts\\nnonce\\nbody\\n）用测试私钥签名出 Wechatpay-* 头。"""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(config.WECHAT_PAY_PRIVATE_KEY.encode(), password=None)
    ts, nonce = "1760000000", "notify-nonce-01"
    sig = base64.b64encode(
        key.sign(f"{ts}\n{nonce}\n{body}\n".encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    ).decode("ascii")
    if tamper:
        sig = "AA" + sig[2:]
    return {
        "Wechatpay-Timestamp": ts,
        "Wechatpay-Nonce": nonce,
        "Wechatpay-Signature": sig,
        "Wechatpay-Serial": _WECHAT_KEY_ID,
    }


def _wechat_resource(order_no: str, *, cents: int = 59800, trade_state: str = "SUCCESS",
                     appid: str | None = None, mchid: str | None = None) -> dict:
    return {
        "mchid": mchid or config.WECHAT_PAY_MCH_ID,
        "appid": appid or config.WECHAT_PAY_APP_ID,
        "out_trade_no": order_no,
        "transaction_id": f"WX{now_stamp()}",
        "trade_state": trade_state,
        "amount": {"total": cents, "currency": "CNY", "payer_total": cents},
        "success_time": "2026-11-19T12:00:00+08:00",
    }


def _wechat_notify_payload(resource: dict, *, event_type: str = "TRANSACTION.SUCCESS") -> str:
    """构造加密回调信封（AEAD_AES_256_GCM，nonce 按原始字节当 IV）。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = "a1b2c3d4e5f6"
    ciphertext = base64.b64encode(
        AESGCM(config.WECHAT_PAY_API_V3_KEY.encode("utf-8")).encrypt(
            nonce.encode("utf-8"),
            json.dumps(resource, separators=(",", ":")).encode("utf-8"),
            b"transaction",
        )
    ).decode("ascii")
    return json.dumps({
        "id": f"evt-{now_stamp()}",
        "event_type": event_type,
        "summary": "支付成功" if "REFUND" not in event_type else "退款成功",
        "resource": {
            "algorithm": "AEAD_AES_256_GCM",
            "ciphertext": ciphertext,
            "associated_data": "transaction",
            "nonce": nonce,
        },
    })


def _post_wechat_notify(client, body: str, *, tamper: bool = False):
    return client.post(
        "/api/billing/wechat/notify",
        content=body.encode("utf-8"),
        headers=_wechat_sign_headers(body, tamper=tamper),
    )


def _create_wechat_order(client, user_id: str, monkeypatch) -> dict:
    """微信下单 helper：mock 网关返回 code_url，返回下单响应。"""
    _patch_wechat_gateway(monkeypatch, {"code_url": "weixin://wxpay/bizpayurl?pr=TEST"})
    resp = client.post(
        "/api/billing/orders", json={"planCode": "standard", "channel": "wechat"},
        headers=_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_pricing_catalog_exposes_wechat_channel(client, auth_headers, alipay_keys, wechat_keys):
    """目录同时暴露两渠道可用性（前端渠道选择的唯一真相，前端不得自判）。"""
    resp = client.get("/api/billing/pricing", headers=auth_headers)
    payload = resp.json()
    assert payload["alipayEnabled"] is True
    assert payload["wechatEnabled"] is True


def test_create_wechat_order_returns_code_url_and_persists_channel(
    client, wechat_keys, monkeypatch
):
    """微信下单：返回 codeUrl（非 payUrl），渠道落库，网关请求体金额为分。"""
    user_id = "wxcreate"
    _ensure_user(user_id)
    capture: dict = {}
    _patch_wechat_gateway(
        monkeypatch, {"code_url": "weixin://wxpay/bizpayurl?pr=TEST01"}, capture=capture
    )
    try:
        resp = client.post(
            "/api/billing/orders",
            json={"planCode": "standard", "channel": "wechat"},
            headers=_headers(user_id),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["channel"] == "wechat"
        assert data["codeUrl"].startswith("weixin://")
        assert "payUrl" not in data
        assert _order_row(data["orderNo"])["status"] == "created"

        body = capture["body"]
        assert capture["url"].endswith("/v3/pay/transactions/native")
        assert body["appid"] == _WECHAT_APP_ID and body["mchid"] == _WECHAT_MCH_ID
        assert body["amount"]["total"] == 59800  # 分（598.00 元）
        assert body["out_trade_no"] == data["orderNo"]
        assert body["time_expire"].endswith("+08:00")
        assert body["notify_url"].endswith("/api/billing/wechat/notify")

        conn = sqlite3.connect(TEST_CORE_DB)
        try:
            row = conn.execute(
                "SELECT payment_channel FROM billing_orders WHERE out_trade_no = ?",
                (data["orderNo"],),
            ).fetchone()
            assert row[0] == "wechat"
        finally:
            conn.close()
    finally:
        _cleanup([user_id])


def test_create_order_rejects_unknown_channel(client, auth_headers, alipay_keys, wechat_keys):
    resp = client.post(
        "/api/billing/orders", json={"planCode": "standard", "channel": "unionpay"},
        headers=_headers("wxchan"),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "CHANNEL_NOT_SUPPORTED"
    _cleanup(["wxchan"])


def test_create_wechat_order_without_config_returns_503(client, auth_headers, alipay_keys, monkeypatch):
    """微信渠道未配置（五项密钥缺一）必须 503 明示，而非静默走支付宝。"""
    for attr in (
        "WECHAT_PAY_MCH_ID", "WECHAT_PAY_APP_ID", "WECHAT_PAY_API_V3_KEY",
        "WECHAT_PAY_SERIAL_NO", "WECHAT_PAY_PRIVATE_KEY",
    ):
        monkeypatch.setattr(config, attr, "")
    resp = client.post(
        "/api/billing/orders", json={"planCode": "standard", "channel": "wechat"},
        headers=_headers("wxnoconf"),
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "WECHAT_PAY_NOT_CONFIGURED"
    _cleanup(["wxnoconf"])


def test_wechat_notify_success_activates_and_replay_is_idempotent(client, wechat_keys, monkeypatch):
    """通知 SUCCESS 激活套餐；重复推送幂等（CAS），到期日不叠加；应答 200 SUCCESS。"""
    user_id = "wxnotify_ok"
    _ensure_user(user_id)
    order = _create_wechat_order(client, user_id, monkeypatch)
    try:
        body = _wechat_notify_payload(_wechat_resource(order["orderNo"]))
        first = _post_wechat_notify(client, body)
        assert first.status_code == 200 and first.json()["code"] == "SUCCESS"
        row = _user_row(user_id)
        assert row["plan_code"] == "standard"
        assert row["plan_expires_at"] == add_months(today, 12).isoformat()
        assert _order_row(order["orderNo"])["status"] == "activated"

        conn = sqlite3.connect(TEST_CORE_DB)
        try:
            trade_no = conn.execute(
                "SELECT trade_no FROM billing_orders WHERE out_trade_no = ?", (order["orderNo"],)
            ).fetchone()[0]
            assert trade_no.startswith("WX")
        finally:
            conn.close()

        replay = _post_wechat_notify(client, body)
        assert replay.status_code == 200 and replay.json()["code"] == "SUCCESS"
        assert _user_row(user_id)["plan_expires_at"] == row["plan_expires_at"]
    finally:
        _cleanup([user_id])


def test_wechat_notify_bad_signature_rejected_non_200(client, wechat_keys, monkeypatch):
    """根因防御：篡改签名必须 401 且不触碰订单/用户——v3 语义下失败绝不能回 200。"""
    user_id = "wxnotify_sign"
    _ensure_user(user_id)
    order = _create_wechat_order(client, user_id, monkeypatch)
    try:
        resp = _post_wechat_notify(
            client, _wechat_notify_payload(_wechat_resource(order["orderNo"])), tamper=True
        )
        assert resp.status_code == 401 and resp.json()["code"] == "FAIL"
        assert _order_row(order["orderNo"])["status"] == "created"
        assert _user_row(user_id)["plan_code"] == "trial"
    finally:
        _cleanup([user_id])


def test_wechat_notify_amount_mismatch_never_activates(client, wechat_keys, monkeypatch):
    """通知金额（分）与订单金额不符绝不激活，回非 200 触发重试留人工证据链。"""
    user_id = "wxnotify_amt"
    _ensure_user(user_id)
    order = _create_wechat_order(client, user_id, monkeypatch)
    try:
        resp = _post_wechat_notify(
            client, _wechat_notify_payload(_wechat_resource(order["orderNo"], cents=100))
        )
        assert resp.status_code == 500 and resp.json()["code"] == "FAIL"
        assert _order_row(order["orderNo"])["status"] == "created"
        assert _user_row(user_id)["plan_code"] == "trial"
        conn = sqlite3.connect(TEST_CORE_DB)
        try:
            result = conn.execute(
                "SELECT result FROM billing_notify_logs WHERE out_trade_no = ?",
                (order["orderNo"],),
            ).fetchone()
            assert result is not None and result[0] == "rejected_amount"
        finally:
            conn.close()
    finally:
        _cleanup([user_id])


def test_wechat_notify_wrong_appid_rejected(client, wechat_keys, monkeypatch):
    user_id = "wxnotify_appid"
    _ensure_user(user_id)
    order = _create_wechat_order(client, user_id, monkeypatch)
    try:
        resp = _post_wechat_notify(
            client,
            _wechat_notify_payload(_wechat_resource(order["orderNo"], appid="wxotherapp9999")),
        )
        assert resp.status_code == 500 and resp.json()["code"] == "FAIL"
        assert _order_row(order["orderNo"])["status"] == "created"
    finally:
        _cleanup([user_id])


def test_wechat_notify_closed_state_closes_created_order(client, wechat_keys, monkeypatch):
    user_id = "wxnotify_closed"
    _ensure_user(user_id)
    order = _create_wechat_order(client, user_id, monkeypatch)
    try:
        resp = _post_wechat_notify(
            client,
            _wechat_notify_payload(_wechat_resource(order["orderNo"], trade_state="CLOSED")),
        )
        assert resp.status_code == 200 and resp.json()["code"] == "SUCCESS"
        assert _order_row(order["orderNo"])["status"] == "closed"
        assert _user_row(user_id)["plan_code"] == "trial"
    finally:
        _cleanup([user_id])


def test_wechat_notify_rescues_lazily_closed_paid_order(client, wechat_keys):
    """根因防御：回调丢失且订单被惰性关单后，验签通过的网关成功证据为准补结算（补单）。

    钱已进商户号（微信侧权威），本地 closed 是我们自己定时器写的——拒绝会把已付
    订单永久卡死；补结算大声留痕，重复通知仍幂等。
    """
    user_id = "wxrescue"
    _ensure_user(user_id)
    order_no = "SEED-WXRESCUE-01"
    _seed_order(order_no, user_id, status="closed", channel="wechat")
    try:
        resp = _post_wechat_notify(client, _wechat_notify_payload(_wechat_resource(order_no)))
        assert resp.status_code == 200 and resp.json()["code"] == "SUCCESS"
        assert _order_row(order_no)["status"] == "activated"
        assert _user_row(user_id)["plan_code"] == "standard"
    finally:
        _cleanup([user_id])


def test_wechat_notify_unknown_order_rejected_non_200(client, wechat_keys):
    resp = _post_wechat_notify(
        client, _wechat_notify_payload(_wechat_resource("SB0000000000WX"))
    )
    assert resp.status_code == 500 and resp.json()["code"] == "FAIL"
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        result = conn.execute(
            "SELECT result FROM billing_notify_logs WHERE out_trade_no = 'SB0000000000WX'"
        ).fetchone()
        assert result is not None and result[0] == "order_not_found"
        conn.execute("DELETE FROM billing_notify_logs WHERE out_trade_no = 'SB0000000000WX'")
        conn.commit()
    finally:
        conn.close()


def test_wechat_order_poll_settles_via_gateway_query(client, wechat_keys, monkeypatch):
    """查单兜底：回调丢失时轮询端点向微信查单，SUCCESS 按同一结算内核激活（金额仍比对）。"""
    user_id = "wxpoll"
    _ensure_user(user_id)
    order = _create_wechat_order(client, user_id, monkeypatch)
    try:
        txn_id = f"WXPOLL{now_stamp()}"
        _patch_wechat_gateway(
            monkeypatch,
            {
                "out_trade_no": order["orderNo"],
                "mchid": _WECHAT_MCH_ID,
                "appid": _WECHAT_APP_ID,
                "transaction_id": txn_id,
                "trade_state": "SUCCESS",
                "amount": {"total": 59800, "currency": "CNY"},
            },
        )
        resp = client.get(f"/api/billing/orders/{order['orderNo']}", headers=_headers(user_id))
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "activated"
        assert data["tradeNo"] == txn_id
        assert _user_row(user_id)["plan_code"] == "standard"
    finally:
        _cleanup([user_id])


def test_wechat_order_poll_ignores_unpaid_and_foreign_mchid(client, wechat_keys, monkeypatch):
    """查单 NOTPAY（未付）与商户号不符的响应都不得改变本地状态。"""
    user_id = "wxpoll2"
    _ensure_user(user_id)
    order = _create_wechat_order(client, user_id, monkeypatch)
    try:
        _patch_wechat_gateway(
            monkeypatch,
            {"out_trade_no": order["orderNo"], "mchid": _WECHAT_MCH_ID,
             "trade_state": "NOTPAY", "amount": {"total": 59800}},
        )
        resp = client.get(f"/api/billing/orders/{order['orderNo']}", headers=_headers(user_id))
        assert resp.json()["status"] == "created"

        _patch_wechat_gateway(
            monkeypatch,
            {"out_trade_no": order["orderNo"], "mchid": "9999999999",
             "trade_state": "SUCCESS", "amount": {"total": 59800}},
        )
        resp2 = client.get(f"/api/billing/orders/{order['orderNo']}", headers=_headers(user_id))
        assert resp2.json()["status"] == "created"
        assert _user_row(user_id)["plan_code"] == "trial"
    finally:
        _cleanup([user_id])


def _wechat_refund_success_node(out_request_no: str) -> dict:
    return {
        "refund_id": "rr-" + now_stamp(),
        "out_request_no": out_request_no,
        "out_refund_no": out_request_no,
        "refund_status": "SUCCESS",
        "success_time": "2026-11-19T12:00:00+08:00",
        "amount": {"refund": 59800, "total": 59800, "currency": "CNY"},
    }


def test_admin_refund_wechat_sync_success_revokes_plan(client, wechat_keys, monkeypatch):
    """微信退款同步 SUCCESS：退款行 success、权益收回；同号幂等重放不二次调网关。"""
    user_id = "wxrefund_ok"
    _ensure_user(user_id, plan_code="standard", plan_expires_at="2027-11-19")
    order_no = "SEED-WXREFUND-01"
    _seed_order(order_no, user_id, status="activated", channel="wechat")

    calls: list[str] = []

    def fake_create_refund(*, out_trade_no, out_refund_no, total_cents, refund_cents, reason):
        calls.append(out_refund_no)
        assert total_cents == 59800 and refund_cents == 59800
        assert out_refund_no == f"{out_trade_no}R1"
        assert reason == "微信退款演练"
        return _wechat_refund_success_node(out_refund_no)

    monkeypatch.setattr("services.wechat_pay_client.create_refund", fake_create_refund)
    _ensure_admin()
    try:
        resp = client.post(
            f"/api/admin/billing/orders/{order_no}/refund",
            json={"reason": "微信退款演练"},
            headers=_headers("admin_billing_ops"),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["refunds"][0]["status"] == "success"
        assert _refund_row(order_no)["status"] == "success"
        user = _user_row(user_id)
        assert user["plan_code"] == "trial" and user["plan_expires_at"] is None

        # 幂等重放：不再调网关
        resp2 = client.post(
            f"/api/admin/billing/orders/{order_no}/refund",
            json={"reason": "微信退款演练"},
            headers=_headers("admin_billing_ops"),
        )
        assert resp2.status_code == 200
        assert len(calls) == 1
    finally:
        _cleanup([user_id])
        _cleanup_admin()


def test_admin_refund_wechat_processing_finalized_by_refund_notify(
    client, wechat_keys, monkeypatch
):
    """微信退款 PROCESSING：行保持 submitted；退款通知 SUCCESS 落终态并收回权益；重复通知幂等。"""
    user_id = "wxrefund_proc"
    _ensure_user(user_id, plan_code="standard", plan_expires_at="2027-11-19")
    order_no = "SEED-WXREFUND-02"
    _seed_order(order_no, user_id, status="activated", channel="wechat")

    monkeypatch.setattr(
        "services.wechat_pay_client.create_refund",
        lambda *, out_trade_no, out_refund_no, total_cents, refund_cents, reason: {
            "refund_id": "rr-proc", "out_refund_no": out_refund_no,
            "refund_status": "PROCESSING", "amount": {"refund": refund_cents},
        },
    )
    _ensure_admin()
    try:
        resp = client.post(
            f"/api/admin/billing/orders/{order_no}/refund",
            json={"reason": "退款受理中演练"},
            headers=_headers("admin_billing_ops"),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["refunds"][0]["status"] == "submitted"
        assert _user_row(user_id)["plan_code"] == "standard"  # 未到终态不动权益

        resource = {
            "mchid": _WECHAT_MCH_ID,
            "out_trade_no": order_no,
            "out_refund_no": f"{order_no}R1",
            "refund_status": "SUCCESS",
            "success_time": "2026-11-19T13:00:00+08:00",
            "amount": {"refund": 59800, "total": 59800, "currency": "CNY"},
        }
        body = _wechat_notify_payload(resource, event_type="REFUND.SUCCESS")
        notify = client.post(
            "/api/billing/wechat/refund-notify",
            content=body.encode("utf-8"),
            headers=_wechat_sign_headers(body),
        )
        assert notify.status_code == 200 and notify.json()["code"] == "SUCCESS"
        assert _refund_row(order_no)["status"] == "success"
        assert _user_row(user_id)["plan_code"] == "trial"

        # 重复退款通知：幂等，不二次收回（也无权益可收）
        replay = client.post(
            "/api/billing/wechat/refund-notify",
            content=body.encode("utf-8"),
            headers=_wechat_sign_headers(body),
        )
        assert replay.status_code == 200
        assert _user_row(user_id)["plan_code"] == "trial"
    finally:
        _cleanup([user_id])
        _cleanup_admin()


def test_wechat_refund_notify_unknown_row_rejected_non_200(client, wechat_keys):
    """退款通知找不到退款行（异常数据）必须非 200 留证据重试，绝不静默吞掉。"""
    resource = {
        "mchid": _WECHAT_MCH_ID,
        "out_trade_no": "SEED-WXREFUND-NONE",
        "out_refund_no": "SEED-WXREFUND-NONE-R1",
        "refund_status": "SUCCESS",
    }
    body = _wechat_notify_payload(resource, event_type="REFUND.SUCCESS")
    resp = client.post(
        "/api/billing/wechat/refund-notify",
        content=body.encode("utf-8"),
        headers=_wechat_sign_headers(body),
    )
    assert resp.status_code == 500 and resp.json()["code"] == "FAIL"


def test_admin_query_wechat_order_dispatch(client, wechat_keys, monkeypatch):
    """管理端网关校准按渠道分发：微信走 v3 查单；404（单不存在）映射 None。"""
    from services.wechat_pay_client import WechatPayGatewayError

    user_id = "wxquery"
    _ensure_user(user_id)
    order_no = "SEED-WXQUERY-01"
    _seed_order(order_no, user_id, status="activated", channel="wechat")

    monkeypatch.setattr(
        "services.wechat_pay_client.query_order",
        lambda no: {"out_trade_no": no, "trade_state": "SUCCESS",
                    "transaction_id": "WXQ1", "mchid": _WECHAT_MCH_ID},
    )
    _ensure_admin()
    try:
        resp = client.post(
            f"/api/admin/billing/orders/{order_no}/query",
            headers=_headers("admin_billing_ops"),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["gatewayTrade"]["trade_state"] == "SUCCESS"
        assert _order_row(order_no)["status"] == "activated"  # 只读，不改本地

        def not_exist(no):
            raise WechatPayGatewayError(code="ORDER_NOT_EXIST", message="订单不存在", http_status=404)

        monkeypatch.setattr("services.wechat_pay_client.query_order", not_exist)
        resp2 = client.post(
            f"/api/admin/billing/orders/{order_no}/query",
            headers=_headers("admin_billing_ops"),
        )
        assert resp2.status_code == 200
        assert resp2.json()["gatewayTrade"] is None
    finally:
        _cleanup([user_id])
        _cleanup_admin()
