# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""无形资产模块测试：起停摊、尾差、幂等、期间锁、状态派生、处置、报表净额。

对应设计方案第 14 章验收断言 1-8 与第 5 章八条规则。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import db.models  # noqa: F401
from core.errors import AccountingError
from core.intangible_categories import INTANGIBLE_CATEGORIES, category_payload
from db.base import Base
from db.models import (
    AccountingPeriod,
    Company,
    CompanyMember,
    IntangibleAmortizationSchedule,
    IntangibleAsset,
    JournalEntry,
    JournalLine,
    RdProject,
)
from services.accounting_core_service import create_entry, ensure_tenant, period_close_checks
from services.amortization_engine import (
    amortization_summary,
    list_due_amortization_periods,
    run_amortization,
    run_amortization_catch_up,
)
from services.intangible_asset_service import (
    approve_asset,
    create_asset,
    create_intangible_asset_from_entry,
    delete_asset,
    get_asset,
    get_intangible_asset_entry_prefill,
    list_assets,
    reject_asset,
    submit_asset,
    update_asset,
)
from services.intangible_disposal_service import (
    approve_disposal,
    cancel_disposal,
    create_disposal,
    submit_disposal,
)
from services.tax_filing_export_service import _build_balance_rows


@pytest.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        # period_close_checks 依赖的原生表（与 test_accrual 同款）
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


def _asset_payload(**overrides) -> dict:
    payload = {
        "name": "XX 管理软件",
        "category": "software",
        "acquisitionType": "purchase",
        "acquisitionDate": "2026-01-15",
        "availableForUseDate": "2026-01-15",
        "originalValue": "120000.00",
        "residualValue": "0",
        "usefulLifeMonths": 120,
    }
    payload.update(overrides)
    return payload


async def _create_and_submit(session, company_id: str, user_id: str = "owner", **overrides):
    asset = await create_asset(
        session, _asset_payload(**overrides), company_id=company_id, user_id=user_id, request_id="t"
    )
    result = await submit_asset(session, asset.id, company_id=company_id, user_id=user_id, request_id="t")
    await session.flush()
    return asset, result


async def _post_intangible_purchase(
    session,
    company_id: str,
    *,
    entry_id: str,
    amount: str = "120000.00",
    occurred_on: str = "2026-01-15",
) -> JournalEntry:
    return await create_entry(
        session,
        {
            "id": entry_id,
            "direction": "expense",
            "amount": amount,
            "title": "购管理软件",
            "category": "无形资产",
            "occurDate": occurred_on,
            "dedupFingerprint": f"intangible-source:{company_id}:{entry_id}",
            "voucherAuthority": "system",
            "voucherLines": {
                "debit": [{"accountCode": "1701", "accountName": "无形资产", "amount": amount}],
                "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": amount}],
            },
        },
        company_id=company_id,
        user_id="owner",
        request_id="t",
        confirm=True,
    )


async def _entry_lines(session, entry_id: str) -> tuple[list[JournalLine], list[JournalLine]]:
    rows = (
        await session.execute(
            select(JournalLine).where(JournalLine.journal_entry_id == entry_id).order_by(JournalLine.line_no)
        )
    ).scalars().all()
    debit = [r for r in rows if r.direction == "debit"]
    credit = [r for r in rows if r.direction == "credit"]
    return debit, credit


def _by_code(lines: list[JournalLine]) -> dict[str, str]:
    return {line.account_code: str(line.amount) for line in lines}


# ── 1. 分类目录 ───────────────────────────────────────────────────────────

def test_categories_catalog():
    assert len(INTANGIBLE_CATEGORIES) == 8
    payload = category_payload()
    assert {c["code"] for c in payload} == {
        "patent", "trademark", "copyright", "land_use_right",
        "software", "franchise", "non_patent_technology", "other",
    }
    software = next(c for c in payload if c["code"] == "software")
    assert software["defaultLifeMonths"] == 60
    assert software["lifeMinMonths"] == 24


