# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""总账/明细账/科目余额表聚合口径测试（ledger_service 此前零覆盖）。

容器化后 1221/2211/2221 的记账全部落在子级，查父科目必须归集子级发生额，
否则总账/明细账静默漏计。余额表另锁定两条口径：
1. 法定账表按记账期间（period_start）归集，补记凭证计入记账期间而非业务日期所在月；
2. 全部启用末级科目出 0 行，容器科目（1221/2211/2221）出子级合计行且不计入合计。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from services.accounting_core_service import create_entry, ensure_tenant
from services.ledger_service import get_balance_summary, query_general_ledger, query_sub_ledger


async def _ensure_tables() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _setup_company(company_id: str) -> None:
    from db.session import session_scope

    await _ensure_tables()
    async with session_scope() as session:
        await ensure_tenant(session, company_id, "owner-1")


async def _post_container_entry(company_id: str) -> None:
    """一张工资计提形态的凭证：借方 5602001/5602016，贷方 221101/221102/122101/2221012。"""
    from db.session import session_scope

    async with session_scope() as session:
        await create_entry(
            session,
            {
                "id": f"{company_id}-payroll",
                "direction": "expense",
                "amount": "26600.00",
                "title": "10月计提工资及社保",
                "category": "人力成本",
                "occurDate": "2026-10-31",
                "sourceType": "salary_accrual",
                "voucherLines": {
                    "debit": [
                        {"accountCode": "5602001", "accountName": "管理人员职工薪酬", "amount": "20000.00"},
                        {"accountCode": "5602016", "accountName": "管理费用-单位社保", "amount": "6600.00"},
                    ],
                    "credit": [
                        {"accountCode": "221101", "accountName": "应付职工薪酬-工资", "amount": "14800.00"},
                        {"accountCode": "221102", "accountName": "应付职工薪酬-单位社保", "amount": "6600.00"},
                        {"accountCode": "122101", "accountName": "其他应收款-代扣代缴社保", "amount": "3700.00"},
                        {"accountCode": "2221012", "accountName": "应交税费-应交个人所得税", "amount": "1500.00"},
                    ],
                },
            },
            company_id=company_id,
            user_id="owner-1",
            request_id=f"{company_id}-req",
            confirm=True,
        )


@pytest.mark.asyncio
async def test_general_ledger_aggregates_container_children():
    """查 2211 必须归集 221101/221102 发生额；查子级则精确命中。"""
    company_id = "c-ledger-gl"
    await _setup_company(company_id)
    await _post_container_entry(company_id)

    parent_rows = await query_general_ledger(company_id, "2211", "2026-10")
    assert len(parent_rows) == 2, "父科目查询应归集两条子级分录行"
    assert len({row["voucherRef"] for row in parent_rows}) == 1, "两行应来自同一张凭证"
    assert sorted(row["credit"] for row in parent_rows) == ["14800.00", "6600.00"]
    # 贷方 14800 + 6600，余额为负（贷方）
    assert Decimal(parent_rows[-1]["balance"]) == Decimal("-21400.00")

    child_rows = await query_general_ledger(company_id, "221101", "2026-10")
    assert len(child_rows) == 1
    assert child_rows[0]["credit"] == "14800.00"

    # 非聚合父码仍是精确匹配：查 1002 不会带出别的
    bank_rows = await query_general_ledger(company_id, "1002", "2026-10")
    assert bank_rows == []


@pytest.mark.asyncio
async def test_general_ledger_aggregates_1221_and_2221():
    company_id = "c-ledger-1221"
    await _setup_company(company_id)
    await _post_container_entry(company_id)

    rows = await query_general_ledger(company_id, "1221", "2026-10")
    assert len(rows) == 1 and rows[0]["credit"] == "3700.00"

    rows = await query_general_ledger(company_id, "2221", "2026-10")
    assert len(rows) == 1 and rows[0]["credit"] == "1500.00"


@pytest.mark.asyncio
async def test_sub_ledger_aggregates_container_children():
    """明细账查 2211：只归集 2211* 行的发生额，对方科目不含子级自身。"""
    company_id = "c-ledger-sl"
    await _setup_company(company_id)
    await _post_container_entry(company_id)

    rows = await query_sub_ledger(company_id, "2211", "2026-10")
    assert len(rows) == 1
    row = rows[0]
    assert Decimal(row["debit"]) == Decimal("0.00")
    assert Decimal(row["credit"]) == Decimal("21400.00")
    counterparty_codes = {item["code"] for item in row["counterparties"]}
    assert "221101" not in counterparty_codes and "221102" not in counterparty_codes
    assert counterparty_codes == {"5602001", "5602016", "122101", "2221012"}

    child_rows = await query_sub_ledger(company_id, "122101", "2026-10")
    assert len(child_rows) == 1
    assert Decimal(child_rows[0]["credit"]) == Decimal("3700.00")


