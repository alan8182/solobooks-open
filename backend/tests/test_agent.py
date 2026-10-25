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
    await _ensure_schema()  # 固定资产表在 P1 WP-B 起由 ORM create_all 建
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
async def test_propose_accrual_amortization_graceful_without_assets():
    """M2：无形资产摊销确认动作——批准路径调用无形资产摊销服务；无资产时优雅返回空结果。"""
    await _ensure_schema()
    from services.entry_service import list_entries

    company_id = "c-agent-amort"
    user_id = "owner-1"

    action = await agent_actions.create_action(
        company_id=company_id,
        user_id=user_id,
        action_type="propose_accrual",
        title="无形资产摊销（2026-09）",
        payload={"kind": "amortization", "month": "2026-09", "preview": {"pendingCount": 0, "pendingTotal": 0}},
        evidence={"created_via": "pytest"},
    )
    decision = await agent_actions.approve_action(
        action["id"], user_id=user_id, company_id=company_id
    )
    assert decision["status"] == "approved"
    accrual = decision["result"]["accrual"]
    assert accrual["kind"] == "amortization"
    assert accrual["month"] == "2026-09"
    summary = accrual["summary"]
    assert isinstance(summary, dict)

    entries = await list_entries(user_id, company_id)
    assert not [e for e in entries if e.get("source") == "agent"]


@pytest.mark.asyncio
async def test_propose_asset_closure_action_branch():
    """资产处理确认包走 propose_accrual 的新 kind，批准后返回 assetClosure。"""
    await _ensure_schema()
    company_id = "c-agent-asset-closure"
    user_id = "owner-1"
    action = await agent_actions.create_action(
        company_id=company_id,
        user_id=user_id,
        action_type="propose_accrual",
        title="资产处理确认（2026-10）",
        payload={
            "kind": "asset_closure",
            "month": "2026-10",
            "throughPeriod": "2026-10",
            "fixedCards": [],
            "intangibleCards": [],
            "runFixedDepreciation": False,
            "runIntangibleAmortization": False,
            "preview": {},
        },
        evidence={"created_via": "pytest"},
    )
    decision = await agent_actions.approve_action(
        action["id"], user_id=user_id, company_id=company_id
    )
    assert decision["status"] == "approved"
    assert decision["result"]["assetClosure"]["fixedCards"] == []
    assert decision["result"]["assetClosure"]["intangibleCards"] == []


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
async def test_voucher_payer_boss_and_employee_credit_payable_not_bank():
    """垫付类凭证的贷方必须是「其他应付款」，不能贷「银行存款」。

    老板自己掏钱/员工先垫，公司银行账户没有资金流出；一旦落成 1002，
    银行日记账和实际流水就对不上，负债也被隐藏了。

    凭证行的科目名以科目表为准（2241 目前是单一末级「其他应付款」），
    垫付人身份落在 entry.payer 上。
    """
    await _ensure_schema()
    company_id = "c-agent-payer"
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
                    "amount": 100,
                    "title": "打车拜访客户",
                    "category": "交通费",
                    "occurDate": "2026-09-20",
                    "payer": "boss_personal",
                },
                {
                    "direction": "expense",
                    "amount": 88,
                    "title": "买办公用品",
                    "category": "办公费",
                    "occurDate": "2026-09-20",
                    "payer": "employee_personal:李四",
                },
                {
                    "direction": "expense",
                    "amount": 66,
                    "title": "买打印纸",
                    "category": "办公费",
                    "occurDate": "2026-09-20",
                    "payer": "company_account",
                },
            ]
        },
        evidence={"created_via": "pytest"},
    )
    decision = await agent_actions.approve_action(
        action["id"], user_id=user_id, company_id=company_id
    )
    assert decision["status"] == "approved"
    assert [it["status"] for it in decision["result"]["items"]] == ["booked"] * 3

    from services.entry_service import list_entries

    entries = {
        e["title"]: e
        for e in await list_entries(user_id, company_id)
        if e.get("source") == "agent"
    }

    boss = entries["打车拜访客户"]
    assert boss["payer"] == "boss_personal"
    assert [line["accountCode"] for line in boss["voucherLines"]["credit"]] == ["2241"]

    employee = entries["买办公用品"]
    assert employee["payer"] == "employee_personal:李四"
    assert [line["accountCode"] for line in employee["voucherLines"]["credit"]] == ["2241"]

    # 对公付款仍是银行账户，别把普通支出也改成挂账
    company = entries["买打印纸"]
    assert company["payer"] == "company_account"
    assert [line["accountCode"] for line in company["voucherLines"]["credit"]] == ["1002"]


