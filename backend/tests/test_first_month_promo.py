# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""新人首月 AI 体验包（¥1 买 80 万 Token，30 天有效，限购 1 次）回归测试。

覆盖重点：
- 定价目录仅后端决定价格、点数与资格（不依赖前端传参）；
- 支付宝/微信下单 → 异步通知回调激活 → 重复通知幂等（只发一次）；
- 限购与资格守卫：已购用户、有效年付用户、历史付费回落用户不可重复购买；
- 退款收回剩余特惠额度并保留 promo_purchased_at（防退后再刷首单），且不阻拦后续套餐退款；
- Alembic 迁移 e5f1a9c3b7d2 升降级安全校验（SQLite）。
"""
from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.pool import StaticPool

import config
from core.plans import (
    FIRST_MONTH_PROMO_CODE,
    FIRST_MONTH_PROMO_NAME,
    FIRST_MONTH_PROMO_ORIGINAL_YUAN,
    FIRST_MONTH_PROMO_PRICE_YUAN,
    FIRST_MONTH_PROMO_TOKENS,
    FIRST_MONTH_PROMO_VALID_DAYS,
)
from services import quota_service

from tests.conftest import TEST_CORE_DB
from tests.test_billing_orders import (  # noqa: F401
    _cleanup_admin,
    _ensure_admin,
    _ensure_user,
    _headers,
    _patch_wechat_gateway,
    _post_notify,
    _post_wechat_notify,
    _signed_notify_body,
    _wechat_notify_payload,
    _wechat_resource,
    alipay_keys,
    wechat_keys,
)

MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "e5f1a9c3b7d2_first_month_promo_tokens.py"
)


@pytest.fixture(autouse=True)
def _quota_enforcement_on(monkeypatch):
    monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", True)


def _cleanup(users: list[str]) -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        for uid in users:
            orders = [
                row[0]
                for row in conn.execute(
                    "SELECT out_trade_no FROM billing_orders WHERE user_id = ?", (uid,)
                ).fetchall()
            ]
            for no in orders:
                conn.execute("DELETE FROM billing_notify_logs WHERE out_trade_no = ?", (no,))
                conn.execute("DELETE FROM billing_refunds WHERE out_trade_no = ?", (no,))
            conn.execute("DELETE FROM billing_orders WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM user_quota_counters WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM company_members WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM auth_users WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM admin_audit_logs WHERE target_id LIKE 'SB%'")
        conn.commit()
    finally:
        conn.close()


def _promo_cols(user_id: str) -> tuple[int, str | None, str | None]:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        row = conn.execute(
            "SELECT promo_token_balance, promo_expires_at, promo_purchased_at"
            " FROM auth_users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        assert row is not None
        return int(row[0]), row[1], row[2]
    finally:
        conn.close()


def test_pricing_exposes_first_month_promo_eligibility(client, alipay_keys, monkeypatch):
    uid_new = "u-promo-pricing-new"
    uid_paid = "u-promo-pricing-paid"
    _ensure_user(uid_new)
    _ensure_user(uid_paid, plan_code="standard", plan_expires_at="2027-12-31")
    quota_service.clear_state_cache()
    try:
        resp_new = client.get("/api/billing/pricing", headers=_headers(uid_new))
        assert resp_new.status_code == 200
        promo = resp_new.json()["firstMonthPromo"]
        assert promo == {
            "enabled": True,
            "eligible": True,
            "purchased": False,
            "code": FIRST_MONTH_PROMO_CODE,
            "name": FIRST_MONTH_PROMO_NAME,
            "priceYuan": FIRST_MONTH_PROMO_PRICE_YUAN,
            "originalPriceYuan": FIRST_MONTH_PROMO_ORIGINAL_YUAN,
            "tokens": FIRST_MONTH_PROMO_TOKENS,
            "validDays": FIRST_MONTH_PROMO_VALID_DAYS,
        }

        resp_paid = client.get("/api/billing/pricing", headers=_headers(uid_paid))
        assert resp_paid.status_code == 200
        assert resp_paid.json()["firstMonthPromo"]["eligible"] is False

        monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", False)
        resp_oss = client.get("/api/billing/pricing", headers=_headers(uid_new))
        assert resp_oss.json()["firstMonthPromo"]["enabled"] is False
        assert resp_oss.json()["firstMonthPromo"]["eligible"] is False
    finally:
        quota_service.clear_state_cache()
        _cleanup([uid_new, uid_paid])


def test_alipay_first_month_promo_activate_idempotent_and_limit_one(client, alipay_keys):
    uid = "u-promo-alipay"
    _ensure_user(uid)
    quota_service.clear_state_cache()
    try:
        create_resp = client.post(
            "/api/billing/first-month-promo",
            json={"channel": "alipay"},
            headers=_headers(uid),
        )
        assert create_resp.status_code == 200, create_resp.text
        order = create_resp.json()
        assert order["orderType"] == FIRST_MONTH_PROMO_CODE
        assert order["tokenAmount"] == FIRST_MONTH_PROMO_TOKENS
        assert order["amount"] == "1.00"
        assert order["payUrl"]

        # 支付宝回调：首次生效，重复回调幂等
        assert _post_notify(client, _signed_notify_body(order["orderNo"], "1.00")).text == "success"
        assert _post_notify(client, _signed_notify_body(order["orderNo"], "1.00")).text == "success"

        bal, expires_at, purchased_at = _promo_cols(uid)
        expected_exp = (
            datetime.now(timezone.utc).date() + timedelta(days=FIRST_MONTH_PROMO_VALID_DAYS)
        ).isoformat()
        assert bal == FIRST_MONTH_PROMO_TOKENS
        assert expires_at == expected_exp
        assert purchased_at is not None

        # 定价目录与再次下单都体现限购 1 次
        pricing = client.get("/api/billing/pricing", headers=_headers(uid)).json()["firstMonthPromo"]
        assert pricing["eligible"] is False
        assert pricing["purchased"] is True

        again = client.post(
            "/api/billing/first-month-promo",
            json={"channel": "alipay"},
            headers=_headers(uid),
        )
        assert again.status_code == 422
        assert again.json()["detail"]["code"] == "PROMO_NOT_ELIGIBLE"
    finally:
        quota_service.clear_state_cache()
        _cleanup([uid])


def test_wechat_first_month_promo_and_refund_revokes_without_re_eligibility(
    client, alipay_keys, wechat_keys, monkeypatch
):
    uid = "u-promo-wechat-refund"
    admin_id = "admin-promo-refund"
    _ensure_user(uid)
    _ensure_admin(admin_id)
    quota_service.clear_state_cache()
    import httpx as _httpx

    def _fake_wechat_request(method, url, headers=None, content=None, timeout=None, **kwargs):
        from tests.test_billing_orders import _FakeWechatHttpResponse

        if "/refund/" in url:
            return _FakeWechatHttpResponse({"refund_status": "SUCCESS", "refund_id": "wx_rf_promo_1"})
        return _FakeWechatHttpResponse({"code_url": "weixin://wxpay/bizpayurl?pr=promo_test"})

    monkeypatch.setattr(_httpx, "request", _fake_wechat_request)
    monkeypatch.setattr(
        "services.alipay_client.refund",
        lambda out_trade_no, refund_amount, *, out_request_no, refund_reason: {
            "code": "10000",
            "msg": "Success",
            "out_trade_no": out_trade_no,
            "refund_fee": refund_amount,
        },
    )
    try:
        # 1. 微信下单并回调激活
        create_resp = client.post(
            "/api/billing/first-month-promo",
            json={"channel": "wechat"},
            headers=_headers(uid),
        )
        assert create_resp.status_code == 200, create_resp.text
        order = create_resp.json()
        assert order["codeUrl"].startswith("weixin://")

        notify_resp = _post_wechat_notify(
            client,
            _wechat_notify_payload(
                _wechat_resource(order["orderNo"], cents=100)
            ),
        )
        assert notify_resp.status_code == 200
        bal, exp, purchased = _promo_cols(uid)
        assert bal == FIRST_MONTH_PROMO_TOKENS
        assert exp is not None
        assert purchased is not None

        # 2. 用户随后升级 598 元标准版
        plan_resp = client.post(
            "/api/billing/orders",
            json={"planCode": "standard", "channel": "alipay"},
            headers=_headers(uid),
        )
        assert plan_resp.status_code == 200
        plan_order_no = plan_resp.json()["orderNo"]
        assert _post_notify(client, _signed_notify_body(plan_order_no, "598.00")).text == "success"

        # 3. 退掉 1 元体验包：收回特惠额度并置空到期日，但不阻拦且不恢复首单资格
        rf = client.post(
            f"/api/admin/billing/orders/{order['orderNo']}/refund",
            json={"reason": "用户误购申请退回体验包"},
            headers=_headers(admin_id),
        )
        assert rf.status_code == 200, rf.text
        assert rf.json()["refunds"][0]["status"] == "success"
        bal_after, exp_after, purchased_after = _promo_cols(uid)
        assert bal_after == 0
        assert exp_after is None
        assert purchased_after is not None  # 必须保留购买记录，防退后再刷首单

        # 4. 退掉 598 年付套餐也不被已退/已支付的体验包阻拦，且回退 trial 后仍不能再买体验包
        rf_plan = client.post(
            f"/api/admin/billing/orders/{plan_order_no}/refund",
            json={"reason": "测试套餐退款不被体验包阻拦"},
            headers=_headers(admin_id),
        )
        assert rf_plan.status_code == 200, rf_plan.text
        again = client.post(
            "/api/billing/first-month-promo",
            json={"channel": "alipay"},
            headers=_headers(uid),
        )
        assert again.status_code == 422
        assert again.json()["detail"]["code"] == "PROMO_NOT_ELIGIBLE"
    finally:
        quota_service.clear_state_cache()
        _cleanup([uid])
        _cleanup_admin(admin_id)


def test_paid_or_expired_user_cannot_buy_first_month_promo(client, alipay_keys):
    uid_active = "u-promo-active-std"
    uid_expired = "u-promo-expired-std"
    _ensure_user(uid_active, plan_code="standard", plan_expires_at="2027-12-31")
    _ensure_user(uid_expired, plan_code="standard", plan_expires_at="2026-01-02")
    quota_service.clear_state_cache()
    try:
        for uid in (uid_active, uid_expired):
            resp = client.post(
                "/api/billing/first-month-promo",
                json={"channel": "alipay"},
                headers=_headers(uid),
            )
            assert resp.status_code == 422
            assert resp.json()["detail"]["code"] == "PROMO_NOT_ELIGIBLE"
    finally:
        quota_service.clear_state_cache()
        _cleanup([uid_active, uid_expired])


def test_migration_e5f1a9c3b7d2_upgrade_and_downgrade_guard(monkeypatch):
    spec = importlib.util.spec_from_file_location("migration_e5f1a9c3b7d2", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    engine = sa.create_engine("sqlite://", poolclass=StaticPool)
    conn = engine.connect()
    conn.exec_driver_sql(
        """
        CREATE TABLE auth_users (
          user_id VARCHAR(128) PRIMARY KEY,
          plan_code VARCHAR(32) NOT NULL DEFAULT 'trial'
        )
        """
    )
    conn.exec_driver_sql(
        """
        CREATE TABLE billing_orders (
          id VARCHAR(128) PRIMARY KEY,
          out_trade_no VARCHAR(32) NOT NULL UNIQUE,
          user_id VARCHAR(128) NOT NULL,
          order_type VARCHAR(16) NOT NULL DEFAULT 'plan',
          status VARCHAR(16) NOT NULL DEFAULT 'created'
        )
        """
    )
    conn.execute(sa.text("INSERT INTO auth_users (user_id) VALUES ('u1')"))
    conn.commit()

    ops = Operations(MigrationContext.configure(conn))
    monkeypatch.setattr(module, "op", ops)
    module.upgrade()
    conn.commit()

    cols = {col["name"] for col in sa.inspect(conn).get_columns("auth_users")}
    assert {"promo_token_balance", "promo_expires_at", "promo_purchased_at"} <= cols
    row = conn.execute(
        sa.text("SELECT promo_token_balance, promo_expires_at, promo_purchased_at FROM auth_users WHERE user_id = 'u1'")
    ).fetchone()
    assert row == (0, None, None)

    # 存在已支付的新人体验包订单时拒绝降级
    conn.execute(
        sa.text(
            "INSERT INTO billing_orders (id, out_trade_no, user_id, order_type, status)"
            " VALUES ('o1', 'SB-PROMO-1', 'u1', 'first_month_promo', 'activated')"
        )
    )
    conn.commit()
    with pytest.raises(RuntimeError, match="拒绝降级"):
        module.downgrade()

    # 清理订单后可正常降级
    conn.execute(sa.text("DELETE FROM billing_orders WHERE id = 'o1'"))
    conn.commit()
    module.downgrade()
    conn.commit()
    cols_after = {col["name"] for col in sa.inspect(conn).get_columns("auth_users")}
    assert not ({"promo_token_balance", "promo_expires_at", "promo_purchased_at"} & cols_after)