@pytest.mark.asyncio
async def test_category_life_validation(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-ia")
            with pytest.raises(AccountingError):
                # 软件最短 24 个月
                await create_asset(
                    session,
                    _asset_payload(usefulLifeMonths=12),
                    company_id="c-ia", user_id="owner", request_id="t",
                )


# ── 2. 外购取得：凭证直接过账、计划预生成 ─────────────────────────────────

@pytest.mark.asyncio
async def test_purchase_submit_posts_and_generates_schedule(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-ia")
            asset, result = await _create_and_submit(session, "c-ia")
            assert asset.status == "active"
            assert result["entryStatus"] == "posted"
            assert asset.source_entry_id == f"intangible-acq-{asset.id}"

            debit, credit = await _entry_lines(session, asset.source_entry_id)
            assert _by_code(debit) == {"1701": "120000.00"}
            assert _by_code(credit) == {"1002": "120000.00"}

            rows = (
                await session.execute(
                    select(IntangibleAmortizationSchedule)
                    .where(IntangibleAmortizationSchedule.asset_id == asset.id)
                    .order_by(IntangibleAmortizationSchedule.sequence_no)
                )
            ).scalars().all()
            assert len(rows) == 120
            assert rows[0].period_start == "2026-01"  # 可供使用当月起摊
            assert str(rows[0].amount) == "1000.00"
            assert asset.amortization_end_period == "2035-12"


# ── 3. maker_checker：pending_review → approve；制单人不能自审 ───────────

@pytest.mark.asyncio
async def test_create_asset_from_posted_entry_without_duplicate_voucher(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-ia-entry")
            source = await _post_intangible_purchase(
                session, "c-ia-entry", entry_id="entry-ia-source"
            )
            before = (
                await session.execute(
                    select(JournalEntry).where(JournalEntry.company_id == "c-ia-entry")
                )
            ).scalars().all()

            prefill = await get_intangible_asset_entry_prefill(
                session, source.id, company_id="c-ia-entry", user_id="owner"
            )
            assert prefill["originalValue"] == Decimal("120000.00")
            assert prefill["suggestedCategory"] == "software"
            assert prefill["existingAssetId"] is None

            asset = await create_intangible_asset_from_entry(
                session,
                {
                    **_asset_payload(name="购管理软件", originalValue="120000.00", usefulLifeMonths=120),
                    "sourceEntryId": source.id,
                },
                company_id="c-ia-entry",
                user_id="owner",
                request_id="t",
            )
            after = (
                await session.execute(
                    select(JournalEntry).where(JournalEntry.company_id == "c-ia-entry")
                )
            ).scalars().all()
            assert len(after) == len(before)
            assert asset.source_entry_id == source.id
            assert asset.status == "active"
            rows = (
                await session.execute(
                    select(IntangibleAmortizationSchedule)
                    .where(IntangibleAmortizationSchedule.asset_id == asset.id)
                    .order_by(IntangibleAmortizationSchedule.sequence_no)
                )
            ).scalars().all()
            assert rows[0].period_start == "2026-01"

            linked = await get_intangible_asset_entry_prefill(
                session, source.id, company_id="c-ia-entry", user_id="owner"
            )
            assert linked["existingAssetId"] == asset.id

            with pytest.raises(AccountingError) as exc:
                await create_intangible_asset_from_entry(
                    session,
                    {
                        **_asset_payload(name="重复软件", originalValue="120000.00", usefulLifeMonths=120),
                        "sourceEntryId": source.id,
                    },
                    company_id="c-ia-entry",
                    user_id="owner",
                    request_id="t",
                )
            assert exc.value.code == "DUPLICATE_SOURCE"
            assert exc.value.context["existingAssetId"] == asset.id


@pytest.mark.asyncio
async def test_amortization_catch_up_posts_each_period_and_is_idempotent(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-ia-catchup")
            source = await _post_intangible_purchase(
                session, "c-ia-catchup", entry_id="entry-ia-catchup"
            )
            asset = await create_intangible_asset_from_entry(
                session,
                {
                    **_asset_payload(name="购管理软件", originalValue="120000.00", usefulLifeMonths=120),
                    "sourceEntryId": source.id,
                },
                company_id="c-ia-catchup",
                user_id="owner",
                request_id="t",
            )
            due = await list_due_amortization_periods(
                session, "c-ia-catchup", "2026-03", asset_ids=[asset.id]
            )
            assert [item["period"] for item in due] == ["2026-01", "2026-02", "2026-03"]

            result = await run_amortization_catch_up(
                session,
                "2026-03",
                company_id="c-ia-catchup",
                user_id="owner",
                request_id="catchup",
                asset_ids=[asset.id],
            )
            assert [item["period"] for item in result["periods"]] == [
                "2026-01", "2026-02", "2026-03",
            ]
            assert result["postedCount"] == 3
            rows = (
                await session.execute(
                    select(IntangibleAmortizationSchedule)
                    .where(IntangibleAmortizationSchedule.asset_id == asset.id)
                    .order_by(IntangibleAmortizationSchedule.sequence_no)
                )
            ).scalars().all()
            assert [row.status for row in rows[:3]] == ["posted", "posted", "posted"]
            assert len({row.journal_entry_id for row in rows[:3]}) == 3

            again = await run_amortization_catch_up(
                session,
                "2026-03",
                company_id="c-ia-catchup",
                user_id="owner",
                request_id="catchup-again",
                asset_ids=[asset.id],
            )
            assert again["processedCount"] == 0


@pytest.mark.asyncio
async def test_amortization_catch_up_rejects_closed_period(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-ia-lock")
            source = await _post_intangible_purchase(
                session, "c-ia-lock", entry_id="entry-ia-lock"
            )
            asset = await create_intangible_asset_from_entry(
                session,
                {
                    **_asset_payload(name="购管理软件", originalValue="120000.00", usefulLifeMonths=120),
                    "sourceEntryId": source.id,
                },
                company_id="c-ia-lock",
                user_id="owner",
                request_id="t",
            )
            period = (
                await session.execute(
                    select(AccountingPeriod).where(
                        AccountingPeriod.company_id == "c-ia-lock",
                        AccountingPeriod.period_start == date(2026, 1, 1),
                    )
                )
            ).scalar_one()
            period.status = "closed"
            await session.flush()

            with pytest.raises(AccountingError) as exc:
                await run_amortization_catch_up(
                    session,
                    "2026-03",
                    company_id="c-ia-lock",
                    user_id="owner",
                    request_id="catchup-lock",
                    asset_ids=[asset.id],
                )
            assert exc.value.code == "PERIOD_CLOSED"
            assert exc.value.context["periods"] == ["2026-01"]


@pytest.mark.asyncio
async def test_maker_checker_flow(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-mc", "boss")
            company = await session.get(Company, "c-mc")
            company.posting_policy = "maker_checker"
            session.add(CompanyMember(company_id="c-mc", user_id="bookkeeper", role="preparer", status="active"))
            session.add(CompanyMember(company_id="c-mc", user_id="chief", role="accountant", status="active"))

            asset = await create_asset(
                session, _asset_payload(), company_id="c-mc", user_id="bookkeeper", request_id="t"
            )
            result = await submit_asset(session, asset.id, company_id="c-mc", user_id="bookkeeper", request_id="t")
            assert result["entryStatus"] == "pending_review"
            assert asset.status == "pending_review"

            # 制单人自审被拒
            with pytest.raises(AccountingError):
                await approve_asset(session, asset.id, company_id="c-mc", user_id="bookkeeper", request_id="t")

            result = await approve_asset(session, asset.id, company_id="c-mc", user_id="chief", request_id="t")
            assert result["entryStatus"] == "posted"
            assert asset.status == "active"


@pytest.mark.asyncio
async def test_reject_returns_asset_to_draft(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-rj", "boss")
            company = await session.get(Company, "c-rj")
            company.posting_policy = "maker_checker"
            session.add(CompanyMember(company_id="c-rj", user_id="bookkeeper", role="preparer", status="active"))
            session.add(CompanyMember(company_id="c-rj", user_id="chief", role="accountant", status="active"))
            asset = await create_asset(
                session, _asset_payload(), company_id="c-rj", user_id="bookkeeper", request_id="t"
            )
            await submit_asset(session, asset.id, company_id="c-rj", user_id="bookkeeper", request_id="t")
            result = await reject_asset(
                session, asset.id, company_id="c-rj", user_id="chief", request_id="t", reason="缺权证号"
            )
            assert result["asset"]["status"] == "draft"
            # 驳回保留凭证引用（凭证已退回草稿），重提时更新同一张凭证
            assert asset.source_entry_id is not None
            result = await submit_asset(session, asset.id, company_id="c-rj", user_id="bookkeeper", request_id="t")
            assert result["entryStatus"] == "pending_review"
            assert result["journalEntryId"] == asset.source_entry_id


# ── 4-5. 起摊与尾差 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schedule_tail_difference(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-tail")
            asset = await create_asset(
                session,
                _asset_payload(
                    originalValue="100000.00", usefulLifeMonths=36,
                    acquisitionDate="2026-03-01", availableForUseDate="2026-03-01",
                ),
                company_id="c-tail", user_id="owner", request_id="t",
            )
            await submit_asset(session, asset.id, company_id="c-tail", user_id="owner", request_id="t")
            rows = (
                await session.execute(
                    select(IntangibleAmortizationSchedule)
                    .where(IntangibleAmortizationSchedule.asset_id == asset.id)
                    .order_by(IntangibleAmortizationSchedule.sequence_no)
                )
            ).scalars().all()
            assert len(rows) == 36
            assert rows[0].period_start == "2026-03"
            total = sum(Decimal(str(r.amount)) for r in rows)
            assert total == Decimal("100000.00")
            assert len({str(r.amount) for r in rows[:-1]}) == 1  # 前 35 期等额
            assert all(Decimal(str(r.amount)) > 0 for r in rows)


# ── 6. 摊销执行：分录、幂等、dryRun、研发分摊、提足自动 fully_amortized ──

@pytest.mark.asyncio
async def test_run_amortization_posts_voucher_and_idempotent(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-run")
            asset, _ = await _create_and_submit(session, "c-run")

            result = await run_amortization(session, "2026-01", company_id="c-run", user_id="owner", request_id="t")
            assert result["processedCount"] == 1
            assert result["postedCount"] == 1
            assert str(result["totalAmount"]) == "1000.00"
            debit, credit = await _entry_lines(session, result["items"][0]["journalEntryId"])
            assert _by_code(debit) == {"5602008": "1000.00"}
            assert _by_code(credit) == {"1702": "1000.00"}
            assert str(asset.accumulated_amortization) == "1000.00"
            assert asset.last_amortized_period == "2026-01"

            # 幂等：同期间重跑 → 0 处理
            again = await run_amortization(session, "2026-01", company_id="c-run", user_id="owner", request_id="t")
            assert again["processedCount"] == 0
            assert again["skippedCount"] == 1

            # dryRun 不写库
            preview = await run_amortization(
                session, "2026-02", company_id="c-run", user_id="owner", request_id="t", dry_run=True
            )
            assert preview["dryRun"] is True and preview["processedCount"] == 1
            rows = (
                await session.execute(
                    select(IntangibleAmortizationSchedule).where(
                        IntangibleAmortizationSchedule.asset_id == asset.id,
                        IntangibleAmortizationSchedule.period_start == "2026-02",
                    )
                )
            ).scalars().all()
            assert rows[0].status == "planned"


@pytest.mark.asyncio
async def test_run_amortization_rd_split(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-rd")
            project = RdProject(
                company_id="c-rd", code="RD001", name="测试项目",
                start_date=date(2026, 1, 1), status="active", created_by="owner",
            )
            session.add(project)
            await session.flush()
            asset = await create_asset(
                session,
                _asset_payload(originalValue="60000.00", usefulLifeMonths=60),
                company_id="c-rd", user_id="owner", request_id="t",
                payment_method="company_account",
            )
            asset.rd_project_id = project.id
            asset.rd_use_rate = Decimal("0.5")
            asset.rd_allocation_basis = "使用时长"
            await submit_asset(session, asset.id, company_id="c-rd", user_id="owner", request_id="t")

            result = await run_amortization(session, "2026-01", company_id="c-rd", user_id="owner", request_id="t")
            debit, credit = await _entry_lines(session, result["items"][0]["journalEntryId"])
            assert _by_code(debit) == {"430100103": "500.00", "5602008": "500.00"}
            assert _by_code(credit) == {"1702": "1000.00"}


@pytest.mark.asyncio
async def test_fully_amortized_transition(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-full")
            asset = await create_asset(
                session,
                _asset_payload(
                    originalValue="240.00", usefulLifeMonths=24,
                    acquisitionDate="2024-02-01", availableForUseDate="2024-02-01",
                ),
                company_id="c-full", user_id="owner", request_id="t",
            )
            await submit_asset(session, asset.id, company_id="c-full", user_id="owner", request_id="t")
            assert asset.status == "active"
            # 直接执行最后一期（2024-02 + 23 = 2026-01，sequence_no=24）
            await run_amortization(session, "2026-01", company_id="c-full", user_id="owner", request_id="t")
            assert asset.status == "fully_amortized"
            third = await run_amortization(session, "2026-02", company_id="c-full", user_id="owner", request_id="t")
            assert third["processedCount"] == 0


# ── 7. 期间锁 ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_closed_period_rejects_amortization(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-lock")
            asset, _ = await _create_and_submit(session, "c-lock")
            # 取得凭证已创建 2026-01 期间，这里直接关掉它
            period = (
                await session.execute(
                    select(AccountingPeriod).where(
                        AccountingPeriod.company_id == "c-lock",
                        AccountingPeriod.period_start == date(2026, 1, 1),
                    )
                )
            ).scalar_one()
            period.status = "closed"

            with pytest.raises(AccountingError) as exc:
                await run_amortization(session, "2026-01", company_id="c-lock", user_id="owner", request_id="t")
            assert exc.value.code == "PERIOD_CLOSED"
            rows = (
                await session.execute(
                    select(IntangibleAmortizationSchedule).where(
                        IntangibleAmortizationSchedule.asset_id == asset.id,
                        IntangibleAmortizationSchedule.period_start == "2026-01",
                    )
                )
            ).scalars().all()
            assert rows[0].status == "planned"

            # 重开期间后可补提
            period.status = "reopened"
            result = await run_amortization(session, "2026-01", company_id="c-lock", user_id="owner", request_id="t")
            assert result["processedCount"] == 1


# ── 8. 处置：快照、当月停摊、分录与状态回写 ──────────────────────────────

@pytest.mark.asyncio
async def test_disposal_sale_flow(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-disp")
            asset, _ = await _create_and_submit(
                session, "c-disp",
                originalValue="120000.00", usefulLifeMonths=120,
                acquisitionDate="2025-01-01", availableForUseDate="2025-01-01",
            )
            # 摊销 9 个月：2025-01 ~ 2025-09，累计 9000
            for i in range(9):
                period = f"2025-{i + 1:02d}"
                await run_amortization(session, period, company_id="c-disp", user_id="owner", request_id="t")
            assert str(asset.accumulated_amortization) == "9000.00"

            disposal = await create_disposal(
                session,
                {
                    "assetId": asset.id,
                    "disposalType": "sale",
                    "disposalDate": "2025-10-20",
                    "proceedsAmount": "80000.00",
                    "outputVatAmount": "4800.00",
                    "reason": "业务调整",
                },
                company_id="c-disp", user_id="owner", request_id="t",
            )
            assert disposal.status == "draft"
            assert asset.status == "disposing"
            assert str(disposal.net_book_value_snapshot) == "111000.00"
            oct_row = (
                await session.execute(
                    select(IntangibleAmortizationSchedule).where(
                        IntangibleAmortizationSchedule.asset_id == asset.id,
                        IntangibleAmortizationSchedule.period_start == "2025-10",
                    )
                )
            ).scalar_one()
            assert oct_row.status == "skipped"

            # 处置当月不再摊销
            result = await run_amortization(session, "2025-10", company_id="c-disp", user_id="owner", request_id="t")
            assert result["processedCount"] == 0

            await submit_disposal(session, disposal.id, company_id="c-disp", user_id="owner", request_id="t")
            approve = await approve_disposal(session, disposal.id, company_id="c-disp", user_id="owner", request_id="t")
            assert approve["assetStatus"] == "disposed"
            assert asset.status == "disposed"
            assert asset.disposed_on is not None

            debit, credit = await _entry_lines(session, disposal.journal_entry_id)
            assert _by_code(debit) == {
                "1002": "84800.00",    # 含税价款
                "1702": "9000.00",     # 累计摊销
                "5711001": "31000.00", # 损失 = 净值 111000 − 不含税价款 80000
            }
            assert _by_code(credit) == {
                "1701": "120000.00",
                "222100105": "4800.00",
            }


@pytest.mark.asyncio
async def test_disposal_cancel_restores_asset(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-cancel")
            asset, _ = await _create_and_submit(session, "c-cancel")
            disposal = await create_disposal(
                session,
                {"assetId": asset.id, "disposalType": "scrap", "disposalDate": "2026-03-10"},
                company_id="c-cancel", user_id="owner", request_id="t",
            )
            assert asset.status == "disposing"
            result = await cancel_disposal(session, disposal.id, company_id="c-cancel", user_id="owner", request_id="t")
            assert result["assetStatus"] == "active"
            row = (
                await session.execute(
                    select(IntangibleAmortizationSchedule).where(
                        IntangibleAmortizationSchedule.asset_id == asset.id,
                        IntangibleAmortizationSchedule.period_start == "2026-03",
                    )
                )
            ).scalar_one()
            assert row.status == "planned"


# ── 9. 报表净额：1701 − 1702 == 账面净值合计 == 报表无形资产行 ───────────

@pytest.mark.asyncio
async def test_balance_sheet_net_value(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-bs")
            asset, _ = await _create_and_submit(
                session, "c-bs",
                originalValue="120000.00", usefulLifeMonths=120,
                acquisitionDate="2025-12-01", availableForUseDate="2025-12-01",
            )
            await run_amortization(session, "2025-12", company_id="c-bs", user_id="owner", request_id="t")
            await run_amortization(session, "2026-01", company_id="c-bs", user_id="owner", request_id="t")

            detail = await get_asset(session, asset.id, company_id="c-bs", user_id="owner")
            assert str(detail["accumulatedAmortization"]) == "2000.00"
            assert str(detail["netValue"]) == "118000.00"
            assert detail["remainingMonths"] == 118

    # _build_balance_rows 按科目余额表取数：1701 借方余额、1702 贷方余额
    current = {"1701": Decimal("120000.00"), "1702": Decimal("2000.00"), "1002": Decimal("0")}
    opening = {"1701": Decimal("0"), "1702": Decimal("0")}
    rows_current, _ = _build_balance_rows(current, opening, {})
    assert str(rows_current["intangible_asset_cost"]) == "120000.00"
    assert str(rows_current["accumulated_amortization"]) == "2000.00"
    assert str(rows_current["intangible_asset_book_value"]) == "118000.00"
    assert str(rows_current["intangible_asset"]) == "118000.00"  # 净额别名
    assert str(rows_current["non_current_asset_total"]) == "118000.00"


# ── 10. 多租户隔离与软删除 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tenant_isolation_and_soft_delete(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-a")
            asset_a, _ = await _create_and_submit(session, "c-a")
            await run_amortization(session, "2026-01", company_id="c-a", user_id="owner", request_id="t")

            # B 公司看不到 A 的资产
            listing = await list_assets(session, company_id="c-b", user_id="owner")
            assert listing["total"] == 0
            with pytest.raises(AccountingError):
                await get_asset(session, asset_a.id, company_id="c-b", user_id="owner")
            # B 公司摊销扫不到 A 的计划行
            await _seed(session, "c-b")
            result = await run_amortization(session, "2026-01", company_id="c-b", user_id="owner", request_id="t")
            assert result["processedCount"] == 0

            # 非 draft 不能删
            with pytest.raises(AccountingError):
                await delete_asset(session, asset_a.id, company_id="c-a", user_id="owner", request_id="t")

            # draft 可删，删除后编码可复用
            draft = await create_asset(
                session, _asset_payload(name="草稿资产"), company_id="c-a", user_id="owner", request_id="t"
            )
            code = draft.code
            await delete_asset(session, draft.id, company_id="c-a", user_id="owner", request_id="t")
            again = await create_asset(
                session, _asset_payload(name="复用编码"), company_id="c-a", user_id="owner", request_id="t"
            )
            assert again.code == code


# ── 11. 月结检查项 ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_close_check_intangible_amortization(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-ck")
            await _create_and_submit(session, "c-ck")

            checks = await period_close_checks(session, "c-ck", "2026-01")
            intangible = next(c for c in checks["checks"] if c["code"] == "INTANGIBLE_AMORTIZATION")
            assert intangible["status"] == "blocking"
            assert intangible["suggestedAction"] == "run_amortization"

            await run_amortization(session, "2026-01", company_id="c-ck", user_id="owner", request_id="t")
            checks = await period_close_checks(session, "c-ck", "2026-01")
            intangible = next(c for c in checks["checks"] if c["code"] == "INTANGIBLE_AMORTIZATION")
            assert intangible["status"] == "passed"


# ── 12. 编辑草稿重建计划与摊销汇总勾稽 ───────────────────────────────────

@pytest.mark.asyncio
async def test_update_draft_rebuilds_schedule(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-upd")
            asset = await create_asset(
                session,
                _asset_payload(originalValue="120000.00", usefulLifeMonths=120),
                company_id="c-upd", user_id="owner", request_id="t",
            )
            await update_asset(
                session,
                asset.id,
                {"usefulLifeMonths": 60},
                company_id="c-upd", user_id="owner", request_id="t",
            )
            assert asset.useful_life_months == 60
            rows = (
                await session.execute(
                    select(IntangibleAmortizationSchedule).where(
                        IntangibleAmortizationSchedule.asset_id == asset.id
                    )
                )
            ).scalars().all()
            assert len(rows) == 60
            assert sum(Decimal(str(r.amount)) for r in rows) == Decimal("120000.00")
            # 2026-01 起 60 期，终止于 2030-12
            assert asset.amortization_end_period == "2030-12"


@pytest.mark.asyncio
async def test_amortization_summary_reconciliation(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-sum")
            await _create_and_submit(session, "c-sum")
            await run_amortization(session, "2026-01", company_id="c-sum", user_id="owner", request_id="t")
            summary = await amortization_summary(
                session, "2026-01", "2026-01", company_id="c-sum", user_id="owner"
            )
            assert len(summary["items"]) == 1
            assert str(summary["items"][0]["amortizationAmount"]) == "1000.00"
            # owner_self_post 下摊销凭证直接过账：计划合计 == 总账科目合计
            recon = summary["reconciliation"]
            assert str(recon["scheduleTotal"]) == "1000.00"
            assert str(recon["ledgerAdminAmount"]) == "1000.00"
            assert str(recon["diff"]) == "0.00"
