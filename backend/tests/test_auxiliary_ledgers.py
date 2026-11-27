# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""辅助核算（职员维度扩展 + 核算项目三表 + 日常记账/草稿清空 + MCP 只读工具）回归测试。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from core.errors import AccountingError
from db.models import JournalLine, RdProject, RdSalaryAllocation
from db.session import session_scope
from services.accounting_core_service import create_entry, ensure_tenant, post_journal, reverse_journal
from services.auxiliary_ledger_service import (
    UNASSIGNED_KEY,
    query_auxiliary_balances,
    query_auxiliary_combination,
    query_auxiliary_sub_ledger,
)
from services.auxiliary_service import (
    assign_entry_auxiliary,
    create_master,
    deactivate_master,
    delete_master,
    list_master,
    update_master,
)
from services.ledger_book_service import build_ledger_book
from services.ledger_service import query_partner_balances


async def _ensure_tables() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _setup_company(company_id: str) -> None:
    await _ensure_tables()
    async with session_scope() as session:
        await ensure_tenant(session, company_id, "owner-1")


async def _create_and_post(
    company_id: str,
    entry_id: str,
    occur_date: str,
    debit_lines: list[dict],
    credit_lines: list[dict],
    *,
    title: str = "测试业务",
    direction: str = "expense",
    category: str = "办公费",
    payer: str = "company_account",
    post: bool = True,
    period: str | None = None,
    allow_backfill: bool = False,
    backfill_reason: str | None = None,
):
    total = sum((Decimal(str(line["amount"])) for line in debit_lines), Decimal("0.00"))
    payload = {
        "id": entry_id,
        "direction": direction,
        "amount": f"{total:.2f}",
        "title": title,
        "category": category,
        "occurDate": occur_date,
        "payer": payer,
        "sourceType": "manual",
        "voucherLines": {"debit": debit_lines, "credit": credit_lines},
    }
    if period:
        payload["period"] = period
    if allow_backfill:
        payload["allowBackfill"] = True
        payload["backfillReason"] = backfill_reason or "跨期补充入账"
    async with session_scope() as session:
        entry = await create_entry(
            session,
            payload,
            company_id=company_id,
            user_id="owner-1",
            request_id=f"{entry_id}-req",
            confirm=post,
        )
        if post and entry.status in {"draft", "pending_review"}:
            entry = await post_journal(
                session,
                entry.id,
                company_id=company_id,
                user_id="owner-1",
                request_id=f"{entry_id}-post",
            )
        return entry


@pytest.mark.asyncio
async def test_employee_master_crud_and_salary_reference_guard():
    company_id = "c-aux-emp-crud"
    await _setup_company(company_id)

    emp = await create_master(
        company_id,
        "employee",
        {"name": "张三", "department": "研发部"},
        user_id="owner-1",
    )
    assert emp["code"].startswith("EM")
    assert emp["department"] == "研发部"
    assert emp["isActive"] is True

    updated = await update_master(
        company_id, "employee", emp["id"], {"department": "产品部", "code": "EM888"}
    )
    assert updated["department"] == "产品部"
    assert updated["code"] == "EM888"

    await deactivate_master(company_id, "employee", emp["id"])
    assert (await list_master(company_id, "employee"))["total"] == 0
    assert (await list_master(company_id, "employee", include_inactive=True))["total"] == 1

    # 激活后挂一条研发工时分摊引用，删除必须被拦
    await update_master(company_id, "employee", emp["id"], {"isActive": True})
    async with session_scope() as session:
        rd_proj = RdProject(
            id="rdp-emp-guard",
            company_id=company_id,
            code="RD01",
            name="研发项目",
            start_date=date(2026, 1, 1),
            status="active",
            created_by="owner-1",
        )
        session.add(rd_proj)
        await session.flush()
        session.add(
            RdSalaryAllocation(
                company_id=company_id,
                month="2026-10",
                employee_id=emp["id"],
                project_id=rd_proj.id,
                allocation_rate=Decimal("0.500000"),
                allocation_basis="工时记录",
                created_by="owner-1",
            )
        )

    with pytest.raises(AccountingError) as in_use:
        await delete_master(company_id, "employee", emp["id"])
    assert in_use.value.code == "AUXILIARY_IN_USE"


