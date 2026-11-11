# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""旧账导入（P0 WP4）：余额表解析/凭证解析/批次幂等。

锁定四条口径：
1. 未知科目不静默跳过，逐行给 reason 并按名称给建议；
2. 逐凭证借贷平衡校验，不平衡整张拒绝；
3. 导入默认 draft，原凭证号保留；
4. 同批次重导被幂等键拦下。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from core.errors import AccountingError
from services.accounting_core_service import ensure_tenant
from services.legacy_import_service import (
    confirm_balance_import,
    confirm_voucher_import,
    parse_balance_rows,
    parse_voucher_rows,
)


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


@pytest.mark.asyncio
async def test_parse_balance_rows_reports_unknown_accounts():
    company_id = "c-legacy-balance"
    await _setup_company(company_id)

    rows = [
        {"科目编码": "1002", "科目名称": "银行存款", "期初余额(借)": "5000.00"},
        {"科目编码": "1122", "科目名称": "应收账款", "期初余额(借)": "3000.00", "往来单位": "甲客户"},
        {"科目编码": "9999", "科目名称": "不存在的科目", "期初余额(贷)": "100.00"},
    ]
    result = await parse_balance_rows(company_id, rows)

    assert len(result["valid"]) == 2
    assert len(result["errors"]) == 1
    assert result["errors"][0]["row"] == 4
    assert "9999" in result["errors"][0]["reason"]
    assert result["totals"]["balanced"] is False
    assert float(result["totals"]["debit"]) == pytest.approx(8000.0)

    # 名称能匹配上的未知编码给出建议
    suggestion_rows = [{"科目编码": "XYZ", "科目名称": "银行存款", "期初余额(借)": "10.00"}]
    suggested = await parse_balance_rows(company_id, suggestion_rows)
    assert suggested["errors"]
    assert suggested["suggestions"][0]["suggestedCode"] == "1002"


@pytest.mark.asyncio
async def test_parse_balance_rows_accepts_two_tier_headers():
    """第三方导出把「期初余额」拆成借/贷两层表头，前端拼成复合列名。

    回归：别名表只有「期初余额(借)」这类写法，真实导出的
    「期初余额借方 / 本年累计发生额借方」全部读不到，79 行数据被当成空行
    静默跳过（valid 0、errors 0），用户看到的就是「旧账导入没功能」。
    """
    company_id = "c-legacy-two-tier"
    await _setup_company(company_id)

    rows = [
        {
            "科目编码": "1002", "科目名称": "银行存款",
            "期初余额借方": "5,000.00", "期初余额贷方": "0.00",
            "本年累计发生额借方": "1,200.00", "本年累计发生额贷方": "0.00",
        },
        {
            "科目编码": "3001", "科目名称": "实收资本",
            "期初余额借方": "0.00", "期初余额贷方": "5,000.00",
            "本年累计发生额借方": "0.00", "本年累计发生额贷方": "0.00",
        },
    ]
    result = await parse_balance_rows(company_id, rows)

    assert len(result["valid"]) == 2
    assert result["totals"]["balanced"] is True
    bank = next(item for item in result["valid"] if item["accountCode"] == "1002")
    assert float(bank["debitBalance"]) == pytest.approx(5000.0)
    assert float(bank["ytdDebit"]) == pytest.approx(1200.0)


@pytest.mark.asyncio
async def test_parse_balance_rows_prefers_period_end_over_opening():
    """同一张表同时有期初和期末列时，资产负债取期末（建账前一月末）余额。

    年中建账时「期初余额」是 1 月 1 日的数，「期末余额」才是建账前一月末的数；
    取错列会让资产负债整体偏移，试算仍然平——从界面上看不出来。
    """
    company_id = "c-legacy-ending"
    await _setup_company(company_id)

    rows = [
        {
            "科目编码": "1002", "科目名称": "银行存款",
            "期初余额借方": "1,000.00", "期初余额贷方": "0.00",
            "期末余额借方": "5,000.00", "期末余额贷方": "0.00",
        },
    ]
    result = await parse_balance_rows(company_id, rows)

    assert len(result["valid"]) == 1
    assert float(result["valid"][0]["debitBalance"]) == pytest.approx(5000.0)


