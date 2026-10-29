# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""用户侧 AI 用量汇总 /api/usage/me：本人口径、跨账套聚合、场景分组与日序列。"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

from services.token_service import create_access_token

from tests.conftest import TEST_CORE_DB

_NOW = datetime.now(timezone.utc)
_TODAY = _NOW.date().isoformat()
# 上月中旬：无论今天是否月初，都稳定落在上一个自然月
_LAST_MONTH_MID = (_NOW.date().replace(day=1) - timedelta(days=1)).replace(day=15).isoformat()


def _headers(user_id: str, company_id: str = "c1") -> dict:
    token = create_access_token({"sub": user_id, "company_id": company_id})
    return {"Authorization": f"Bearer {token}"}


def _seed_usage() -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        rows = [
            # 本人本月（跨两个账套 c1/c2）：失败调用 token 为 0 但计次数
            ("u-usage-me", "c1", "agent_chat", "qwen-plus", 100, 50, f"{_TODAY} 01:00:00.000000", "ok"),
            ("u-usage-me", "c2", "agent_chat", "qwen-plus", 200, 100, f"{_TODAY} 02:00:00.000000", "ok"),
            ("u-usage-me", "c1", "classify", "qwen-plus", 30, 20, f"{_TODAY} 03:00:00.000000", "ok"),
            ("u-usage-me", "c1", "invoice_ocr", "qwen-vl-plus", 0, 0, f"{_TODAY} 04:00:00.000000", "error"),
            # 本人上月
            ("u-usage-me", "c1", "report", "qwen-plus", 500, 250, f"{_LAST_MONTH_MID} 12:00:00.000000", "ok"),
            # 他人本月：不得计入本人汇总
            ("u-usage-other", "c1", "agent_chat", "qwen-plus", 999, 999, f"{_TODAY} 05:00:00.000000", "ok"),
        ]
        for uid, cid, scene, model, prompt, completion, created_at, status in rows:
            conn.execute(
                "INSERT INTO llm_usage (user_id, company_id, scene, model, prompt_tokens,"
                " completion_tokens, total_tokens, duration_ms, status, error_type, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 100, ?, NULL, ?)",
                (uid, cid, scene, model, prompt, completion, prompt + completion, status, created_at),
            )
        conn.commit()
    finally:
        conn.close()


def _cleanup() -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute("DELETE FROM llm_usage WHERE user_id LIKE 'u-usage-%'")
        conn.commit()
    finally:
        conn.close()


def test_usage_me_summary(client):
    _seed_usage()
    try:
        resp = client.get("/api/usage/me", headers=_headers("u-usage-me"))
        assert resp.status_code == 200
        data = resp.json()

        assert data["quota"] is None  # P2 配额体系预留位

        cur = data["currentMonth"]
        assert cur["calls"] == 4
        assert cur["promptTokens"] == 330
        assert cur["completionTokens"] == 170
        assert cur["totalTokens"] == 500

        prev = data["previousMonth"]
        assert prev["calls"] == 1
        assert prev["totalTokens"] == 750

        scenes = {row["scene"]: row for row in data["byScene"]}
        assert scenes["agent_chat"]["totalTokens"] == 450  # 跨账套合并
        assert scenes["classify"]["totalTokens"] == 50
        assert scenes["invoice_ocr"]["calls"] == 1
        assert scenes["invoice_ocr"]["totalTokens"] == 0
        assert "report" not in scenes  # 上月数据不进本月分场景

        daily = data["daily"]
        assert len(daily) == 30
        today_row = next(row for row in daily if row["date"] == _TODAY)
        assert today_row["calls"] == 4
        assert today_row["totalTokens"] == 500
        # 上月种子行是否落在 30 天窗口内取决于运行日期（月初运行时落在窗口内）
        window_start = _NOW.date() - timedelta(days=29)
        prev_seed_in_window = date.fromisoformat(_LAST_MONTH_MID) >= window_start
        assert sum(row["calls"] for row in daily) == 4 + (1 if prev_seed_in_window else 0)
    finally:
        _cleanup()


def test_usage_me_requires_auth(client):
    resp = client.get("/api/usage/me")
    assert resp.status_code == 401
