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
async def test_legacy_unbound_depreciation_action_expires_without_writing():
    """历史计提动作没有快照绑定，审批时必须过期且不执行任何写入。"""
    await _ensure_schema()  # 固定资产表在 P1 WP-B 起由 ORM create_all 建
    from core.errors import AccountingError

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
    with pytest.raises(AccountingError) as exc:
        await agent_actions.approve_action(
            action["id"], user_id=user_id, company_id=company_id
        )
    assert exc.value.code == "CLOSING_SNAPSHOT_STALE"
    draft = await agent_actions.get_action(action["id"], company_id=company_id)
    assert draft["status"] == "expired"
    assert draft["result"]["code"] == "CLOSING_SNAPSHOT_STALE"


@pytest.mark.asyncio
async def test_legacy_unbound_amortization_action_expires_without_writing():
    """历史摊销动作没有快照绑定，审批时必须过期且不执行任何写入。"""
    await _ensure_schema()
    from core.errors import AccountingError

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
    with pytest.raises(AccountingError) as exc:
        await agent_actions.approve_action(
            action["id"], user_id=user_id, company_id=company_id
        )
    assert exc.value.code == "CLOSING_SNAPSHOT_STALE"
    draft = await agent_actions.get_action(action["id"], company_id=company_id)
    assert draft["status"] == "expired"


@pytest.mark.asyncio
async def test_legacy_unbound_asset_closure_action_expires():
    """历史资产处理动作也必须有结账快照绑定，否则不得执行。"""
    await _ensure_schema()
    from core.errors import AccountingError

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
    with pytest.raises(AccountingError) as exc:
        await agent_actions.approve_action(
            action["id"], user_id=user_id, company_id=company_id
        )
    assert exc.value.code == "CLOSING_SNAPSHOT_STALE"
    draft = await agent_actions.get_action(action["id"], company_id=company_id)
    assert draft["status"] == "expired"


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

    # 网络重试再次批准同一动作 → 返回原结果，不重复记账
    replay = await agent_actions.approve_action(
        action["id"], user_id=user_id, company_id=company_id
    )
    assert replay["status"] == "approved"
    assert replay["result"] == decision["result"]


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


def test_product_agent_receives_closing_draft_tools_but_no_lock_tool():
    """产品内 AI 可检查和起草修复动作，但不能直接锁账。"""
    from agent.tools import (
        PRODUCT_AGENT_DISABLED_TOOLS,
        PRODUCT_AGENT_TOOL_SCHEMAS,
        TOOLS_BY_NAME,
    )

    exposed = {schema["function"]["name"] for schema in PRODUCT_AGENT_TOOL_SCHEMAS}
    assert PRODUCT_AGENT_DISABLED_TOOLS == frozenset()
    assert {
        "run_close_checks",
        "list_unmatched",
        "preview_accruals",
        "preview_asset_actions",
        "propose_match",
        "propose_accrual",
        "propose_asset_closure",
    } <= exposed
    assert "close_period" not in TOOLS_BY_NAME
    assert "close_period" not in exposed


@pytest.mark.asyncio
async def test_product_agent_runs_closing_check_with_product_context(monkeypatch):
    """对话可执行共享检查工具，并明确携带 product 来源。"""
    await _ensure_schema()
    import json

    import agent.loop as loop_mod

    round_no = 0

    async def fake_chat_tools_stream_async(*_args, **_kwargs):
        nonlocal round_no
        round_no += 1
        if round_no == 1:
            yield {
                "type": "final",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc-close",
                        "name": "run_close_checks",
                        "arguments": json.dumps({"month": "2026-10"}),
                    }
                ],
            }
        else:
            yield {
                "type": "final",
                "content": "请回到月度结账向导处理。",
                "tool_calls": [],
            }

    execute_calls = []

    async def execute_check(name, arguments, ctx):
        execute_calls.append((name, arguments, ctx))
        return {
            "schemaVersion": "closing.v1",
            "snapshotHash": "a" * 64,
            "canClose": False,
            "period": "2026-10",
            "manualClose": {
                "url": "https://solobooks.cn/closing-wizard?month=2026-10&step=3",
                "label": "打开网页处理阻断项并重新检查",
                "status": "action_required",
                "requiresLogin": True,
                "message": "当前仍有结账阻断项。",
            },
            "findings": [],
        }

    monkeypatch.setattr(loop_mod, "chat_tools_stream_async", fake_chat_tools_stream_async)
    monkeypatch.setattr(loop_mod, "execute_tool", execute_check)

    ctx = loop_mod.LoopContext(
        user_id="owner-1",
        company_id="c-agent-close-boundary",
        conversation_id="conv-close-boundary",
    )
    events = []
    async for event in loop_mod.run_agent_stream(
        ctx,
        [{"role": "user", "content": "帮我结 2026-10 的账"}],
    ):
        events.append(event)

    assert execute_calls == [
        (
            "run_close_checks",
            {"month": "2026-10"},
            loop_mod.LoopContext(
                user_id="owner-1",
                company_id="c-agent-close-boundary",
                conversation_id="conv-close-boundary",
            ).tool_ctx(),
        )
    ]
    result_event = next(
        event
        for event in events
        if event["type"] == "tool_result" and event["name"] == "run_close_checks"
    )
    assert result_event["ok"] is True
    assert execute_calls[0][2].source == "product"
    assert execute_calls[0][2].conversation_id == "conv-close-boundary"
    link_event = next(event for event in events if event["type"] == "closing_link")
    assert link_event["closingLink"]["url"].endswith(
        "/closing-wizard?month=2026-10&step=3"
    )
    assert link_event["closingLink"]["month"] == "2026-10"
    assert link_event["closingLink"]["requiresLogin"] is True


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


