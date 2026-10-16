# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""管理后台 M3：Agent 审计 + 异常调用榜 + 数据清理。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from services.token_service import create_access_token

from tests.conftest import TEST_CORE_DB

_TODAY = datetime.now(timezone.utc).date().isoformat()
_OLD = "2026-08-01 01:00:00.000000"


def _ensure_users() -> None:
    """本文件可独立运行：确保 u-admin 存在（其他测试文件播种过则幂等跳过）。"""
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO auth_users ("
            " user_id, openid, phone, phone_verified_at, wechat_openid_bound_at,"
            " current_company_id, nickname, avatar, is_platform_admin, is_disabled,"
            " created_at, last_login_at, updated_at"
            ") VALUES ('u-admin', '', '', '', '', 'c1', '管理员', '', 1, 0, '2026-01-01', '2026-01-01', '')"
        )
        conn.commit()
    finally:
        conn.close()


def _seed_agent_actions(rows: list[tuple]) -> None:
    """rows: (id, status, created_at, resolved?)"""
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.executemany(
            """
            INSERT OR REPLACE INTO agent_actions (
                id, company_id, user_id, conversation_id, action_type, title,
                payload, evidence, result, status, created_at, resolved_at, resolved_by
            ) VALUES (?, 'c-m3', 'u-m3-owner', 'conv-m3', 'propose_voucher', ?, '{}', '{}', NULL, ?, ?, ?, ?)
            """,
            [
                (
                    aid,
                    title,
                    status,
                    created_at,
                    created_at if status != "pending" else None,
                    None if status == "pending" else "u-m3-owner",
                )
                for (aid, status, title, created_at) in rows
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _seed_llm_error() -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute(
            "INSERT INTO llm_usage (user_id, company_id, scene, model, prompt_tokens,"
            " completion_tokens, total_tokens, duration_ms, status, error_type, created_at)"
            " VALUES ('u-m3-err', 'c-m3', 'agent_chat', 'deepseek-flash', 0, 0, 0, 100,"
            f" 'error', 'RateLimitError', '{_TODAY} 00:30:00.000000')"
        )
        conn.commit()
    finally:
        conn.close()


def _cleanup_rows() -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute("DELETE FROM agent_actions WHERE id LIKE 'aa-m3-%'")
        conn.execute("DELETE FROM llm_usage WHERE user_id LIKE 'u-m3-%'")
        conn.execute(
            "DELETE FROM admin_audit_logs WHERE operator_id = 'u-admin'"
            " AND (target_id LIKE 'aa-m3-%' OR action = 'cleanup')"
        )
        conn.commit()
    finally:
        conn.close()


def _headers(user_id: str, company_id: str = "c1") -> dict:
    token = create_access_token({"sub": user_id, "company_id": company_id})
    return {"Authorization": f"Bearer {token}"}


def test_agent_actions_list_and_detail(client):
    _ensure_users()
    _seed_agent_actions([
        ("aa-m3-1", "pending", "计提折旧（2026-09）", f"{_TODAY} 01:00:00.000000"),
        ("aa-m3-2", "approved", "批量记账（3 笔）", "2026-08-01 01:00:00.000000"),
    ])
    try:
        pending = client.get(
            "/api/admin/agent-actions",
            params={"status": "pending"},
            headers=_headers("u-admin"),
        ).json()
        ids = [a["id"] for a in pending["items"]]
        assert "aa-m3-1" in ids and "aa-m3-2" not in ids

        all_rows = client.get(
            "/api/admin/agent-actions", params={"status": "all"}, headers=_headers("u-admin")
        ).json()
        assert all_rows["total"] >= 2

        detail = client.get(
            "/api/admin/agent-actions/aa-m3-1", headers=_headers("u-admin")
        ).json()
        assert detail["payload"] == {} and detail["actionType"] == "propose_voucher"

        assert (
            client.get(
                "/api/admin/agent-actions/aa-m3-none", headers=_headers("u-admin")
            ).status_code
            == 404
        )
    finally:
        _cleanup_rows()


def test_mark_agent_action(client):
    _ensure_users()
    _seed_agent_actions([
        ("aa-m3-1", "pending", "计提折旧（2026-09）", f"{_TODAY} 01:00:00.000000"),
        ("aa-m3-2", "approved", "批量记账（3 笔）", "2026-08-01 01:00:00.000000"),
    ])
    try:
        resp = client.patch(
            "/api/admin/agent-actions/aa-m3-1",
            json={"status": "rejected", "note": "清理过期建议"},
            headers=_headers("u-admin"),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

        detail = client.get(
            "/api/admin/agent-actions/aa-m3-1", headers=_headers("u-admin")
        ).json()
        assert detail["status"] == "rejected" and detail["resolvedBy"] == "u-admin"

        # 已处理动作不可再标记
        assert (
            client.patch(
                "/api/admin/agent-actions/aa-m3-1",
                json={"status": "approved"},
                headers=_headers("u-admin"),
            ).status_code
            == 422
        )
        # 非法状态被 422 拦截
        assert (
            client.patch(
                "/api/admin/agent-actions/aa-m3-2",
                json={"status": "bogus"},
                headers=_headers("u-admin"),
            ).status_code
            == 422
        )
        # 留痕
        logs = client.get("/api/admin/audit-logs", headers=_headers("u-admin")).json()["items"]
        assert any(
            log["action"] == "agent_action_rejected" and log["targetId"] == "aa-m3-1"
            for log in logs
        )
    finally:
        _cleanup_rows()


def test_overview_recent_errors(client):
    _ensure_users()
    _seed_llm_error()
    try:
        body = client.get("/api/admin/overview", headers=_headers("u-admin")).json()
        assert body["llm"]["errorCount7d"] >= 1
        assert any(e["type"] == "RateLimitError" for e in body["llm"]["topErrors"])
        assert any(e["userId"] == "u-m3-err" for e in body["llm"]["recentErrors"])
    finally:
        _cleanup_rows()


def test_cleanup(client):
    _ensure_users()
    _seed_agent_actions([
        ("aa-m3-clean-1", "approved", "今日动作", f"{_TODAY} 01:00:00.000000"),
        ("aa-m3-clean-2", "rejected", "旧动作", _OLD),
    ])
    try:
        # dryRun 预览
        preview = client.post(
            "/api/admin/cleanup",
            json={"table": "agent_actions", "before": _TODAY, "dryRun": True},
            headers=_headers("u-admin"),
        ).json()
        assert preview["matched"] >= 1 and preview["deleted"] == 0

        # 执行删除
        result = client.post(
            "/api/admin/cleanup",
            json={"table": "agent_actions", "before": _TODAY, "dryRun": False},
            headers=_headers("u-admin"),
        ).json()
        assert result["deleted"] == preview["matched"]

        # 旧动作已删、今日动作仍在
        remaining = client.get(
            "/api/admin/agent-actions", params={"status": "all"}, headers=_headers("u-admin")
        ).json()
        ids = [a["id"] for a in remaining["items"]]
        assert "aa-m3-clean-2" not in ids and "aa-m3-clean-1" in ids

        # 留痕
        logs = client.get("/api/admin/audit-logs", headers=_headers("u-admin")).json()["items"]
        cleanup_logs = [log for log in logs if log["action"] == "cleanup"]
        assert cleanup_logs and cleanup_logs[0]["targetId"] == "agent_actions"

        # 非法表名
        assert (
            client.post(
                "/api/admin/cleanup",
                json={"table": "auth_users", "before": _TODAY, "dryRun": True},
                headers=_headers("u-admin"),
            ).status_code
            == 422
        )
    finally:
        _cleanup_rows()
