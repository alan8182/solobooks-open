# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""WP-A 账表补齐：序时账 / 多栏账 / 试算平衡统一口径 / 所有者权益变动表 / 明细账往来过滤。

口径基线与 test_ledger_service 相同：法定账表按记账期间（period_start）归集；
多栏账对父科目始终按前缀归集子级（不限于 AGGREGATE_PARENT_CODES，如 5301）。
"""
from __future__ import annotations

import pytest

from services.accounting_core_service import create_entry, ensure_tenant
from services.equity_service import get_equity_changes
from services.ledger_service import (
    get_balance_summary,
    query_chronological,
    query_multi_column,
    query_sub_ledger,
)


async def _ensure_tables() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _setup_company(company_id: str, user_id: str = "owner-1") -> None:
    from db.session import session_scope

    await _ensure_tables()
    async with session_scope() as session:
        await ensure_tenant(session, company_id, user_id)


async def _post(
    company_id: str,
    entry_id: str,
    occur_date: str,
    summary: str,
    debit: list[dict],
    credit: list[dict],
    user_id: str = "owner-1",
) -> None:
    from db.session import session_scope

    total = sum(float(line["amount"]) for line in debit)
    async with session_scope() as session:
        await create_entry(
            session,
            {
                "id": f"{company_id}-{entry_id}",
                "direction": "expense",
                "amount": f"{total:.2f}",
                "title": summary,
                "category": "其他支出",
                "occurDate": occur_date,
                "voucherLines": {"debit": debit, "credit": credit},
            },
            company_id=company_id,
            user_id=user_id,
            request_id=f"{company_id}-{entry_id}-req",
            confirm=True,
        )


def _line(code: str, name: str, amount: str, **extra) -> dict:
    line = {"accountCode": code, "accountName": name, "amount": amount}
    line.update(extra)
    return line


@pytest.mark.asyncio
async def test_chronological_lists_only_target_month():
    company_id = "c-wpa-chro"
    await _setup_company(company_id)
    await _post(
        company_id, "sep", "2026-09-20", "9月办公费",
        [_line("5602004", "管理费用-办公费", "100.00")],
        [_line("1002", "银行存款", "100.00")],
    )
    await _post(
        company_id, "oct-a", "2026-10-08", "10月差旅",
        [_line("5602006", "管理费用-差旅费", "200.00")],
        [_line("1002", "银行存款", "200.00")],
    )
    await _post(
        company_id, "oct-b", "2026-10-15", "10月办公",
        [_line("5602004", "管理费用-办公费", "50.00")],
        [_line("1002", "银行存款", "50.00")],
    )

    result = await query_chronological(company_id, "2026-10")
    assert result["voucherCount"] == 2
    assert result["isBalanced"] is True
    assert result["totalDebit"] == "250.00"
    assert result["totalCredit"] == "250.00"
    assert all(row["period"] == "2026-10-01" for row in result["items"])
    # 同一凭证的借贷行相邻，凭证按 voucher_no 升序
    refs = [row["voucherRef"] for row in result["items"]]
    assert refs == sorted(refs, key=lambda v: (v or ""))
    assert [row["accountCode"] for row in result["items"]][:2] == ["5602006", "1002"]


@pytest.mark.asyncio
async def test_multi_column_debit_mode_buckets_children():
    company_id = "c-wpa-mc"
    await _setup_company(company_id)
    await _post(
        company_id, "e1", "2026-10-08", "费用一批",
        [_line("5602001", "管理人员职工薪酬", "200.00"), _line("5602006", "管理费用-差旅费", "100.00")],
        [_line("1002", "银行存款", "300.00")],
    )
    await _post(
        company_id, "e2", "2026-10-20", "再加一笔",
        [_line("5602001", "管理人员职工薪酬", "50.00")],
        [_line("1002", "银行存款", "50.00")],
    )

    result = await query_multi_column(company_id, "5602", "2026-10")
    assert result["direction"] == "debit"
    column_codes = {col["code"] for col in result["columns"]}
    assert {"5602001", "5602006"} <= column_codes
    assert result["columnTotals"]["5602001"] == "250.00"
    assert result["columnTotals"]["5602006"] == "100.00"
    assert len(result["items"]) == 2
    # 对方科目（银行存款）行计入凭证侧借贷合计，不落栏
    assert result["debitTotal"] == "350.00"
    assert result["creditTotal"] == "350.00"
    assert result["mainTotal"] == "350.00"


@pytest.mark.asyncio
async def test_multi_column_credit_mode_for_income_and_no_children_hint():
    company_id = "c-wpa-mc-cr"
    await _setup_company(company_id)
    await _post(
        company_id, "donation", "2026-10-10", "收到捐赠",
        [_line("1002", "银行存款", "500.00")],
        [_line("5301001", "营业外收入-捐赠利得", "500.00")],
    )
    # 5301 不在 AGGREGATE_PARENT_CODES，多栏账仍必须归集子级
    result = await query_multi_column(company_id, "5301", "2026-10")
    assert result["direction"] == "credit"
    assert result["columnTotals"].get("5301001") == "500.00"
    assert result["mainTotal"] == "500.00"

    # 5051 无子科目 → 提示而非空报错（5001 自咨询服务/技术服务映射后已有明细子级）
    hint = await query_multi_column(company_id, "5051", "2026-10")
    assert hint["columns"] == []
    assert "没有启用的明细子科目" in (hint["hint"] or "")


@pytest.mark.asyncio
async def test_trial_balance_unified_with_balance_summary(client, auth_headers):
    """试算平衡与科目余额表同一实现：合计一致、容器合计行不进试算。"""
    company_id = "c1"
    await _setup_company(company_id, user_id="test_user_001")
    await _post(
        company_id, "payroll", "2026-10-31", "计提工资",
        [_line("5602001", "管理人员职工薪酬", "200.00")],
        [
            _line("221101", "应付职工薪酬-工资", "150.00"),
            _line("122101", "其他应收款-代扣代缴社保", "50.00"),
        ],
        user_id="test_user_001",
    )

    response = client.post("/api/ledger/trial-balance", json={"month": "2026-10"}, headers=auth_headers)
    assert response.status_code == 200
    trial = response.json()

    summary = await get_balance_summary(company_id, "2026-10")
    expected = [item for item in summary["items"] if not item["isParent"]]
    assert {row["accountCode"] for row in trial["items"]} == {item["accountCode"] for item in expected}
    # 试算合计（由行求和）与科目余额表合计一致
    trial_debit = sum(float(row["debitTotal"]) for row in trial["items"])
    trial_credit = sum(float(row["creditTotal"]) for row in trial["items"])
    assert f"{trial_debit:.2f}" == summary["totalDebit"]
    assert f"{trial_credit:.2f}" == summary["totalCredit"]
    assert trial["isBalanced"] == summary["isBalanced"]
    # 容器合计行（1221/2211/2221）不进试算
    assert "2211" not in {row["accountCode"] for row in trial["items"]}


@pytest.mark.asyncio
async def test_equity_changes_prior_year_and_distribution():
    company_id = "c-wpa-eq"
    await _setup_company(company_id)
    # 上年利润
    await _post(
        company_id, "py", "2025-06-15", "上年收入",
        [_line("1002", "银行存款", "30000.00")],
        [_line("5001", "主营业务收入", "30000.00")],
    )
    # 本年实收资本
    await _post(
        company_id, "capital", "2026-01-10", "股东投入",
        [_line("1002", "银行存款", "100000.00")],
        [_line("3001", "实收资本", "100000.00")],
    )
    # 本年利润 20000（收入 50000 - 费用 30000）
    await _post(
        company_id, "rev", "2026-06-01", "本年收入",
        [_line("1002", "银行存款", "50000.00")],
        [_line("5001", "主营业务收入", "50000.00")],
    )
    await _post(
        company_id, "exp", "2026-07-01", "本年费用",
        [_line("5602001", "管理人员职工薪酬", "30000.00")],
        [_line("1002", "银行存款", "30000.00")],
    )
    # 利润分配 10000
    await _post(
        company_id, "div", "2026-12-20", "股东分红",
        [_line("3104", "利润分配", "10000.00")],
        [_line("1002", "银行存款", "10000.00")],
    )

    result = await get_equity_changes(company_id, 2026)
    rows = {row["key"]: row for row in result["rows"]}
    assert result["netProfit"] == "20000.00"
    assert rows["paid_in_capital"]["priorYearEnd"] == "0.00"
    assert rows["paid_in_capital"]["increase"] == "100000.00"
    assert rows["paid_in_capital"]["yearEnd"] == "100000.00"
    retained = rows["retained_earnings"]
    # 上年末 = 上年利润 30000；本年净利 20000 进增加，分红 10000 进减少
    assert retained["priorYearEnd"] == "30000.00"
    assert retained["increase"] == "20000.00"
    assert retained["decrease"] == "10000.00"
    assert retained["yearEnd"] == "40000.00"
    assert result["totals"]["yearEnd"] == "140000.00"
    # 恒等式逐行成立
    for row in result["rows"]:
        prior = float(row["priorYearEnd"])
        assert abs(prior + float(row["increase"]) - float(row["decrease"]) - float(row["yearEnd"])) < 0.005

    # 上年视角：净利润 30000 全部进未分配利润
    prior_view = await get_equity_changes(company_id, 2025)
    prior_rows = {row["key"]: row for row in prior_view["rows"]}
    assert prior_view["netProfit"] == "30000.00"
    assert prior_rows["retained_earnings"]["yearEnd"] == "30000.00"


@pytest.mark.asyncio
async def test_sub_ledger_filters_by_partner():
    company_id = "c-wpa-spl"
    await _setup_company(company_id)
    # P0 的服务端收口校验 partner_id 必须真实存在——先建档
    from services.auxiliary_service import ensure_partner_by_name

    partner = await ensure_partner_by_name(company_id, "owner-1", "甲公司")
    assert partner and partner["id"]

    await _post(
        company_id, "inv-a", "2026-10-05", "赊销给甲",
        [_line("1122", "应收账款", "800.00", auxiliary={"partner_id": partner["id"], "partner_name": "甲公司"})],
        [_line("5001", "主营业务收入", "800.00")],
    )
    await _post(
        company_id, "inv-b", "2026-10-06", "赊销未挂往来",
        [_line("1122", "应收账款", "200.00")],
        [_line("5001", "主营业务收入", "200.00")],
    )

    both = await query_sub_ledger(company_id, "1122", "2026-10")
    assert len(both) == 2

    filtered = await query_sub_ledger(company_id, "1122", "2026-10", partner_id=partner["id"])
    assert len(filtered) == 1
    assert filtered[0]["entryId"].endswith("inv-a")
    assert filtered[0]["debit"] == "800.00"
