# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""垫付台账：按垫付人汇总其他应付款的期初、本期垫付/报销与期末未报。

锁定四条口径：
1. 老板与员工分开列，不混成一行"其他应付款"；
2. 期初按 period_start 切（与法定账表同口径），不是按 occurred_on；
3. 非垫付业务（对公付款、发票待付款）不进任何人的余额，只做勾稽统计；
4. 草稿凭证不计入。
"""
from __future__ import annotations

import pytest

from core.money import money
from services.account_mapper import get_voucher_hint
from services.accounting_core_service import create_entry, ensure_tenant
from services.ledger_service import query_advance_balances


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


async def _post(
    company_id: str,
    entry_id: str,
    *,
    date: str,
    amount: str,
    payer: str,
    category: str = "交通费",
    direction: str = "expense",
    confirm: bool = True,
    voucher_lines: dict | None = None,
) -> None:
    """按科目映射真实生成的凭证行入账（与生产记账同一条管线）。"""
    from db.session import session_scope

    lines = voucher_lines or get_voucher_hint(direction, category, amount, payer=payer)
    async with session_scope() as session:
        await create_entry(
            session,
            {
                "id": entry_id,
                "direction": direction,
                "amount": amount,
                "title": f"{category}-{entry_id}",
                "category": category,
                "occurDate": date,
                "payer": payer,
                "sourceType": "manual",
                "voucherLines": lines,
            },
            company_id=company_id,
            user_id="owner-1",
            request_id=f"{entry_id}-req",
            confirm=confirm,
        )


@pytest.mark.asyncio
async def test_advance_balances_splits_boss_and_employee():
    company_id = "c-advance-split"
    await _setup_company(company_id)

    # 9 月：老板垫 100、员工阿飞垫 80
    await _post(company_id, "adv-b-9", date="2026-09-10", amount="100.00", payer="boss_personal")
    await _post(
        company_id, "adv-e-9", date="2026-09-20", amount="80.00", payer="employee_personal:阿飞"
    )
    # 10 月：老板又垫 50，公司报销老板 30
    await _post(company_id, "adv-b-10", date="2026-10-08", amount="50.00", payer="boss_personal")
    await _post(
        company_id,
        "adv-reimb-10",
        date="2026-10-15",
        amount="30.00",
        payer="boss_personal",
        category="股东垫付报销",
        direction="owner_transfer",
    )

    ledger = await query_advance_balances(company_id, "2026-10")
    by_name = {item["name"]: item for item in ledger["items"]}

    boss = by_name["老板（股东）"]
    assert boss["kind"] == "boss"
    assert float(boss["opening"]) == pytest.approx(100.0)
    assert float(boss["advance"]) == pytest.approx(50.0)
    assert float(boss["reimbursed"]) == pytest.approx(30.0)
    assert float(boss["closing"]) == pytest.approx(120.0)
    assert boss["lastDate"] == "2026-10-15"

    employee = by_name["阿飞"]
    assert employee["kind"] == "employee"
    assert float(employee["opening"]) == pytest.approx(80.0)
    assert float(employee["advance"]) == pytest.approx(0.0)
    assert float(employee["closing"]) == pytest.approx(80.0)

    # 老板排第一，员工按未报余额排
    assert ledger["items"][0]["kind"] == "boss"
    assert float(ledger["totals"]["closing"]) == pytest.approx(200.0)
    # 没有非垫付业务混进来
    assert ledger["other"]["count"] == 0


@pytest.mark.asyncio
async def test_non_advance_business_excluded_but_reconciled():
    company_id = "c-advance-other"
    await _setup_company(company_id)

    # 对公支出：贷 1002，根本不在 2241 范围
    await _post(company_id, "adv-company", date="2026-10-05", amount="66.00", payer="company_account")
    # 发票待付款：挂 2241 但不是谁垫的钱（payer 为对公），只能进勾稽
    await _post(
        company_id,
        "adv-invoice",
        date="2026-10-06",
        amount="200.00",
        payer="company_account",
        category="办公费",
        voucher_lines={
            "debit": [{"accountCode": "5602004", "accountName": "管理费用-办公费", "amount": money("200.00")}],
            "credit": [{"accountCode": "2241", "accountName": "其他应付款", "amount": money("200.00")}],
        },
    )
    await _post(company_id, "adv-boss", date="2026-10-07", amount="40.00", payer="boss_personal")

    ledger = await query_advance_balances(company_id, "2026-10")
    assert [item["name"] for item in ledger["items"]] == ["老板（股东）"]
    assert float(ledger["totals"]["closing"]) == pytest.approx(40.0)
    # 非垫付的 2241 业务单列出来，会计才能勾稽出与科目余额表的差额
    assert ledger["other"]["count"] == 1
    assert float(ledger["other"]["advance"]) == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_draft_entries_are_not_counted():
    company_id = "c-advance-draft"
    await _setup_company(company_id)

    await _post(
        company_id,
        "adv-draft",
        date="2026-10-09",
        amount="99.00",
        payer="employee_personal:李四",
        confirm=False,
    )

    ledger = await query_advance_balances(company_id, "2026-10")
    assert ledger["items"] == []
    assert float(ledger["totals"]["closing"]) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_bad_month_rejected():
    company_id = "c-advance-month"
    await _setup_company(company_id)

    with pytest.raises(ValueError):
        await query_advance_balances(company_id, "2026/10")


def test_advance_amounts_are_money_text():
    """金额一律字符串出参（与其余账表一致），前端不做浮点累加。"""
    from core.money import money_text

    assert money_text(money("120")) == "120.00"
