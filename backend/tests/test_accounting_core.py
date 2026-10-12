from __future__ import annotations

import json
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
    post_journal,
    submit_journal_for_review,
    assert_bank_reconciliable,
    close_period,
    create_entry,
    delete_draft,
    get_journal,
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
                        "debit": [{"accountCode": "1012", "accountName": "其他货币资金", "amount": "5.00"}],
                        "credit": [{"accountCode": "6001", "accountName": "主营业务收入", "amount": "5.00"}],
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
            assert "1012" in line_codes and "1002" not in line_codes
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
                        "debit": [{"accountCode": "5608", "accountName": "管理费用", "amount": "40.00"}],
                        "credit": [{"accountCode": "1221", "accountName": "其他应收款", "amount": "40.00"}],
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
            assert all(check["uiStatus"] != "complete" for check in result["checks"])
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


@pytest.mark.asyncio
async def test_bank_import_duplicate_wechat_income_cannot_be_posted(accounting_sessions):
    async with accounting_sessions() as session:
        async with session.begin():
            for entry_id, direction, amount in [("wechat-income", "income", "5.00"), ("wechat-fee", "expense", "0.05")]:
                payload = sample_payload(entry_id, "2026-07-14")
                payload.update({"source": "wechat_bill", "sourceType": "wechat_bill", "wechat_order_no": "order-1", "direction": direction, "amount": amount})
                payload["voucherLines"] = {
                    "debit": [{"accountCode": "1012" if direction == "income" else "5608", "accountName": "微信待结算" if direction == "income" else "手续费", "amount": amount}],
                    "credit": [{"accountCode": "6001" if direction == "income" else "1012", "accountName": "收入" if direction == "income" else "微信待结算", "amount": amount}],
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


async def _settlement_fixture(session, negative=False, orders=1):
    ids = []
    for index in range(orders):
        for kind, value, debit, credit in [("sale", "100.00", "1012", "6001"), ("fee", "0.60", "5608", "1012")]:
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
            assert (await period_close_checks(session, "c-settlement", "2026-08"))["canClose"]


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
            assert (await period_close_checks(session, 'c-settlement', '2026-08'))['canClose']


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
        "voucherLines": {"debit": [{"accountCode": "5605", "accountName": "管理费用-信息技术服务费", "amount": "300"}],
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
