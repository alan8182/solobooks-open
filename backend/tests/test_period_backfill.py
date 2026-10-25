# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""跨期补记：业务日期 → 记账期间 的解析、台账与凭证行为。

覆盖《小企业会计准则》第 88 条未来适用法的系统口径：
- 业务所属期间已关账时，补记到**最早**的未关账期间（不是最新月份，避免跨季度）
- 跨季度只标记，跨年阻断
- 补记必须显式同意并填写原因，台账与 metadata、摘要前缀三者一致
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import db.models  # noqa: F401
from core.errors import (
    BACKFILL_CROSS_YEAR,
    BACKFILL_NO_OPEN_PERIOD,
    BACKFILL_REASON_REQUIRED,
    BACKFILL_TARGET_INVALID,
    PERIOD_CLOSED,
    AccountingError,
)
from db.base import Base
from db.models import AccountingPeriod, JournalEntry, PeriodBackfill, SourceTransaction
from services.accounting_core_service import (
    create_entry,
    ensure_tenant,
    period_close_checks,
    seed_accounts,
    serialize_journal,
    update_draft,
)
from services.backfill_service import (
    backfills_csv,
    backfills_in_period,
    cross_period_pending,
    cross_year_csv,
    csv_response_body,
    vat_period_csv,
    vat_period_mismatch,
)
from services.period_resolution_service import resolve_posting_period


@pytest.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        # bank_statements / bank_statement_matches 是 raw SQL 表，不在 Base.metadata；
        # period_close_checks 的账实核对会直接查它们。
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


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    """把「当前会计月」钉在 2026-10，避免用例随真实日期漂移。"""
    monkeypatch.setattr(
        "services.period_resolution_service._current_month", lambda: date(2026, 10, 1)
    )
    return date(2026, 10, 1)


async def _seed(session, company_id: str, user_id: str = "owner") -> None:
    await ensure_tenant(session, company_id, user_id)
    await seed_accounts(session, company_id)


def _close(session, company_id: str, month: str, status: str = "closed") -> None:
    year, month_no = (int(part) for part in month.split("-"))
    session.add(
        AccountingPeriod(
            company_id=company_id, period_start=date(year, month_no, 1), status=status
        )
    )


def _payload(entry_id: str, occurred_on: str, **extra) -> dict:
    payload = {
        "id": entry_id,
        "direction": "expense",
        "amount": "113.00",
        "title": "办公用品",
        "category": "办公费",
        "occurDate": occurred_on,
        "sourceType": "manual",
        "voucherLines": {
            "debit": [{"accountCode": "5602004", "accountName": "管理费用-办公费", "amount": "113.00"}],
            "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "113.00"}],
        },
    }
    payload.update(extra)
    return payload


# ---------------------------------------------------------------- 解析器纯逻辑


