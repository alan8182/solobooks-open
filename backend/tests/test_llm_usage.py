"""llm_usage 埋点：mock 供应商流式响应，断言 stream_options 携带与用量落库。"""
from __future__ import annotations

import asyncio


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


async def _ensure_schema() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