@pytest.mark.asyncio
async def test_employee_advance_auto_binds_employee_id():
    company_id = "c-aux-emp-adv"
    await _setup_company(company_id)
    emp = await create_master(company_id, "employee", {"name": "李四"}, user_id="owner-1")

    await _create_and_post(
        company_id,
        "emp-adv-1",
        "2026-10-12",
        [{"accountCode": "5602004", "accountName": "管理费用-办公费", "amount": "200.00"}],
        [{"accountCode": "2241", "accountName": "其他应付款", "amount": "200.00"}],
        payer="employee_personal:李四",
    )

    async with session_scope() as session:
        lines = (
            await session.execute(
                select(JournalLine).where(JournalLine.journal_entry_id == "emp-adv-1")
            )
        ).scalars().all()
    by_code = {line.account_code: line for line in lines}
    assert by_code["2241"].auxiliary.get("employee_id") == emp["id"]
    assert by_code["2241"].auxiliary.get("employee_name") == "李四"
    assert by_code["2241"].auxiliary.get("partner_name") == "李四"


@pytest.mark.asyncio
async def test_auxiliary_three_reports_reconcile_and_exclude_same_period_reversals():
    company_id = "c-aux-three-reports"
    await _setup_company(company_id)

    dept_rd = await create_master(company_id, "department", {"name": "研发部"}, user_id="owner-1")
    dept_mkt = await create_master(company_id, "department", {"name": "市场部"}, user_id="owner-1")
    proj_a = await create_master(company_id, "project", {"name": "项目A"}, user_id="owner-1")
    proj_b = await create_master(company_id, "project", {"name": "项目B"}, user_id="owner-1")
    emp_z = await create_master(company_id, "employee", {"name": "张工"}, user_id="owner-1")

    # 9 月期初发生：研发部 + 项目A + 张工 300.00
    await _create_and_post(
        company_id,
        "three-sep-1",
        "2026-09-18",
        [
            {
                "accountCode": "5602004",
                "accountName": "管理费用-办公费",
                "amount": "300.00",
                "auxiliary": {
                    "department_id": dept_rd["id"],
                    "project_id": proj_a["id"],
                    "employee_id": emp_z["id"],
                },
            }
        ],
        [{"accountCode": "1002", "accountName": "银行存款", "amount": "300.00"}],
    )

    # 10 月业务 1：市场部 + 项目B 500.00
    await _create_and_post(
        company_id,
        "three-oct-1",
        "2026-10-05",
        [
            {
                "accountCode": "5601",
                "accountName": "销售费用",
                "amount": "500.00",
                "auxiliary": {"department_id": dept_mkt["id"], "project_id": proj_b["id"]},
            }
        ],
        [{"accountCode": "1002", "accountName": "银行存款", "amount": "500.00"}],
    )

    # 10 月业务 2：同期间冲销对（400.00 被当月红冲），不得虚增 10 月发生额
    rev_target = await _create_and_post(
        company_id,
        "three-oct-rev-orig",
        "2026-10-08",
        [
            {
                "accountCode": "5602004",
                "accountName": "管理费用-办公费",
                "amount": "400.00",
                "auxiliary": {"department_id": dept_rd["id"], "project_id": proj_a["id"]},
            }
        ],
        [{"accountCode": "1002", "accountName": "银行存款", "amount": "400.00"}],
    )
    async with session_scope() as session:
        await reverse_journal(
            session,
            rev_target.id,
            company_id=company_id,
            user_id="owner-1",
            request_id="rev-oct-1",
            reason="录入有误冲销",
            reversal_date=date(2026, 10, 9),
        )

    # 10 月业务 3：未挂部门/项目的一笔 5602 费用 150.00
    await _create_and_post(
        company_id,
        "three-oct-unassigned",
        "2026-10-15",
        [{"accountCode": "5602004", "accountName": "管理费用-办公费", "amount": "150.00"}],
        [{"accountCode": "1002", "accountName": "银行存款", "amount": "150.00"}],
    )

    # 1) 核算项目余额表（按部门，查全部科目）：10 月仅市场部新增 500 + 未指定 150，研发部同期间冲销不虚增
    dept_bal = await query_auxiliary_balances(company_id, "department", "2026-10")
    by_dept = {row["itemName"]: row for row in dept_bal["items"]}
    assert by_dept["研发部"]["opening"] == "300.00"
    assert by_dept["研发部"]["debit"] == "0.00"
    assert by_dept["研发部"]["credit"] == "0.00"
    assert by_dept["研发部"]["closing"] == "300.00"
    assert by_dept["市场部"]["debit"] == "500.00"
    assert dept_bal["unassigned"] is not None
    assert dept_bal["unassigned"]["debit"] == "150.00"
    assert dept_bal["totalDebit"] == "650.00"

    # 与电子账簿 5602 余额逐分勾稽
    bal_5602 = await query_auxiliary_balances(company_id, "department", "2026-10", account_code="5602")
    book = await build_ledger_book(company_id, month="2026-10", include_zero_rows=False)
    book_5602_rows = [
        row for row in book["balance"]["items"] if row["accountCode"].startswith("5602") and not row["isParent"]
    ]
    assert Decimal(bal_5602["totalOpening"]) == sum((Decimal(r["openingBalance"]) for r in book_5602_rows), Decimal("0.00"))
    assert Decimal(bal_5602["totalDebit"]) == sum((Decimal(r["debitTotal"]) for r in book_5602_rows), Decimal("0.00"))
    assert Decimal(bal_5602["totalCredit"]) == sum((Decimal(r["creditTotal"]) for r in book_5602_rows), Decimal("0.00"))
    assert Decimal(bal_5602["totalClosing"]) == sum((Decimal(r["closingBalance"]) for r in book_5602_rows), Decimal("0.00"))

    # 2) 核算项目明细账：研发部在 10 月应能看到被冲销与红冲两笔完整审计留痕，但 periodDebit 剔除后为 0.00，期末仍为 300.00
    rd_sub = await query_auxiliary_sub_ledger(company_id, "department", dept_rd["id"], "2026-10")
    assert rd_sub["opening"] == "300.00"
    assert len(rd_sub["lines"]) == 2
    assert rd_sub["periodDebit"] == "0.00"
    assert rd_sub["periodCredit"] == "0.00"
    assert rd_sub["closing"] == "300.00"

    # 未指定项可钻取排查
    unassigned_sub = await query_auxiliary_sub_ledger(company_id, "department", UNASSIGNED_KEY, "2026-10")
    assert len(unassigned_sub["lines"]) == 1
    assert unassigned_sub["lines"][0]["debit"] == "150.00"

    # 3) 核算项目组合表：部门 × 项目（debit 口径）与科目 × 职员（closing 口径）行列守恒
    combo_dp = await query_auxiliary_combination(company_id, "department", "project", "2026-10", metric="debit")
    assert combo_dp["grandTotal"] == "650.00"
    row_mkt = next(r for r in combo_dp["rows"] if r["name"] == "市场部")
    assert row_mkt["cells"][proj_b["id"]] == "500.00"
    assert row_mkt["rowTotal"] == "500.00"
    sum_row_totals = sum(Decimal(r["rowTotal"]) for r in combo_dp["rows"])
    sum_col_totals = sum(Decimal(v) for v in combo_dp["colTotals"].values())
    assert sum_row_totals == Decimal(combo_dp["grandTotal"])
    assert sum_col_totals == Decimal(combo_dp["grandTotal"])

    combo_ae = await query_auxiliary_combination(company_id, "account", "employee", "2026-10", metric="closing")
    row_5602004 = next(r for r in combo_ae["rows"] if r["id"] == "5602004")
    assert row_5602004["cells"][emp_z["id"]] == "300.00"
    assert row_5602004["cells"][UNASSIGNED_KEY] == "150.00"
    assert row_5602004["rowTotal"] == "450.00"

    # 旧兼容入口 query_partner_balances 同样受惠于冲销对剔除
    compat = await query_partner_balances(company_id, "5602", "2026-10")
    assert compat["totalDebit"] == "150.00"


