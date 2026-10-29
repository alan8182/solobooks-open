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


def _ensure_user(user_id: str, plan_code: str = "trial", bonus: int = 0) -> None:
    """播种用户行（幂等）：锚点缺省走 created_at=2026-01-01。"""
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO auth_users ("
            " user_id, openid, phone, phone_verified_at, wechat_openid_bound_at,"
            " current_company_id, nickname, avatar, is_platform_admin, is_disabled,"
            " plan_code, plan_anchor_at, quota_bonus_tokens,"
            " created_at, last_login_at, updated_at"
            ") VALUES (?, '', '', '', '', 'c1', '', '', 0, 0, ?, NULL, ?,"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', '')",
            (user_id, plan_code, bonus),
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


def _seed_counter(user_id: str, used_tokens: int, period_start: str = "2026-01-01") -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO user_quota_counters (user_id, period_type, period_start, used_tokens, updated_at)"
            " VALUES (?, 'annual', ?, ?, '2026-01-01')",
            (user_id, period_start, used_tokens),
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

def test_gate_passes_when_no_limit_configured():
    """当前默认状态（目录内全 None）必须零开销直通，连用户行都不查。"""
    quota_service.ensure_within_quota("u-quota-nobody")  # 不应抛出
    quota_service.ensure_within_quota(None)


def test_gate_passes_when_under_limit(client, monkeypatch):
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
        assert row == (80, "annual", "2026-01-01")
    finally:
        _cleanup(["u-quota-acc"])


def test_usage_me_quota_state_fallback_to_llm_usage(client):
    """计数行缺失时 /api/usage/me 的 quota.usedTokens 回退聚合 llm_usage。"""
    _ensure_user("u-quota-me")
    _seed_member("u-quota-me")  # 播种了用户行后鉴权会查成员归属，须补齐
    _seed_llm_usage("u-quota-me", 120)
    try:
        resp = client.get("/api/usage/me", headers=_headers("u-quota-me"))
        assert resp.status_code == 200
        quota = resp.json()["quota"]
        assert quota is not None
        assert quota["planCode"] == "trial"
        assert quota["limitTokens"] is None  # 额度未定 = 未设限
        assert quota["remainingTokens"] is None
        assert quota["periodStart"] == "2026-01-01"
        assert quota["usedTokens"] >= 120
    finally:
        _cleanup(["u-quota-me"])


# ---------------------------------------------------------------------------
# 402 端到端与 safe_* 放行
# ---------------------------------------------------------------------------

def test_quota_exceeded_returns_402_end_to_end(client, monkeypatch):
    """启用限额 + 超限计数 → /api/invoice/parse-columns 返回 402（卡口在 LLM 调用前）。"""
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
