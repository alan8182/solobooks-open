# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Test mid-year opening balance migration, YTD profit/loss accumulation, and reset flow."""
import pytest
from datetime import date
from decimal import Decimal
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from db.base import Base
import db.models  # noqa: F401
from db.models import Company, CompanyMember, ChartAccount, JournalEntry, JournalLine
from services.accounting_core_service import ensure_tenant, issue_step_up_token, posted_entries_for_reports
from services.opening_balance_service import (
    get_opening_details,
    reset_opening_balances,
    save_opening_balances,
    check_balanced,
)


@pytest.fixture
async def midyear_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=True)
    async with sm() as session:
        async with session.begin():
            await ensure_tenant(session, "c_test", "owner_u1")
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_midyear_opening_balance_and_ytd_accumulation(midyear_session):
    session = midyear_session
    # 模拟企业于 2026 年 6 月迁入：
    # 1. 资产负债期初（2026-06-01）：银行存款 100,000，应付账款 20,000，实收资本 80,000
    # 2. 1-5 月损益本年累计发生额：主营业务收入 500,000，管理费用 200,000（累计净利 300,000）
    # 3. 税务备忘：以前年度亏损 50,000，已预缴所得税 15,000
    token = await issue_step_up_token(
        session,
        company_id="c_test",
        user_id="owner_u1",
        action="adjust_opening_balance",
    )
    
    asset_items = [
        {"account_code": "1002", "account_name": "银行存款", "debit_balance": "100000.00", "credit_balance": "0.00"},
        {"account_code": "2202", "account_name": "应付账款", "debit_balance": "0.00", "credit_balance": "20000.00", "partner_name": "供应商甲"},
        {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0.00", "credit_balance": "80000.00"},
    ]
    ytd_items = [
        {"account_code": "5001", "account_name": "主营业务收入", "debit_balance": "0.00", "credit_balance": "500000.00"},
        {"account_code": "5602999", "account_name": "管理费用-其他", "debit_balance": "200000.00", "credit_balance": "0.00"},
    ]
    tax_memory = {
        "prior_year_loss": 50000,
        "prepaid_cit_tax": 15000,
        "migration_note": "从金蝶云会计迁入",
    }

    res = await save_opening_balances(
        "c_test",
        2026,
        asset_items,
        user_id="owner_u1",
        step_up_token=token,
        request_id="req-midyear-01",
        start_period="2026-06",
        ytd_items=ytd_items,
        tax_memory=tax_memory,
        session=session,
    )
    assert res["voucherStatus"] == "posted"
    assert "ytdEntry" in res
    assert res["ytdEntry"]["voucherStatus"] == "posted"

    # 验证资产负债凭证发生日期为 2026-06-01
    assert res["occurDate"] == "2026-06-01"
    # 验证 YTD 凭证发生日期为 2026-05-31（5月末）
    assert res["ytdEntry"]["occurDate"] == "2026-05-31"

    # 验证 get_opening_details 读取结果
    details = await get_opening_details("c_test", 2026, session=session)
    assert details["is_locked"] is True
    assert details["start_period"] == "2026-06"
    assert details["tax_memory"]["priorYearLoss"] == 50000
    assert details["tax_memory"]["prepaidCitTax"] == 15000
    assert details["tax_memory"]["migrationNote"] == "从金蝶云会计迁入"

    # 验证 YTD 凭证自动由 3103 本年利润配平
    ytd_debits = res["ytdEntry"]["voucherLines"]["debit"]
    profit_line = next(line for line in ytd_debits if line["accountCode"] == "3103")
    assert Decimal(str(profit_line["amount"])) == Decimal("300000.00")

    # 验证总账报表查询：所有凭证均为 posted 状态且属于当前账套
    authoritative = await posted_entries_for_reports(session, "c_test")
    entry_ids = [e["id"] for e in authoritative]
    assert "opening-c_test-2026" in entry_ids
    assert "opening-c_test-2026-ytd" in entry_ids

    # 测试冲销重置
    reset_token = await issue_step_up_token(
        session,
        company_id="c_test",
        user_id="owner_u1",
        action="adjust_opening_balance",
    )
    reset_res = await reset_opening_balances(
        "c_test",
        2026,
        user_id="owner_u1",
        step_up_token=reset_token,
        request_id="req-midyear-reset",
        session=session,
    )
    assert reset_res["ok"] is True
    assert reset_res["reversal"]["voucherStatus"] == "posted"
