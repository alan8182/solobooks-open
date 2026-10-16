# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Agent M1：批量记账动作 + 画像写入回路回归测试。

走真实 create_entry 链路（ACCOUNTING_TEST_MODE + sqlite），不 mock 会计核心。
"""
import pytest

from agent import actions as agent_actions
from agent.memory import get_profile


async def _ensure_schema() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_propose_accrual_depreciation_graceful_without_assets():
    """M2：计提确认动作——批准路径调用现有计提服务；无资产时优雅返回空结果。"""
    await _ensure_schema()
    from services.fixed_asset_service import _ensure_schema as _ensure_asset_schema

    await _ensure_asset_schema()  # fixed_assets 表由旧式服务自建
    from services.entry_service import list_entries

    company_id = "c-agent-accrual"
    user_id = "owner-1"

    action = await agent_actions.create_action(
        company_id=company_id,
        user_id=user_id,
        action_type="propose_accrual",
        title="折旧计提（2026-09）",
        payload={"kind": "depreciation", "month": "2026-09", "preview": {"pendingCount": 0, "pendingTotal": 0}},
        evidence={"created_via": "pytest"},
    )
    decision = await agent_actions.approve_action(
        action["id"], user_id=user_id, company_id=company_id
    )
    assert decision["status"] == "approved"
    accrual = decision["result"]["accrual"]
    assert accrual["kind"] == "depreciation"
    assert accrual["month"] == "2026-09"
    # 无在用资产 → 计提服务空跑（processed/skipped 均为 0，不报错）
    summary = accrual["summary"]
    assert isinstance(summary, dict)

    # 没有任何账目产生
    entries = await list_entries(user_id, company_id)
    assert not [e for e in entries if e.get("source") == "agent"]


@pytest.mark.asyncio
async def test_propose_match_validation():
    """M2：对账建议动作缺少关键字段时创建被拒绝。"""
    await _ensure_schema()
    import pytest as _pytest

    try:
        await agent_actions.create_action(
            company_id="c-agent-match",
            user_id="owner-1",
            action_type="propose_match",
            title="对账建议",
            payload={"statementId": "", "entryIds": []},
        )
        raised = False
    except ValueError:
        raised = True
    assert raised, "缺少 statementId/entryIds 的 propose_match 应被拒绝"


@pytest.mark.asyncio
async def test_batch_propose_and_partial_approve_writes_profile():
    await _ensure_schema()
    company_id = "c-agent-batch"
    user_id = "owner-1"

    action = await agent_actions.create_action(
        company_id=company_id,
        user_id=user_id,
        action_type="propose_vouchers",
        title="批量记账（2 笔）",
        payload={
            "items": [
                {
                    "direction": "expense",
                    "amount": 32,
                    "title": "UU跑腿代送文件",
                    "category": "办公费",
                    "occurDate": "2026-09-25",
                    "merchant": "UU跑腿",
                },
                {
                    "direction": "income",
                    "amount": 3000,
                    "title": "咨询服务费",
                    "category": "咨询服务收入",
                    "occurDate": "2026-09-28",
                    "merchant": "甲方公司",
                },
            ]
        },
        evidence={"created_via": "pytest"},
    )
    assert action["status"] == "pending"

    # 用户排除第 2 行，并把第 1 行分类从"办公费"改成"交通费"
    decision = await agent_actions.approve_action(
        action["id"],
        user_id=user_id,
        company_id=company_id,
        exclude_indexes=[1],
        edits={"0": {"category": "交通费"}},
    )
    assert decision["status"] == "approved"
    items = decision["result"]["items"]
    assert items[0]["status"] == "booked"
    assert items[0]["voucherRef"]
    assert items[1]["status"] == "excluded"

    # 只有第 1 行真实落账
    from services.entry_service import list_entries

    entries = await list_entries(user_id, company_id)
    agent_entries = [e for e in entries if e.get("source") == "agent"]
    assert len(agent_entries) == 1
    assert agent_entries[0]["category"] == "交通费"
    assert str(agent_entries[0]["amount"]) == "32.00"

    # 画像写入回路：商家→最终分类 已沉淀，且带"修正"信号
    profile = await get_profile(company_id)
    prefs = profile["categoryPreferences"]
    assert prefs["UU跑腿"]["category"] == "交通费"
    assert prefs["修正:UU跑腿"]["category"] == "交通费"

    # 再次批准同一动作 → 409
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await agent_actions.approve_action(action["id"], user_id=user_id, company_id=company_id)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_run_agent_stream_thinking_and_reasoning_events(monkeypatch):
    """测试 agent 循环对 enable_thinking 与 reasoning 事件的透传支持。"""
    from agent.loop import LoopContext, run_agent_stream
    from routers.agent import AgentChatRequest

    # 1. 验证 Pydantic Schema 解析 enableThinking
    req = AgentChatRequest(
        messages=[{"role": "user", "content": "查一下9月收支"}],
        enableThinking=True,
    )
    assert req.enableThinking is True

    # 2. Mock chat_tools_stream_async 产出 reasoning + delta
    captured_kwargs = {}

    async def fake_chat_tools_stream_async(*args, **kwargs):
        captured_kwargs.update(kwargs)
        yield {"type": "reasoning", "content": "用户想查询9月收支，正在思考..."}
        yield {"type": "delta", "content": "9月收入共计 50000 元。"}
        yield {"type": "final", "content": "9月收入共计 50000 元。", "tool_calls": []}

    import agent.loop as loop_mod

    monkeypatch.setattr(loop_mod, "chat_tools_stream_async", fake_chat_tools_stream_async)

    ctx = LoopContext(user_id="u1", company_id="c1", conversation_id="conv-1")
    events = []
    async for ev in run_agent_stream(ctx, [{"role": "user", "content": "查一下9月收支"}], enable_thinking=True):
        events.append(ev)

    # 验证 meta 包含 enableThinking: True
    meta = next(e for e in events if e["type"] == "meta")
    assert meta["enableThinking"] is True
    assert captured_kwargs.get("enable_thinking") is True

    # 验证 reasoning 事件成功产出
    reasoning_events = [e for e in events if e["type"] == "reasoning"]
    assert len(reasoning_events) == 1
    assert "正在思考" in reasoning_events[0]["content"]

    # 验证 token 事件与 final 正常
    token_events = [e for e in events if e["type"] == "token"]
    assert len(token_events) == 1
    assert "50000" in token_events[0]["content"]

