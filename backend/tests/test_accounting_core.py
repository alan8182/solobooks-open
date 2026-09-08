from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from core.errors import AccountingError, ENTRY_POSTED_IMMUTABLE, PERIOD_CLOSED
from core.money import money, money_text
from db.base import Base
from db.models import (
    AuditEvent,
    AuthUser,
    Company,
    CompanyMember,
    JournalEntry,
    ReconciliationLink,
    SourceTransaction,
)
import db.models  # noqa: F401
from services.accounting_core_service import (
    approve_journal,
    close_period,
    create_entry,
    ensure_tenant,
    issue_step_up_token,
    period_close_checks,
    reverse_journal,
    serialize_journal,
    sync_bank_reconciliation,
    update_draft,
)
from services.opening_balance_service import get_opening_balances, save_opening_balances
from services.bank_service import import_statements_core
from services.rd_service import correct_unallocated_entry, create_project, rd_reconciliation_summary


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
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def sample_payload(entry_id: str = "entry-1", occurred_on: str = "2026-07-01") -> dict:
    return {
        "id": entry_id,
        "direction": "income",
        "amount": "100.10",
        "title": "咨询服务收入",
        "category": "主营业务收入",
        "occurDate": occurred_on,
        "sourceType": "manual",
        "voucherLines": {
            "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "100.10"}],
            "credit": [{"accountCode": "6001", "accountName": "主营业务收入", "amount": "100.10"}],
        },
    }


def test_money_is_fixed_point_and_half_up():
    assert money("1.005") == Decimal("1.01")
    assert money(0.1) + money(0.2) == Decimal("0.30")
    assert money_text("123") == "123.00"


