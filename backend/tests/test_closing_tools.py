# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Month isolation, grouped matches and unavailable-data reporting."""
import pytest
from agent import tools


@pytest.mark.asyncio
async def test_bank_tool_uses_transaction_month_and_preserves_match_ids(monkeypatch):
    async def statements(company_id):
        assert company_id == "c-tools"
        return [
            {"id": "june", "transaction_date": "2026-06-30", "created_at": "2026-07-01", "amount": 20},
            {"id": "july", "transaction_date": "2026-07-15", "created_at": "2026-08-01", "amount": 4.95, "matched_entry_ids": ["income", "fee"]},
        ]
    monkeypatch.setattr("services.bank_service.get_statements", statements)
    result = await tools._list_bank_statements(tools.ToolContext("owner", "c-tools"), "2026-07")
    assert result["count"] == 1
    assert result["statements"][0]["id"] == "july"
    assert result["statements"][0]["date"] == "2026-07-15"
    assert result["statements"][0]["matched"] is True
    assert result["statements"][0]["matchedEntryIds"] == ["income", "fee"]


@pytest.mark.asyncio
async def test_bank_tool_matched_filter_and_total(monkeypatch):
    """matched=false 只看未匹配流水；total 是过滤后、截断前的总命中数。"""
    async def statements(company_id):
        return [
            {"id": "a", "transaction_date": "2026-09-07", "amount": 4.95},
            {"id": "b", "transaction_date": "2026-09-12", "amount": 4.95},
            {"id": "c", "transaction_date": "2026-09-01", "amount": 4.95, "matched_entry_ids": ["income", "fee"]},
        ]
    monkeypatch.setattr("services.bank_service.get_statements", statements)
    ctx = tools.ToolContext("owner", "c-tools")

    unmatched = await tools._list_bank_statements(ctx, "2026-09", matched=False)
    assert [row["id"] for row in unmatched["statements"]] == ["a", "b"]
    assert unmatched["count"] == 2 and unmatched["total"] == 2

    matched = await tools._list_bank_statements(ctx, "2026-09", matched=True)
    assert [row["id"] for row in matched["statements"]] == ["c"]

    truncated = await tools._list_bank_statements(ctx, "2026-09", matched=False, limit=1)
    assert truncated["count"] == 1 and truncated["total"] == 2


@pytest.mark.asyncio
async def test_todos_tool_filters_type_level_and_total(monkeypatch):
    rows = [
        {"type": "filing", "level": "high", "title": "逾期申报"},
        {"type": "filing", "level": "medium", "title": "征期临近"},
        {"type": "bank", "level": "high", "title": "流水待处理"},
        {"type": "receipt", "level": "high", "title": "补票据"},
    ]

    async def todos(user_id, company_id):
        return rows
    monkeypatch.setattr("services.todo_service.list_todos", todos)
    ctx = tools.ToolContext("owner", "c-tools")

    filings = await tools._list_todos(ctx, type="filing")
    assert [t["title"] for t in filings["todos"]] == ["逾期申报", "征期临近"]
    assert filings["total"] == 2

    urgent = await tools._list_todos(ctx, type="filing", level="high")
    assert [t["title"] for t in urgent["todos"]] == ["逾期申报"]

    capped = await tools._list_todos(ctx, level="high", limit=2)
    assert capped["count"] == 2 and capped["total"] == 3

    everything = await tools._list_todos(ctx)
    assert everything["count"] == 4 and everything["total"] == 4


@pytest.mark.asyncio
async def test_list_entries_billing_status_filter(monkeypatch):
    rows = [
        {"id": "e1", "title": "已对账", "amount": 5, "occurDate": "2026-09-01", "billingStatus": "reconciled"},
        {"id": "e2", "title": "有发票", "amount": 85, "occurDate": "2026-09-02", "billingStatus": "has_invoice"},
        {"id": "e3", "title": "未标记", "amount": 10, "occurDate": "2026-09-03"},
    ]

    async def entries(user_id, company_id):
        return rows
    monkeypatch.setattr("services.entry_service.list_entries", entries)
    ctx = tools.ToolContext("owner", "c-tools")

    reconciled = await tools._list_entries(ctx, month="2026-09", billing_status="reconciled")
    assert [row["id"] for row in reconciled["entries"]] == ["e1"]

    unmarked = await tools._list_entries(ctx, month="2026-09", billing_status="")
    assert [row["id"] for row in unmarked["entries"]] == ["e3"]
    assert unmarked["total"] == 1


