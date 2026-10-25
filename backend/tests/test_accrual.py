# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""暂估入账：月末暂估 → 次月红冲 → 票到冲抵。

三条必须守住的口径：
- 暂估不确认进项税额（无票不得抵扣）
- 冲回复用 reverse_journal，一对一，不新造机制
- 票到冲抵是原子操作，不允许留下「暂估已冲、发票没入」
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import db.models  # noqa: F401
from core.errors import AccountingError
from db.base import Base
from db.models import AccountingPeriod, JournalEntry
from services.accounting_core_service import (
    _entry_lines,
    ensure_tenant,
    period_close_checks,
    seed_accounts,
)
from services.accrual_service import (
    accrual_risk_csv,
    accrual_risk_report,
    create_accrual,
    list_pending_accruals,
    reverse_accrual,
    settle_accrual,
)


@pytest.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.exec_driver_sql(
            "CREATE TABLE bank_statements ("
            "id TEXT PRIMARY KEY, company_id TEXT NOT NULL, transaction_date TEXT NOT NULL, "
            "description TEXT NOT NULL, amount NUMERIC(20,2) NOT NULL, "
            "balance NUMERIC(20,2) NOT NULL, statement_id TEXT NOT NULL, "
            "matched_entry_id TEXT, description_normalized INTEGER NOT NULL, created_at TEXT NOT NULL)"
        )
        await connection.exec_driver_sql(
            "CREATE TABLE bank_statement_matches (statement_id TEXT, company_id TEXT, entry_id TEXT, "
            "previous_billing_status TEXT, previous_payment_date TEXT, created_at TEXT, "
            "PRIMARY KEY(statement_id,entry_id))"
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed(session, company_id: str, user_id: str = "owner") -> None:
    await ensure_tenant(session, company_id, user_id)
    await seed_accounts(session, company_id)


def _payload(**overrides) -> dict:
    payload = {
        "amount": "113.00",
        "businessDate": "2026-08-31",
        "vendor": "某某供应商",
        "accountCode": "5602004",
        "accountName": "管理费用-办公费",
        "category": "办公费",
        "expectedInvoiceDate": "2026-09-30",
        "basis": "contract",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_accrual_books_expense_without_input_vat(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-accrual")
            entry = await create_accrual(
                session,
                _payload(),
                company_id="c-accrual",
                user_id="owner",
                request_id="req-accrual-1",
            )
            lines = await _entry_lines(session, entry.id)

    debit = [line for line in lines if line.direction == "debit"]
    credit = [line for line in lines if line.direction == "credit"]
    assert entry.entry_type == "accrual"
    assert entry.status == "posted"
    assert entry.summary.startswith("暂估：某某供应商")
    assert [(line.account_code, str(line.amount)) for line in debit] == [("5602004", "113.00")]
    assert [(line.account_code, str(line.amount)) for line in credit] == [("2202", "113.00")]
    # 无票不得抵扣：暂估凭证里不允许出现任何应交税费科目
    assert all(not line.account_code.startswith("2221") for line in lines)
    assert entry.metadata_json["isAccrual"] is True
    assert entry.metadata_json["expectedInvoiceDate"] == "2026-09-30"


@pytest.mark.asyncio
async def test_accrual_rejects_cash_or_payable_debit_account(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-accrual-bad")
            with pytest.raises(AccountingError) as excinfo:
                await create_accrual(
                    session,
                    _payload(accountCode="1002", accountName="银行存款"),
                    company_id="c-accrual-bad",
                    user_id="owner",
                    request_id="req-bad",
                )
    assert excinfo.value.code == "ACCRUAL_ACCOUNT_INVALID"


@pytest.mark.asyncio
async def test_accrual_requires_vendor_and_expected_invoice_date(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-accrual-req")
            with pytest.raises(AccountingError) as no_vendor:
                await create_accrual(
                    session,
                    _payload(vendor=""),
                    company_id="c-accrual-req",
                    user_id="owner",
                    request_id="req-v",
                )
            assert no_vendor.value.code == "ACCRUAL_VENDOR_REQUIRED"
            with pytest.raises(AccountingError) as no_date:
                await create_accrual(
                    session,
                    _payload(expectedInvoiceDate=""),
                    company_id="c-accrual-req",
                    user_id="owner",
                    request_id="req-d",
                )
            assert no_date.value.code == "INVALID_ACCOUNTING_DATE"


@pytest.mark.asyncio
async def test_reverse_accrual_defaults_to_next_month_first(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-accrual-rev")
            accrual = await create_accrual(
                session,
                _payload(),
                company_id="c-accrual-rev",
                user_id="owner",
                request_id="req-rev-1",
            )
            reversal = await reverse_accrual(
                session,
                accrual.id,
                company_id="c-accrual-rev",
                user_id="owner",
                request_id="req-rev-2",
            )
            pending = await list_pending_accruals(session, "c-accrual-rev", limit=None)
            original = await session.get(JournalEntry, accrual.id)

    assert reversal.reversal_of_id == accrual.id
    assert reversal.period_start.isoformat() == "2026-09-01"  # 次月 1 日红冲
    assert original.status == "reversed"
    assert pending["count"] == 0


@pytest.mark.asyncio
async def test_settle_accrual_reverses_and_books_invoice_with_delta(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-accrual-settle")
            accrual = await create_accrual(
                session,
                _payload(amount="100.00"),
                company_id="c-accrual-settle",
                user_id="owner",
                request_id="req-settle-1",
            )
            result = await settle_accrual(
                session,
                accrual.id,
                {"amount": "103.00", "occurDate": "2026-09-05", "invoiceNumber": "INV-1"},
                company_id="c-accrual-settle",
                user_id="owner",
                request_id="req-settle-2",
            )
            invoice = await session.get(JournalEntry, result["invoiceEntryId"])
            invoice_lines = await _entry_lines(session, invoice.id)
            pending = await list_pending_accruals(session, "c-accrual-settle", limit=None)

    assert result["delta"] == "3.00"
    assert invoice.status == "posted"
    assert invoice.metadata_json["settlesAccrualId"] == accrual.id
    assert [(line.account_code, str(line.amount)) for line in invoice_lines if line.direction == "debit"] == [
        ("5602004", "103.00")
    ]
    assert [(line.account_code, str(line.amount)) for line in invoice_lines if line.direction == "credit"] == [
        ("2241", "103.00")
    ]
    assert pending["count"] == 0


@pytest.mark.asyncio
async def test_close_checks_report_pending_accruals(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-accrual-notice")
            await create_accrual(
                session,
                _payload(),
                company_id="c-accrual-notice",
                user_id="owner",
                request_id="req-notice-1",
            )
            # 8 月的暂估，9 月结账时应提示「未冲回」
            september = await period_close_checks(session, "c-accrual-notice", "2026-09")
            august = await period_close_checks(session, "c-accrual-notice", "2026-08")

    stale = next(item for item in september["notices"] if item["code"] == "ACCRUAL_PENDING_REVERSAL")
    assert stale["count"] == 1
    assert stale["amount"] == "113.00"
    assert stale["suggestedAction"] == "reverse_accrual"

    current = next(item for item in august["notices"] if item["code"] == "ACCRUAL_IN_PERIOD")
    assert current["count"] == 1
    assert not any(item["code"] == "ACCRUAL_PENDING_REVERSAL" for item in august["notices"])


@pytest.mark.asyncio
async def test_accrual_risk_report_and_csv(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-accrual-risk")
            await create_accrual(
                session,
                _payload(expectedInvoiceDate="2026-01-31"),
                company_id="c-accrual-risk",
                user_id="owner",
                request_id="req-risk-1",
            )
            payload = await accrual_risk_report(session, "c-accrual-risk", 2026)
            text = accrual_risk_csv(payload)

    assert payload["count"] == 1
    assert payload["amount"] == "113.00"
    assert payload["overdueCount"] == 1  # 预计取票日已过
    assert "2018 年第 28 号" in text
    assert "某某供应商" in text


@pytest.mark.asyncio
async def test_accrual_can_derive_account_from_category(sessions):
    """只给分类也能暂估：复用记账同一套分类→科目映射，避免前端各写一份。"""
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-accrual-cat")
            entry = await create_accrual(
                session,
                {
                    "amount": "88.00",
                    "businessDate": "2026-08-31",
                    "vendor": "某服务商",
                    "category": "办公费",
                    "expectedInvoiceDate": "2026-09-30",
                },
                company_id="c-accrual-cat",
                user_id="owner",
                request_id="req-cat",
            )
            lines = await _entry_lines(session, entry.id)

    debit = [line for line in lines if line.direction == "debit"]
    assert debit[0].account_code.startswith("5602")
    assert str(debit[0].amount) == entry.metadata_json["amount"]
