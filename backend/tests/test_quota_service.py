# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""配额体系预留：周期推算、卡口惰性生效、周期计数、402 端到端。

额度数值待定（core/plans.py 全 None），测试通过 monkeypatch 注入限额来验证
启用后的行为——这正是将来「填数上线」要走的路径。
"""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

import config
from core.errors import QuotaExceededError
from core.plans import PLAN_CATALOG, PlanDef
from services import quota_service
from services.token_service import create_access_token

from tests.conftest import TEST_CORE_DB

# 锚点 2026-01-01（created_at 缺省锚）→ annual 周期起点恒为 2026-01-01
_LIMITED_TRIAL = PlanDef(code="trial", name="免费体验版", period_type="annual", token_quota=100)


def _headers(user_id: str, company_id: str = "c1") -> dict:
    token = create_access_token({"sub": user_id, "company_id": company_id})
    return {"Authorization": f"Bearer {token}"}


def _ensure_user(
    user_id: str,
    plan_code: str = "trial",
    bonus: int = 0,
    created_at: str = "2026-01-01T00:00:00+00:00",
    plan_expires_at: str | None = None,
) -> None:
    """播种用户行（幂等）：锚点缺省走 created_at。"""
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO auth_users ("
            " user_id, openid, phone, phone_verified_at, wechat_openid_bound_at,"
            " current_company_id, nickname, avatar, is_platform_admin, is_disabled,"
            " plan_code, plan_anchor_at, plan_expires_at, quota_bonus_tokens,"
            " created_at, last_login_at, updated_at"
            ") VALUES (?, '', '', '', '', 'c1', '', '', 0, 0, ?, NULL, ?, ?,"
            " ?, ?, '')",
            (user_id, plan_code, plan_expires_at, bonus, created_at, created_at),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_member(user_id: str) -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO company_members (company_id, user_id, role, status, created_at, updated_at)"
            " VALUES ('c1', ?, 'owner', 'active', '2026-01-01', '2026-01-01')",
            (user_id,),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_counter(
    user_id: str,
    used_tokens: int,
    period_start: str = "2026-01-01",
    period_type: str = "annual",
) -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO user_quota_counters (user_id, period_type, period_start, used_tokens, updated_at)"
            " VALUES (?, ?, ?, ?, '2026-01-01')",
            (user_id, period_type, period_start, used_tokens),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_llm_usage(user_id: str, total_tokens: int) -> None:
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d 00:30:00.000000")
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute(
            "INSERT INTO llm_usage (user_id, company_id, scene, model, prompt_tokens,"
            " completion_tokens, total_tokens, duration_ms, status, error_type, created_at)"
            " VALUES (?, 'c1', 'agent_chat', 'qwen-plus', ?, 0, ?, 100, 'ok', NULL, ?)",
            (user_id, total_tokens, total_tokens, today),
        )
        conn.commit()
    finally:
        conn.close()


def _cleanup(user_ids: list[str]) -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        for uid in user_ids:
            conn.execute("DELETE FROM user_quota_counters WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM llm_usage WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM company_members WHERE user_id = ?", (uid,))
            conn.execute(
                "DELETE FROM company_members WHERE company_id IN "
                "(SELECT company_id FROM auth_companies WHERE owner_user_id = ?)",
                (uid,),
            )
            conn.execute("DELETE FROM auth_companies WHERE owner_user_id = ?", (uid,))
            conn.execute("DELETE FROM auth_users WHERE user_id = ?", (uid,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 周期推算（纯函数）
# ---------------------------------------------------------------------------

def test_add_months_clamps_to_month_end():
    assert quota_service.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert quota_service.add_months(date(2026, 1, 15), 13) == date(2027, 2, 15)


def test_current_period_monthly_anniversary():
    plan = PlanDef(code="plus", name="订阅版", period_type="monthly", token_quota=None)
    # 本月锚点日已过 → 本周期从本月 15 日起
    start, end = quota_service.current_period(plan, date(2026, 3, 15), date(2026, 4, 20))
    assert (start, end) == (date(2026, 4, 15), date(2026, 5, 15))
    # 未到锚点日 → 回退到上一个周期
    start, end = quota_service.current_period(plan, date(2026, 3, 15), date(2026, 4, 10))
    assert (start, end) == (date(2026, 3, 15), date(2026, 4, 15))


def test_current_period_annual():
    plan = PlanDef(code="trial", name="免费体验版", period_type="annual", token_quota=None)
    # 年周年前夜仍在首年周期内；年周年当天进入第二年周期
    assert quota_service.current_period(plan, date(2026, 3, 15), date(2027, 3, 14))[0] == date(2026, 3, 15)
    assert quota_service.current_period(plan, date(2026, 3, 15), date(2027, 3, 16))[0] == date(2027, 3, 15)


# ---------------------------------------------------------------------------
# 卡口
# ---------------------------------------------------------------------------

def test_gate_passes_when_no_limit_configured(monkeypatch):
    """目录内全 None 时必须零开销直通，连用户行都不查。"""
    monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", True)
    for code, plan in list(PLAN_CATALOG.items()):
        monkeypatch.setitem(
            PLAN_CATALOG,
            code,
            PlanDef(code=plan.code, name=plan.name, period_type=plan.period_type, token_quota=None),
        )
    quota_service.ensure_within_quota("u-quota-nobody")  # 不应抛出
    quota_service.ensure_within_quota(None)


def test_default_trial_plan_has_monthly_100k_quota():
    """商业化方案：免费体验版首月 20 万 token（可 ¥1 扩容至 100 万）、次月起月度 10 万 token，最多 3 个账套。"""
    trial = PLAN_CATALOG["trial"]
    assert trial.period_type == "monthly"
    assert trial.token_quota == 100_000
    assert trial.first_month_token_quota == 200_000
    assert trial.max_companies == 3


@pytest.mark.asyncio
async def test_new_trial_user_gets_200k_in_first_month_and_reverts_to_100k(client, monkeypatch):
    """回归：新用户注册首月享 20 万 token 体验（不扣永久点数），次月起自动恢复 10 万/月。"""
    from datetime import datetime, timedelta, timezone

    from db.session import session_scope
    from services.quota_service import record_consumption_async

    monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", True)
    today = datetime.now(timezone.utc).date()
    new_uid = "u-quota-first-month"
    old_uid = "u-quota-second-month"
    _ensure_user(new_uid, created_at=f"{today.isoformat()}T00:00:00+00:00")
    _seed_member(new_uid)
    # 次月边界：注册日正好在 1 个月前，当前已进入第 2 个月度周期
    month_ago = quota_service.add_months(today, -1)
    _ensure_user(old_uid, created_at=f"{month_ago.isoformat()}T00:00:00+00:00")
    _seed_member(old_uid)
    # 充值 10,000 永久点数，验证首月 20 万以内消耗绝不误扣永久余额
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute("UPDATE auth_users SET token_balance = 10000 WHERE user_id = ?", (new_uid,))
        conn.commit()
    finally:
        conn.close()
    quota_service.clear_state_cache()

    try:
        resp_new = client.get("/api/usage/me", headers=_headers(new_uid))
        assert resp_new.status_code == 200
        q_new = resp_new.json()["quota"]
        assert q_new["limitTokens"] == 200_000
        assert q_new["remainingTokens"] == 210_000  # 首月 20 万 + 1 万永久余额
        assert q_new["promoEligible"] is True
        assert q_new["promoLowQuota"] is False

        async with session_scope() as session:
            await record_consumption_async(session, user_id=new_uid, tokens=150_000, scene="test")
        quota_service.clear_state_cache()
        quota_service.ensure_within_quota(new_uid)  # 超 10 万但未超 20 万，必须放行

        conn = sqlite3.connect(TEST_CORE_DB)
        try:
            bal = conn.execute("SELECT token_balance FROM auth_users WHERE user_id = ?", (new_uid,)).fetchone()[0]
        finally:
            conn.close()
        assert bal == 10_000  # 未超首月 20 万，永久点数分毫未扣

        # 再消耗 60,000（累计 210,000）：超出首月 20 万的 1 万从永久点数扣完，此时额度彻底归零
        async with session_scope() as session:
            await record_consumption_async(session, user_id=new_uid, tokens=60_000, scene="test")
        conn = sqlite3.connect(TEST_CORE_DB)
        try:
            bal_after = conn.execute("SELECT token_balance FROM auth_users WHERE user_id = ?", (new_uid,)).fetchone()[0]
        finally:
            conn.close()
        assert bal_after == 0
        quota_service.clear_state_cache()
        with pytest.raises(QuotaExceededError) as exc_info:
            quota_service.ensure_within_quota(new_uid)
        assert exc_info.value.context["promoEligible"] is True

        # 次月用户恢复 10 万/月
        resp_old = client.get("/api/usage/me", headers=_headers(old_uid))
        assert resp_old.status_code == 200
        assert resp_old.json()["quota"]["limitTokens"] == 100_000

        # 曾经付费到期回落 trial 的用户（即便注册在首月内）不重复享首月 20 万，且无首单特惠资格
        expired_uid = "u-quota-expired-first-month"
        _ensure_user(
            expired_uid,
            plan_code="standard",
            created_at=f"{today.isoformat()}T00:00:00+00:00",
            plan_expires_at=(today - timedelta(days=1)).isoformat(),
        )
        _seed_member(expired_uid)
        quota_service.clear_state_cache()
        resp_exp = client.get("/api/usage/me", headers=_headers(expired_uid))
        assert resp_exp.status_code == 200
        exp_q = resp_exp.json()["quota"]
        assert exp_q["planCode"] == "trial"
        assert exp_q["limitTokens"] == 100_000
        assert exp_q["promoEligible"] is False
    finally:
        quota_service.clear_state_cache()
        _cleanup([new_uid, old_uid, "u-quota-expired-first-month"])


@pytest.mark.asyncio
async def test_first_month_promo_consumption_order_and_expiry(client, monkeypatch):
    """回归：消耗顺序固定为「先套餐 → 再有效期内新人体验包 → 后永久余额」，且过期惰性归零，80% 触发提示。"""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import Session

    from db.session import get_sync_engine, session_scope
    from services.quota_service import record_consumption_async, record_consumption_sync

    monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", True)
    today = datetime.now(timezone.utc).date()
    uid = "u-promo-order-expiry"
    _ensure_user(uid, created_at=f"{today.isoformat()}T00:00:00+00:00")
    _seed_member(uid)

    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute("UPDATE auth_users SET token_balance = 50000 WHERE user_id = ?", (uid,))
        conn.commit()
    finally:
        conn.close()
    quota_service.clear_state_cache()

    try:
        # 1. 消耗 159,999（< 80% of 200k）：不触发低额度提醒
        async with session_scope() as session:
            await record_consumption_async(session, user_id=uid, tokens=159_999, scene="test")
        quota_service.clear_state_cache()
        q1 = client.get("/api/usage/me", headers=_headers(uid)).json()["quota"]
        assert q1["promoEligible"] is True
        assert q1["promoLowQuota"] is False

        # 2. 再消耗 1（累计 160,000 = 80%）：触发 promoLowQuota
        async with session_scope() as session:
            await record_consumption_async(session, user_id=uid, tokens=1, scene="test")
        quota_service.clear_state_cache()
        q2 = client.get("/api/usage/me", headers=_headers(uid)).json()["quota"]
        assert q2["promoLowQuota"] is True

        # 3. 购买新人体验包后：800,000 到账，有效期 30 天，promoEligible=False, promoLowQuota=False
        expires_in_30d = (today + timedelta(days=30)).isoformat()
        conn = sqlite3.connect(TEST_CORE_DB)
        try:
            conn.execute(
                "UPDATE auth_users SET promo_token_balance = 800000, promo_expires_at = ?,"
                " promo_purchased_at = ? WHERE user_id = ?",
                (expires_in_30d, f"{today.isoformat()}T01:00:00+00:00", uid),
            )
            conn.commit()
        finally:
            conn.close()
        quota_service.clear_state_cache()
        q3 = client.get("/api/usage/me", headers=_headers(uid)).json()["quota"]
        assert q3["promoEligible"] is False
        assert q3["promoLowQuota"] is False
        assert q3["promoTokenBalance"] == 800_000
        assert q3["promoExpiresAt"] == expires_in_30d
        assert q3["remainingTokens"] == 40_000 + 800_000 + 50_000

        # 4. 再消耗 140,000（40,000 走首月剩余套餐 + 100,000 走特惠包，永久余额 50,000 不动）
        async with session_scope() as session:
            await record_consumption_async(session, user_id=uid, tokens=140_000, scene="test")
        conn = sqlite3.connect(TEST_CORE_DB)
        try:
            promo_bal, perm_bal = conn.execute(
                "SELECT promo_token_balance, token_balance FROM auth_users WHERE user_id = ?",
                (uid,),
            ).fetchone()
        finally:
            conn.close()
        assert promo_bal == 700_000
        assert perm_bal == 50_000

        # 5. 同步埋点路径再消耗 720,000（700,000 耗尽特惠包 + 20,000 扣永久余额）
        with Session(get_sync_engine()) as sync_session:
            record_consumption_sync(sync_session, user_id=uid, tokens=720_000, scene="test_sync")
            sync_session.commit()
        conn = sqlite3.connect(TEST_CORE_DB)
        try:
            promo_bal2, perm_bal2 = conn.execute(
                "SELECT promo_token_balance, token_balance FROM auth_users WHERE user_id = ?",
                (uid,),
            ).fetchone()
        finally:
            conn.close()
        assert promo_bal2 == 0
        assert perm_bal2 == 30_000

        # 6. 模拟未用完但已过期（promo_expires_at = 昨天）：惰性归零，remainingTokens 不包含过期特惠
        yesterday = (today - timedelta(days=1)).isoformat()
        conn = sqlite3.connect(TEST_CORE_DB)
        try:
            conn.execute(
                "UPDATE auth_users SET promo_token_balance = 500000, promo_expires_at = ? WHERE user_id = ?",
                (yesterday, uid),
            )
            conn.commit()
        finally:
            conn.close()
        quota_service.clear_state_cache()
        q4 = client.get("/api/usage/me", headers=_headers(uid)).json()["quota"]
        assert q4["promoTokenBalance"] == 0
        assert q4["promoExpiresAt"] is None
        assert q4["remainingTokens"] == 30_000
    finally:
        quota_service.clear_state_cache()
        _cleanup([uid])


def test_paid_plans_have_annual_10m_token_quota():
    """云端标准版与人机双签版年享 1000 万 token 额度，最多支持 3 个账套。"""
    for code in ("standard", "pro", "plus"):
        plan = PLAN_CATALOG[code]
        assert plan.period_type == "annual"
        assert plan.token_quota == 10_000_000
        assert plan.max_companies == 3


def test_self_hosted_mode_bypasses_quota_gate(client, monkeypatch):
    """开源自部署模式（QUOTA_ENFORCEMENT_ENABLED=False）不拦截超出 10 万额度的请求且展示不限量。"""
    monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", False)
    _ensure_user("u-quota-oss")
    _seed_member("u-quota-oss")
    _seed_counter(
        "u-quota-oss",
        500_000,
        period_start=_current_month_start_iso(),
        period_type="monthly",
    )
    quota_service.clear_state_cache()
    try:
        quota_service.ensure_within_quota("u-quota-oss")  # 超过 100k 仍必须放行
        resp = client.get("/api/usage/me", headers=_headers("u-quota-oss"))
        assert resp.status_code == 200
        quota = resp.json()["quota"]
        assert quota["planCode"] == "trial"
        assert quota["limitTokens"] is None
        assert quota["maxCompanies"] is None
        assert quota["remainingTokens"] is None
        assert quota["usedTokens"] >= 500_000
    finally:
        quota_service.clear_state_cache()
        _cleanup(["u-quota-oss"])


def test_gate_passes_when_under_limit(client, monkeypatch):
    monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", True)
    _ensure_user("u-quota-under")
    _seed_counter("u-quota-under", 50)
    monkeypatch.setitem(PLAN_CATALOG, "trial", _LIMITED_TRIAL)
    quota_service.clear_state_cache()
    try:
        quota_service.ensure_within_quota("u-quota-under")  # 50 < 100，放行
    finally:
        quota_service.clear_state_cache()
        _cleanup(["u-quota-under"])


def test_gate_raises_when_over_limit(client, monkeypatch):
    monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", True)
    _ensure_user("u-quota-over")
    _seed_counter("u-quota-over", 500)
    monkeypatch.setitem(PLAN_CATALOG, "trial", _LIMITED_TRIAL)
    quota_service.clear_state_cache()
    try:
        with pytest.raises(QuotaExceededError) as exc_info:
            quota_service.ensure_within_quota("u-quota-over")
        assert exc_info.value.status_code == 402
        assert exc_info.value.code == "QUOTA_EXCEEDED"
    finally:
        quota_service.clear_state_cache()
        _cleanup(["u-quota-over"])


def test_gate_counts_bonus_tokens(client, monkeypatch):
    """管理员一次性加量并入当期额度：100 + 200 = 300，用 250 放行、301 超限。"""
    monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", True)
    _ensure_user("u-quota-bonus", bonus=200)
    _seed_counter("u-quota-bonus", 250)
    monkeypatch.setitem(PLAN_CATALOG, "trial", _LIMITED_TRIAL)
    quota_service.clear_state_cache()
    try:
        quota_service.ensure_within_quota("u-quota-bonus")  # 250 < 300，放行
        _seed_counter("u-quota-bonus", 301)
        quota_service.clear_state_cache()
        with pytest.raises(QuotaExceededError):
            quota_service.ensure_within_quota("u-quota-bonus")
    finally:
        quota_service.clear_state_cache()
        _cleanup(["u-quota-bonus"])


# ---------------------------------------------------------------------------
# 周期计数与状态
# ---------------------------------------------------------------------------

def _current_month_start_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date().replace(day=1).isoformat()


@pytest.mark.asyncio
async def test_record_consumption_upserts_counter(client):
    _ensure_user("u-quota-acc")
    from db.session import session_scope

    from services.quota_service import record_consumption_async

    try:
        async with session_scope() as session:
            await record_consumption_async(session, user_id="u-quota-acc", tokens=30)
        async with session_scope() as session:
            await record_consumption_async(session, user_id="u-quota-acc", tokens=50)

        conn = sqlite3.connect(TEST_CORE_DB)
        try:
            row = conn.execute(
                "SELECT used_tokens, period_type, period_start FROM user_quota_counters"
                " WHERE user_id = 'u-quota-acc'"
            ).fetchone()
        finally:
            conn.close()
        assert row == (80, "monthly", _current_month_start_iso())
    finally:
        _cleanup(["u-quota-acc"])


def test_usage_me_quota_state_fallback_to_llm_usage(client, monkeypatch):
    """计数行缺失时 /api/usage/me 的 quota.usedTokens 回退聚合 llm_usage，且默认 trial 为月度 10 万 token。"""
    monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", True)
    _ensure_user("u-quota-me")
    _seed_member("u-quota-me")  # 播种了用户行后鉴权会查成员归属，须补齐
    _seed_llm_usage("u-quota-me", 120)
    try:
        resp = client.get("/api/usage/me", headers=_headers("u-quota-me"))
        assert resp.status_code == 200
        quota = resp.json()["quota"]
        assert quota is not None
        assert quota["planCode"] == "trial"
        assert quota["periodType"] == "monthly"
        assert quota["limitTokens"] == 100_000
        assert quota["remainingTokens"] == 100_000 - 120
        assert quota["periodStart"] == _current_month_start_iso()
        assert quota["usedTokens"] >= 120
    finally:
        _cleanup(["u-quota-me"])


# ---------------------------------------------------------------------------
# 402 端到端与 safe_* 放行
# ---------------------------------------------------------------------------

def test_quota_exceeded_returns_402_end_to_end(client, monkeypatch):
    """启用限额 + 超限计数 → /api/invoice/parse-columns 返回 402（卡口在 LLM 调用前）。"""
    monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", True)
    _ensure_user("u-quota-e2e")
    _seed_member("u-quota-e2e")
    _seed_counter("u-quota-e2e", 500)
    monkeypatch.setitem(PLAN_CATALOG, "trial", _LIMITED_TRIAL)
    quota_service.clear_state_cache()
    try:
        resp = client.post(
            "/api/invoice/parse-columns",
            json={"columns": ["日期", "金额"]},
            headers=_headers("u-quota-e2e"),
        )
        assert resp.status_code == 402
        assert resp.json()["detail"]["code"] == "QUOTA_EXCEEDED"
    finally:
        quota_service.clear_state_cache()
        _cleanup(["u-quota-e2e"])


def test_safe_chat_text_json_reraises_accounting_error(client, monkeypatch):
    """safe_* 的兜底必须放行 AccountingError（含配额异常），只吞普通异常。"""
    import asyncio

    from core.errors import AccountingError
    from services import llm

    monkeypatch.setattr(llm, "_LLM_DISABLED", False)
    monkeypatch.setattr(llm, "_LLM_DISABLED_AT", None)

    def _raise_quota(*args, **kwargs):
        raise AccountingError(code="QUOTA_EXCEEDED", message="AI 用量已达到当前套餐上限", status_code=402)

    monkeypatch.setattr(llm, "chat_text_json", _raise_quota)
    with pytest.raises(AccountingError):
        llm.safe_chat_text_json("sys", "user", fallback={})

    def _raise_value_error(*args, **kwargs):
        raise ValueError("无法解析")

    monkeypatch.setattr(llm, "chat_text_json", _raise_value_error)
    assert llm.safe_chat_text_json("sys", "user", fallback={"x": 1}) == {"x": 1}


def test_company_creation_enforces_plan_max_companies(client, monkeypatch):
    """商业化开关开启时所有套餐最多创建 3 个账套；开源自部署模式不拦截。"""
    monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", True)
    uid = "u-company-limit"
    _ensure_user(uid, plan_code="standard")
    _seed_member(uid)
    quota_service.clear_state_cache()
    try:
        for idx in range(1, 4):
            resp = client.post(
                "/api/auth/companies",
                json={"name": f"账套 {idx}"},
                headers=_headers(uid),
            )
            assert resp.status_code == 200, resp.text

        blocked = client.post(
            "/api/auth/companies",
            json={"name": "第 4 个账套"},
            headers=_headers(uid),
        )
        assert blocked.status_code == 403
        assert blocked.json()["detail"]["code"] == "COMPANY_LIMIT_EXCEEDED"

        # /api/usage/me 返回账套计数与上限
        me = client.get("/api/usage/me", headers=_headers(uid))
        assert me.status_code == 200
        assert me.json()["quota"]["maxCompanies"] == 3
        assert me.json()["quota"]["companyCount"] == 3

        # 开源自部署（QUOTA_ENFORCEMENT_ENABLED=False）放行第 4 个账套
        monkeypatch.setattr(config, "QUOTA_ENFORCEMENT_ENABLED", False)
        allowed = client.post(
            "/api/auth/companies",
            json={"name": "自部署第 4 个账套"},
            headers=_headers(uid),
        )
        assert allowed.status_code == 200, allowed.text
    finally:
        quota_service.clear_state_cache()
        _cleanup([uid])