@pytest.mark.asyncio
async def test_difference_tool_scopes_suggestions_and_preserves_group(monkeypatch):
    async def statements(_company):
        return [
            {"id": "july", "transaction_date": "2026-07-15", "description": "财付通", "amount": 4.95},
            {"id": "june", "transaction_date": "2026-06-15", "amount": 10},
            {"id": "matched", "transaction_date": "2026-07-16", "amount": 10, "matched_entry_ids": ["already"]},
        ]
    async def diff(company_id, month):
        assert month == "2026-07"
        return {"bank_only_count": 1}
    async def suggestions(company_id, limit, month):
        assert month == "2026-07"
        return [
            {"statementId": "july", "type": "match", "matchedEntryId": "income", "matchedEntryIds": ["income", "fee"], "matchedEntries": [{"id": "income"}, {"id": "fee"}]},
            {"statementId": "june", "type": "match", "matchedEntryId": "other"},
        ]
    monkeypatch.setattr("services.bank_service.get_statements", statements)
    monkeypatch.setattr("services.bank_service.get_difference_report", diff)
    monkeypatch.setattr("services.bank_service.ai_suggest_matches", suggestions)
    result = await tools._list_unmatched(tools.ToolContext("owner", "c-tools"), "2026-07")
    assert [row["statementId"] for row in result["unmatchedStatements"]] == ["july"]
    assert len(result["aiSuggestions"]) == 1
    assert result["aiSuggestions"][0]["matchedEntryIds"] == ["income", "fee"]
    assert "完整 matchedEntryIds" in result["usage"]


@pytest.mark.asyncio
async def test_difference_tool_surfaces_suggestion_failure(monkeypatch):
    """AI 建议生成失败必须对 agent 可见：空数组与「无可匹配」从外部无法区分，
    会误导对账决策（agent 会误以为流水都核过没问题）。"""
    async def statements(_company):
        return [{"id": "july", "transaction_date": "2026-07-15", "description": "财付通", "amount": 4.95}]
    async def diff(company_id, month):
        return {"bank_only_count": 1}
    async def broken(*_, **__):
        raise RuntimeError("llm unavailable")
    monkeypatch.setattr("services.bank_service.get_statements", statements)
    monkeypatch.setattr("services.bank_service.get_difference_report", diff)
    monkeypatch.setattr("services.bank_service.ai_suggest_matches", broken)
    result = await tools._list_unmatched(tools.ToolContext("owner", "c-tools"), "2026-07")
    assert result["aiSuggestions"] == []
    assert "生成失败" in result["aiSuggestionsError"]
    assert len(result["unmatchedStatements"]) == 1


@pytest.mark.asyncio
async def test_report_does_not_treat_query_failure_as_no_differences(monkeypatch):
    async def checks(*_):
        return {"canClose": True, "periodStatus": "open", "blockingCount": 0, "checks": []}
    async def unavailable(*_):
        raise RuntimeError("database unavailable")
    async def accruals(*_):
        return {}
    async def audit(*_):
        pass
    monkeypatch.setattr(tools, "_run_close_checks", checks)
    monkeypatch.setattr(tools, "_list_unmatched", unavailable)
    monkeypatch.setattr(tools, "_preview_accruals", accruals)
    monkeypatch.setattr("services.audit_service.log_action", audit)
    result = await tools._generate_close_report(tools.ToolContext("owner", "c-tools"), "2026-07")
    assert result["differenceStatus"] == "unavailable"
    assert result["canClose"] is False
    assert "未完成核验" in result["report"]
    assert "不能将查询失败理解为没有差异" in result["report"]


@pytest.mark.asyncio
async def test_report_includes_amortization_and_depreciation(monkeypatch):
    async def checks(*_):
        return {"canClose": True, "periodStatus": "open", "blockingCount": 0, "checks": []}
    async def diff(*_):
        return {"status": "available", "unmatchedStatements": [], "aiSuggestions": []}
    async def accruals(*_):
        return {
            "depreciation": {"pendingCount": 2, "pendingTotal": 1200.0},
            "amortization": {"pendingCount": 1, "pendingTotal": 500.0},
            "salary": {"message": "工资表已审核"},
        }
    async def audit(*_):
        pass
    monkeypatch.setattr(tools, "_run_close_checks", checks)
    monkeypatch.setattr(tools, "_list_unmatched", diff)
    monkeypatch.setattr(tools, "_preview_accruals", accruals)
    monkeypatch.setattr("services.audit_service.log_action", audit)
    result = await tools._generate_close_report(tools.ToolContext("owner", "c-tools"), "2026-07")
    assert "- 折旧：待计提 2 项，合计 1200.0 元" in result["report"]
    assert "- 摊销：待计提 1 项，合计 500.0 元" in result["report"]
    assert "- 工资：工资表已审核" in result["report"]


