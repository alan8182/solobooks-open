from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from core.errors import AccountingError
from db.base import Base
from db.models import (
    Company,
    CompanyMember,
    JournalEntry,
    JournalLine,
    SourceTransaction,
)
import db.models  # noqa: F401
from deps.auth import AuthContext
from models.schemas import BatchPostRequest
from routers.accounting import batch_post_entries_route
from services.accounting_core_service import (
    create_entry,
    ensure_tenant,
    get_journal,
)
from services.bank_settlement_service import confirm_statement_match


@pytest.fixture
async def accounting_sessions():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
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


def sample_payload(entry_id: str, occurred_on: str = "2026-07-01") -> dict:
    return {
        "id": entry_id,
        "direction": "income",
        "amount": "100.00",
        "title": f"服务收入-{entry_id}",
        "category": "主营业务收入",
        "occurDate": occurred_on,
        "sourceType": "manual",
        "voucherLines": {
            "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "100.00"}],
            "credit": [{"accountCode": "6001", "accountName": "主营业务收入", "amount": "100.00"}],
        },
    }


@pytest.mark.asyncio
async def test_batch_post_all_valid_entries(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-batch-1", "user-owner")

            # Create 3 draft entries
            for i in range(1, 4):
                await create_entry(
                    session,
                    sample_payload(f"entry-{i}"),
                    company_id="c-batch-1",
                    user_id="user-owner",
                    request_id=f"create-{i}",
                    confirm=False,
                )

        # Call batch post route
        req = MagicMock()
        req.headers.get.return_value = "req-batch-test"
        ctx = AuthContext(user_id="user-owner", company_id="c-batch-1")

        result = await batch_post_entries_route(
            BatchPostRequest(entryIds=["entry-1", "entry-2", "entry-3"]),
            request=req,
            ctx=ctx,
            session=session,
        )

        assert result.total == 3
        assert result.postedCount == 3
        assert result.failedCount == 0
        assert set(result.postedEntryIds) == {"entry-1", "entry-2", "entry-3"}

        # Check in DB that all 3 are posted and have voucher_no
        for i in range(1, 4):
            e = await get_journal(session, f"entry-{i}", company_id="c-batch-1")
            assert e.status == "posted"
            assert e.voucher_no.startswith("VOU-202607-")


@pytest.mark.asyncio
async def test_batch_post_isolates_failure_with_savepoint(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-batch-2", "user-owner")

            # 1 valid draft entry
            await create_entry(
                session,
                sample_payload("good-entry"),
                company_id="c-batch-2",
                user_id="user-owner",
                request_id="create-good",
                confirm=False,
            )

            # 1 draft entry, then manually mess up its lines so it's unbalanced
            bad = await create_entry(
                session,
                sample_payload("bad-entry"),
                company_id="c-batch-2",
                user_id="user-owner",
                request_id="create-bad",
                confirm=False,
            )
            # Delete credit line to make it unbalanced
            await session.execute(
                JournalLine.__table__.delete().where(
                    JournalLine.journal_entry_id == bad.id,
                    JournalLine.direction == "credit",
                )
            )

        req = MagicMock()
        req.headers.get.return_value = "req-batch-test-2"
        ctx = AuthContext(user_id="user-owner", company_id="c-batch-2")

        result = await batch_post_entries_route(
            BatchPostRequest(entryIds=["good-entry", "bad-entry"]),
            request=req,
            ctx=ctx,
            session=session,
        )

        assert result.total == 2
        assert result.postedCount == 1
        assert result.failedCount == 1
        assert result.postedEntryIds == ["good-entry"]
        assert len(result.failedEntries) == 1
        assert result.failedEntries[0].entryId == "bad-entry"
        assert result.failedEntries[0].code == "UNBALANCED_ENTRY"

        # Verify good entry is posted, bad entry remains draft
        good = await get_journal(session, "good-entry", company_id="c-batch-2")
        assert good.status == "posted"
        bad_after = await get_journal(session, "bad-entry", company_id="c-batch-2")
        assert bad_after.status == "draft"


@pytest.mark.asyncio
async def test_batch_post_by_month(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-batch-3", "user-owner")

            # Create 2 drafts in 2026-08
            await create_entry(
                session,
                sample_payload("aug-1", "2026-08-05"),
                company_id="c-batch-3",
                user_id="user-owner",
                request_id="create-aug-1",
                confirm=False,
            )
            await create_entry(
                session,
                sample_payload("aug-2", "2026-08-10"),
                company_id="c-batch-3",
                user_id="user-owner",
                request_id="create-aug-2",
                confirm=False,
            )
            # Create 1 draft in 2026-09 (should not be affected by month=2026-08)
            await create_entry(
                session,
                sample_payload("sep-1", "2026-09-01"),
                company_id="c-batch-3",
                user_id="user-owner",
                request_id="create-sep-1",
                confirm=False,
            )

        req = MagicMock()
        req.headers.get.return_value = "req-batch-test-3"
        ctx = AuthContext(user_id="user-owner", company_id="c-batch-3")

        result = await batch_post_entries_route(
            BatchPostRequest(month="2026-08"),
            request=req,
            ctx=ctx,
            session=session,
        )

        assert result.total == 2
        assert result.postedCount == 2
        assert set(result.postedEntryIds) == {"aug-1", "aug-2"}

        sep_entry = await get_journal(session, "sep-1", company_id="c-batch-3")
        assert sep_entry.status == "draft"
