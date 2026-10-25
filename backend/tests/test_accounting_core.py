# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from core.errors import AccountingError, ENTRY_BEFORE_START_DATE, ENTRY_POSTED_IMMUTABLE, PERIOD_CLOSED
from core.money import money, money_text
from db.base import Base
from db.models import (
    AccountingPeriod,
    AuditEvent,
    AuthUser,
    Company,
    CompanyMember,
    ChartAccount,
    JournalEntry,
    JournalLine,
    LegacyEntryProjection,
    ReconciliationLink,
    SourceTransaction,
)
import db.models  # noqa: F401
from services.accounting_core_service import (
    approve_journal,
    available_periods,
    post_journal,
    pre_book_start_activity,
    preceding_open_months,
    submit_journal_for_review,
    assert_bank_reconciliable,
    close_period,
    create_entry,
    delete_draft,
    get_journal,
    ensure_tenant,
    issue_step_up_token,
    journal_entries_for_reports,
    period_close_checks,
    reverse_journal,
    serialize_journal,
    sync_bank_reconciliation,
    update_draft,
)
from services.opening_balance_service import get_opening_balances, save_opening_balances
from services.bank_service import import_statements_core
from services.bank_settlement_service import confirm_statement_match
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
            "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": "100.10"}],
        },
    }


def test_money_is_fixed_point_and_half_up():
    assert money("1.005") == Decimal("1.01")
    assert money(0.1) + money(0.2) == Decimal("0.30")
    assert money_text("123") == "123.00"


@pytest.mark.asyncio
async def test_report_entries_only_include_drafts_when_requested(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            await create_entry(
                session,
                sample_payload("report-draft"),
                company_id="c-report-drafts",
                user_id="owner",
                request_id="create-report-draft",
                confirm=False,
            )

            posted_only = await journal_entries_for_reports(
                session, "c-report-drafts",
            )
            management = await journal_entries_for_reports(
                session, "c-report-drafts", include_drafts=True,
            )

    assert posted_only == []
    assert [entry["id"] for entry in management] == ["report-draft"]


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
async def test_bank_match_accepts_settlement_account_entry(accounting_sessions):
    """微信待结算（1012）进出、无 1002 分录的凭证也应建立核销关联。"""
    async with accounting_sessions() as session:
        async with session.begin():
            await import_statements_core(
                session,
                company_id="c-bank-settle",
                user_id="owner-bank-settle",
                rows=[{
                    "transaction_date": "2026-07-05",
                    "description": "微信收款",
                    "amount": "5.00",
                    "balance": "1005.00",
                }],
                statement_id="batch-settle",
                request_id="req-bank-settle-import",
            )
            source = (await session.execute(select(SourceTransaction))).scalar_one()
            entry = await create_entry(
                session,
                {
                    **sample_payload("bank-settle-entry", "2026-07-05"),
                    "amount": "5.00",
                    "voucherLines": {
                        "debit": [{"accountCode": "1012001", "accountName": "其他货币资金", "amount": "5.00"}],
                        "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": "5.00"}],
                    },
                },
                company_id="c-bank-settle",
                user_id="owner-bank-settle",
                request_id="req-bank-settle-entry",
                confirm=True,
            )
            line_codes = {
                line.account_code
                for line in (
                    await session.execute(
                        select(JournalLine).where(JournalLine.journal_entry_id == entry.id)
                    )
                ).scalars()
            }
            assert "1012001" in line_codes and "1002" not in line_codes
            await assert_bank_reconciliable(session, company_id="c-bank-settle", entry_ids=[entry.id])
            await sync_bank_reconciliation(
                session,
                company_id="c-bank-settle",
                user_id="owner-bank-settle",
                statement_id=source.external_key,
                entry_ids=[entry.id],
                transaction_date="2026-07-05",
                description="微信收款",
                signed_amount="5.00",
                request_id="req-bank-settle-match",
            )

    async with accounting_sessions() as session:
        link = (await session.execute(select(ReconciliationLink))).scalar_one()
        assert link.amount == Decimal("5.00")
        assert link.status == "confirmed"


@pytest.mark.asyncio
async def test_bank_match_rejects_entry_without_money_account(accounting_sessions):
    """既无 1002 也无 1012 的凭证不能核销，预检与 sync 校验口径一致。"""
    async with accounting_sessions() as session:
        async with session.begin():
            await import_statements_core(
                session,
                company_id="c-bank-nomoney",
                user_id="owner-bank-nomoney",
                rows=[{
                    "transaction_date": "2026-07-06",
                    "description": "无资金科目流水",
                    "amount": "40.00",
                    "balance": "1040.00",
                }],
                statement_id="batch-nomoney",
                request_id="req-bank-nomoney-import",
            )
            source = (await session.execute(select(SourceTransaction))).scalar_one()
            entry = await create_entry(
                session,
                {
                    **sample_payload("bank-nomoney-entry", "2026-07-06"),
                    "amount": "40.00",
                    "voucherLines": {
                        "debit": [{"accountCode": "5603002", "accountName": "管理费用", "amount": "40.00"}],
                        "credit": [{"accountCode": "2241", "accountName": "其他应付款", "amount": "40.00"}],
                    },
                },
                company_id="c-bank-nomoney",
                user_id="owner-bank-nomoney",
                request_id="req-bank-nomoney-entry",
                confirm=True,
            )
            with pytest.raises(AccountingError) as pre_exc:
                await assert_bank_reconciliable(session, company_id="c-bank-nomoney", entry_ids=[entry.id])
            assert pre_exc.value.code == "BANK_EFFECT_REQUIRED"
            with pytest.raises(AccountingError) as sync_exc:
                await sync_bank_reconciliation(
                    session,
                    company_id="c-bank-nomoney",
                    user_id="owner-bank-nomoney",
                    statement_id=source.external_key,
                    entry_ids=[entry.id],
                    transaction_date="2026-07-06",
                    description="无资金科目流水",
                    signed_amount="40.00",
                    request_id="req-bank-nomoney-match",
                )
            assert sync_exc.value.code == "BANK_EFFECT_REQUIRED"


@pytest.mark.asyncio
async def test_serialize_reflects_reconciliation_status_from_legacy_projection(accounting_sessions):
    """confirm-suggestion 直接在 legacy 投影 payload 上维护 billingStatus（json_set/json_remove），
    /api/record/entries（serialize_journal 链路）必须能看到，否则前端"未对账列表"漏过滤已核销条目。"""
    async with accounting_sessions() as session:
        async with session.begin():
            entry = await create_entry(
                session,
                sample_payload("legacy-reconcile-entry"),
                company_id="c-legacy-reconcile",
                user_id="owner-legacy-reconcile",
                request_id="req-legacy-reconcile",
                confirm=True,
            )
            before = await serialize_journal(session, entry)
            assert before.get("billingStatus") != "reconciled"
            # 模拟 routers/bank.py confirm-suggestion 的 legacy 更新（json_set）
            projection = await session.get(LegacyEntryProjection, entry.id)
            assert projection is not None
            payload = json.loads(projection.payload)
            payload["billingStatus"] = "reconciled"
            payload["paymentDate"] = "2026-07-19"
            projection.payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            await session.flush()

            after = await serialize_journal(session, entry)
            assert after["billingStatus"] == "reconciled"
            assert after["paymentDate"] == "2026-07-19"
            # 再经 _sync_projection 写回后仍保持一致（值收敛，不会丢失）
            from services.accounting_core_service import _sync_projection

            await _sync_projection(session, entry)
            final = await serialize_journal(session, entry)
            assert final["billingStatus"] == "reconciled"


@pytest.mark.asyncio
async def test_available_periods_merges_entries_periods_bank_and_salary(accounting_sessions, monkeypatch):
    class _FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        async def fetchall(self):
            return self._rows

    class _FakeConn:
        def __init__(self):
            self.closed = False

        async def execute(self, sql, params=None):
            if "bank_statements" in sql:
                return _FakeCursor([{"month": "2026-02"}, {"month": "2026-04"}])
            return _FakeCursor([{"month": "2026-01"}])

        async def close(self):
            self.closed = True

    fake_conn = _FakeConn()

    async def fake_get_conn():
        return fake_conn

    monkeypatch.setattr("services.database.get_conn", fake_get_conn)

    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-avail", "owner-avail")
            session.add(JournalEntry(
                company_id="c-avail", occurred_on=date(2026, 4, 10), period_start=date(2026, 4, 1),
                summary="4月凭证", created_by="owner-avail",
            ))
            session.add(JournalEntry(
                company_id="c-avail", occurred_on=date(2026, 9, 5), period_start=date(2026, 9, 1),
                summary="9月凭证", created_by="owner-avail",
            ))
            session.add(AccountingPeriod(company_id="c-avail", period_start=date(2026, 4, 1), status="closed"))

    async with accounting_sessions() as session:
        result = await available_periods(session, "c-avail")

    months = {item["month"]: item["status"] for item in result["months"]}
    assert months["2026-04"] == "closed"
    assert months["2026-09"] is None
    assert months["2026-02"] is None
    assert months["2026-01"] is None
    assert result["months"] == sorted(result["months"], key=lambda item: item["month"], reverse=True)
    assert fake_conn.closed


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
                "INTANGIBLE_AMORTIZATION",
                "FIXED_ASSET_DEPRECIATION",
                "PERIOD_ORDER",
            ]
            assert checks["periodStatus"] == "open"
            assert checks["blockingCount"] == 0
            assert {item["uiStatus"] for item in checks["checks"]} == {"complete"}