@pytest.mark.asyncio
async def test_propose_company_setup_tool():
    """验证 AI CFO 对话内的主体建账工具。

    期初余额曾也在这里：`propose_opening_balance` 让老板口述科目余额、
    对话侧生成期初卡。该路径已整体移除——期初是整张表的事，口述既容易听错
    科目，也承载不了固定资产卡片与往来单位明细。现在只走期初建账工作台。
    """
    from agent.tools import ToolContext, execute_tool

    await _ensure_schema()
    ctx = ToolContext(user_id="owner-onboard", company_id="c-onboard-1")

    setup_res = await execute_tool(
        "propose_company_setup",
        {
            "onboarding_type": "migration",
            "name": "杭州迁入测试科技有限公司",
            "tax_id": "91330100MA27XXXXXX",
            "start_date": "2026-10-01",
            "industry": "软件和信息技术服务业",
        },
        ctx,
    )
    assert setup_res["status"] == "pending"
    assert setup_res["onboardingType"] == "migration"


@pytest.mark.asyncio
async def test_company_setup_approval_requires_tax_id():
    """对话建账卡与新增主体表单同口径：税号必填，缺税号不得建出主体。"""
    from fastapi import HTTPException

    await _ensure_schema()
    action = await agent_actions.create_action(
        company_id="c-agent-setup-notax",
        user_id="owner-setup",
        action_type="propose_company_setup",
        title="新公司从零建账确认",
        payload={
            "onboardingType": "new",
            "name": "杭州无税号测试有限公司",
            "taxId": "",
            "startDate": "2026-11-01",
        },
        evidence={"created_via": "pytest"},
    )

    with pytest.raises(HTTPException) as exc:
        await agent_actions.approve_action(
            action["id"], user_id="owner-setup", company_id="c-agent-setup-notax"
        )

    assert exc.value.status_code == 422
    assert "统一社会信用代码" in str(exc.value.detail)
    draft = await agent_actions.get_action(action["id"], company_id="c-agent-setup-notax")
    assert draft["status"] == "pending"


@pytest.mark.asyncio
async def test_company_setup_from_demo_creates_company_and_token():
    """演示账套确认建账卡要真的建出正式账套并返回切换 token。

    这条路径曾因 `services.jwt_service` 改名后残留旧 import 直接 ModuleNotFoundError，
    前端只看到「确认失败」，所以除了税号校验还要守住创建链路本身。
    """
    await _ensure_schema()
    from services.account_service import (
        create_company,
        get_or_create_user_by_phone,
        set_user_current_company,
    )
    from services.token_service import decode_access_token

    user, _, _ = await get_or_create_user_by_phone("13900000009")
    demo = await create_company(user["user_id"], "演示账套", {"isDemo": True})
    await set_user_current_company(user["user_id"], demo["company_id"])
    action = await agent_actions.create_action(
        company_id=demo["company_id"],
        user_id=user["user_id"],
        action_type="propose_company_setup",
        title="新公司从零建账确认",
        payload={
            "onboardingType": "new",
            "name": "杭州对话建账测试有限公司",
            "taxId": "91330100MA2TEST001",
            "startDate": "2026-11-01",
        },
        evidence={"created_via": "pytest"},
    )

    decision = await agent_actions.approve_action(
        action["id"], user_id=user["user_id"], company_id=demo["company_id"]
    )

    result = decision["result"]
    assert result["switchedFromDemo"] is True
    assert result["companyId"] != demo["company_id"]
    token_payload = decode_access_token(result["accessToken"])
    assert token_payload["company_id"] == result["companyId"]