@pytest.mark.asyncio
async def test_confirm_balance_requires_step_up_token():
    company_id = "c-legacy-balance-stepup"
    await _setup_company(company_id)
    items = [
        {"accountCode": "1002", "accountName": "银行存款", "debitBalance": "1000.00", "creditBalance": "0"},
        {"accountCode": "3001", "accountName": "实收资本", "debitBalance": "0", "creditBalance": "1000.00"},
    ]
    with pytest.raises(AccountingError) as error:
        await confirm_balance_import(
            company_id, "owner-1", items, fiscal_year=2026, step_up_token=None
        )
    assert error.value.code == "STEP_UP_REQUIRED"


@pytest.mark.asyncio
async def test_parse_voucher_rows_rejects_unbalanced():
    company_id = "c-legacy-vouchers"
    await _setup_company(company_id)

    rows = [
        {"凭证号": "记-1", "日期": "2026-10-01", "摘要": "收款", "科目编码": "1002", "借方金额": "1000.00"},
        {"凭证号": "记-1", "日期": "2026-10-01", "摘要": "收款", "科目编码": "5001", "贷方金额": "900.00"},
        {"凭证号": "记-2", "日期": "2026-10-02", "摘要": "付款", "科目编码": "5602004", "借方金额": "200.00"},
        {"凭证号": "记-2", "日期": "2026-10-02", "摘要": "付款", "科目编码": "1002", "贷方金额": "200.00"},
    ]
    result = await parse_voucher_rows(company_id, rows)

    assert len(result["valid"]) == 1
    assert result["valid"][0]["voucherNo"] == "记-2"
    assert len(result["errors"]) == 1
    assert "借贷不平" in result["errors"][0]["reason"]
    assert result["totals"]["vouchers"] == 1


@pytest.mark.asyncio
async def test_confirm_vouchers_preserves_number_and_is_idempotent():
    from db.models import JournalEntry
    from db.session import session_scope

    company_id = "c-legacy-confirm"
    await _setup_company(company_id)

    vouchers = [
        {
            "key": "记-1",
            "voucherNo": "记-1",
            "date": "2026-10-01",
            "summary": "收到投资款",
            "lines": [
                {"accountCode": "1002", "accountName": "银行存款", "direction": "debit", "amount": "10000.00"},
                {"accountCode": "3001", "accountName": "实收资本", "direction": "credit", "amount": "10000.00"},
            ],
            "debitTotal": "10000.00",
            "creditTotal": "10000.00",
        },
        {
            "key": "记-2",
            "voucherNo": "记-2",
            "date": "2026-10-03",
            "summary": "采购办公用品",
            "lines": [
                {
                    "accountCode": "5602004",
                    "accountName": "管理费用-办公费",
                    "direction": "debit",
                    "amount": "300.00",
                    "partnerName": "文具供应商",
                },
                {"accountCode": "1002", "accountName": "银行存款", "direction": "credit", "amount": "300.00"},
            ],
            "debitTotal": "300.00",
            "creditTotal": "300.00",
        },
    ]

    first = await confirm_voucher_import(company_id, "owner-1", vouchers, batch_id="batch-a")
    assert first["imported"] == 2
    assert first["posted"] == 0

    async with session_scope() as session:
        entries = (
            await session.execute(
                select(JournalEntry).where(JournalEntry.company_id == company_id).order_by(JournalEntry.voucher_no)
            )
        ).scalars().all()
    assert [entry.voucher_no for entry in entries] == ["记-1", "记-2"]
    assert all(entry.status == "draft" for entry in entries)

    second = await confirm_voucher_import(company_id, "owner-1", vouchers, batch_id="batch-a")
    assert second["imported"] == 0
    assert len(second["skipped"]) == 2


@pytest.mark.asyncio
async def test_confirm_vouchers_can_post():
    company_id = "c-legacy-post"
    await _setup_company(company_id)
    vouchers = [
        {
            "key": "记-9",
            "voucherNo": "记-9",
            "date": "2026-10-09",
            "summary": "过账测试",
            "lines": [
                {"accountCode": "1002", "accountName": "银行存款", "direction": "debit", "amount": "50.00"},
                {"accountCode": "3001", "accountName": "实收资本", "direction": "credit", "amount": "50.00"},
            ],
            "debitTotal": "50.00",
            "creditTotal": "50.00",
        }
    ]
    result = await confirm_voucher_import(company_id, "owner-1", vouchers, batch_id="batch-post", post=True)
    assert result["imported"] == 1
    assert result["posted"] == 1

    from db.models import JournalEntry
    from db.session import session_scope

    async with session_scope() as session:
        entry = (
            await session.execute(select(JournalEntry).where(JournalEntry.company_id == company_id))
        ).scalar_one()
    assert entry.status == "posted"