@pytest.mark.asyncio
async def test_draft_assign_and_clear_auxiliary_plus_agent_action_and_mcp_tools():
    company_id = "c-aux-flow"
    await _setup_company(company_id)

    partner = await create_master(company_id, "partner", {"name": "杭州乙供应商"}, user_id="owner-1")
    dept = await create_master(company_id, "department", {"name": "运营部"}, user_id="owner-1")
    proj = await create_master(company_id, "project", {"name": "增长专项"}, user_id="owner-1")
    emp = await create_master(company_id, "employee", {"name": "王五"}, user_id="owner-1")

    # 1) 草稿凭证先挂 部门 + 职员，再显式清除 职员
    await _create_and_post(
        company_id,
        "draft-aux-1",
        "2026-10-10",
        [{"accountCode": "5602004", "accountName": "管理费用-办公费", "amount": "120.00"}],
        [{"accountCode": "1002", "accountName": "银行存款", "amount": "120.00"}],
        post=False,
    )
    res_set = await assign_entry_auxiliary(
        company_id,
        "owner-1",
        "draft-aux-1",
        {"department_id": dept["id"], "employee_id": emp["id"]},
    )
    assert res_set["updatedLines"] == 1

    res_clear = await assign_entry_auxiliary(
        company_id,
        "owner-1",
        "draft-aux-1",
        {"department_id": dept["id"], "employee_id": None},
    )
    assert res_clear["updatedLines"] == 1
    async with session_scope() as session:
        lines = (
            await session.execute(
                select(JournalLine).where(JournalLine.journal_entry_id == "draft-aux-1")
            )
        ).scalars().all()
    by_code = {line.account_code: line for line in lines}
    assert by_code["5602004"].auxiliary.get("department_id") == dept["id"]
    assert "employee_id" not in (by_code["5602004"].auxiliary or {})

    # 2) Agent 日常记一笔确认卡：approve_action 带 edits 辅助核算维度入账
    from agent.actions import approve_action, create_action

    action = await create_action(
        company_id=company_id,
        user_id="owner-1",
        action_type="propose_voucher",
        title="采购办公耗材",
        payload={
            "direction": "expense",
            "amount": 250.0,
            "title": "采购办公耗材",
            "category": "办公",
            "occurDate": "2026-10-20",
            "payer": "company_account",
        },
        evidence={"created_via": "agent_chat"},
    )
    approved = await approve_action(
        action["id"],
        user_id="owner-1",
        company_id=company_id,
        edits={
            "0": {
                "department_id": dept["id"],
                "project_id": proj["id"],
                "employee_id": emp["id"],
                "partner_id": partner["id"],
            }
        },
    )
    booked_id = approved["result"]["items"][0]["entryId"]
    async with session_scope() as session:
        booked_lines = (
            await session.execute(select(JournalLine).where(JournalLine.journal_entry_id == booked_id))
        ).scalars().all()
        if approved["result"]["items"][0].get("voucherStatus") in {"draft", "pending_review"}:
            await post_journal(
                session, booked_id, company_id=company_id, user_id="owner-1", request_id="post-agent-aux"
            )
    expense_line = next(l for l in booked_lines if l.direction == "debit")
    assert expense_line.auxiliary.get("department_id") == dept["id"]
    assert expense_line.auxiliary.get("project_id") == proj["id"]
    assert expense_line.auxiliary.get("employee_id") == emp["id"]
    # 5602 不在 partner 白名单内，partner 被安全裁掉
    assert "partner_id" not in (expense_line.auxiliary or {})

    # 3) MCP / Agent 四个只读工具调用验证
    from agent.tools import ToolContext, execute_tool

    ctx = ToolContext(user_id="owner-1", company_id=company_id)
    emp_list = await execute_tool("list_auxiliary_items", {"kind": "employee"}, ctx)
    assert emp_list["total"] == 1
    bal = await execute_tool("get_auxiliary_balances", {"kind": "employee", "month": "2026-10"}, ctx)
    assert bal["items"][0]["closing"] == "250.00"
    sub = await execute_tool(
        "get_auxiliary_sub_ledger",
        {"kind": "employee", "item_id": emp["id"], "month": "2026-10"},
        ctx,
    )
    assert len(sub["lines"]) == 1
    assert sub["closing"] == "250.00"
    combo = await execute_tool(
        "get_auxiliary_combination",
        {"row_dim": "department", "col_dim": "project", "month": "2026-10", "metric": "debit"},
        ctx,
    )
    assert combo["grandTotal"] == "250.00"