# ---------------------------------------------------------------------------
# 税务政策回答规范与政策要点卡（提示词纪律）
# ---------------------------------------------------------------------------


def test_agent_prompt_contains_tax_policy_discipline():
    """系统提示词包含政策回答规范、要点卡锚点与反编造条款。"""
    from datetime import datetime

    from agent.prompts import AGENT_SYSTEM_PROMPT
    from services.tax_policy_card import POLICY_CARD_MD, POLICY_CARD_VERIFIED_AT

    assert "## 税务政策回答规范" in AGENT_SYSTEM_PROMPT
    assert "## 政策要点卡" in AGENT_SYSTEM_PROMPT
    assert "禁止编造政策文号" in AGENT_SYSTEM_PROMPT
    assert "不得凭记忆作答" in AGENT_SYSTEM_PROMPT
    assert "具体以主管税务机关执行口径为准" in AGENT_SYSTEM_PROMPT
    assert "manualClose.url" in AGENT_SYSTEM_PROMPT
    assert "禁止输出 `/closing-wizard?...` 这种相对路径" in AGENT_SYSTEM_PROMPT

    # 要点卡内容锚点（文号与有效期已经 cn-tax-sources 流程核验后冻结）
    assert "2023 年第 19 号" in POLICY_CARD_MD
    assert "2023 年第 12 号" in POLICY_CARD_MD
    assert "2023 年第 37 号" in POLICY_CARD_MD
    assert "2027-12-31" in POLICY_CARD_MD
    assert "增值税法" in POLICY_CARD_MD
    datetime.strptime(POLICY_CARD_VERIFIED_AT, "%Y-%m-%d")

    # 模板占位符与 format 注入闭环：loop._build_system_prompt 以 card=/opening_url= 填充
    assert "{card}" in AGENT_SYSTEM_PROMPT
    assert "{opening_url}" in AGENT_SYSTEM_PROMPT
    rendered = AGENT_SYSTEM_PROMPT.format(
        today="2026-10-30",
        weekday="周五",
        month="2026-10",
        card=POLICY_CARD_MD,
        opening_url="https://solobooks.cn/mine/opening",
    )
    assert "财政部 税务总局公告 2023 年第 19 号" in rendered
    # 期初口述路径已移除：提示词只给工作台入口，不再有对话内生成期初卡的说法
    assert "https://solobooks.cn/mine/opening" in rendered
    assert "propose_opening_balance" not in AGENT_SYSTEM_PROMPT
    # 回归「回复里还在教口述/传表两选一」：期初一律引到工作台，对话里不追问科目明细
    assert "上传期初表" not in AGENT_SYSTEM_PROMPT
    assert "不要在对话里追问期初科目明细" in AGENT_SYSTEM_PROMPT
    assert "期初一律引到工作台" in AGENT_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_system_prompt_general_taxpayer_branch():
    """一般纳税人画像注入"小规模政策不适用"分支；小规模不注入。"""
    await _ensure_schema()
    from agent.loop import LoopContext, _build_system_prompt
    from agent.memory import save_profile

    general_id = "c-agent-policy-general"
    await save_profile(general_id, {"taxpayerType": "general", "industry": ""})
    prompt = await _build_system_prompt(LoopContext(user_id="owner-1", company_id=general_id))
    assert "该公司为增值税一般纳税人" in prompt
    assert "小规模纳税人减免政策（1% 征收率、月销售额 10 万元以下免征）不适用" in prompt

    small_id = "c-agent-policy-small"
    await save_profile(small_id, {"taxpayerType": "small_scale", "industry": ""})
    prompt_small = await _build_system_prompt(LoopContext(user_id="owner-1", company_id=small_id))
    assert "该公司为增值税一般纳税人" not in prompt_small


def test_tool_result_compaction_always_returns_valid_json():
    """长工具结果只能按结构裁剪，不能把残缺 JSON 交给模型猜测。"""
    import json

    from agent.loop import _tool_result_content
    from agent.prompts import TOOL_RESULT_MAX_CHARS

    result = {
        "count": 100,
        "items": [
            {
                "statementId": f"stmt-{index}",
                "description": "很长的银行流水描述" * 300,
            }
            for index in range(100)
        ],
    }
    content = _tool_result_content(result)
    parsed = json.loads(content)

    assert len(content) <= TOOL_RESULT_MAX_CHARS
    assert parsed["count"] == 100
    assert isinstance(parsed["items"], list)


