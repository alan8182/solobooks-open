# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""管理后台 M1：llm_usage 埋点与用量统计接口。

测试直接写 SQLite 播种（确定性时间戳），每个用例结束自清理，
避免共享库上的统计互相污染。
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

from services.token_service import create_access_token

from tests.conftest import TEST_CORE_DB


def _seed_usage(rows: list[tuple]) -> None:
    """rows: (user_id, company_id, scene, model, prompt, completion, created_at)

    先清空整表再播种：本文件的断言是平台级精确总数，任何其他用例留下的
    llm_usage 行（fire-and-forget 埋点跨文件落地）都会污染统计。
    """
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute("DELETE FROM llm_usage")
        conn.executemany(
            """
            INSERT INTO llm_usage (
                user_id, company_id, scene, model, prompt_tokens,
                completion_tokens, total_tokens, duration_ms, status, error_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 800, 'ok', NULL, ?)
            """,
            [(u, c, s, m, p, q, p + q, ts) for (u, c, s, m, p, q, ts) in rows],
        )
        conn.commit()
    finally:
        conn.close()


def _cleanup_usage(user_ids: tuple[str, ...]) -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        marks = ",".join("?" * len(user_ids))
        conn.execute(f"DELETE FROM llm_usage WHERE user_id IN ({marks})", user_ids)
        conn.commit()
    finally:
        conn.close()


def _ensure_users() -> None:
    """本文件可独立运行：确保 u-admin/u-normal 存在（test_admin.py 播种过则幂等跳过）。"""
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO auth_users ("
            " user_id, openid, phone, phone_verified_at, wechat_openid_bound_at,"
            " current_company_id, nickname, avatar, is_platform_admin, is_disabled,"
            " created_at, last_login_at, updated_at"
            ") VALUES (?, '', '', '', '', 'c1', ?, '', ?, 0, '2026-01-01', '2026-01-01', '')",
            [("u-admin", "管理员", 1), ("u-normal", "普通用户", 0)],
        )
        conn.commit()
    finally:
        conn.close()


def _headers(user_id: str, company_id: str = "c1") -> dict:
    token = create_access_token({"sub": user_id, "company_id": company_id})
    return {"Authorization": f"Bearer {token}"}


def test_usage_requires_admin(client):
    _ensure_users()
    assert client.get("/api/admin/usage/tokens", headers=_headers("u-normal")).status_code == 403
    assert client.get("/api/admin/usage/recent", headers=_headers("u-normal")).status_code == 403


def test_usage_tokens_group_bys(client):
    _ensure_users()
    today = datetime.now(timezone.utc).date().isoformat()
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    _seed_usage([
        ("u1", "c1", "agent_chat", "qwen-plus", 100, 50, f"{today} 01:00:00"),
        ("u1", "c1", "agent_chat", "qwen-plus", 10, 5, f"{yesterday} 01:00:00"),
        ("u2", "c1", "copilot", "qwen-plus", 40, 20, f"{today} 02:00:00"),
        ("u1", "c1", "invoice_ocr", "qwen-vl-plus", 200, 30, f"{today} 03:00:00"),
    ])
    try:
        # 按模型
        body = client.get(
            "/api/admin/usage/tokens", params={"group_by": "model"}, headers=_headers("u-admin")
        ).json()
        by_model = {r["key"]: r for r in body["rows"]}
        assert by_model["qwen-plus"]["totalTokens"] == 225
        assert by_model["qwen-plus"]["calls"] == 3
        assert by_model["qwen-vl-plus"]["totalTokens"] == 230

        # 按用户
        body = client.get(
            "/api/admin/usage/tokens", params={"group_by": "user"}, headers=_headers("u-admin")
        ).json()
        by_user = {r["key"]: r for r in body["rows"]}
        assert by_user["u1"]["totalTokens"] == 395
        assert by_user["u2"]["totalTokens"] == 60

        # 按天 + from/to 过滤（只保留今天）
        body = client.get(
            "/api/admin/usage/tokens",
            params={"group_by": "day", "from": today, "to": today},
            headers=_headers("u-admin"),
        ).json()
        assert [r["key"] for r in body["rows"]] == [today]
        assert body["rows"][0]["totalTokens"] == 440

        # 按公司
        body = client.get(
            "/api/admin/usage/tokens", params={"group_by": "company"}, headers=_headers("u-admin")
        ).json()
        c1 = next(r for r in body["rows"] if r["key"] == "c1")
        assert c1["totalTokens"] == 455
    finally:
        _cleanup_usage(("u1", "u2"))