@pytest.mark.asyncio
async def test_close_distinguishes_pending_review_and_source_and_does_not_green_drafts(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            for i in range(15):
                await create_entry(session, sample_payload(f"draft-{i}"), company_id="c-drafts", user_id="owner", request_id=f"d-{i}", confirm=False)
            entry = await session.get(JournalEntry, "draft-0")
            entry.status = "pending_review"
            missing = await session.get(JournalEntry, "draft-1")
            missing.source_transaction_id = None
            await session.flush()
            result = await period_close_checks(session, "c-drafts", "2026-07")
            voucher = result["checks"][0]
            assert voucher["draftCount"] == 14
            assert voucher["pendingReviewCount"] == 1
            assert voucher["orphanVoucherCount"] == 1
            assert len(voucher["records"]) == 15
            assert next(item for item in voucher["records"] if item["entryId"] == "draft-1")["reasons"] == ["pending_post", "missing_source"]
            # 关账顺序是前置门禁，不描述“账做平了没有”，不随待过账一起转人工
            assert all(check["uiStatus"] != "complete" for check in result["checks"]
                       if check["code"] != "PERIOD_ORDER")
            assert result["canClose"] is False


@pytest.mark.asyncio
async def test_maker_checker_submit_review_persists_and_other_reviewer_posts(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            company, _ = await ensure_tenant(session, "c-review", "maker")
            company.posting_policy = "maker_checker"
            await create_entry(session, sample_payload("review-entry"), company_id="c-review", user_id="maker", request_id="review-create", confirm=False)
            submitted = await submit_journal_for_review(session, "review-entry", company_id="c-review", user_id="maker", request_id="submit")
            assert submitted.status == "pending_review"
    async with accounting_sessions() as session:
        async with session.begin():
            with pytest.raises(AccountingError) as exc:
                await approve_journal(session, "review-entry", company_id="c-review", user_id="maker", request_id="self-review")
            assert exc.value.code == "REVIEW_REQUIRED"
            await ensure_tenant(session, "other-company", "reviewer")
            session.add(CompanyMember(company_id="c-review", user_id="reviewer", role="accountant", status="active"))
            await session.flush()
            approved = await approve_journal(session, "review-entry", company_id="c-review", user_id="reviewer", request_id="approve")
            assert approved.status == "posted"
            assert approved.voucher_no
            assert approved.reviewed_by == "reviewer"


@pytest.mark.asyncio
async def test_reconciled_bank_source_stays_reconciled_after_post_and_close_checks_effect(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            await import_statements_core(session, company_id="c-effect", user_id="owner", rows=[{
                "transaction_date": "2026-07-01", "description": "服务收入", "amount": "100.10", "balance": "100.10",
            }], statement_id="effect", request_id="import")
            source = (await session.execute(select(SourceTransaction).where(SourceTransaction.source_type == "bank"))).scalar_one()
            await create_entry(session, sample_payload("effect-entry"), company_id="c-effect", user_id="owner", request_id="draft", confirm=False)
            await sync_bank_reconciliation(session, company_id="c-effect", user_id="owner", statement_id=source.external_key, entry_ids=["effect-entry"], transaction_date="2026-07-01", description="服务收入", signed_amount="100.10", request_id="match")
            pending = await period_close_checks(session, "c-effect", "2026-07")
            assert any(issue["type"] == "unposted_match" for issue in pending["checks"][2]["issues"])
            await post_journal(session, "effect-entry", company_id="c-effect", user_id="owner", request_id="post")
            assert source.status == "reconciled"
            ready = await period_close_checks(session, "c-effect", "2026-07")
            assert ready["canClose"] is True
            source.raw_payload = {**source.raw_payload, "signedAmount": "99.00"}
            await session.flush()
            blocked = await period_close_checks(session, "c-effect", "2026-07")
            assert any(issue["type"] == "bank_effect_mismatch" for issue in blocked["checks"][2]["issues"])
            with pytest.raises(AccountingError) as exc:
                await close_period(session, "c-effect", "2026-07", user_id="owner", request_id="close")
            assert exc.value.code == "CLOSE_CHECK_FAILED"

            # 兼容历史迁移数据：无 signedAmount 但有 raw_payload["amount"] 时不应误报 missing_direction
            source.raw_payload = {"amount": "100.10", "balance": "100.10"}
            await session.flush()
            migrated_ok = await period_close_checks(session, "c-effect", "2026-07")
            assert migrated_ok["canClose"] is True


@pytest.mark.asyncio
async def test_bank_import_duplicate_wechat_income_cannot_be_posted(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            for entry_id, direction, amount in [("wechat-income", "income", "5.00"), ("wechat-fee", "expense", "0.05")]:
                payload = sample_payload(entry_id, "2026-07-14")
                payload.update({"source": "wechat_bill", "sourceType": "wechat_bill", "wechat_order_no": "order-1", "direction": direction, "amount": amount})
                payload["voucherLines"] = {
                    "debit": [{"accountCode": "1012001" if direction == "income" else "5603002", "accountName": "微信待结算" if direction == "income" else "手续费", "amount": amount}],
                    "credit": [{"accountCode": "5001" if direction == "income" else "1012001", "accountName": "收入" if direction == "income" else "微信待结算", "amount": amount}],
                }
                await create_entry(session, payload, company_id="c-dupe", user_id="owner", request_id=entry_id, confirm=False)
            bank = sample_payload("bank-duplicate", "2026-07-15")
            bank.update({"source": "bank_import", "sourceType": "bank_import", "title": "财付通入账", "amount": "4.95"})
            for lines in bank["voucherLines"].values():
                lines[0]["amount"] = "4.95"
            await create_entry(session, bank, company_id="c-dupe", user_id="owner", request_id="bank", confirm=False)
            with pytest.raises(AccountingError) as exc:
                await post_journal(session, "bank-duplicate", company_id="c-dupe", user_id="owner", request_id="post-duplicate")
            assert exc.value.code == "POSSIBLE_DUPLICATE_WECHAT_INCOME"
            assert set(exc.value.context["candidateEntryIds"]) == {"wechat-income", "wechat-fee"}


@pytest.mark.asyncio
async def test_close_detects_actual_report_mapping_gap_even_when_journal_is_balanced(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-report-gap", "owner")
            session.add(ChartAccount(id="custom-asset", company_id="c-report-gap", code="1003", name="未映射资产", normal_side="debit", created_at="2026-07-01", updated_at="2026-07-01"))
            await session.flush()
            payload = sample_payload("unmapped-report")
            payload["voucherLines"] = {
                "debit": [{"accountCode": "1003", "accountName": "未映射资产", "amount": "100.10"}],
                "credit": [{"accountCode": "3001", "accountName": "实收资本", "amount": "100.10"}],
            }
            await create_entry(session, payload, company_id="c-report-gap", user_id="owner", request_id="report-entry", confirm=True)
            result = await period_close_checks(session, "c-report-gap", "2026-07")
            assert result["checks"][1]["status"] == "passed"
            assert result["checks"][3]["status"] == "blocking"
            assert any("资产负债表" in issue["message"] for issue in result["checks"][3]["issues"])
            assert result["canClose"] is False


@pytest.mark.asyncio
async def test_close_blocks_duplicate_bank_links_even_if_each_amount_matches(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            entry = await create_entry(session, sample_payload("duplicate-link"), company_id="c-duplicate-link", user_id="owner", request_id="entry", confirm=True)
            await import_statements_core(session, company_id="c-duplicate-link", user_id="owner", rows=[
                {"transaction_date": "2026-07-01", "description": "第一笔", "amount": "100.10", "balance": "100.10"},
                {"transaction_date": "2026-07-02", "description": "第二笔", "amount": "100.10", "balance": "200.20"},
            ], statement_id="duplicate-links", request_id="import")
            sources = (await session.execute(select(SourceTransaction).where(SourceTransaction.source_type == "bank"))).scalars().all()
            for source in sources:
                await sync_bank_reconciliation(session, company_id="c-duplicate-link", user_id="owner", statement_id=source.external_key, entry_ids=[entry.id], transaction_date=source.occurred_on, description=source.description, signed_amount="100.10", request_id=source.id)
            result = await period_close_checks(session, "c-duplicate-link", "2026-07")
            assert any(issue["type"] == "duplicate_match" for issue in result["checks"][2]["issues"])
            assert any(issue["type"] == "bank_balance_mismatch" for issue in result["checks"][2]["issues"])
            assert result["canClose"] is False


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
            # 凭证署名：自过账（owner_self_post）→ 制单=记账=创建人；无独立复核 → 审核人留空
            assert payload["createdByName"] == "owner-1"  # fixture 无 auth_users 行，回落 user_id
            assert payload["postedByName"] == "owner-1"
            assert payload["reviewedByName"] == ""


@pytest.mark.asyncio
async def test_serialize_prefers_nickname_for_voucher_names(accounting_sessions):
    """署名显示名：昵称优先于掩码手机号；未复核/未过账留空。"""
    from db.models import AuthUser

    async with accounting_sessions() as session:
        async with session.begin():
            session.add(
                AuthUser(
                    user_id="owner-1",
                    nickname="杨会计",
                    phone="13800001234",
                    created_at="2026-01-01T00:00:00",
                    last_login_at="2026-01-01T00:00:00",
                )
            )
            entry = await create_entry(
                session,
                sample_payload(),
                company_id="c1",
                user_id="owner-1",
                request_id="req-1",
                confirm=True,
            )
            payload = await serialize_journal(session, entry)
            assert payload["createdByName"] == "杨会计"
            assert payload["postedByName"] == "杨会计"

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
async def test_posted_entry_allows_billing_status_only_update(accounting_sessions):
    """已过账凭证：仅改开票状态走投影层放行；权威凭证行与其余字段组合仍不可改。"""
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
            metadata_before = dict(entry.metadata_json or {})

    async with accounting_sessions() as session:
        async with session.begin():
            updated = await update_draft(
                session,
                "entry-1",
                {"billingStatus": "issued"},
                company_id="c1",
                user_id="owner-1",
                request_id="req-2",
            )
            assert updated.status == "posted"
            payload = await serialize_journal(session, updated)
            assert payload["billingStatus"] == "issued"

    async with accounting_sessions() as session:
        async with session.begin():
            entry = await get_journal(session, "entry-1", company_id="c1")
            # 权威层 metadata 不被污染，凭证状态与版本不变
            assert dict(entry.metadata_json or {}) == metadata_before
            assert entry.status == "posted"
            projection = await session.get(LegacyEntryProjection, "entry-1")
            assert json.loads(projection.payload)["billingStatus"] == "issued"

    async with accounting_sessions() as session:
        async with session.begin():
            with pytest.raises(AccountingError) as exc:
                await update_draft(
                    session,
                    "entry-1",
                    {"billingStatus": "no_invoice", "category": "其他应收款"},
                    company_id="c1",
                    user_id="owner-1",
                    request_id="req-3",
                )
            assert exc.value.code == ENTRY_POSTED_IMMUTABLE


@pytest.mark.asyncio
async def test_posted_entry_payment_status_is_tracking_only(accounting_sessions):
    """付款状态只写投影、不动借贷；但去向与凭证科目矛盾（贷记 1002 却标未付/垫付）必须冲销重记。"""
    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-pay-status", "owner-1")
            await create_entry(
                session, sample_payload("pay-1"), company_id="c-pay-status",
                user_id="owner-1", request_id="req-1", confirm=True,
            )

    async with accounting_sessions() as session:
        async with session.begin():
            updated = await update_draft(
                session, "pay-1", {"paymentStatus": "paid"},
                company_id="c-pay-status", user_id="owner-1", request_id="req-2",
            )
            assert updated.status == "posted"
            payload = await serialize_journal(session, updated)
            assert payload["paymentStatus"] == "paid"

    for bad in ({"paymentStatus": "unpaid"}, {"payer": "boss_personal"}, {"paymentStatus": "paid", "amount": Decimal("1")}):
        async with accounting_sessions() as session:
            async with session.begin():
                with pytest.raises(AccountingError) as exc:
                    await update_draft(
                        session, "pay-1", bad,
                        company_id="c-pay-status", user_id="owner-1", request_id="req-3",
                    )
                assert exc.value.code == ENTRY_POSTED_IMMUTABLE

    # 开启 autoReverseRepost 时，自动红冲原凭证并按新去向重新生成已过账凭证
    async with accounting_sessions() as session:
        async with session.begin():
            reposted = await update_draft(
                session, "pay-1", {"paymentStatus": "unpaid", "autoReverseRepost": True},
                company_id="c-pay-status", user_id="owner-1", request_id="req-4",
            )
            assert reposted.id != "pay-1"
            assert reposted.status == "posted"
            reposted_payload = await serialize_journal(session, reposted)
            assert reposted_payload["paymentStatus"] == "unpaid"
            assert any(line["accountCode"] == "1122" for line in reposted_payload["voucherLines"]["debit"])
            orig = await get_journal(session, "pay-1", company_id="c-pay-status")
            assert orig.status == "reversed"


@pytest.mark.asyncio
async def test_duplicate_bank_entry_detection_blocks_repeat_bookkeeping(accounting_sessions):
    """等额同向、日期相近的银行存款凭证应被判为重复，阻止再记一笔。"""
    from services.bank_service import find_duplicate_bank_entries

    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-dup-bank", "owner-1")
            await create_entry(
                session, sample_payload("dup-1"), company_id="c-dup-bank",
                user_id="owner-1", request_id="req-1", confirm=True,
            )

            hits = await find_duplicate_bank_entries(
                session, "c-dup-bank", date(2026, 7, 2), Decimal("100.10")
            )
            assert [item["id"] for item in hits] == ["dup-1"]

            # 金额不同 / 日期超出 ±3 天 → 不算重复
            assert await find_duplicate_bank_entries(
                session, "c-dup-bank", date(2026, 7, 2), Decimal("100.11")
            ) == []
            assert await find_duplicate_bank_entries(
                session, "c-dup-bank", date(2026, 7, 10), Decimal("100.10")
            ) == []


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
            assert payload["voucherLines"]["debit"][0]["accountCode"] == "5001"


@pytest.mark.asyncio
async def test_closing_entry_with_container_code_still_posts(accounting_sessions):
    """期末结账类凭证携带容器科目码（如旧式 2211）时，创建与过账都不再卡末级校验。

    closing/reversal 是系统重放路径：忠实还原历史分录行，行里可能有后来才被
    拆出子级的父级科目。standard 用户凭证仍然严格末级。
    """
    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-closing-container", "owner-1")
            closing = await create_entry(
                session,
                {
                    "id": "closing-legacy-container",
                    "direction": "owner_transfer",
                    "amount": "5000.00",
                    "title": "历史容器科目结转",
                    "category": "期末损益结转",
                    "occurDate": "2026-07-31",
                    "sourceType": "closing_transfer",
                    "entryType": "closing",
                    "voucherLines": {
                        "debit": [{"accountCode": "5602001", "accountName": "管理人员职工薪酬", "amount": "5000.00"}],
                        "credit": [{"accountCode": "2211", "accountName": "应付职工薪酬", "amount": "5000.00"}],
                    },
                },
                company_id="c-closing-container",
                user_id="owner-1",
                request_id="req-closing-container",
                confirm=True,
            )
            assert closing.status == "posted"

            # 同一套分录走 standard 必须仍被 INVALID_ACCOUNT 拒绝（末级校验不放松）
            with pytest.raises(AccountingError) as exc:
                await create_entry(
                    session,
                    {
                        "id": "standard-container-rejected",
                        "direction": "expense",
                        "amount": "5000.00",
                        "title": "标准凭证不允许容器科目",
                        "category": "人力成本",
                        "occurDate": "2026-07-31",
                        "sourceType": "manual",
                        "voucherLines": {
                            "debit": [{"accountCode": "5602001", "accountName": "管理人员职工薪酬", "amount": "5000.00"}],
                            "credit": [{"accountCode": "2211", "accountName": "应付职工薪酬", "amount": "5000.00"}],
                        },
                    },
                    company_id="c-closing-container",
                    user_id="owner-1",
                    request_id="req-standard-container",
                    confirm=True,
                )
            assert exc.value.code == "INVALID_ACCOUNT"


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
                        "debit": [{"accountCode": "4301", "accountName": "研发支出-费用化支出（待归集）", "amount": "100.00"}],
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
            assert all(line["accountCode"] == "430100102" for line in corrected["voucherLines"]["debit"])
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
            await import_statements_core(session, company_id="c1", user_id="owner-1", rows=[{
                "transaction_date": "2026-07-01", "description": "咨询服务收入", "amount": "100.10", "balance": "100.10",
            }], statement_id="close-bank", request_id="close-import")
            source = (await session.execute(select(SourceTransaction).where(SourceTransaction.source_type == "bank"))).scalar_one()
            await sync_bank_reconciliation(session, company_id="c1", user_id="owner-1", statement_id=source.external_key, entry_ids=["entry-1"], transaction_date="2026-07-01", description="咨询服务收入", signed_amount="100.10", request_id="close-match")
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
        {"account_code": "3001", "account_name": "实收资本", "credit_balance": "50000.00"},
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
                    {"account_code": "3001", "account_name": "实收资本", "credit_balance": "1.00"},
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
                        {"account_code": "3001", "account_name": "实收资本", "credit_balance": "1.00"},
                    ],
                    user_id="owner-a",
                    step_up_token=token,
                    request_id="single-use-replay",
                    session=session,
                )
            assert replayed.value.code == "STEP_UP_REQUIRED"


async def _settlement_fixture(session, negative=False, orders=1):
    ids = []
    for index in range(orders):
        for kind, value, debit, credit in [("sale", "100.00", "1012001", "5001"), ("fee", "0.60", "5603002", "1012001")]:
            if negative:
                debit, credit = credit, debit
            entry_id = f"wx-{kind}-{index}"
            await create_entry(session, {"id": entry_id, "source": "wechat_bill", "amount": value,
                "direction": "income" if kind == "sale" else "expense", "occurDate": "2026-07-31",
                "title": f"微信{kind}", "wechat_order_no": f"order-{index}",
                "voucherLines": {"debit": [{"accountCode": debit, "amount": value}],
                                 "credit": [{"accountCode": credit, "amount": value}]}},
                company_id="c-settlement", user_id="owner", request_id=entry_id, confirm=True)
            ids.append(entry_id)
    net = money("99.40") * orders * (-1 if negative else 1)
    await import_statements_core(session, company_id="c-settlement", user_id="owner", rows=[{
        "transaction_date": "2026-08-01", "description": "财付通结算", "amount": money_text(net), "balance": money_text(net),
    }], statement_id="settlement", request_id="bank")
    bank = (await session.execute(select(SourceTransaction).where(SourceTransaction.source_type == "bank"))).scalar_one()
    return ids, dict(company_id="c-settlement", user_id="owner", statement_id=bank.external_key, request_id="match")


@pytest.mark.asyncio
@pytest.mark.parametrize("negative", [False, True])
async def test_settlement_cross_month_manual_post_idempotence(accounting_sessions, negative):
    async with accounting_sessions() as session:
        async with session.begin():
            ids, args = await _settlement_fixture(session, negative)
            await close_period(session, "c-settlement", "2026-07", user_id="owner", request_id="close-july")
            originals = [await serialize_journal(session, await get_journal(session, eid, company_id="c-settlement")) for eid in ids]
            result = await confirm_statement_match(session, **args, entry_ids=ids)
            assert result["settlementCreated"] and result["settlementStatus"] == "draft"
            entry = await get_journal(session, result["settlementEntryId"], company_id="c-settlement")
            assert entry.occurred_on == date(2026, 8, 1) and entry.voucher_no is None
            assert entry.metadata_json["direction"] == "owner_transfer"
            assert entry.metadata_json["originalEntryIds"] == ids
            with pytest.raises(AccountingError, match="不能直接修改"):
                await update_draft(session, entry.id, {"amount": "100"}, company_id="c-settlement", user_id="owner", request_id="edit")
            with pytest.raises(AccountingError, match="不能直接删除"):
                await delete_draft(session, entry.id, company_id="c-settlement", user_id="owner", request_id="delete")
            assert len((await session.execute(select(ReconciliationLink).where(ReconciliationLink.status == "confirmed"))).scalars().all()) == 3
            retry = await confirm_statement_match(session, **args, entry_ids=result["entryIds"])
            assert not retry["settlementCreated"] and retry["settlementEntryId"] == entry.id
            assert not (await period_close_checks(session, "c-settlement", "2026-08"))["canClose"]
            await post_journal(session, entry.id, company_id="c-settlement", user_id="owner", request_id="post")
            assert entry.voucher_no
            assert (await period_close_checks(session, "c-settlement", "2026-08"))["canClose"]
            for original in originals:
                current = await serialize_journal(session, await get_journal(session, original["id"], company_id="c-settlement"))
                for field in ("voucherLines", "occurDate", "voucherRef", "amount"):
                    assert current[field] == original[field]
            with pytest.raises(AccountingError, match="结算凭证已过账"):
                await confirm_statement_match(session, **args, entry_ids=[])


@pytest.mark.asyncio
async def test_invoice_partial_settlement_rejected(accounting_sessions):
    """发票转销必须是全额结算：流水与票面金额不一致时拒绝（INVOICE_PARTIAL_SETTLEMENT）。

    发票凭证的 2241 是整张票的金额；若允许部分付款直接转销，整张票被换成
    银行存款会虚减应付、虚增费用。必须拆分发票或补差额调整凭证。
    """
    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-inv-part", "owner-1")
            await create_entry(
                session,
                {
                    "id": "inv-fee-1",
                    "source": "invoice_import",
                    "sourceType": "invoice_import",
                    "direction": "expense",
                    "amount": "500.00",
                    "title": "5月办公费发票",
                    "category": "办公费",
                    "occurDate": "2026-05-20",
                    "billingStatus": "has_invoice",
                    "voucherLines": {
                        "debit": [{"accountCode": "5602999", "accountName": "管理费用-其他", "amount": "500.00"}],
                        "credit": [{"accountCode": "2241", "accountName": "其他应付款-发票待付款", "amount": "500.00"}],
                    },
                },
                company_id="c-inv-part", user_id="owner-1", request_id="req-inv", confirm=True,
            )
            await import_statements_core(session, company_id="c-inv-part", user_id="owner-1", rows=[{
                "transaction_date": "2026-06-01", "description": "支付办公费发票款（部分付款）",
                "amount": "-300.00", "balance": "-300.00",
            }], statement_id="stmt-part", request_id="bank")
            bank = (await session.execute(
                select(SourceTransaction).where(SourceTransaction.source_type == "bank")
            )).scalar_one()
            with pytest.raises(AccountingError) as exc_info:
                await confirm_statement_match(
                    session, company_id="c-inv-part", user_id="owner-1",
                    statement_id=bank.external_key, entry_ids=["inv-fee-1"],
                    request_id="match-part",
                )
            assert exc_info.value.code == "INVOICE_PARTIAL_SETTLEMENT"
            assert exc_info.value.context["invoiceAmount"] == "500.00"
            assert exc_info.value.context["statementAmount"] == "300.00"


@pytest.mark.asyncio
async def test_settlement_upgrade_legacy_projection_cancel_rematch(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            ids, args = await _settlement_fixture(session)
            sid = f"bank-settlement-{args['statement_id']}"
            session.add(LegacyEntryProjection(id=sid, company_id="c-settlement", user_id="owner",
                payload=json.dumps({"id": sid, "source": "bank_reconciliation_repair"}), created_at="old", updated_at="old"))
            await session.flush()
            for eid in [*ids, sid]:
                await session.execute(text("INSERT INTO bank_statement_matches VALUES (:bank,'c-settlement',:entry,NULL,NULL,'old')"), {"bank": args["statement_id"], "entry": eid})
            result = await confirm_statement_match(session, **args, entry_ids=None)
            assert result["settlementCreated"]
            await confirm_statement_match(session, **args, entry_ids=[])
            assert await session.get(JournalEntry, sid) is None
            assert (await session.execute(text("SELECT count(*) FROM bank_statement_matches"))).scalar_one() == 0
            assert (await confirm_statement_match(session, **args, entry_ids=ids))["settlementCreated"]


@pytest.mark.asyncio
async def test_settlement_large_group_and_failure_rollback(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            ids, args = await _settlement_fixture(session, orders=11)
        with pytest.raises(AccountingError, match="净额"):
            async with session.begin():
                await confirm_statement_match(session, **args, entry_ids=ids[:-1])
        async with session.begin():
            assert (await session.execute(text("SELECT count(*) FROM bank_statement_matches"))).scalar_one() == 0
            assert await session.get(JournalEntry, f"bank-settlement-{args['statement_id']}") is None
            result = await confirm_statement_match(session, **args, entry_ids=ids)
            assert len(result["entryIds"]) == 23
            await post_journal(session, result["settlementEntryId"], company_id="c-settlement", user_id="owner", request_id="post")
            # 7 月仍未关账，关账顺序门禁会拦下 8 月；这里只关心结算凭证本身不再阻断
            checks = await period_close_checks(session, "c-settlement", "2026-08")
            assert [c["status"] for c in checks["checks"] if c["code"] != "PERIOD_ORDER"] == ["passed"] * (len(checks["checks"]) - 1)


@pytest.mark.asyncio
async def test_settlement_respects_closed_arrival_period_and_review_policy(accounting_sessions):
    from db.models import AccountingPeriod
    async with accounting_sessions() as session:
        async with session.begin():
            ids, args = await _settlement_fixture(session)
            session.add(AccountingPeriod(company_id='c-settlement', period_start=date(2026,8,1), status='closed'))
        with pytest.raises(AccountingError) as exc:
            async with session.begin():
                await confirm_statement_match(session, **args, entry_ids=ids)
        assert exc.value.code == PERIOD_CLOSED
        async with session.begin():
            assert (await session.execute(text('SELECT count(*) FROM bank_statement_matches'))).scalar_one() == 0
            period=(await session.execute(select(AccountingPeriod).where(AccountingPeriod.period_start == date(2026,8,1)))).scalar_one()
            period.status='open'
            company=await session.get(Company, 'c-settlement')
            company.posting_policy='maker_checker'
            result=await confirm_statement_match(session, **args, entry_ids=ids)
            assert result['settlementStatus']=='draft'
            submitted=await submit_journal_for_review(session, result['settlementEntryId'], company_id='c-settlement', user_id='owner', request_id='submit')
            assert submitted.status=='pending_review'
            with pytest.raises(AccountingError) as exc:
                await approve_journal(session, submitted.id, company_id='c-settlement', user_id='owner', request_id='self')
            assert exc.value.code=='REVIEW_REQUIRED'
            await ensure_tenant(session, 'reviewer-company', 'reviewer')
            session.add(CompanyMember(company_id='c-settlement',user_id='reviewer',role='accountant',status='active'))
            await session.flush()
            await approve_journal(session, submitted.id, company_id='c-settlement',user_id='reviewer',request_id='approve')
            checks = await period_close_checks(session, 'c-settlement', '2026-08')
            assert all(c["status"] == "passed" for c in checks["checks"] if c["code"] != "PERIOD_ORDER")


@pytest.mark.asyncio
async def test_settlement_backfills_when_user_confirms(accounting_sessions):
    """流水所属期间已关账时，用户在导入/对账界面确认后可以补记，并留下台账。"""
    from db.models import AccountingPeriod, PeriodBackfill

    async with accounting_sessions() as session:
        async with session.begin():
            ids, args = await _settlement_fixture(session)
            session.add(AccountingPeriod(company_id="c-settlement", period_start=date(2026, 8, 1), status="closed"))
        async with session.begin():
            result = await confirm_statement_match(
                session, **args, entry_ids=ids,
                allow_backfill=True, backfill_reason="8 月流水 9 月才核对",
            )
        settlement = await session.get(JournalEntry, result["settlementEntryId"])
        ledger = (await session.execute(select(PeriodBackfill))).scalar_one()

    assert settlement is not None
    assert settlement.period_start == date(2026, 9, 1)  # 最早的未关账期间
    assert ledger.original_period == date(2026, 8, 1)
    assert ledger.posting_period == date(2026, 9, 1)
    assert ledger.reason == "8 月流水 9 月才核对"


@pytest.mark.asyncio
async def test_settlement_cannot_reuse_originals_for_another_bank(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            ids, args = await _settlement_fixture(session)
            await confirm_statement_match(session, **args, entry_ids=ids)
            await import_statements_core(session, company_id='c-settlement', user_id='owner', rows=[{
                'transaction_date':'2026-08-02','description':'财付通第二笔到账','amount':'99.40','balance':'198.80',
            }], statement_id='another-settlement', request_id='second')
            banks=(await session.execute(select(SourceTransaction).where(SourceTransaction.source_type=='bank'))).scalars().all()
            second=next(bank for bank in banks if bank.external_key!=args['statement_id'])
            with pytest.raises(AccountingError, match='已匹配其他银行流水'):
                await confirm_statement_match(session, **{**args,'statement_id':second.external_key}, entry_ids=ids)
            assert await session.get(JournalEntry, f'bank-settlement-{second.external_key}') is None


async def _pending_invoice_fixture(session, *, posted: bool):
    """一张待付款费用发票（借费用/贷2241）+ 对应金额的付款流水。"""
    await create_entry(session, {
        "id": "inv-pending-1", "source": "invoice_import", "amount": "300",
        "direction": "expense", "category": "信息技术服务费", "occurDate": "2026-07-10",
        "title": "发票-云服务", "billingStatus": "has_invoice",
        "voucherLines": {"debit": [{"accountCode": "5602015", "accountName": "管理费用-信息技术服务费", "amount": "300"}],
                         "credit": [{"accountCode": "2241", "accountName": "其他应付款-发票待付款", "amount": "300"}]},
    }, company_id="c-invoice-pay", user_id="owner", request_id="inv", confirm=posted)
    await import_statements_core(session, company_id="c-invoice-pay", user_id="owner", rows=[{
        "transaction_date": "2026-07-12", "description": "云服务付款", "amount": "-300.00", "balance": "-300.00",
    }], statement_id="invoice-batch", request_id="bank-inv")
    bank = (await session.execute(select(SourceTransaction).where(SourceTransaction.source_type == "bank"))).scalar_one()
    return dict(company_id="c-invoice-pay", user_id="owner", statement_id=bank.external_key, request_id="match-inv")


@pytest.mark.asyncio
async def test_pending_invoice_draft_match_swaps_payable_to_bank(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            args = await _pending_invoice_fixture(session, posted=False)
            result = await confirm_statement_match(session, **args, entry_ids=["inv-pending-1"])
            assert result["status"] == "matched"
            payload = await serialize_journal(session, await get_journal(session, "inv-pending-1", company_id=args["company_id"]))
            credits = {line["accountCode"] for line in payload["voucherLines"]["credit"]}
            assert credits == {"1002"} and "2241" not in credits
            assert payload["billingStatus"] == "reconciled"

            # 取消关联回滚为待付款
            await confirm_statement_match(session, **args, entry_ids=[])
            payload = await serialize_journal(session, await get_journal(session, "inv-pending-1", company_id=args["company_id"]))
            credits = {line["accountCode"] for line in payload["voucherLines"]["credit"]}
            assert credits == {"2241"} and "1002" not in credits


@pytest.mark.asyncio
async def test_pending_invoice_posted_match_creates_payment_voucher(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            args = await _pending_invoice_fixture(session, posted=True)
            result = await confirm_statement_match(session, **args, entry_ids=["inv-pending-1"])
            assert result["status"] == "matched"
            payment_id = f"invoice-payment-{args['statement_id']}-inv-pend"
            assert payment_id in result["entryIds"]
            payment = await serialize_journal(session, await get_journal(session, payment_id, company_id=args["company_id"]))
            codes = {line["accountCode"] for line in payment["voucherLines"]["debit"]}
            assert codes == {"2241"}
            codes = {line["accountCode"] for line in payment["voucherLines"]["credit"]}
            assert codes == {"1002"}
            # 原发票凭证保持不变（posted 不可变），净额经付款凭证与流水平衡
            invoice = await serialize_journal(session, await get_journal(session, "inv-pending-1", company_id=args["company_id"]))
            assert {line["accountCode"] for line in invoice["voucherLines"]["credit"]} == {"2241"}


@pytest.mark.asyncio
async def test_serialize_reflects_archive_flag_from_legacy_projection(accounting_sessions):
    """归档标志由导入/迁移路径写在投影 payload 上，/api/record/entries 必须能看到，
    否则发票管理页的"仅归档"判定失效（显示参与记账、提供不该有的操作）。"""
    async with accounting_sessions() as session:
        async with session.begin():
            entry = await create_entry(
                session,
                sample_payload("archive-reflect-entry"),
                company_id="c-archive-reflect",
                user_id="owner-archive-reflect",
                request_id="req-archive-reflect",
                confirm=True,
            )
            projection = await session.get(LegacyEntryProjection, entry.id)
            assert projection is not None
            payload = json.loads(projection.payload)
            payload["is_archive_only"] = True
            payload["isArchiveOnly"] = True
            projection.payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            await session.flush()

            after = await serialize_journal(session, entry)
            assert after["is_archive_only"] is True
            assert after["isArchiveOnly"] is True


@pytest.mark.asyncio
async def test_reversal_defaults_to_original_period(accounting_sessions):
    """红字冲销默认落原凭证期间：与原凭证同期相消，月度报表不失真。"""
    async with accounting_sessions() as session:
        async with session.begin():
            entry = await create_entry(
                session,
                sample_payload("rev-period-entry", occurred_on="2026-07-10"),
                company_id="c-rev-period",
                user_id="owner",
                request_id="req-rev-period",
                confirm=True,
            )
            await post_journal(session, entry.id, company_id="c-rev-period", user_id="owner", request_id="post")
            reversal = await reverse_journal(
                session, entry.id, company_id="c-rev-period", user_id="owner",
                request_id="rev", reason="测试冲销",
            )
            assert reversal.occurred_on == date(2026, 7, 10)
            assert reversal.status == "posted"


@pytest.mark.asyncio
async def test_reversal_falls_back_to_today_when_original_period_closed(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            entry = await create_entry(
                session,
                {**sample_payload("rev-closed-entry", occurred_on="2026-07-10"), "sourceType": "manual"},
                company_id="c-rev-closed",
                user_id="owner",
                request_id="req-rev-closed",
                confirm=True,
            )
            await post_journal(session, entry.id, company_id="c-rev-closed", user_id="owner", request_id="post")
            # 模拟原期间已关账（不实际跑 close_period，避免其他检查项阻断）
            from sqlalchemy import text as _text

            await session.execute(_text(
                "UPDATE accounting_periods SET status='closed' WHERE company_id='c-rev-closed'"
            ))
            reversal = await reverse_journal(
                session, entry.id, company_id="c-rev-closed", user_id="owner",
                request_id="rev", reason="测试冲销",
            )
            # 原期间已关账 → 回落到当天（当期调整口径）
            assert reversal.occurred_on == date.today()
            assert reversal.status == "posted"


@pytest.mark.asyncio
async def test_auto_closing_transfer_reopen_and_year_end_retained_earnings(accounting_sessions):
    """验证关账全自动损益结转（5xxx -> 3103）、报表防重算、反结账自动红冲及 12 月末结转未分配利润（3103 -> 3104006）。"""
    from services.accounting_core_service import posted_entries_for_reports, reopen_period
    from services.tax_filing_export_service import build_statement_data_from_entries, parse_period

    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-auto-close", "owner-1")
            # 1. 2026-07 录入一笔收入 1000.00 与一笔管理费用 300.00（均走现金 1001 免银行流水核销阻断）
            await create_entry(
                session,
                {
                    "id": "inc-july",
                    "direction": "income",
                    "amount": "1000.00",
                    "title": "7月技术服务收入",
                    "category": "服务收入",
                    "occurDate": "2026-07-10",
                    "sourceType": "manual",
                    "voucherLines": {
                        "debit": [{"accountCode": "1001", "accountName": "库存现金", "amount": "1000.00"}],
                        "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": "1000.00"}],
                    },
                },
                company_id="c-auto-close",
                user_id="owner-1",
                request_id="req-inc-july",
                confirm=True,
            )
            await create_entry(
                session,
                {
                    "id": "exp-july",
                    "direction": "expense",
                    "amount": "300.00",
                    "title": "7月办公耗材",
                    "category": "办公费",
                    "occurDate": "2026-07-15",
                    "sourceType": "manual",
                    "voucherLines": {
                        "debit": [{"accountCode": "5602999", "accountName": "管理费用-其他", "amount": "300.00"}],
                        "credit": [{"accountCode": "1001", "accountName": "库存现金", "amount": "300.00"}],
                    },
                },
                company_id="c-auto-close",
                user_id="owner-1",
                request_id="req-exp-july",
                confirm=True,
            )

            # 执行 7 月关账：应自动生成期末损益结转凭证
            close_july = await close_period(
                session, "c-auto-close", "2026-07", user_id="owner-1", request_id="req-close-july"
            )
            assert close_july["status"] == "closed"
            assert close_july["closingCount"] == 1
            assert len(close_july["closingVoucherRefs"]) == 1

            # 检查关账快照：5001 与 5602999 余额归零，3103 本年利润为贷方 700.00（借减贷口径为 -700.00）
            snap_accounts = {row["accountCode"]: row["balance"] for row in close_july["snapshot"]["accounts"]}
            assert snap_accounts["5001"] == "0.00"
            assert snap_accounts["5602999"] == "0.00"
            assert snap_accounts["3103"] == "-700.00"

            # 检查报表服务：利润表仍显示收入 1000、管理费用 300、净利润 700，资产负债表未分配利润不重复累加
            report_entries = await posted_entries_for_reports(session, "c-auto-close")
            opening_items = await get_opening_balances("c-auto-close", 2026, session=session)
            stmt_july = build_statement_data_from_entries(
                report_entries, opening_items, parse_period("2026-07", "month"), {"name": "测试公司", "taxId": "91310000TEST"}
            )
            assert stmt_july.profit_current["revenue"] == Decimal("1000.00")
            assert stmt_july.profit_current["management_expense"] == Decimal("300.00")
            assert stmt_july.profit_current["net_profit"] == Decimal("700.00")
            assert stmt_july.balance_current["retained_earnings"] == Decimal("700.00")

            # 2. 执行 7 月反结账：自动红字冲销期末结转凭证
            token = await issue_step_up_token(
                session, company_id="c-auto-close", user_id="owner-1", action="reopen_period"
            )
            reopen_res = await reopen_period(
                session,
                "c-auto-close",
                "2026-07",
                user_id="owner-1",
                request_id="req-reopen-july",
                reason="补录7月差旅费",
                step_up_token=token,
            )
            assert reopen_res["status"] == "reopened"
            assert len(reopen_res["reversedClosingEntryIds"]) == 1

            # 补录一笔 100.00 费用后再次关账
            await create_entry(
                session,
                {
                    "id": "exp-july-2",
                    "direction": "expense",
                    "amount": "100.00",
                    "title": "7月补录差旅",
                    "category": "差旅费",
                    "occurDate": "2026-07-20",
                    "sourceType": "manual",
                    "voucherLines": {
                        "debit": [{"accountCode": "5602999", "accountName": "管理费用-其他", "amount": "100.00"}],
                        "credit": [{"accountCode": "1001", "accountName": "库存现金", "amount": "100.00"}],
                    },
                },
                company_id="c-auto-close",
                user_id="owner-1",
                request_id="req-exp-july-2",
                confirm=True,
            )
            reclose_july = await close_period(
                session, "c-auto-close", "2026-07", user_id="owner-1", request_id="req-reclose-july"
            )
            snap_after_reclose = {row["accountCode"]: row["balance"] for row in reclose_july["snapshot"]["accounts"]}
            assert snap_after_reclose["5001"] == "0.00"
            assert snap_after_reclose["5602999"] == "0.00"
            assert snap_after_reclose["3103"] == "-600.00"

            # 3. 2026-12 录入一笔收入 400.00 并执行年末关账：生成月度损益结转 + 年末本年利润结转未分配利润（共 2 张结转凭证）
            await create_entry(
                session,
                {
                    "id": "inc-dec",
                    "direction": "income",
                    "amount": "400.00",
                    "title": "12月咨询收入",
                    "category": "服务收入",
                    "occurDate": "2026-12-18",
                    "sourceType": "manual",
                    "voucherLines": {
                        "debit": [{"accountCode": "1001", "accountName": "库存现金", "amount": "400.00"}],
                        "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": "400.00"}],
                    },
                },
                company_id="c-auto-close",
                user_id="owner-1",
                request_id="req-inc-dec",
                confirm=True,
            )
            close_dec = await close_period(
                session, "c-auto-close", "2026-12", user_id="owner-1", request_id="req-close-dec"
            )
            assert close_dec["status"] == "closed"
            assert close_dec["closingCount"] == 2
            snap_dec = {row["accountCode"]: row["balance"] for row in close_dec["snapshot"]["accounts"]}
            assert snap_dec["5001"] == "0.00"
            assert snap_dec["3103"] == "0.00"
            assert snap_dec["3104006"] == "-1000.00"



@pytest.mark.asyncio
async def test_entry_before_start_date_rejected(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            company, _ = await ensure_tenant(session, "c-start-bound", "owner-start-bound")
            company.payload = {"startDate": "2026-07-01"}
            with pytest.raises(AccountingError) as exc_info:
                await create_entry(
                    session,
                    sample_payload("early-entry", "2026-06-15"),
                    company_id="c-start-bound",
                    user_id="owner-start-bound",
                    request_id="req-early",
                    confirm=True,
                )
            assert exc_info.value.code == ENTRY_BEFORE_START_DATE
            assert exc_info.value.context == {"startDate": "2026-07-01", "occurDate": "2026-06-15"}


@pytest.mark.asyncio
async def test_opening_entry_exempt_from_start_date(accounting_sessions):
    """期初凭证承载启用日之前的历史，是启用日期约束的豁免通道。"""
    async with accounting_sessions() as session:
        async with session.begin():
            company, _ = await ensure_tenant(session, "c-start-opening", "owner-start-opening")
            company.payload = {"startDate": "2026-07-01"}
            entry = await create_entry(
                session,
                {
                    **sample_payload("opening-entry", "2026-06-01"),
                    "entryType": "opening",
                    "sourceType": "opening_balance",
                },
                company_id="c-start-opening",
                user_id="owner-start-opening",
                request_id="req-opening",
                confirm=True,
            )
            assert entry.occurred_on == date(2026, 6, 1)


@pytest.mark.asyncio
async def test_update_draft_date_before_start_rejected(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            company, _ = await ensure_tenant(session, "c-start-update", "owner-start-update")
            company.payload = {"startDate": "2026-07-01"}
            await create_entry(
                session,
                sample_payload("draft-move", "2026-07-05"),
                company_id="c-start-update",
                user_id="owner-start-update",
                request_id="req-draft-move",
                confirm=False,
            )
            with pytest.raises(AccountingError) as exc_info:
                await update_draft(
                    session,
                    "draft-move",
                    {"occurDate": "2026-06-20"},
                    company_id="c-start-update",
                    user_id="owner-start-update",
                    request_id="req-draft-move-2",
                )
            assert exc_info.value.code == ENTRY_BEFORE_START_DATE


@pytest.mark.asyncio
async def test_reverse_explicit_date_before_start_rejected(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            company, _ = await ensure_tenant(session, "c-start-reverse", "owner-start-reverse")
            company.payload = {"startDate": "2026-07-01"}
            await create_entry(
                session,
                sample_payload("posted-rev", "2026-07-05"),
                company_id="c-start-reverse",
                user_id="owner-start-reverse",
                request_id="req-rev",
                confirm=True,
            )
            with pytest.raises(AccountingError) as exc_info:
                await reverse_journal(
                    session,
                    "posted-rev",
                    company_id="c-start-reverse",
                    user_id="owner-start-reverse",
                    request_id="req-rev-2",
                    reason="错账更正",
                    reversal_date="2026-06-20",
                )
            assert exc_info.value.code == ENTRY_BEFORE_START_DATE


@pytest.mark.asyncio
async def test_start_date_move_conflicts_with_existing_entries():
    """启用日期后移不得越过最早非期初分录；期初凭证不参与比较。"""
    import aiosqlite

    from core.errors import START_DATE_CONFLICT
    from services.account_service import _assert_start_date_not_before_entries

    async with aiosqlite.connect(":memory:") as conn:
        await conn.execute(
            "CREATE TABLE journal_entries (company_id TEXT, occurred_on TEXT, entry_type TEXT)"
        )
        await conn.execute("INSERT INTO journal_entries VALUES ('c-x', '2026-03-01', 'standard')")
        await conn.commit()
        with pytest.raises(AccountingError) as exc_info:
            await _assert_start_date_not_before_entries(conn, "c-x", "2026-07-01")
        assert exc_info.value.code == START_DATE_CONFLICT
        assert exc_info.value.context == {"startDate": "2026-07-01", "earliestEntryDate": "2026-03-01"}
        # 前移或对齐最早分录不受限
        await _assert_start_date_not_before_entries(conn, "c-x", "2026-03-01")
        await _assert_start_date_not_before_entries(conn, "c-x", "2026-01-01")
        # 无分录的账套不受限
        await conn.execute("DELETE FROM journal_entries")
        await conn.commit()
        await _assert_start_date_not_before_entries(conn, "c-x", "2026-07-01")
        # 期初凭证承载启用前历史，不参与比较
        await conn.execute("INSERT INTO journal_entries VALUES ('c-x', '2026-03-01', 'opening')")
        await conn.commit()
        await _assert_start_date_not_before_entries(conn, "c-x", "2026-07-01")


@pytest.mark.asyncio
async def test_quarter_close_transfers_exempt_vat_to_non_operating_income(accounting_sessions):
    """季度销售额未达起征点：已计提的小规模增值税应转入营业外收入，不能永久挂负债。"""
    from services.accounting_core_service import posted_entries_for_reports
    from services.tax_filing_export_service import build_statement_data_from_entries, parse_period

    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-vat-close", "owner-1")
            await create_entry(
                session,
                {
                    "id": "inc-q3-vat",
                    "direction": "income",
                    "amount": "1010.00",
                    "title": "9月技术服务收入（含税）",
                    "category": "主营业务收入",
                    "occurDate": "2026-09-10",
                    "sourceType": "manual",
                    "vatSeparated": True,
                    "vatRate": "0.01",
                    "vatAmount": "10.00",
                    "voucherLines": {
                        "debit": [{"accountCode": "1001", "accountName": "库存现金", "amount": "1010.00"}],
                        "credit": [
                            {"accountCode": "5001", "accountName": "主营业务收入", "amount": "1000.00"},
                            {"accountCode": "2221026", "accountName": "应交增值税（小规模纳税人专用）", "amount": "10.00"},
                        ],
                    },
                },
                company_id="c-vat-close",
                user_id="owner-1",
                request_id="req-inc-q3-vat",
                confirm=True,
            )

            result = await close_period(
                session, "c-vat-close", "2026-09", user_id="owner-1", request_id="req-close-q3"
            )
            assert result["status"] == "closed"
            # 一张免征增值税转出 + 一张损益结转
            assert result["closingCount"] == 2

            balances = {row["accountCode"]: row["balance"] for row in result["snapshot"]["accounts"]}
            assert balances["2221026"] == "0.00"
            assert balances["5301"] == "0.00"
            # 收入 1000 + 免税转入 10 全部进入本年利润
            assert balances["3103"] == "-1010.00"

            entries = await posted_entries_for_reports(session, "c-vat-close")
            opening_items = await get_opening_balances("c-vat-close", 2026, session=session)
            stmt = build_statement_data_from_entries(
                entries, opening_items, parse_period("2026-09", "month"),
                {"name": "测试公司", "taxId": "91310000TEST"},
            )
            assert stmt.profit_current["revenue"] == Decimal("1000.00")
            assert stmt.profit_current["non_operating_income"] == Decimal("10.00")
            assert stmt.profit_current["net_profit"] == Decimal("1010.00")

            # 反结账：免征转出凭证随期末结转一起红字冲销，2221026 恢复挂账
            from services.accounting_core_service import reopen_period
            token = await issue_step_up_token(
                session, company_id="c-vat-close", user_id="owner-1", action="reopen_period"
            )
            reopen_res = await reopen_period(
                session, "c-vat-close", "2026-09",
                user_id="owner-1", request_id="req-reopen-vat",
                reason="补充进项发票信息", step_up_token=token,
            )
            assert reopen_res["status"] == "reopened"
            # reversedClosingEntryIds 是新冲销凭证；免征转出凭证必须有对应红字冲销
            exemption_id = "closing-vat-exempt-c-vat-close-2026Q3-v1"
            reversal = (await session.execute(
                select(JournalEntry).where(JournalEntry.reversal_of_id == exemption_id)
            )).scalar_one_or_none()
            assert reversal is not None and reversal.status == "posted"

            reclose = await close_period(
                session, "c-vat-close", "2026-09", user_id="owner-1", request_id="req-reclose-vat"
            )
            assert reclose["status"] == "closed"
            reclose_balances = {row["accountCode"]: row["balance"] for row in reclose["snapshot"]["accounts"]}
            assert reclose_balances["2221026"] == "0.00"
            assert reclose_balances["3103"] == "-1010.00"


@pytest.mark.asyncio
async def test_close_does_not_transfer_vat_above_threshold(accounting_sessions):
    """超过起征点：增值税是真实负债，不得转入损益。"""
    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-vat-taxable", "owner-1")
            await create_entry(
                session,
                {
                    "id": "inc-q3-taxable",
                    "direction": "income",
                    "amount": "404000.00",
                    "title": "季度服务收入（含税）",
                    "category": "主营业务收入",
                    "occurDate": "2026-09-10",
                    "sourceType": "manual",
                    "vatSeparated": True,
                    "voucherLines": {
                        "debit": [{"accountCode": "1001", "accountName": "库存现金", "amount": "404000.00"}],
                        "credit": [
                            {"accountCode": "5001", "accountName": "主营业务收入", "amount": "400000.00"},
                            {"accountCode": "2221026", "accountName": "应交增值税（小规模纳税人专用）", "amount": "4000.00"},
                        ],
                    },
                },
                company_id="c-vat-taxable",
                user_id="owner-1",
                request_id="req-inc-q3-taxable",
                confirm=True,
            )

            result = await close_period(
                session, "c-vat-taxable", "2026-09", user_id="owner-1", request_id="req-close-taxable"
            )
            assert result["status"] == "closed"
            assert result["closingCount"] == 1
            balances = {row["accountCode"]: row["balance"] for row in result["snapshot"]["accounts"]}
            # 快照 balance 为借减贷口径，负债类为负数
            assert balances["2221026"] == "-4000.00"
            assert balances["3103"] == "-400000.00"


@pytest.mark.asyncio
async def test_research_expense_participates_in_profit_closing(accounting_sessions):
    """研发费用化支出 4301001* 必须结转本年利润，否则账载利润与利润表净利润不一致。"""
    from services.accounting_core_service import posted_entries_for_reports
    from services.tax_filing_export_service import build_statement_data_from_entries, parse_period

    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-rd-close", "owner-1")
            from db.models import RdProject
            session.add(RdProject(
                id="rd-1", company_id="c-rd-close", code="RD-001", name="研发项目一",
                start_date=date(2026, 1, 1), status="active",
                initiation_document_status="complete", created_by="owner-1",
            ))
            await session.flush()
            await create_entry(
                session,
                {
                    "id": "rd-aug",
                    "direction": "expense",
                    "amount": "500.00",
                    "title": "8月研发人员人工",
                    "category": "研发支出-费用化支出",
                    "occurDate": "2026-08-10",
                    "sourceType": "manual",
                    "rdProjectId": "rd-1",
                    "rdExpenseType": "personnel",
                    "voucherLines": {
                        "debit": [{
                            "accountCode": "430100101",
                            "accountName": "研发支出-人员人工",
                            "amount": "500.00",
                            "rdDetail": {
                                "projectId": "rd-1", "expenseType": "personnel",
                                "originalAmount": "500.00", "allocationMethod": "direct",
                            },
                        }],
                        "credit": [{"accountCode": "1001", "accountName": "库存现金", "amount": "500.00"}],
                    },
                },
                company_id="c-rd-close",
                user_id="owner-1",
                request_id="req-rd-aug",
                confirm=True,
            )

            result = await close_period(
                session, "c-rd-close", "2026-08", user_id="owner-1", request_id="req-close-aug"
            )
            assert result["status"] == "closed"
            balances = {row["accountCode"]: row["balance"] for row in result["snapshot"]["accounts"]}
            assert balances["430100101"] == "0.00"
            assert balances["3103"] == "500.00"

            entries = await posted_entries_for_reports(session, "c-rd-close")
            opening_items = await get_opening_balances("c-rd-close", 2026, session=session)
            stmt = build_statement_data_from_entries(
                entries, opening_items, parse_period("2026-08", "month"),
                {"name": "测试公司", "taxId": "91310000TEST"},
            )
            assert stmt.profit_current["research_expense"] == Decimal("500.00")
            assert stmt.profit_current["net_profit"] == Decimal("-500.00")


@pytest.mark.asyncio
async def test_close_blocked_by_preceding_month_with_only_bank_activity(accounting_sessions, monkeypatch):
    """更早的月份只有银行流水、一张凭证都没有时也要拦住后一个月的关账。

    否则该月的流水不进账实核对、损益不结转，后一个月却照常结转，
    月末资产负债表的本年利润口径会缺一块。
    """
    class _FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        async def fetchall(self):
            return self._rows

    class _FakeConn:
        async def execute(self, sql, params=None):
            if "bank_statements" in sql:
                return _FakeCursor([{"month": "2026-08"}])
            return _FakeCursor([])

        async def close(self):
            pass

    async def fake_get_conn():
        return _FakeConn()

    monkeypatch.setattr("services.database.get_conn", fake_get_conn)

    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-order-bank", "owner-1")
            await create_entry(
                session,
                {
                    "id": "order-sep-inc", "direction": "income", "amount": "100.00",
                    "title": "9 月收入", "category": "主营业务收入",
                    "occurDate": "2026-09-10", "sourceType": "manual",
                    "voucherLines": {
                        "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "100.00"}],
                        "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": "100.00"}],
                    },
                },
                company_id="c-order-bank", user_id="owner-1",
                request_id="req-order-sep", confirm=True,
            )
            checks = await period_close_checks(session, "c-order-bank", "2026-09")
            order = next(item for item in checks["checks"] if item["code"] == "PERIOD_ORDER")
            assert order["status"] == "blocking"
            assert order["earliestOpenMonth"] == "2026-08"

            with pytest.raises(AccountingError) as exc:
                await close_period(session, "c-order-bank", "2026-09", user_id="owner-1", request_id="req-order-1")
            assert exc.value.code == "PERIOD_OUT_OF_ORDER"

            session.add(AccountingPeriod(company_id="c-order-bank", period_start=date(2026, 8, 1), status="closed"))
            await session.flush()
            checks_after = await period_close_checks(session, "c-order-bank", "2026-09")
            order_after = next(item for item in checks_after["checks"] if item["code"] == "PERIOD_ORDER")
            assert order_after["status"] == "passed"


@pytest.mark.asyncio
async def test_pre_book_start_months_are_not_counted_as_pending_close(accounting_sessions, monkeypatch):
    """启用日期之前的流水/工资表不算「更早的未关账月份」。

    账套 3 月启用、1 月导进一批历史流水时，曾把 1 月列成待结账期间：
    期次选择器里冒出「2026年1月」，体检 PERIOD_ORDER 判 blocking，
    3 月永远结不掉（PERIOD_OUT_OF_ORDER）。启用日期是记账起点，
    这些月份不在本账套的记账范围。
    """
    class _FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        async def fetchall(self):
            return self._rows

    class _FakeConn:
        async def execute(self, sql, params=None):
            if "bank_statements" in sql:
                return _FakeCursor([{"month": "2026-01"}])
            return _FakeCursor([{"month": "2026-02"}])

        async def close(self):
            pass

    async def fake_get_conn():
        return _FakeConn()

    monkeypatch.setattr("services.database.get_conn", fake_get_conn)

    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-start-floor", "owner-1")
            company = await session.get(Company, "c-start-floor")
            company.payload = {**(company.payload or {}), "startDate": "2026-03-01"}
            session.add(AccountingPeriod(company_id="c-start-floor", period_start=date(2026, 3, 1), status="open"))
            await session.flush()

        async with session.begin():
            periods = await available_periods(session, "c-start-floor")
            # 启用前的 1、2 月不出现在期次选择器里
            assert [row["month"] for row in periods["months"]] == ["2026-03"]
            # 因此也不构成「更早的未关账月份」：3 月结账时不会被 1、2 月拦住
            assert await preceding_open_months(session, "c-start-floor", date(2026, 3, 1)) == (0, None)
            # 但数据不能凭空消失：体检的提示项要报出来
            activity = await pre_book_start_activity(session, "c-start-floor")
            assert activity["count"] == 2
            assert activity["bankCount"] == 1
            assert activity["salaryCount"] == 1
            assert activity["months"] == ["2026-01", "2026-02"]

            checks = await period_close_checks(session, "c-start-floor", "2026-03")
            order = next(item for item in checks["checks"] if item["code"] == "PERIOD_ORDER")
            assert order["status"] == "passed"
            notice = next(item for item in checks["notices"] if item["code"] == "PRE_BOOK_START_ACTIVITY")
            assert notice["count"] == 2
            assert notice["bookStartDate"] == "2026-03-01"
            assert "2026-03-01" in notice["message"]


@pytest.mark.asyncio
async def test_close_rejects_period_before_book_start(accounting_sessions):
    """结启用日期之前的月份要被明确拒绝，且不留一条脏账期行。"""
    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-close-floor", "owner-1")
            company = await session.get(Company, "c-close-floor")
            company.payload = {**(company.payload or {}), "startDate": "2026-03-01"}
            await session.flush()

        async with session.begin():
            with pytest.raises(AccountingError) as exc:
                await close_period(
                    session, "c-close-floor", "2026-01", user_id="owner-1", request_id="req-floor-1"
                )
            assert exc.value.code == "PERIOD_BEFORE_BOOK_START"
            assert exc.value.status_code == 422
            assert exc.value.context["bookStartMonth"] == "2026-03"

        async with session.begin():
            rows = (
                await session.execute(
                    select(AccountingPeriod).where(AccountingPeriod.company_id == "c-close-floor")
                )
            ).scalars().all()
            assert rows == []


@pytest.mark.asyncio
async def test_sequential_close_and_reopen_order(accounting_sessions):
    """不允许先结 3 月再结 1 月，也不允许反结账早于已结的后续月份。"""
    from services.accounting_core_service import reopen_period

    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-seq-close", "owner-1")
            for month, entry_id in (("2026-01", "seq-inc-jan"), ("2026-03", "seq-inc-mar")):
                await create_entry(
                    session,
                    {
                        "id": entry_id, "direction": "income", "amount": "100.00",
                        "title": f"{month} 收入", "category": "主营业务收入",
                        "occurDate": f"{month}-10", "sourceType": "manual",
                        "vatSeparated": True,
                        "voucherLines": {
                            "debit": [{"accountCode": "1001", "accountName": "库存现金", "amount": "100.00"}],
                            "credit": [
                                {"accountCode": "5001", "accountName": "主营业务收入", "amount": "99.01"},
                                {"accountCode": "2221026", "accountName": "应交增值税（小规模纳税人专用）", "amount": "0.99"},
                            ],
                        },
                    },
                    company_id="c-seq-close", user_id="owner-1",
                    request_id=f"req-{entry_id}", confirm=True,
                )
            with pytest.raises(AccountingError) as exc_info:
                await close_period(session, "c-seq-close", "2026-03", user_id="owner-1", request_id="req-seq-1")
            assert exc_info.value.code == "PERIOD_OUT_OF_ORDER"

            await close_period(session, "c-seq-close", "2026-01", user_id="owner-1", request_id="req-seq-2")
            await close_period(session, "c-seq-close", "2026-03", user_id="owner-1", request_id="req-seq-3")

            token = await issue_step_up_token(
                session, company_id="c-seq-close", user_id="owner-1", action="reopen_period"
            )
            with pytest.raises(AccountingError) as reopen_exc:
                await reopen_period(
                    session, "c-seq-close", "2026-01", user_id="owner-1",
                    request_id="req-seq-4", reason="补一笔 1 月费用", step_up_token=token,
                )
            assert reopen_exc.value.code == "LATER_PERIOD_CLOSED"


@pytest.mark.asyncio
async def test_fiscal_year_close_ignores_periods_before_book_start(accounting_sessions):
    """年中建账：启用日期之前的月份不该挡住年度结账。

    3 月启用的账套，1、2 月本来就不会被关账；早年误建的账期行也不能让
    年度结账永久卡在「还有 2 个月未关账」。
    """
    from services.fiscal_year_service import close_fiscal_year

    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-year-floor", "owner-1")
            company = await session.get(Company, "c-year-floor")
            company.payload = {**(company.payload or {}), "startDate": "2026-03-01"}
            # 误操作留下的启用前月份（未关账），以及按期结完的 3~12 月
            session.add(AccountingPeriod(company_id="c-year-floor", period_start=date(2026, 1, 1), status="open"))
            for month in range(3, 13):
                session.add(
                    AccountingPeriod(
                        company_id="c-year-floor", period_start=date(2026, month, 1), status="closed"
                    )
                )
            await session.flush()

            result = await close_fiscal_year(
                session, "c-year-floor", 2026, user_id="owner-1", request_id="req-fyc-floor"
            )
            assert result["status"] == "closed"


@pytest.mark.asyncio
async def test_fiscal_year_close_locks_and_derives_next_year_opening(accounting_sessions):
    """年度结账：锁定不能再入账；下年期初从历史派生而不是生成 opening 凭证。"""
    from services.accounting_core_service import posted_entries_for_reports, reopen_period
    from services.fiscal_year_service import (
        assert_fiscal_year_open,
        close_fiscal_year,
        derive_year_opening_balances,
        reopen_fiscal_year,
    )
    from services.opening_balance_service import get_opening_balances
    from services.tax_filing_export_service import (
        build_statement_data_from_entries, parse_period,
    )

    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-year-close", "owner-1")
            for month, amount in (("2026-06", "1000.00"), ("2026-12", "500.00")):
                await create_entry(
                    session,
                    {
                        "id": f"year-inc-{month}", "direction": "income", "amount": amount,
                        "title": f"{month} 收入", "category": "主营业务收入",
                        "occurDate": f"{month}-10", "sourceType": "manual",
                        "vatSeparated": True,
                        "voucherLines": {
                            "debit": [{"accountCode": "1001", "accountName": "库存现金", "amount": amount}],
                            "credit": [
                                {"accountCode": "5001", "accountName": "主营业务收入",
                                 "amount": money(money(amount) / Decimal("1.01"))},
                                {"accountCode": "2221026", "accountName": "应交增值税（小规模纳税人专用）",
                                 "amount": money(money(amount) - money(money(amount) / Decimal("1.01")))},
                            ],
                        },
                    },
                    company_id="c-year-close", user_id="owner-1",
                    request_id=f"req-year-inc-{month}", confirm=True,
                )
                await close_period(
                    session, "c-year-close", month, user_id="owner-1", request_id=f"req-close-{month}"
                )

            result = await close_fiscal_year(
                session, "c-year-close", 2026, user_id="owner-1", request_id="req-fyc-2026"
            )
            assert result["status"] == "closed"

            # 已结年度禁止再入账：即使把 12 月反结账成 reopened（期间可写），
            # 年度锁仍然生效——期间控制拦不住的场景由年度锁兜底
            await assert_fiscal_year_open(session, "c-year-close", 2027)
            token_reopen = await issue_step_up_token(
                session, company_id="c-year-close", user_id="owner-1", action="reopen_period"
            )
            await reopen_period(
                session, "c-year-close", "2026-12", user_id="owner-1",
                request_id="req-reopen-dec", reason="年度已结，先反结账期间验证年度锁",
                step_up_token=token_reopen,
            )
            with pytest.raises(AccountingError) as exc_info:
                await create_entry(
                    session,
                    {
                        "id": "late-2026-entry", "direction": "expense", "amount": "10.00",
                        "title": "结账后补记", "category": "办公费",
                        "occurDate": "2026-12-31", "sourceType": "manual",
                    },
                    company_id="c-year-close", user_id="owner-1",
                    request_id="req-late-2026", confirm=True,
                )
            assert exc_info.value.code == "FISCAL_YEAR_CLOSED"

            # 下年期初从历史派生：银行存款 1500（含 12 月免税转出与结转后的净额口径）
            derived = await derive_year_opening_balances(session, "c-year-close", 2027)
            assert derived["1001"] == (Decimal("1500.00"), Decimal("0.00"))
            opening_items = await get_opening_balances("c-year-close", 2027, session=session)
            derived_items = [i for i in opening_items if i.get("derived")]
            assert derived_items, "2027 年期初必须由历史分录派生"
            assert not any(
                entry_id.startswith("opening-c-year-close-2027")
                for entry_id in []
            )

            # 2027 年 1 月入账并出报表：期初现金 1500 + 当期收入
            await create_entry(
                session,
                {
                    "id": "inc-2027-01", "direction": "income", "amount": "1010.00",
                    "title": "2027 年 1 月收入", "category": "主营业务收入",
                    "occurDate": "2027-01-10", "sourceType": "manual",
                    "vatSeparated": True,
                    "voucherLines": {
                        "debit": [{"accountCode": "1001", "accountName": "库存现金", "amount": "1010.00"}],
                        "credit": [
                            {"accountCode": "5001", "accountName": "主营业务收入", "amount": "1000.00"},
                            {"accountCode": "2221026", "accountName": "应交增值税（小规模纳税人专用）", "amount": "10.00"},
                        ],
                    },
                },
                company_id="c-year-close", user_id="owner-1",
                request_id="req-inc-2027-01", confirm=True,
            )
            entries = await posted_entries_for_reports(session, "c-year-close")
            stmt = build_statement_data_from_entries(
                entries, opening_items, parse_period("2027-01", "month"),
                {"name": "测试公司", "taxId": "91310000TEST"},
            )
            assert stmt.balance_opening["cash"] == Decimal("1500.00")
            assert stmt.balance_current["cash"] == Decimal("2510.00")

            # 反结账年度后可继续入账
            token = await issue_step_up_token(
                session, company_id="c-year-close", user_id="owner-1", action="reopen_fiscal_year"
            )
            await reopen_fiscal_year(
                session, "c-year-close", 2026, user_id="owner-1",
                request_id="req-fyc-reopen", reason="补记 2026 年凭证", step_up_token=token,
            )
            await create_entry(
                session,
                {
                    "id": "late-2026-entry", "direction": "expense", "amount": "10.00",
                    "title": "结账后补记", "category": "办公费",
                    "occurDate": "2026-12-31", "sourceType": "manual",
                },
                company_id="c-year-close", user_id="owner-1",
                request_id="req-late-2026", confirm=True,
            )


@pytest.mark.asyncio
async def test_inventory_transfer_closes_cost(accounting_sessions):
    """存货采购后销售必须结转成本，否则有收入无成本、毛利虚高。"""
    from services.accounting_core_service import posted_entries_for_reports
    from services.inventory_service import get_inventory_balance, transfer_inventory_to_cost
    from services.tax_filing_export_service import build_statement_data_from_entries, parse_period

    async with accounting_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-inventory", "owner-1")
            for entry_id, amount in (("inv-purchase-1", "2000.00"), ("inv-purchase-2", "1000.00")):
                await create_entry(
                    session,
                    {
                        "id": entry_id, "direction": "expense", "amount": amount,
                        "title": "商品采购", "category": "商品采购",
                        "occurDate": "2026-07-05", "sourceType": "manual",
                        "voucherLines": {
                            "debit": [{"accountCode": "1405", "accountName": "库存商品", "amount": amount}],
                            "credit": [{"accountCode": "1001", "accountName": "库存现金", "amount": amount}],
                        },
                    },
                    company_id="c-inventory", user_id="owner-1",
                    request_id=f"req-{entry_id}", confirm=True,
                )
                await create_entry(
                    session,
                    {
                        "id": f"sale-{entry_id}", "direction": "income", "amount": amount,
                        "title": "商品销售", "category": "主营业务收入",
                        "occurDate": "2026-07-20", "sourceType": "manual",
                        "vatSeparated": True,
                        "voucherLines": {
                            "debit": [{"accountCode": "1001", "accountName": "库存现金", "amount": amount}],
                            "credit": [
                                {"accountCode": "5001", "accountName": "主营业务收入",
                                 "amount": money(money(amount) / Decimal("1.01"))},
                                {"accountCode": "2221026", "accountName": "应交增值税（小规模纳税人专用）",
                                 "amount": money(money(amount) - money(money(amount) / Decimal("1.01")))},
                            ],
                        },
                    },
                    company_id="c-inventory", user_id="owner-1",
                    request_id=f"req-sale-{entry_id}", confirm=True,
                )

            balance = await get_inventory_balance(session, "c-inventory", month="2026-07")
            assert balance["total"] == Decimal("3000.00")

            # 超余额结转必须拒绝
            with pytest.raises(AccountingError) as exc_info:
                await transfer_inventory_to_cost(
                    session, "c-inventory", month="2026-07", amount="3000.01",
                    user_id="owner-1", request_id="req-inv-over",
                )
            assert exc_info.value.code == "INVENTORY_INSUFFICIENT"

            result = await transfer_inventory_to_cost(
                session, "c-inventory", month="2026-07", amount="2500.00",
                user_id="owner-1", request_id="req-inv-transfer",
            )
            assert result["amount"] == "2500.00"

            balance_after = await get_inventory_balance(session, "c-inventory", month="2026-07")
            assert balance_after["total"] == Decimal("500.00")

            entries = await posted_entries_for_reports(session, "c-inventory")
            opening_items = await get_opening_balances("c-inventory", 2026, session=session)
            stmt = build_statement_data_from_entries(
                entries, opening_items, parse_period("2026-07", "month"),
                {"name": "测试公司", "taxId": "91310000TEST"},
            )
            assert stmt.profit_current["cost"] == Decimal("2500.00")