@pytest.mark.asyncio
async def test_bank_import_writes_source_and_audit_in_one_transaction(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            result = await import_statements_core(
                session,
                company_id="c-bank-core",
                user_id="owner-bank-core",
                rows=[{
                    "transaction_date": "2026-07-02",
                    "description": "客户回款",
                    "amount": "88.10",
                    "balance": "1000.00",
                }],
                statement_id="batch-core-1",
                request_id="req-bank-core-1",
            )
            assert result["imported"] == 1

    async with accounting_sessions() as session:
        source = (await session.execute(select(SourceTransaction))).scalar_one()
        audit = (await session.execute(select(AuditEvent))).scalar_one()
        bank_count = (await session.execute(text("SELECT count(*) FROM bank_statements"))).scalar_one()
        assert source.amount == Decimal("88.10")
        assert source.status == "candidate"
        assert source.raw_payload["signedAmount"] == "88.10"
        assert audit.action == "import_bank_statements"
        assert bank_count == 1


@pytest.mark.asyncio
async def test_bank_import_rolls_back_bank_source_and_audit_together(accounting_sessions):
    with pytest.raises(RuntimeError):
        async with accounting_sessions() as session:
            async with session.begin():
                await import_statements_core(
                    session,
                    company_id="c-bank-rollback",
                    user_id="owner-bank-rollback",
                    rows=[{
                        "transaction_date": "2026-07-03",
                        "description": "回滚测试",
                        "amount": "9.99",
                        "balance": "99.99",
                    }],
                    statement_id="batch-rollback",
                    request_id="req-bank-rollback",
                )
                raise RuntimeError("force rollback")

    async with accounting_sessions() as session:
        assert (await session.execute(select(SourceTransaction))).scalars().all() == []
        assert (await session.execute(select(AuditEvent))).scalars().all() == []
        assert (await session.execute(text("SELECT count(*) FROM bank_statements"))).scalar_one() == 0


@pytest.mark.asyncio
async def test_bank_match_updates_authoritative_source_and_link(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            await import_statements_core(
                session,
                company_id="c-bank-link",
                user_id="owner-bank-link",
                rows=[{
                    "transaction_date": "2026-07-04",
                    "description": "银行核销",
                    "amount": "100.10",
                    "balance": "1100.10",
                }],
                statement_id="batch-link",
                request_id="req-bank-link-import",
            )
            source = (await session.execute(select(SourceTransaction))).scalar_one()
            entry = await create_entry(
                session,
                sample_payload("bank-link-entry", "2026-07-04"),
                company_id="c-bank-link",
                user_id="owner-bank-link",
                request_id="req-bank-link-entry",
                confirm=True,
            )
            await sync_bank_reconciliation(
                session,
                company_id="c-bank-link",
                user_id="owner-bank-link",
                statement_id=source.external_key,
                entry_ids=[entry.id],
                transaction_date="2026-07-04",
                description="银行核销",
                signed_amount="100.10",
                request_id="req-bank-link-match",
            )

    async with accounting_sessions() as session:
        source = (await session.execute(select(SourceTransaction).where(SourceTransaction.source_type == "bank"))).scalar_one()
        link = (await session.execute(select(ReconciliationLink))).scalar_one()
        assert source.status == "reconciled"
        assert link.journal_entry_id == "bank-link-entry"
        assert link.amount == Decimal("100.10")
        assert link.status == "confirmed"


@pytest.mark.asyncio
async def test_period_close_runs_all_five_reconciliation_classes(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-close-checks", "owner-close-checks")
            checks = await period_close_checks(session, "c-close-checks", "2026-07")
            assert [item["code"] for item in checks["checks"]] == [
                "VOUCHER_TO_SOURCE",
                "LEDGER_TO_LEDGER",
                "BOOK_TO_ACTUAL",
                "LEDGER_TO_REPORT",
                "REPORT_TO_TAX",
            ]
            assert checks["periodStatus"] == "open"
            assert checks["blockingCount"] == 0
            assert {item["uiStatus"] for item in checks["checks"]} == {"complete"}


@pytest.mark.asyncio
async def test_confirm_posts_balanced_journal_and_freezes_money(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            entry = await create_entry(
                session,
                sample_payload(),
                company_id="c1",
                user_id="owner-1",
                request_id="req-1",
                confirm=True,
            )
            payload = await serialize_journal(session, entry)
            assert entry.status == "posted"
            assert entry.voucher_no == "VOU-202607-000001"
            assert payload["amount"] == "100.10"
            assert payload["voucherLines"]["debit"][0]["amount"] == "100.10"

    async with accounting_sessions() as session:
        async with session.begin():
            with pytest.raises(AccountingError) as exc:
                await update_draft(
                    session,
                    "entry-1",
                    {"amount": "99.00"},
                    company_id="c1",
                    user_id="owner-1",
                    request_id="req-2",
                )
            assert exc.value.code == ENTRY_POSTED_IMMUTABLE


@pytest.mark.asyncio
async def test_reversal_swaps_lines_and_marks_original_reversed(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            await create_entry(
                session,
                sample_payload(),
                company_id="c1",
                user_id="owner-1",
                request_id="req-1",
                confirm=True,
            )
            reversal = await reverse_journal(
                session,
                "entry-1",
                company_id="c1",
                user_id="owner-1",
                request_id="req-2",
                reason="客户退款",
                reversal_date="2026-07-02",
            )
            payload = await serialize_journal(session, reversal)
            original = await session.get(JournalEntry, "entry-1")
            assert original and original.status == "reversed"
            assert reversal.status == "posted"
            assert payload["voucherLines"]["credit"][0]["accountCode"] == "1002"
            assert payload["voucherLines"]["debit"][0]["accountCode"] == "6001"


@pytest.mark.asyncio
async def test_rd_reversal_preserves_detail_and_multi_project_correction(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-rd-correction", "owner-rd-correction")
            project_a = await create_project(
                session, "c-rd-correction", "owner-rd-correction",
                {"code": "RD-A", "name": "项目A", "startDate": date(2026, 1, 1),
                 "endDate": date(2026, 12, 31), "status": "active",
                 "initiationDocumentStatus": "complete"}, request_id="project-a",
            )
            project_b = await create_project(
                session, "c-rd-correction", "owner-rd-correction",
                {"code": "RD-B", "name": "项目B", "startDate": date(2026, 1, 1),
                 "endDate": date(2026, 12, 31), "status": "active",
                 "initiationDocumentStatus": "complete"}, request_id="project-b",
            )
            legacy = await create_entry(
                session,
                {
                    "id": "legacy-rd-1", "direction": "expense", "amount": "100.00",
                    "title": "历史研发材料", "category": "研发支出-费用化支出",
                    "occurDate": "2026-06-01", "voucherAuthority": "legacy_import",
                    "voucherLines": {
                        "debit": [{"accountCode": "5301", "accountName": "研发支出-费用化支出（待归集）", "amount": "100.00"}],
                        "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "100.00"}],
                    },
                },
                company_id="c-rd-correction", user_id="owner-rd-correction",
                request_id="legacy-rd", confirm=True,
            )
            result = await correct_unallocated_entry(
                session, "c-rd-correction", "owner-rd-correction", legacy.id,
                [
                    {"projectId": project_a["id"], "expenseType": "direct_input", "amount": "60.00",
                     "allocationMethod": "quantity", "allocationBasis": "领料单A占60%"},
                    {"projectId": project_b["id"], "expenseType": "direct_input", "amount": "40.00",
                     "allocationMethod": "quantity", "allocationBasis": "领料单B占40%"},
                ],
                correction_date=date(2026, 6, 2),
                reason="历史研发材料按领料单拆分", request_id="correct-rd",
            )
            corrected = result["correctedEntry"]
            assert result["reversalEntryId"]
            assert [line["amount"] for line in corrected["voucherLines"]["debit"]] == ["60.00", "40.00"]
            assert all(line["accountCode"] == "530102" for line in corrected["voucherLines"]["debit"])
            assert all(line["rdDetail"]["projectId"] in {project_a["id"], project_b["id"]} for line in corrected["voucherLines"]["debit"])

            summary = await rd_reconciliation_summary(
                session, "c-rd-correction", "owner-rd-correction",
                start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31),
            )
            assert summary["ledgerTotal"] == "100.00"
            assert summary["generalLedgerTotal"] == "100.00"
            assert summary["difference"] == "0.00"

            reversal = await reverse_journal(
                session, corrected["id"], company_id="c-rd-correction", user_id="owner-rd-correction",
                request_id="reverse-corrected", reason="取消更正", reversal_date="2026-06-03",
            )
            reversal_payload = await serialize_journal(session, reversal)
            assert all(line.get("rdDetail") for line in reversal_payload["voucherLines"]["credit"])


@pytest.mark.asyncio
async def test_rd_permissions_allow_viewer_read_but_reject_write(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-rd-permission", "owner-rd-permission")
            session.add(AuthUser(
                user_id="viewer-rd", openid="", phone="", phone_verified_at="",
                wechat_openid_bound_at="", current_company_id="c-rd-permission",
                nickname="只读用户", avatar="", created_at="2026-01-01T00:00:00+00:00",
                last_login_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00",
            ))
            session.add(CompanyMember(
                company_id="c-rd-permission", user_id="viewer-rd", role="viewer", status="active",
            ))
            await session.flush()
            summary = await rd_reconciliation_summary(
                session, "c-rd-permission", "viewer-rd",
                start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
            )
            assert summary["isReconciled"] is True
            with pytest.raises(AccountingError) as exc:
                await create_project(
                    session, "c-rd-permission", "viewer-rd",
                    {"code": "DENIED", "name": "不应创建", "startDate": date(2026, 1, 1)},
                    request_id="denied-project",
                )
            assert exc.value.code == "FORBIDDEN"


@pytest.mark.asyncio
async def test_closed_period_rejects_new_entry(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            await create_entry(
                session,
                sample_payload(),
                company_id="c1",
                user_id="owner-1",
                request_id="req-1",
                confirm=True,
            )
            result = await close_period(
                session,
                "c1",
                "2026-07",
                user_id="owner-1",
                request_id="req-close",
            )
            assert result["status"] == "closed"

    async with accounting_sessions() as session:
        async with session.begin():
            with pytest.raises(AccountingError) as exc:
                await create_entry(
                    session,
                    sample_payload("entry-2"),
                    company_id="c1",
                    user_id="owner-1",
                    request_id="req-2",
                    confirm=True,
                )
            assert exc.value.code == PERIOD_CLOSED


@pytest.mark.asyncio
async def test_maker_checker_requires_different_reviewer(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            company, _ = await ensure_tenant(session, "c1", "owner-1")
            company.posting_policy = "maker_checker"
            now = "2026-07-01T00:00:00+00:00"
            session.add(AuthUser(
                user_id="preparer-1", openid="", phone="", phone_verified_at="",
                wechat_openid_bound_at="", current_company_id="c1", nickname="", avatar="",
                created_at=now, last_login_at=now, updated_at=now,
            ))
            session.add(CompanyMember(company_id="c1", user_id="preparer-1", role="preparer", status="active"))
            await session.flush()
            entry = await create_entry(
                session,
                sample_payload(),
                company_id="c1",
                user_id="preparer-1",
                request_id="req-1",
                confirm=True,
            )
            assert entry.status == "pending_review"
            approved = await approve_journal(
                session,
                entry.id,
                company_id="c1",
                user_id="owner-1",
                request_id="req-2",
            )
            assert approved.status == "posted"
            assert approved.reviewed_by == "owner-1"


@pytest.mark.asyncio
async def test_transaction_rollback_does_not_leave_partial_journal(accounting_sessions):
    with pytest.raises(RuntimeError):
        async with accounting_sessions() as session:
            async with session.begin():
                await create_entry(
                    session,
                    sample_payload(),
                    company_id="c1",
                    user_id="owner-1",
                    request_id="req-1",
                    confirm=True,
                )
                raise RuntimeError("force rollback")

    async with accounting_sessions() as session:
        rows = (await session.execute(select(JournalEntry))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_opening_balance_is_a_step_up_protected_immutable_journal(accounting_sessions):
    items = [
        {"account_code": "1002", "account_name": "银行存款", "debit_balance": "50000.00"},
        {"account_code": "4001", "account_name": "实收资本", "credit_balance": "50000.00"},
    ]
    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c1", "owner-1")
            with pytest.raises(AccountingError) as missing:
                await save_opening_balances(
                    "c1", 2026, items, user_id="owner-1", step_up_token=None,
                    request_id="opening-missing-step-up", session=session,
                )
            assert missing.value.code == "STEP_UP_REQUIRED"

            token = await issue_step_up_token(
                session,
                company_id="c1",
                user_id="owner-1",
                action="adjust_opening_balance",
            )
            entry = await save_opening_balances(
                "c1", 2026, items, user_id="owner-1", step_up_token=token,
                request_id="opening-create", session=session,
            )
            assert entry["voucherStatus"] == "posted"
            assert entry["voucherLines"]["debit"][0]["amount"] == "50000.00"

    async with accounting_sessions() as session:
        async with session.begin():
            balances = await get_opening_balances("c1", 2026, session=session)
            bank = next(item for item in balances if item["account_code"] == "1002")
            assert bank["debit_balance"] == "50000.00"
            token = await issue_step_up_token(
                session,
                company_id="c1",
                user_id="owner-1",
                action="adjust_opening_balance",
            )
            with pytest.raises(AccountingError) as duplicate:
                await save_opening_balances(
                    "c1", 2026, items, user_id="owner-1", step_up_token=token,
                    request_id="opening-rewrite", session=session,
                )
            assert duplicate.value.code == ENTRY_POSTED_IMMUTABLE


@pytest.mark.asyncio
async def test_tenant_isolation_and_step_up_single_use(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            await create_entry(
                session,
                sample_payload("tenant-entry"),
                company_id="tenant-a",
                user_id="owner-a",
                request_id="tenant-create",
                confirm=True,
            )
            with pytest.raises(AccountingError) as isolated:
                await update_draft(
                    session,
                    "tenant-entry",
                    {"title": "cross-tenant"},
                    company_id="tenant-b",
                    user_id="owner-b",
                    request_id="cross-tenant",
                )
            assert isolated.value.code == "NOT_FOUND"

            token = await issue_step_up_token(
                session,
                company_id="tenant-a",
                user_id="owner-a",
                action="adjust_opening_balance",
            )
            await save_opening_balances(
                "tenant-a",
                2025,
                [
                    {"account_code": "1002", "account_name": "银行存款", "debit_balance": "1.00"},
                    {"account_code": "4001", "account_name": "实收资本", "credit_balance": "1.00"},
                ],
                user_id="owner-a",
                step_up_token=token,
                request_id="single-use-first",
                session=session,
            )
            with pytest.raises(AccountingError) as replayed:
                await save_opening_balances(
                    "tenant-a",
                    2024,
                    [
                        {"account_code": "1002", "account_name": "银行存款", "debit_balance": "1.00"},
                        {"account_code": "4001", "account_name": "实收资本", "credit_balance": "1.00"},
                    ],
                    user_id="owner-a",
                    step_up_token=token,
                    request_id="single-use-replay",
                    session=session,
                )
            assert replayed.value.code == "STEP_UP_REQUIRED"