@pytest.mark.asyncio
async def test_propose_voucher_tool_normalizes_payer():
    """工具层就收口付款方：模型给历史值 personal、或员工没点名，都落成合法草稿。"""
    await _ensure_schema()
    from agent.tools import ToolContext, _propose_voucher

    ctx = ToolContext(user_id="owner-1", company_id="c-agent-payer-tool")

    legacy = await _propose_voucher(
        ctx, direction="expense", amount=50, title="打车", category="交通费", payer="personal"
    )
    draft = await agent_actions.get_action(legacy["actionId"], company_id=ctx.company_id)
    assert draft["payload"]["payer"] == "boss_personal"

    # 员工垫付但没点名：保留无姓名值，由确认卡片让用户选人，不能编个名字
    unnamed = await _propose_voucher(
        ctx, direction="expense", amount=20, title="买水", category="办公费", payer="employee_personal"
    )
    draft2 = await agent_actions.get_action(unnamed["actionId"], company_id=ctx.company_id)
    assert draft2["payload"]["payer"] == "employee_personal"


@pytest.mark.asyncio
async def test_approve_applies_payer_edit_from_confirm_card():
    """确认卡片里改付款方：edits 带 payer 时按新付款方重算凭证行。"""
    await _ensure_schema()
    company_id = "c-agent-payer-edit"
    user_id = "owner-1"

    action = await agent_actions.create_action(
        company_id=company_id,
        user_id=user_id,
        action_type="propose_voucher",
        title="打车拜访客户",
        payload={
            "direction": "expense",
            "amount": 100,
            "title": "打车拜访客户",
            "category": "交通费",
            "occurDate": "2026-09-21",
            "payer": "company_account",
        },
        evidence={"created_via": "pytest"},
    )

    decision = await agent_actions.approve_action(
        action["id"],
        user_id=user_id,
        company_id=company_id,
        edits={"0": {"payer": "employee_personal:张三"}},
    )
    assert decision["status"] == "approved"

    from services.entry_service import list_entries

    entry = next(
        e
        for e in await list_entries(user_id, company_id)
        if e.get("source") == "agent" and e["title"] == "打车拜访客户"
    )
    assert entry["payer"] == "employee_personal:张三"
    assert [line["accountCode"] for line in entry["voucherLines"]["credit"]] == ["2241"]


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


@pytest.mark.asyncio
async def test_workspace_view_is_injected_into_agent_prompt(monkeypatch):
    """老板/会计视图应进入系统提示词，但视图本身不改变权限。"""
    import agent.loop as loop_mod
    from routers.agent import AgentChatRequest

    async def fake_profile(_company_id):
        return {}

    monkeypatch.setattr(loop_mod, "get_profile", fake_profile)

    req = AgentChatRequest(
        messages=[{"role": "user", "content": "这个月赚了多少"}],
        workspaceView="boss",
    )
    assert req.workspaceView == "boss"

    boss_prompt = await loop_mod._build_system_prompt(
        loop_mod.LoopContext(user_id="u1", company_id="c1", workspace_view="boss")
    )
    accounting_prompt = await loop_mod._build_system_prompt(
        loop_mod.LoopContext(user_id="u1", company_id="c1", workspace_view="accounting")
    )

    assert "老板视图" in boss_prompt
    assert "经营语言" in boss_prompt
    assert "会计视图" in accounting_prompt
    assert "科目编码" in accounting_prompt