@pytest.mark.asyncio
async def test_statement_vouchers_use_database_fields(monkeypatch):
    """银行流水凭证只接收 ID 与分类，金额/日期/摘要由数据库记录决定。"""
    await _ensure_schema()
    import services.bank_service as bank_service
    import services.category_service as category_service
    from agent.tools import ToolContext, _propose_statement_vouchers

    statements = [
        {
            "id": "stmt-in",
            "amount": 50000.0,
            "transaction_date": "2026-05-22",
            "description": "入账 | 附言: 实缴出资款 (对方: 张三)",
            "matched_entry_ids": [],
            "matched_entry_id": None,
        },
        {
            "id": "stmt-out",
            "amount": -500.0,
            "transaction_date": "2026-05-31",
            "description": "出账 | 附言: 往来款 (对方: 李四)",
            "matched_entry_ids": [],
            "matched_entry_id": None,
        },
    ]

    async def fake_get_statements(_company_id, _statement_id=None):
        return statements

    async def fake_list_categories(_company_id):
        return [
            {"name": "股东投入", "direction": "owner_transfer"},
            {"name": "往来款", "direction": "expense"},
        ]

    monkeypatch.setattr(bank_service, "get_statements", fake_get_statements)
    monkeypatch.setattr(category_service, "list_categories", fake_list_categories)

    ctx = ToolContext(user_id="owner-1", company_id="c-agent-statement-vouchers")
    result = await _propose_statement_vouchers(
        ctx,
        proposals=[
            {"statementId": "stmt-in", "category": "股东投入"},
            {"statementId": "stmt-out", "category": "往来款"},
        ],
    )

    assert result["status"] == "pending"
    assert result["count"] == 2
    assert result["totals"] == {
        "signedNet": 49500.0,
        "inflow": 50000.0,
        "outflow": 500.0,
        "absolute": 50500.0,
    }
    assert result["items"][0]["amount"] == 50000.0
    assert result["items"][0]["occurDate"] == "2026-05-22"
    assert result["items"][0]["title"] == "实缴出资款"
    assert result["items"][1]["amount"] == 500.0

    draft = await agent_actions.get_action(result["actionId"], company_id=ctx.company_id)
    assert draft["payload"]["items"][0]["statementId"] == "stmt-in"
    assert draft["payload"]["items"][0]["sourceType"] == "bank_statement"


@pytest.mark.asyncio
async def test_general_voucher_tool_rejects_statement_source():
    """通用记账工具不得接收银行流水 ID，避免模型自由改写流水字段。"""
    from agent.tools import ToolContext, _propose_vouchers

    ctx = ToolContext(user_id="owner-1", company_id="c-agent-statement-guard")
    with pytest.raises(ValueError, match="propose_statement_vouchers"):
        await _propose_vouchers(
            ctx,
            proposals=[
                {
                    "direction": "income",
                    "amount": 100,
                    "title": "伪造流水",
                    "category": "营业外收入",
                    "statementId": "stmt-1",
                }
            ],
        )


@pytest.mark.asyncio
async def test_statement_voucher_rejects_authoritative_field_edits():
    """审批时不允许改写银行流水的金额、日期、摘要或方向。"""
    from fastapi import HTTPException

    await _ensure_schema()
    action = await agent_actions.create_action(
        company_id="c-agent-statement-edit",
        user_id="owner-1",
        action_type="propose_vouchers",
        title="银行流水待确认记账（1 笔）",
        payload={
            "items": [
                {
                    "direction": "income",
                    "amount": 50000,
                    "title": "实缴出资款",
                    "category": "股东投入",
                    "occurDate": "2026-05-22",
                    "statementId": "stmt-1",
                    "sourceType": "bank_statement",
                }
            ]
        },
        evidence={"created_via": "pytest"},
    )

    with pytest.raises(HTTPException) as exc:
        await agent_actions.approve_action(
            action["id"],
            user_id="owner-1",
            company_id="c-agent-statement-edit",
            edits={"0": {"amount": 999999}},
        )

    assert exc.value.status_code == 422
    draft = await agent_actions.get_action(action["id"], company_id="c-agent-statement-edit")
    assert draft["status"] == "pending"