async def _insert_backfill_entry(company_id: str) -> None:
    """补记形态凭证：业务日期 2026-09-20、记账期间 2026-10。

    直接落库绕过 create_entry 的补记拦截，只测查询侧的期间归集口径。
    """
    from db.models import ChartAccount, JournalEntry, JournalLine
    from db.session import session_scope

    async with session_scope() as session:
        chart = (
            await session.execute(
                select(ChartAccount).where(
                    ChartAccount.company_id == company_id,
                    ChartAccount.code.in_(("1001", "5001")),
                )
            )
        ).scalars().all()
        by_code = {row.code: row for row in chart}
        entry = JournalEntry(
            company_id=company_id,
            voucher_no="TEST-BF-001",
            occurred_on=date(2026, 9, 20),
            period_start=date(2026, 10, 1),
            summary="补记9月收入（10月记账）",
            status="posted",
            created_by="owner-1",
        )
        session.add(entry)
        await session.flush()
        session.add_all(
            [
                JournalLine(
                    journal_entry_id=entry.id,
                    company_id=company_id,
                    line_no=1,
                    account_id=by_code["1001"].id,
                    account_code="1001",
                    account_name="库存现金",
                    direction="debit",
                    amount=Decimal("500.00"),
                ),
                JournalLine(
                    journal_entry_id=entry.id,
                    company_id=company_id,
                    line_no=2,
                    account_id=by_code["5001"].id,
                    account_code="5001",
                    account_name="主营业务收入",
                    direction="credit",
                    amount=Decimal("500.00"),
                ),
            ]
        )


def _by_code(result: dict) -> dict[str, dict]:
    return {item["accountCode"]: item for item in result["items"]}


@pytest.mark.asyncio
async def test_ledger_queries_use_posting_period_for_backfill():
    """补记凭证（业务日期9月、记账期间10月）必须计入10月，不得出现在9月。"""
    company_id = "c-ledger-backfill"
    await _setup_company(company_id)
    await _insert_backfill_entry(company_id)

    # 科目余额表
    sept = _by_code(await get_balance_summary(company_id, "2026-09"))
    assert Decimal(sept["1001"]["debitTotal"]) == Decimal("0.00")
    assert Decimal(sept["5001"]["creditTotal"]) == Decimal("0.00")
    assert Decimal(sept["1001"]["openingBalance"]) == Decimal("0.00")

    october = _by_code(await get_balance_summary(company_id, "2026-10"))
    assert Decimal(october["1001"]["debitTotal"]) == Decimal("500.00")
    assert Decimal(october["1001"]["closingBalance"]) == Decimal("500.00")
    assert Decimal(october["5001"]["creditTotal"]) == Decimal("500.00")

    # 总分类账：9月空，10月含该行（业务日期仍显示 2026-09-20）
    assert await query_general_ledger(company_id, "1001", "2026-09") == []
    gl_oct = await query_general_ledger(company_id, "1001", "2026-10")
    assert len(gl_oct) == 1
    assert gl_oct[0]["date"] == "2026-09-20"
    assert Decimal(gl_oct[0]["debit"]) == Decimal("500.00")

    # 明细账：同口径
    assert await query_sub_ledger(company_id, "1001", "2026-09") == []
    sl_oct = await query_sub_ledger(company_id, "1001", "2026-10")
    assert len(sl_oct) == 1
    assert Decimal(sl_oct[0]["debit"]) == Decimal("500.00")


@pytest.mark.asyncio
async def test_balance_summary_zero_rows_and_container_parent_rows():
    """无发生额的启用末级科目出 0 行；容器科目出子级合计行且不计入合计平衡。"""
    company_id = "c-ledger-zero"
    await _setup_company(company_id)

    empty = _by_code(await get_balance_summary(company_id, "2026-10"))
    assert Decimal(empty["1001"]["closingBalance"]) == Decimal("0.00")
    assert Decimal(empty["5602001"]["closingBalance"]) == Decimal("0.00")
    parent_empty = empty["2211"]
    assert parent_empty["isParent"] is True
    assert Decimal(parent_empty["creditTotal"]) == Decimal("0.00")

    await _post_container_entry(company_id)

    result = await get_balance_summary(company_id, "2026-10")
    rows = _by_code(result)
    parent = rows["2211"]
    assert parent["isParent"] is True
    assert Decimal(parent["creditTotal"]) == Decimal("21400.00")
    assert Decimal(parent["closingBalance"]) == Decimal("-21400.00")
    # 中层级科目（4301001 等 is_leaf=False 非容器）不出 0 行
    assert "4301001" not in rows
    # 合计只统计末级行，父级行不重复计数
    assert result["totalDebit"] == "26600.00"
    assert result["totalCredit"] == "26600.00"
    assert result["isBalanced"] is True


def test_account_matches_uses_prefix_not_whitelist():
    """归集判定用前缀而非白名单：任何科目只要有下级都能查（白名单已删除）。

    曾经的坑：`AGGREGATE_PARENT_CODES` 只列 8 个码，5001/5051/5402 一旦有明细
    子科目，查父科目会静默返回空——账页看起来"这科目没发生额"，实际是漏计。
    """
    from services.ledger_service import _account_matches

    assert _account_matches("5001", "5001") is True
    assert _account_matches("5001", "5001001") is True, "5001 有子科目时必须归集"
    assert _account_matches("5001", "5002") is False, "不同科目不能被前缀吞掉"
    assert _account_matches("5602", "5602001") is True
    assert _account_matches("5602001", "56020011") is True
    assert _account_matches("5602001", "5602002") is False