def test_auxiliary_http_endpoints_end_to_end(client, auth_headers):
    """验证 /api/employees、/api/ledger/accounts(auxiliaryKinds)、三表接口与 confirm/clear 路由。"""
    headers = auth_headers

    # 1) 科目表下发 auxiliaryKinds
    accts = client.get("/api/ledger/accounts", headers=headers)
    assert accts.status_code == 200
    by_code = {a["code"]: a for a in accts.json()["accounts"]}
    assert set(by_code["3001"]["auxiliaryKinds"]) == {"partner"}
    assert {"department", "project", "employee"} <= set(by_code["5602004"]["auxiliaryKinds"])

    # 2) 创建职员/部门档案（不用"研发部"并在末尾停用，避免污染共用 c1 账套的研发工时测试）
    emp_resp = client.post(
        "/api/employees",
        json={"name": "HTTP测试职员", "department": "运营部"},
        headers=headers,
    )
    assert emp_resp.status_code == 200
    emp_id = emp_resp.json()["id"]

    dept_resp = client.post(
        "/api/departments",
        json={"name": "HTTP测试部门"},
        headers=headers,
    )
    assert dept_resp.status_code == 200
    dept_id = dept_resp.json()["id"]

    # 3) /api/record/confirm 带 employee_id & department_id 入账
    confirm_resp = client.post(
        "/api/record/confirm",
        json={
            "direction": "expense",
            "amount": 188.0,
            "title": "研发差旅",
            "category": "差旅",
            "occurDate": "2026-10-22",
            "payer": "company_account",
            "department_id": dept_id,
            "employee_id": emp_id,
        },
        headers=headers,
    )
    assert confirm_resp.status_code == 200
    entry_id = confirm_resp.json()["id"]

    # 4) 三表 HTTP 接口查询
    bal_resp = client.get(
        "/api/ledger/auxiliary-balances",
        params={"kind": "employee", "month": "2026-10"},
        headers=headers,
    )
    assert bal_resp.status_code == 200
    assert any(row["itemId"] == emp_id and row["debit"] == "188.00" for row in bal_resp.json()["items"])

    sub_resp = client.get(
        "/api/ledger/auxiliary-sub-ledger",
        params={"kind": "employee", "item_id": emp_id, "month": "2026-10"},
        headers=headers,
    )
    assert sub_resp.status_code == 200
    assert any(line["entryId"] == entry_id for line in sub_resp.json()["lines"])

    combo_resp = client.get(
        "/api/ledger/auxiliary-combination",
        params={"row_dim": "department", "col_dim": "employee", "month": "2026-10", "metric": "debit"},
        headers=headers,
    )
    assert combo_resp.status_code == 200
    row_dept = next(r for r in combo_resp.json()["rows"] if r["id"] == dept_id)
    assert row_dept["cells"][emp_id] == "188.00"

    # 停用测试职员，避免影响共用 c1 账套的其他测试
    deact = client.post(f"/api/employees/{emp_id}/deactivate", headers=headers)
    assert deact.status_code == 200
