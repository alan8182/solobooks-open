# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks license v2.0.

"""AI 点数充值回归测试（1 元 = 10,000 点，1 点 = 1 token，余额永久有效）。

重点防御的根因（对应 A 档不变量「唯一活凭证 / 审计只增 / 资金可解释」）：
- 到账点数只能由后端按目录换算，前端不能传 token 数或价格；
- 重复支付通知/重放必须幂等，余额只加一次；
- 套餐额度用尽后超额部分从永久余额扣减，并写 consume 流水；
- 退款只退「未消耗」点数，成功后扣回对应点数且只扣一次；
- 管理员人工调整余额必须留流水，不允许无痕改写。
"""
from __future__ import annotations

import sqlite3

import pytest

import config
from core.plans import PLAN_CATALOG, PlanDef, TOKEN_TOPUP_TOKENS_PER_YUAN
from services import quota_service

from tests.conftest import TEST_CORE_DB
from tests.test_billing_orders import (  # noqa: F401 — 夹具需在本模块可见
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

_LIMITED_TRIAL = PlanDef(code="trial", name="免费体验版", period_type="annual", token_quota=100)


@pytest.fixture(autouse=True)
def _quota_enforcement_on(monkeypatch):
    """充值只在官方云端（额度卡口开启）可售：CI 无 backend/.env 时也要显式打开。"""
    monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", True)


# ---------------------------------------------------------------------------
# 播种与读取
# ---------------------------------------------------------------------------

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
            conn.execute("DELETE FROM ai_token_ledger WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM billing_orders WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM user_quota_counters WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM company_members WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM auth_users WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM admin_audit_logs WHERE target_id LIKE 'SB-TOPUP%'")
        conn.commit()
    finally:
        conn.close()


def _set_balance(user_id: str, balance: int) -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute(
            "UPDATE auth_users SET token_balance = ? WHERE user_id = ?", (balance, user_id)
        )
        conn.commit()
    finally:
        conn.close()


def _balance(user_id: str) -> int:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        row = conn.execute(
            "SELECT token_balance FROM auth_users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def _ledger(user_id: str, kind: str | None = None) -> list[tuple]:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        sql = "SELECT kind, delta_tokens, balance_after, order_no FROM ai_token_ledger WHERE user_id = ?"
        args: list = [user_id]
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        sql += " ORDER BY id"
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _order_row(order_no: str) -> dict:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        row = conn.execute(
            "SELECT order_type, period, token_amount, amount, status"
            " FROM billing_orders WHERE out_trade_no = ?",
            (order_no,),
        ).fetchone()
        return (
            {
                "order_type": row[0],
                "period": row[1],
                "token_amount": row[2],
                "amount": row[3],
                "status": row[4],
            }
            if row
            else {}
        )
    finally:
        conn.close()


def _create_topup(client, user_id: str, amount_yuan: int = 10, channel: str = "alipay") -> dict:
    resp = client.post(
        "/api/billing/token-topup",
        json={"amountYuan": amount_yuan, "channel": channel},
        headers=_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _pay_alipay(client, order_no: str, amount: str) -> str:
    return _post_notify(client, _signed_notify_body(order_no, amount)).text


# ---------------------------------------------------------------------------
# 定价目录
# ---------------------------------------------------------------------------

def test_pricing_exposes_token_topup_catalog(client, auth_headers, alipay_keys, monkeypatch):
    """充值目录由后端唯一真相给出；额度卡口关闭（自部署）时不开放充值入口。"""
    monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", True)
    payload = client.get("/api/billing/pricing", headers=auth_headers).json()["tokenTopup"]
    assert payload == {
        "enabled": True,
        "tokensPerYuan": TOKEN_TOPUP_TOKENS_PER_YUAN,
        "minYuan": 1,
        "maxYuan": 10_000,
    }

    monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", False)
    payload = client.get("/api/billing/pricing", headers=auth_headers).json()["tokenTopup"]
    assert payload["enabled"] is False


# ---------------------------------------------------------------------------
# 下单：点数只能由后端换算
# ---------------------------------------------------------------------------

def test_create_topup_order_computes_tokens_server_side(client, alipay_keys, monkeypatch):
    uid = "u-topup-create"
    _ensure_user(uid)
    try:
        monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", True)
        body = _create_topup(client, uid, amount_yuan=10)
        assert body["orderType"] == "token_topup"
        assert body["tokenAmount"] == 100_000
        assert body["amount"] == "10.00"
        assert body["payUrl"]

        row = _order_row(body["orderNo"])
        assert row == {
            "order_type": "token_topup",
            "period": "one_time",
            "token_amount": 100_000,
            "amount": 10,
            "status": "created",
        }
    finally:
        _cleanup([uid])


@pytest.mark.parametrize("amount_yuan", [0, -5, 10_001])
def test_create_topup_order_rejects_out_of_range_amount(
    client, alipay_keys, monkeypatch, amount_yuan
):
    """金额必须是 1–10,000 的整数元；越界一律 422，且不产生订单。"""
    uid = f"u-topup-bad-{amount_yuan}"
    _ensure_user(uid)
    try:
        monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", True)
        resp = client.post(
            "/api/billing/token-topup",
            json={"amountYuan": amount_yuan},
            headers=_headers(uid),
        )
        assert resp.status_code == 422
        conn = sqlite3.connect(TEST_CORE_DB)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM billing_orders WHERE user_id = ?", (uid,)
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 0
    finally:
        _cleanup([uid])


def test_create_topup_disabled_when_quota_enforcement_off(client, alipay_keys, monkeypatch):
    """自部署/自带 Key 环境不计费也不限量：接口必须拒绝充值（不能卖出无处消耗的点数）。"""
    uid = "u-topup-disabled"
    _ensure_user(uid)
    try:
        monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", False)
        resp = client.post(
            "/api/billing/token-topup",
            json={"amountYuan": 10},
            headers=_headers(uid),
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "TOPUP_DISABLED"
    finally:
        _cleanup([uid])


# ---------------------------------------------------------------------------
# 到账：幂等
# ---------------------------------------------------------------------------

def test_alipay_notify_credits_balance_exactly_once(client, alipay_keys):
    uid = "u-topup-notify"
    _ensure_user(uid)
    try:
        order = _create_topup(client, uid, amount_yuan=10)
        assert _pay_alipay(client, order["orderNo"], "10.00") == "success"
        assert _balance(uid) == 100_000
        assert _ledger(uid, "topup") == [("topup", 100_000, 100_000, order["orderNo"])]

        # 支付宝重推同一条通知：余额与流水都不能再加
        assert _pay_alipay(client, order["orderNo"], "10.00") == "success"
        assert _balance(uid) == 100_000
        assert len(_ledger(uid, "topup")) == 1
        assert _order_row(order["orderNo"])["status"] == "activated"
    finally:
        _cleanup([uid])


def test_wechat_notify_credits_topup_balance(client, wechat_keys, monkeypatch):
    """微信渠道同样只入账一次（第二渠道复用同一张流水表与唯一键）。"""
    uid = "u-topup-wechat"
    _ensure_user(uid)
    try:
        _patch_wechat_gateway(monkeypatch, {"code_url": "weixin://wxpay/bizpayurl?pr=TOPUP"})
        order = _create_topup(client, uid, amount_yuan=3, channel="wechat")
        assert order["codeUrl"]
        body = _wechat_notify_payload(_wechat_resource(order["orderNo"], cents=300))
        first = _post_wechat_notify(client, body)
        assert first.status_code == 200
        assert _balance(uid) == 30_000

        replay = _post_wechat_notify(client, body)
        assert replay.status_code == 200
        assert _balance(uid) == 30_000
        assert len(_ledger(uid, "topup")) == 1
    finally:
        _cleanup([uid])


# ---------------------------------------------------------------------------
# 退款：只退未消耗部分
# ---------------------------------------------------------------------------

class _RefundRecorder:
    def __init__(self) -> None:
        self.amounts: list[str] = []

    def __call__(self, out_trade_no, refund_amount, *, out_request_no, refund_reason):
        self.amounts.append(str(refund_amount))
        return {
            "code": "10000",
            "msg": "Success",
            "out_trade_no": out_trade_no,
            "trade_no": "TREFUND",
            "fund_change": "Y",
            "refund_fee": str(refund_amount),
        }


def test_topup_refund_refunds_only_unconsumed_tokens(client, alipay_keys, monkeypatch):
    uid = "u-topup-refund"
    _ensure_user(uid)
    _ensure_admin()
    try:
        order = _create_topup(client, uid, amount_yuan=10)
        assert _pay_alipay(client, order["orderNo"], "10.00") == "success"
        # 已消耗 40,000 点 → 只剩 60,000 点可退（= 6.00 元）
        _set_balance(uid, 60_000)

        recorder = _RefundRecorder()
        monkeypatch.setattr("services.alipay_client.refund", recorder)

        resp = client.post(
            f"/api/admin/billing/orders/{order['orderNo']}/refund",
            json={"reason": "客户充值后未使用完，协商退余款"},
            headers=_headers("admin_billing_ops"),
        )
        assert resp.status_code == 200, resp.text
        assert recorder.amounts == ["6.00"]
        assert _balance(uid) == 0
        assert _ledger(uid, "refund") == [("refund", -60_000, 0, order["orderNo"])]

        # 幂等重放：不再次调网关、不重复扣点
        replay = client.post(
            f"/api/admin/billing/orders/{order['orderNo']}/refund",
            json={"reason": "客户充值后未使用完，协商退余款"},
            headers=_headers("admin_billing_ops"),
        )
        assert replay.status_code == 200
        assert len(recorder.amounts) == 1
        assert _balance(uid) == 0
    finally:
        _cleanup([uid])
        _cleanup_admin()


def test_topup_refund_rejected_when_fully_consumed(client, alipay_keys, monkeypatch):
    """余额为 0 时没有可退金额，必须拒绝且绝不调用网关。"""
    uid = "u-topup-refund-empty"
    _ensure_user(uid)
    _ensure_admin()
    try:
        order = _create_topup(client, uid, amount_yuan=1)
        assert _pay_alipay(client, order["orderNo"], "1.00") == "success"
        _set_balance(uid, 0)

        recorder = _RefundRecorder()
        monkeypatch.setattr("services.alipay_client.refund", recorder)

        resp = client.post(
            f"/api/admin/billing/orders/{order['orderNo']}/refund",
            json={"reason": "已全部消耗"},
            headers=_headers("admin_billing_ops"),
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "TOPUP_NOT_REFUNDABLE"
        assert recorder.amounts == []
    finally:
        _cleanup([uid])
        _cleanup_admin()


# ---------------------------------------------------------------------------
# 管理员人工调整：必须留流水
# ---------------------------------------------------------------------------

def test_admin_patch_token_balance_writes_ledger_and_rejects_negative(client):
    uid = "u-topup-admin"
    _ensure_user(uid)
    _ensure_admin()
    try:
        resp = client.patch(
            f"/api/admin/users/{uid}",
            json={"tokenBalance": 5_000},
            headers=_headers("admin_billing_ops"),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["user"]["tokenBalance"] == 5_000
        assert _balance(uid) == 5_000
        assert _ledger(uid) == [("admin_grant", 5_000, 5_000, None)]

        bad = client.patch(
            f"/api/admin/users/{uid}",
            json={"tokenBalance": -1},
            headers=_headers("admin_billing_ops"),
        )
        assert bad.status_code == 422
        assert _balance(uid) == 5_000
    finally:
        _cleanup([uid])
        _cleanup_admin()


# ---------------------------------------------------------------------------
# 消耗：先套餐额度，超额从永久余额扣减
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consumption_within_plan_does_not_touch_balance(client, monkeypatch):
    monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", True)
    monkeypatch.setitem(PLAN_CATALOG, "trial", _LIMITED_TRIAL)
    uid = "u-topup-under"
    _ensure_user(uid)
    _set_balance(uid, 500)
    quota_service.clear_state_cache()
    try:
        from db.session import session_scope
        from services.quota_service import record_consumption_async

        async with session_scope() as session:
            await record_consumption_async(session, user_id=uid, tokens=80, scene="agent_chat")
        assert _balance(uid) == 500
        assert _ledger(uid) == []
    finally:
        quota_service.clear_state_cache()
        _cleanup([uid])


@pytest.mark.asyncio
async def test_consumption_overflow_deducts_permanent_balance(client, monkeypatch):
    """套餐 100 点用尽后再消耗 50 点：只扣新增超额 50 点，并写 consume 流水。"""
    monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", True)
    monkeypatch.setitem(PLAN_CATALOG, "trial", _LIMITED_TRIAL)
    uid = "u-topup-over"
    _ensure_user(uid)
    _set_balance(uid, 500)
    quota_service.clear_state_cache()
    try:
        from db.session import session_scope
        from services.quota_service import record_consumption_async

        async with session_scope() as session:
            await record_consumption_async(session, user_id=uid, tokens=150, scene="agent_chat")
        assert _balance(uid) == 450
        assert _ledger(uid) == [("consume", -50, 450, None)]

        # 额度用尽但有余额：卡口必须放行，且状态把余额并入剩余可用
        quota_service.clear_state_cache()
        quota_service.ensure_within_quota(uid)
        state = await quota_service.quota_state(uid)
        assert state is not None
        assert state["usedTokens"] == 150
        assert state["tokenBalance"] == 450
        assert state["remainingTokens"] == 450

        # 余额也耗尽 → 卡口拦截
        _set_balance(uid, 0)
        quota_service.clear_state_cache()
        from core.errors import QuotaExceededError

        with pytest.raises(QuotaExceededError):
            quota_service.ensure_within_quota(uid)
    finally:
        quota_service.clear_state_cache()
        _cleanup([uid])