def test_usage_recent(client):
    _ensure_users()
    _seed_usage([
        ("u1", "c1", "agent_chat", "qwen-plus", 100, 50, "2026-09-01 01:00:00"),
        ("u9", "c9", "copilot", "qwen-plus", 1, 1, "2026-09-02 01:00:00"),
        ("u8", "c8", "report", "qwen-plus", 5, 5, "2026-09-03 01:00:00"),
        ("u-err", "c1", "agent_chat", "qwen-plus", 0, 0, "2026-09-04 01:00:00"),
    ])
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.execute(
            "UPDATE llm_usage SET status='error', error_type='RateLimitError', duration_ms=120"
            " WHERE user_id='u-err'"
        )
        conn.commit()
    finally:
        conn.close()
    try:
        items = client.get("/api/admin/usage/recent", headers=_headers("u-admin")).json()["items"]
        assert items[0]["scene"] == "agent_chat" and items[0]["status"] == "error"  # 最新在前
        assert len(items) == 4

        err_only = client.get(
            "/api/admin/usage/recent", params={"status": "error"}, headers=_headers("u-admin")
        ).json()["items"]
        assert len(err_only) == 1 and err_only[0]["errorType"] == "RateLimitError"

        limited = client.get(
            "/api/admin/usage/recent", params={"limit": 2}, headers=_headers("u-admin")
        ).json()["items"]
        assert len(limited) == 2
    finally:
        _cleanup_usage(("u1", "u9", "u8", "u-err"))


def test_overview_usage_totals(client):
    _ensure_users()
    today = datetime.now(timezone.utc).date()
    outside_month = (today.replace(day=1) - timedelta(days=5)).isoformat()
    _seed_usage([
        ("u1", "c1", "agent_chat", "qwen-plus", 100, 50, f"{today.isoformat()} 05:00:00"),
        ("u2", "c1", "copilot", "qwen-plus", 30, 20, f"{today.isoformat()} 06:00:00"),
        ("u3", "c1", "report", "qwen-plus", 999, 999, f"{outside_month} 05:00:00"),
    ])
    try:
        body = client.get("/api/admin/overview", headers=_headers("u-admin")).json()
        assert body["usage"]["trackingEnabled"] is True
        assert body["usage"]["tokensToday"] == 200
        assert body["usage"]["tokensThisMonth"] == 200
    finally:
        _cleanup_usage(("u1", "u2", "u3"))


async def test_stream_usage_recorded(client, monkeypatch):
    """mock 供应商流式响应：断言 stream_options 已携带、用量成功落库，随后自清理。"""
    from sqlalchemy import select

    from db.models import LlmUsage
    from db.session import session_scope
    from services.llm import chat_tools_stream_async

    await _ensure_schema()

    class _Delta:
        content = "你好"
        reasoning_content = None
        tool_calls = None

    class _Choice:
        delta = _Delta()

    class _Chunk:
        def __init__(self, choices=None, usage=None):
            self.choices = choices if choices is not None else []
            self.usage = usage

    class _Usage:
        prompt_tokens = 100
        completion_tokens = 50
        total_tokens = 150

    store: dict = {}

    class _Completions:
        async def create(self, **payload):
            store["payload"] = payload

            async def _gen():
                yield _Chunk(choices=[_Choice()])
                yield _Chunk(usage=_Usage())

            return _gen()

    class _FakeClient:
        def __init__(self):
            self.chat = type("_Chat", (), {})()
            self.chat.completions = _Completions()

    monkeypatch.setattr("services.llm._oai_async", lambda: _FakeClient())

    events = [
        event
        async for event in chat_tools_stream_async(
            "sys",
            [{"role": "user", "content": "hi"}],
            model="test-stream-model",
            scene="agent_chat",
            user_id="u-stream",
            company_id="c-stream",
        )
    ]
    assert [e["type"] for e in events][-1] == "final"
    assert store["payload"]["stream_options"] == {"include_usage": True}

    row = None
    for _ in range(40):  # fire-and-forget 写入轮询
        async with session_scope() as session:
            row = (
                await session.execute(
                    select(LlmUsage).where(LlmUsage.user_id == "u-stream")
                )
            ).scalar_one_or_none()
        if row:
            break
        await asyncio.sleep(0.05)

    assert row is not None, "埋点未写入 llm_usage"
    assert row.prompt_tokens == 100 and row.completion_tokens == 50 and row.total_tokens == 150
    assert row.model == "test-stream-model" and row.status == "ok"

    _cleanup_usage(("u-stream",))


async def _ensure_schema() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