@pytest.mark.asyncio
async def test_get_advance_balances_tool(monkeypatch):
    """测试股东/员工垫付台账工具调用与账期默认值。"""
    captured = {}

    async def fake_query_advance_balances(company_id, month):
        captured["company_id"] = company_id
        captured["month"] = month
        return {
            "accountCode": "2241",
            "month": month,
            "asOf": f"{month}-30",
            "items": [
                {
                    "payer": "boss_personal",
                    "kind": "boss",
                    "name": "老板本人垫付",
                    "opening": "1000.00",
                    "advance": "500.00",
                    "reimbursed": "200.00",
                    "closing": "1300.00",
                    "count": 3,
                    "lastDate": f"{month}-15",
                }
            ],
            "totals": {
                "opening": "1000.00",
                "advance": "500.00",
                "reimbursed": "200.00",
                "closing": "1300.00",
            },
            "other": {"count": 0, "advance": "0.00", "reimbursed": "0.00", "note": "勾稽说明"},
        }

    monkeypatch.setattr("services.ledger_service.query_advance_balances", fake_query_advance_balances)

    ctx = tools.ToolContext(user_id="u1", company_id="c-advance")

    # 1. 指定月份
    res = await tools._get_advance_balances(ctx, "2026-09")
    assert captured["company_id"] == "c-advance"
    assert captured["month"] == "2026-09"
    assert res["totals"]["closing"] == "1300.00"
    assert len(res["items"]) == 1
    assert res["items"][0]["kind"] == "boss"

    # 2. 未指定月份，默认当前 UTC 月
    res_default = await tools._get_advance_balances(ctx)
    assert len(captured["month"]) == 7
    assert captured["month"].count("-") == 1


@pytest.mark.asyncio
async def test_list_intangible_assets_tool(monkeypatch):
    """测试无形资产台账工具调用与字段紧凑投影。"""
    captured = {}

    async def fake_list_assets(session, **kwargs):
        captured.update(kwargs)
        return {
            "items": [
                {
                    "id": "asset-1",
                    "code": "IP-001",
                    "name": "梭罗记账软件V1.0",
                    "category": "software",
                    "categoryName": "软件",
                    "status": "active",
                    "originalValue": "12000.00",
                    "accumulatedAmortization": "2000.00",
                    "netValue": "10000.00",
                    "usefulLifeMonths": 24,
                    "remainingMonths": 20,
                    "lastAmortizedPeriod": "2026-08",
                    "amortizationEndPeriod": "2028-04",
                    "legalCertNo": "2026SR123456",
                    "expiryDate": "2036-12-31",
                    "rdProjectId": "rd-1",
                    "sourceEntryId": "entry-1",  # 不在投影中
                    "custodian": "张三",  # 不在投影中
                    "departmentId": "dep-1",  # 不在投影中
                }
            ],
            "total": 1,
            "page": kwargs.get("page", 1),
            "pageSize": kwargs.get("page_size", 50),
        }

    # 伪造 session_scope 上下文管理器
    class DummySessionScope:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    monkeypatch.setattr("db.session.session_scope", lambda: DummySessionScope())
    monkeypatch.setattr("services.intangible_asset_service.list_assets", fake_list_assets)

    ctx = tools.ToolContext(user_id="u-ip", company_id="c-ip")
    res = await tools._list_intangible_assets(
        ctx,
        keyword="记账",
        category="software",
        status="active",
        expiring_in_days=90,
        page=1,
        page_size=20,
    )

    assert captured["company_id"] == "c-ip"
    assert captured["user_id"] == "u-ip"
    assert captured["keyword"] == "记账"
    assert captured["category"] == "software"
    assert captured["status"] == "active"
    assert captured["expiring_in_days"] == 90
    assert captured["page"] == 1
    assert captured["page_size"] == 20

    assert res["total"] == 1
    assert res["count"] == 1
    assert len(res["assets"]) == 1
    asset = res["assets"][0]
    assert asset["code"] == "IP-001"
    assert asset["netValue"] == "10000.00"
    assert asset["legalCertNo"] == "2026SR123456"
    assert "sourceEntryId" not in asset
    assert "custodian" not in asset