@pytest.mark.asyncio
async def test_resolve_same_month_when_business_period_open(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-resolve-open")
            result = await resolve_posting_period(session, "c-resolve-open", "2026-09-12")
    assert result.posting_period == date(2026, 9, 1)
    assert result.is_backfill is False
    assert result.cross_quarter is False
    assert result.reason == ""


@pytest.mark.asyncio
async def test_resolve_lands_on_earliest_open_period_not_latest(sessions):
    """9 月、10 月都开着时落 9 月：会计的「本月」是他手上正在做的账期。"""
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-resolve-earliest")
            _close(session, "c-resolve-earliest", "2026-08")
            await session.flush()
            result = await resolve_posting_period(session, "c-resolve-earliest", "2026-08-15")
    assert result.posting_period == date(2026, 9, 1)
    assert result.is_backfill is True
    assert result.cross_quarter is False  # 8 月与 9 月同属 Q3
    assert "未来适用法" in result.reason


@pytest.mark.asyncio
async def test_resolve_skips_periods_that_are_already_closed(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-resolve-skip")
            _close(session, "c-resolve-skip", "2026-08")
            _close(session, "c-resolve-skip", "2026-09")
            await session.flush()
            result = await resolve_posting_period(session, "c-resolve-skip", "2026-08-31")
    assert result.posting_period == date(2026, 10, 1)
    assert result.cross_quarter is True  # Q3 → Q4


@pytest.mark.asyncio
async def test_resolve_requires_explicit_consent_and_offers_target(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-resolve-consent")
            _close(session, "c-resolve-consent", "2026-08")
            await session.flush()
            with pytest.raises(AccountingError) as excinfo:
                await resolve_posting_period(
                    session, "c-resolve-consent", "2026-08-15", allow_backfill=False
                )
    assert excinfo.value.code == PERIOD_CLOSED
    assert excinfo.value.context["canBackfill"] is True
    assert excinfo.value.context["suggestedPostingPeriod"] == "2026-09"
    assert excinfo.value.context["crossQuarter"] is False


@pytest.mark.asyncio
async def test_resolve_blocks_cross_year(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-resolve-cross-year")
            _close(session, "c-resolve-cross-year", "2025-11")
            _close(session, "c-resolve-cross-year", "2025-12")
            await session.flush()
            with pytest.raises(AccountingError) as excinfo:
                await resolve_posting_period(session, "c-resolve-cross-year", "2025-12-20")
    assert excinfo.value.code == BACKFILL_CROSS_YEAR
    assert excinfo.value.context["businessPeriod"] == "2025-12"
    assert excinfo.value.context["postingPeriod"] == "2026-01"


@pytest.mark.asyncio
async def test_resolve_cannot_reach_a_fully_closed_timeline(sessions, monkeypatch):
    monkeypatch.setattr(
        "services.period_resolution_service._current_month", lambda: date(2026, 3, 1)
    )
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-resolve-none")
            for month in ("2026-01", "2026-02", "2026-03"):
                _close(session, "c-resolve-none", month)
            await session.flush()
            with pytest.raises(AccountingError) as excinfo:
                await resolve_posting_period(session, "c-resolve-none", "2026-01-10")
    assert excinfo.value.code == BACKFILL_NO_OPEN_PERIOD


@pytest.mark.asyncio
async def test_resolve_rejects_requested_period_before_business_date(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-resolve-invalid")
            with pytest.raises(AccountingError) as excinfo:
                await resolve_posting_period(
                    session, "c-resolve-invalid", "2026-09-10", requested_period="2026-08"
                )
    assert excinfo.value.code == BACKFILL_TARGET_INVALID


@pytest.mark.asyncio
async def test_resolve_accepts_explicit_open_period(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-resolve-explicit")
            _close(session, "c-resolve-explicit", "2026-08")
            await session.flush()
            result = await resolve_posting_period(
                session, "c-resolve-explicit", "2026-08-20", requested_period="2026-10"
            )
    assert result.posting_period == date(2026, 10, 1)
    assert result.is_backfill is True


# ---------------------------------------------------------------- 凭证行为


@pytest.mark.asyncio
async def test_create_entry_backfills_with_prefix_metadata_and_ledger(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-backfill")
            _close(session, "c-backfill", "2026-08")
            await session.flush()
            entry = await create_entry(
                session,
                _payload(
                    "bf-1",
                    "2026-08-15",
                    allowBackfill=True,
                    backfillReason="8 月发票 9 月才到",
                ),
                company_id="c-backfill",
                user_id="owner",
                request_id="req-bf-1",
                confirm=False,
            )
        ledger = (await session.execute(select(PeriodBackfill))).scalar_one()

    assert entry.occurred_on == date(2026, 8, 15)  # 业务日期不变，绝不改原始日期
    assert entry.period_start == date(2026, 9, 1)  # 记账期间落在最早未关账期
    assert entry.summary.startswith("补记2026-08｜")
    assert entry.metadata_json["isBackfill"] is True
    assert entry.metadata_json["backfillFromPeriod"] == "2026-08"
    assert entry.metadata_json["backfillReason"] == "8 月发票 9 月才到"
    assert ledger.original_period == date(2026, 8, 1)
    assert ledger.posting_period == date(2026, 9, 1)
    assert ledger.status == "active"
    assert ledger.reason == "8 月发票 9 月才到"


@pytest.mark.asyncio
async def test_create_entry_rejects_backfill_without_reason(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-backfill-reason")
            _close(session, "c-backfill-reason", "2026-08")
            await session.flush()
            with pytest.raises(AccountingError) as excinfo:
                await create_entry(
                    session,
                    _payload("bf-2", "2026-08-15", allowBackfill=True),
                    company_id="c-backfill-reason",
                    user_id="owner",
                    request_id="req-bf-2",
                    confirm=False,
                )
    assert excinfo.value.code == BACKFILL_REASON_REQUIRED
    assert excinfo.value.context["postingPeriod"] == "2026-09"


@pytest.mark.asyncio
async def test_create_entry_does_not_shift_period_without_consent(sessions):
    """默认仍报 PERIOD_CLOSED：补记会写进用户没指定的月份，不能静默发生。"""
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-backfill-consent")
            _close(session, "c-backfill-consent", "2026-08")
            await session.flush()
            with pytest.raises(AccountingError) as excinfo:
                await create_entry(
                    session,
                    _payload("bf-3", "2026-08-15"),
                    company_id="c-backfill-consent",
                    user_id="owner",
                    request_id="req-bf-3",
                    confirm=False,
                )
            assert excinfo.value.code == PERIOD_CLOSED
            assert excinfo.value.context["canBackfill"] is True
            assert excinfo.value.context["suggestedPostingPeriod"] == "2026-09"
            assert (await session.execute(select(PeriodBackfill))).scalars().all() == []


@pytest.mark.asyncio
async def test_create_entry_same_month_writes_no_ledger(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-no-backfill")
            entry = await create_entry(
                session,
                _payload("nb-1", "2026-09-05"),
                company_id="c-no-backfill",
                user_id="owner",
                request_id="req-nb-1",
                confirm=False,
            )
            ledgers = (await session.execute(select(PeriodBackfill))).scalars().all()
    assert entry.period_start == date(2026, 9, 1)
    assert "isBackfill" not in entry.metadata_json
    assert not entry.summary.startswith("补记")
    assert ledgers == []


@pytest.mark.asyncio
async def test_update_draft_back_to_business_period_reverses_ledger_and_prefix(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-backfill-update")
            _close(session, "c-backfill-update", "2026-08")
            await session.flush()
            entry = await create_entry(
                session,
                _payload(
                    "bf-upd", "2026-08-15", allowBackfill=True, backfillReason="补记测试"
                ),
                company_id="c-backfill-update",
                user_id="owner",
                request_id="req-bf-upd",
                confirm=False,
            )
            # 9 月把 8 月的账重新打开后，用户把凭证改回 8 月
            period = (
                await session.execute(
                    select(AccountingPeriod).where(
                        AccountingPeriod.company_id == "c-backfill-update",
                        AccountingPeriod.period_start == date(2026, 8, 1),
                    )
                )
            ).scalar_one()
            period.status = "reopened"
            await session.flush()
            updated = await update_draft(
                session,
                entry.id,
                {"occurDate": "2026-08-15"},
                company_id="c-backfill-update",
                user_id="owner",
                request_id="req-bf-upd-2",
            )
        ledger = (await session.execute(select(PeriodBackfill))).scalar_one()

    assert updated.period_start == date(2026, 8, 1)
    assert updated.summary == "办公用品"  # 前缀自动摘除
    assert "isBackfill" not in updated.metadata_json
    assert ledger.status == "reversed"


@pytest.mark.asyncio
async def test_serialize_journal_exposes_posting_period(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-serialize")
            _close(session, "c-serialize", "2026-08")
            await session.flush()
            entry = await create_entry(
                session,
                _payload("ser-1", "2026-08-20", allowBackfill=True, backfillReason="补记"),
                company_id="c-serialize",
                user_id="owner",
                request_id="req-ser-1",
                confirm=False,
            )
            payload = await serialize_journal(session, entry)
    assert payload["period"] == "2026-09"
    assert payload["occurDate"] == "2026-08-20"


@pytest.mark.asyncio
async def test_backfill_ledger_is_unique_per_entry(sessions):
    """同一张凭证不能出现两条台账：改单走更新，不走新增。"""
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-backfill-unique")
            _close(session, "c-backfill-unique", "2026-08")
            await session.flush()
            entry = await create_entry(
                session,
                _payload("bf-uq", "2026-08-15", allowBackfill=True, backfillReason="首次"),
                company_id="c-backfill-unique",
                user_id="owner",
                request_id="req-bf-uq",
                confirm=False,
            )
            await update_draft(
                session,
                entry.id,
                {"backfillReason": "改原因"},
                company_id="c-backfill-unique",
                user_id="owner",
                request_id="req-bf-uq-2",
            )
            rows = (await session.execute(select(PeriodBackfill))).scalars().all()
    assert len(rows) == 1
    assert rows[0].reason == "首次"  # 未改日期时原因不随普通编辑变动


@pytest.mark.asyncio
async def test_posted_entry_keeps_business_date_of_backfill(sessions):
    """过账后业务日期仍然是最初的单据日期，只有记账期间被挪动。"""
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-backfill-posted")
            _close(session, "c-backfill-posted", "2026-08")
            await session.flush()
            entry = await create_entry(
                session,
                _payload("bf-post", "2026-08-31", allowBackfill=True, backfillReason="月末票未到"),
                company_id="c-backfill-posted",
                user_id="owner",
                request_id="req-bf-post",
                confirm=True,
            )
        stored = await session.get(JournalEntry, entry.id)
    assert stored.status == "posted"
    assert stored.occurred_on == date(2026, 8, 31)
    assert stored.period_start == date(2026, 9, 1)


# ---------------------------------------------------------------- 结账提示与导出


@pytest.mark.asyncio
async def test_close_checks_report_cross_period_pending_without_blocking(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-notice-pending")
            session.add(
                SourceTransaction(
                    company_id="c-notice-pending",
                    source_type="bank",
                    external_key=None,
                    occurred_on=date(2026, 8, 20),
                    description="8 月银行流水未入账",
                    amount=Decimal("1200.00"),
                    currency="CNY",
                    raw_payload={},
                    status="candidate",
                    created_by="owner",
                )
            )
            await session.flush()
            result = await period_close_checks(session, "c-notice-pending", "2026-09")

    notice = next(item for item in result["notices"] if item["code"] == "CROSS_PERIOD_PENDING")
    assert notice["count"] == 1
    assert notice["amount"] == "1200.00"
    assert notice["records"][0]["businessPeriod"] == "2026-08"
    assert notice["suggestedAction"] == "backfill"
    # 提示项不参与 canClose：五项核对仍然全绿，用户可以照常结账
    assert result["canClose"] is True
    assert [item["code"] for item in result["checks"]] == [
        "VOUCHER_TO_SOURCE",
        "LEDGER_TO_LEDGER",
        "BOOK_TO_ACTUAL",
        "LEDGER_TO_REPORT",
        "REPORT_TO_TAX",
        "INTANGIBLE_AMORTIZATION",
        "FIXED_ASSET_DEPRECIATION",
        "PERIOD_ORDER",
    ]


@pytest.mark.asyncio
async def test_close_checks_report_backfill_in_period(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-notice-backfill")
            _close(session, "c-notice-backfill", "2026-08")
            await session.flush()
            await create_entry(
                session,
                _payload("nbf-1", "2026-08-18", allowBackfill=True, backfillReason="票迟到"),
                company_id="c-notice-backfill",
                user_id="owner",
                request_id="req-nbf-1",
                confirm=True,
            )
            result = await period_close_checks(session, "c-notice-backfill", "2026-09")

    notice = next(item for item in result["notices"] if item["code"] == "BACKFILL_IN_PERIOD")
    assert notice["count"] == 1
    assert notice["records"][0]["originalPeriod"] == "2026-08"
    assert notice["records"][0]["voucherRef"].startswith("VOU-202609-")


@pytest.mark.asyncio
async def test_backfill_ledger_list_and_csv_export(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-ledger")
            _close(session, "c-ledger", "2026-08")
            await session.flush()
            await create_entry(
                session,
                _payload("lg-1", "2026-08-09", allowBackfill=True, backfillReason="供应商晚开票"),
                company_id="c-ledger",
                user_id="owner",
                request_id="req-lg-1",
                confirm=False,
            )
            payload = await backfills_in_period(session, "c-ledger", "2026-09", limit=None)
            text = backfills_csv(payload)

    assert payload["count"] == 1
    assert payload["amount"] == "113.00"
    assert payload["records"][0]["originalPeriod"] == "2026-08"
    assert "跨期补记台账（记账期间 2026-09）" in text
    assert "供应商晚开票" in text
    assert csv_response_body(text).startswith("\ufeff".encode("utf-8"))


@pytest.mark.asyncio
async def test_cross_year_csv_carries_policy_basis(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-cross-year")
            session.add(
                SourceTransaction(
                    company_id="c-cross-year",
                    source_type="invoice",
                    external_key=None,
                    occurred_on=date(2025, 11, 6),
                    description="去年成本发票未入账",
                    amount=Decimal("5000.00"),
                    currency="CNY",
                    raw_payload={},
                    status="candidate",
                    created_by="owner",
                )
            )
            await session.flush()
            payload = await cross_period_pending(session, "c-cross-year", date(2026, 1, 1), limit=None)
            text = cross_year_csv(payload, 2026)

    assert payload["count"] == 1
    assert "2012 年第 15 号" in text
    assert "追补确认期限不超过 5 年" in text
    assert "2025-11-06" in text


@pytest.mark.asyncio
async def test_backfill_export_route_returns_csv_attachment(sessions):
    from types import SimpleNamespace

    from routers.accounting import backfills_export_route

    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-route")
            await create_entry(
                session,
                _payload("rt-1", "2026-09-01"),
                company_id="c-route",
                user_id="owner",
                request_id="req-rt-1",
                confirm=False,
            )
            ctx = SimpleNamespace(company_id="c-route", user_id="owner")
            response = await backfills_export_route(
                scope="backfill", month="2026-09", year=None, ctx=ctx, session=session
            )

    assert response.media_type == "text/csv; charset=utf-8"
    assert "attachment" in response.headers["content-disposition"]
    assert response.body.startswith("\ufeff".encode("utf-8"))


# ---------------------------------------------------------------- 提交前预判


@pytest.mark.asyncio
async def test_backfill_target_preview_for_open_period(sessions):
    from types import SimpleNamespace

    from routers.accounting import backfill_target_route

    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-preview-open")
            result = await backfill_target_route(
                businessDate="2026-09-10",
                ctx=SimpleNamespace(company_id="c-preview-open", user_id="owner"),
                session=session,
            )

    assert result["needsBackfill"] is False
    assert result["targetPeriod"] == "2026-09"
    assert result["canBackfill"] is True


@pytest.mark.asyncio
async def test_backfill_target_preview_for_closed_period(sessions):
    from types import SimpleNamespace

    from routers.accounting import backfill_target_route

    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-preview-closed")
            _close(session, "c-preview-closed", "2026-08")
            await session.flush()
            result = await backfill_target_route(
                businessDate="2026-08-15",
                ctx=SimpleNamespace(company_id="c-preview-closed", user_id="owner"),
                session=session,
            )

    assert result["needsBackfill"] is True
    assert result["businessPeriod"] == "2026-08"
    assert result["targetPeriod"] == "2026-09"
    assert result["crossQuarter"] is False
    assert result["canBackfill"] is True


@pytest.mark.asyncio
async def test_backfill_target_preview_marks_cross_year_as_not_allowed(sessions):
    from types import SimpleNamespace

    from routers.accounting import backfill_target_route

    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-preview-year")
            _close(session, "c-preview-year", "2025-11")
            _close(session, "c-preview-year", "2025-12")
            await session.flush()
            result = await backfill_target_route(
                businessDate="2025-12-20",
                ctx=SimpleNamespace(company_id="c-preview-year", user_id="owner"),
                session=session,
            )

    assert result["needsBackfill"] is True
    assert result["crossYear"] is True
    assert result["canBackfill"] is False


@pytest.mark.asyncio
async def test_agent_booking_threads_backfill_flags(sessions):
    """agent 确认动作与手工记账同一条管线：带 allow_backfill 才补记。"""
    from agent.actions import _book_voucher_payload

    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-agent-backfill")
            _close(session, "c-agent-backfill", "2026-08")
            await session.flush()
            booking = await _book_voucher_payload(
                session,
                {
                    "direction": "expense",
                    "amount": "113.00",
                    "title": "办公用品",
                    "category": "办公费",
                    "occurDate": "2026-08-15",
                    "payer": "company_account",
                },
                user_id="owner",
                company_id="c-agent-backfill",
                request_id="req-agent-bf",
                allow_backfill=True,
                backfill_reason="8 月票据 9 月才到",
            )
            entry = await session.get(JournalEntry, booking["entryId"])
            ledger = (await session.execute(select(PeriodBackfill))).scalar_one()

    assert entry.period_start == date(2026, 9, 1)
    assert entry.occurred_on == date(2026, 8, 15)
    assert entry.summary.startswith("补记2026-08｜")
    assert ledger.reason == "8 月票据 9 月才到"


@pytest.mark.asyncio
async def test_bulk_import_backfills_and_records_ledger():
    """微信账单等批量导入走 save_entries_bulk（自带 session），验证补记链路。"""
    from db.session import get_engine, session_scope
    from services.accounting_core_service import ensure_tenant, seed_accounts
    from services.entry_service import save_entries_bulk

    company_id = "c-bulk-backfill"
    # 这条用例走全局 session_scope（与微信账单导入同一条路径），先确保建表
    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_scope() as session:
        await ensure_tenant(session, company_id, "owner-bulk")
        await seed_accounts(session, company_id)
        session.add(
            AccountingPeriod(
                company_id=company_id, period_start=date(2026, 8, 1), status="closed"
            )
        )

    results = await save_entries_bulk(
        [
            {
                "direction": "expense",
                "amount": "50.00",
                "title": "8 月微信手续费",
                "category": "银行及金融手续费",
                "occurDate": "2026-08-20",
            }
        ],
        user_id="owner-bulk",
        company_id=company_id,
        allow_backfill=True,
        backfill_reason="微信账单导入：8 月账单 9 月才导入",
    )

    assert results[0]["period"] == "2026-09"
    assert results[0]["occurDate"] == "2026-08-20"
    assert results[0]["isBackfill"] is True

    async with session_scope() as session:
        ledger = (
            await session.execute(
                select(PeriodBackfill).where(PeriodBackfill.company_id == company_id)
            )
        ).scalars().all()
    assert len(ledger) == 1
    assert ledger[0].reason == "微信账单导入：8 月账单 9 月才导入"


# ---------------------------------------------------------------- 双口径利润表


@pytest.mark.asyncio
async def test_income_statement_posting_vs_business_basis(sessions):
    from types import SimpleNamespace

    from models.schemas import IncomeStatementRequest
    from routers.report import income_statement

    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-dual-basis")
            _close(session, "c-dual-basis", "2026-08")
            await session.flush()
            # 8 月业务、9 月补记
            await create_entry(
                session,
                _payload("dual-aug", "2026-08-15", allowBackfill=True, backfillReason="票迟到"),
                company_id="c-dual-basis",
                user_id="owner",
                request_id="req-dual-1",
                confirm=True,
            )
            # 9 月正常业务
            await create_entry(
                session,
                _payload("dual-sep", "2026-09-05"),
                company_id="c-dual-basis",
                user_id="owner",
                request_id="req-dual-2",
                confirm=True,
            )
            ctx = SimpleNamespace(company_id="c-dual-basis", user_id="owner")
            posting = await income_statement(
                IncomeStatementRequest(month="2026-09", basis="posting"), ctx, session
            )
            business = await income_statement(
                IncomeStatementRequest(month="2026-09", basis="business"), ctx, session
            )

    posting_current = posting.totals["netProfit"]["current"]
    business_current = business.totals["netProfit"]["current"]
    # 法定口径：两笔都在 9 月（补记凭证的记账期间是 9 月）
    assert posting_current == Decimal("-226.00")
    # 管理口径：补记的那笔归回 8 月，9 月只剩自己的 113
    assert business_current == Decimal("-113.00")
    # 差额正好等于补记金额：这就是「本月利润里有多少不是本月的业务」
    assert business_current - posting_current == Decimal("113.00")
    # 本年累计两种口径一致（都含这笔，只是归属月份不同）
    assert posting.totals["netProfit"]["ytd"] == business.totals["netProfit"]["ytd"]

    assert posting.backfillImpact["count"] == 1
    assert posting.backfillImpact["periods"][0]["originalPeriod"] == "2026-08"
    assert posting.backfillImpact["amount"] == "113.00"


# ---------------------------------------------------------------- 增值税申报所属期


def _sales_payload(entry_id: str, occurred_on: str, **extra) -> dict:
    """含税 10100 的销售分录：小规模 1% → 不含税 10000 + 应交增值税 100。"""
    payload = {
        "id": entry_id,
        "direction": "income",
        "amount": "10100.00",
        "title": "服务收入",
        "category": "主营业务收入",
        "occurDate": occurred_on,
        "sourceType": "manual",
        "voucherLines": {
            "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "10100.00"}],
            "credit": [
                {"accountCode": "5001", "accountName": "主营业务收入", "amount": "10000.00"},
                {"accountCode": "2221026", "accountName": "应交增值税", "amount": "100.00"},
            ],
        },
    }
    payload.update(extra)
    return payload


async def _set_vat_cycle(session, company_id: str, cycle: str) -> None:
    from db.models import Company

    company = await session.get(Company, company_id)
    company.payload = {**(company.payload or {}), "vatFilingCycle": cycle}


@pytest.mark.asyncio
async def test_no_vat_mismatch_when_backfill_stays_in_same_quarter(sessions):
    """7 月业务补记到 8 月，季报同属 Q3：申报口径不变，不该打扰用户。"""
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-vat-same-q")
            _close(session, "c-vat-same-q", "2026-07")
            await session.flush()
            await create_entry(
                session,
                _sales_payload("vat-1", "2026-07-18", allowBackfill=True, backfillReason="票迟到"),
                company_id="c-vat-same-q",
                user_id="owner",
                request_id="req-vat-1",
                confirm=True,
            )
            result = await period_close_checks(session, "c-vat-same-q", "2026-08")

    codes = [item["code"] for item in result["notices"]]
    assert "BACKFILL_IN_PERIOD" in codes
    assert "VAT_PERIOD_MISMATCH" not in codes


@pytest.mark.asyncio
async def test_vat_mismatch_notice_when_backfill_crosses_quarter(sessions):
    """6 月业务落到 9 月：Q2 的销项被记进 Q3，必须提示。"""
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-vat-cross-q")
            for month in ("2026-06", "2026-07", "2026-08"):
                _close(session, "c-vat-cross-q", month)
            await session.flush()
            await create_entry(
                session,
                _sales_payload("vat-2", "2026-06-20", allowBackfill=True, backfillReason="6 月票 9 月才到"),
                company_id="c-vat-cross-q",
                user_id="owner",
                request_id="req-vat-2",
                confirm=True,
            )
            result = await period_close_checks(session, "c-vat-cross-q", "2026-09")

    notice = next(item for item in result["notices"] if item["code"] == "VAT_PERIOD_MISMATCH")
    assert notice["count"] == 1
    assert notice["amount"] == "100.00"
    assert notice["filingCycle"] == "quarter"
    record = notice["records"][0]
    assert record["businessFilingPeriod"] == "2026-Q2"
    assert record["postingFilingPeriod"] == "2026-Q3"
    assert record["vatAmount"] == "100.00"
    # 提示项不进五项核对，也就不参与 canClose / blockingCount
    assert notice["severity"] == "warning"
    assert "VAT_PERIOD_MISMATCH" not in [item["code"] for item in result["checks"]]
    assert result["blockingCount"] == sum(
        1 for item in result["checks"] if item["status"] == "blocking"
    )


@pytest.mark.asyncio
async def test_vat_mismatch_by_month_for_monthly_filers(sessions):
    """月报纳税人只要跨月就错配，哪怕只差一个月。"""
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-vat-month")
            await _set_vat_cycle(session, "c-vat-month", "month")
            _close(session, "c-vat-month", "2026-08")
            await session.flush()
            await create_entry(
                session,
                _sales_payload("vat-3", "2026-08-11", allowBackfill=True, backfillReason="票迟到"),
                company_id="c-vat-month",
                user_id="owner",
                request_id="req-vat-3",
                confirm=True,
            )
            payload = await vat_period_mismatch(session, "c-vat-month", posting_period="2026-09")
            text = vat_period_csv(payload)

    assert payload["count"] == 1
    assert payload["filingCycle"] == "month"
    assert payload["records"][0]["businessFilingPeriod"] == "2026-08"
    assert payload["records"][0]["postingFilingPeriod"] == "2026-09"
    assert "月报" in text
    assert "纳税义务" in text
    assert "合计" in text


@pytest.mark.asyncio
async def test_vat_mismatch_ignores_entries_without_vat(sessions):
    """纯费用票补记不产生申报期差异——没有增值税就没有纳税义务错位。"""
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-vat-none")
            for month in ("2026-06", "2026-07", "2026-08"):
                _close(session, "c-vat-none", month)
            await session.flush()
            await create_entry(
                session,
                _payload("novat-1", "2026-06-15", allowBackfill=True, backfillReason="票迟到"),
                company_id="c-vat-none",
                user_id="owner",
                request_id="req-novat-1",
                confirm=True,
            )
            payload = await vat_period_mismatch(session, "c-vat-none", posting_period="2026-09")

    assert payload["count"] == 0
    assert payload["records"] == []
