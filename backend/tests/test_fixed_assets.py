# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""固定资产模块测试（P1 WP-B）：次月起提、尾差、一次性折旧、幂等、期间锁、
处置 1606 中转、期初衔接、核对总账、存量迁移回填、月结检查。

对应 P1场景扩展落地计划.md WP-B 测试要点。
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import db.models  # noqa: F401
from core.errors import AccountingError
from core.fixed_asset_categories import (
    FIXED_ASSET_CATEGORIES,
    ONE_TIME_DEPRECIATION_LIMIT,
    category_payload,
)
from db.base import Base
from db.models import (
    AccountingPeriod,
    Company,
    CompanyMember,
    FixedAsset,
    FixedAssetDepreciationSchedule,
    FixedAssetDisposal,
    JournalEntry,
    JournalLine,
    RdProject,
)
from services.accounting_core_service import create_entry, ensure_tenant, period_close_checks
from services.depreciation_engine import (
    get_schedule,
    list_due_depreciation_periods,
    run_depreciation,
    run_depreciation_catch_up,
)
from services.fixed_asset_disposal_service import (
    approve_disposal,
    cancel_disposal,
    create_disposal,
    submit_disposal,
)
from services.fixed_asset_reconciliation_service import asset_reconciliation
from services.fixed_asset_service import (
    approve_asset,
    create_asset,
    create_asset_from_entry,
    delete_asset,
    get_fixed_asset_entry_prefill,
    ingest_card,
    list_assets,
    reject_asset,
    submit_asset,
    update_asset,
)


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
        "name": "XX 台式机",
        "category": "electronics",
        "acquisitionType": "purchase",
        "acquisitionDate": "2026-01-15",
        "availableForUseDate": "2026-01-15",
        "originalValue": "120000.00",
        "residualValue": "0",
        "usefulLifeMonths": 36,
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


async def _post_asset_purchase(
    session,
    company_id: str,
    *,
    entry_id: str,
    amount: str = "16800.00",
    occurred_on: str = "2026-01-15",
) -> JournalEntry:
    return await create_entry(
        session,
        {
            "id": entry_id,
            "direction": "expense",
            "amount": amount,
            "title": "购办公电脑及显示器",
            "category": "固定资产",
            "occurDate": occurred_on,
            "dedupFingerprint": f"asset-source:{company_id}:{entry_id}",
            "voucherAuthority": "system",
            "voucherLines": {
                "debit": [{"accountCode": "1601", "accountName": "固定资产", "amount": amount}],
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


# ── 1. 分类目录 ───────────────────────────────────────────────────────────

def test_categories_catalog():
    assert len(FIXED_ASSET_CATEGORIES) == 6
    payload = category_payload()
    assert {c["code"] for c in payload} == {
        "building", "machine", "vehicle", "electronics", "furniture", "other",
    }
    building = next(c for c in payload if c["code"] == "building")
    assert building["defaultLifeMonths"] == 240
    assert building["oneTimeAllowed"] is False
    electronics = next(c for c in payload if c["code"] == "electronics")
    assert electronics["defaultLifeMonths"] == 36
    assert ONE_TIME_DEPRECIATION_LIMIT == Decimal("5000000")


@pytest.mark.asyncio
async def test_category_life_and_method_validation(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-cat")
            # 房屋建筑物最短 240 个月
            with pytest.raises(AccountingError):
                await create_asset(
                    session,
                    _asset_payload(category="building", usefulLifeMonths=36),
                    company_id="c-fa-cat", user_id="owner", request_id="t",
                )
            # 房屋建筑物不适用一次性折旧
            with pytest.raises(AccountingError):
                await create_asset(
                    session,
                    _asset_payload(category="building", usefulLifeMonths=240, depreciationMethod="one_time"),
                    company_id="c-fa-cat", user_id="owner", request_id="t",
                )
            # 超过 500 万不适用一次性折旧
            with pytest.raises(AccountingError):
                await create_asset(
                    session,
                    _asset_payload(
                        originalValue="5000001.00", usefulLifeMonths=36, depreciationMethod="one_time"
                    ),
                    company_id="c-fa-cat", user_id="owner", request_id="t",
                )
            # 一次性折旧要求残值为零
            with pytest.raises(AccountingError):
                await create_asset(
                    session,
                    _asset_payload(
                        originalValue="50000.00", residualValue="1000",
                        usefulLifeMonths=36, depreciationMethod="one_time",
                    ),
                    company_id="c-fa-cat", user_id="owner", request_id="t",
                )


# ── 2. 外购取得：凭证 1601 / 计划次月起提 ─────────────────────────────────

@pytest.mark.asyncio
async def test_purchase_submit_posts_voucher_and_schedule(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa")
            asset, result = await _create_and_submit(session, "c-fa")
            assert asset.status == "active"
            assert result["entryStatus"] == "posted"
            assert asset.source_entry_id == f"fixed-asset-acq-{asset.id}"

            debit, credit = await _entry_lines(session, f"fixed-asset-acq-{asset.id}")
            assert {l.account_code for l in debit} == {"1601"}
            assert {l.account_code for l in credit} == {"1002"}

            rows = (
                await session.execute(
                    select(FixedAssetDepreciationSchedule)
                    .where(FixedAssetDepreciationSchedule.asset_id == asset.id)
                    .order_by(FixedAssetDepreciationSchedule.sequence_no)
                )
            ).scalars().all()
            assert len(rows) == 36
            # 次月起提：可供使用 2026-01 → 首期 2026-02，末期 2029-01
            assert rows[0].period_start == "2026-02"
            assert rows[-1].period_start == "2029-01"
            total = sum(Decimal(str(r.amount)) for r in rows)
            assert total == Decimal("120000.00")
            assert asset.depreciation_end_period == "2029-01"


@pytest.mark.asyncio
async def test_maker_checker_flow(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-mc", "boss")
            company = await session.get(Company, "c-fa-mc")
            company.posting_policy = "maker_checker"
            session.add(CompanyMember(company_id="c-fa-mc", user_id="bookkeeper", role="preparer", status="active"))
            session.add(CompanyMember(company_id="c-fa-mc", user_id="chief", role="accountant", status="active"))

            asset = await create_asset(
                session, _asset_payload(), company_id="c-fa-mc", user_id="bookkeeper", request_id="t"
            )
            result = await submit_asset(session, asset.id, company_id="c-fa-mc", user_id="bookkeeper", request_id="t")
            assert result["entryStatus"] == "pending_review"
            assert asset.status == "pending_review"

            with pytest.raises(AccountingError):
                await approve_asset(session, asset.id, company_id="c-fa-mc", user_id="bookkeeper", request_id="t")

            result = await approve_asset(session, asset.id, company_id="c-fa-mc", user_id="chief", request_id="t")
            assert result["entryStatus"] == "posted"
            assert asset.status == "active"


@pytest.mark.asyncio
async def test_reject_returns_asset_to_draft(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-rj", "boss")
            company = await session.get(Company, "c-fa-rj")
            company.posting_policy = "maker_checker"
            session.add(CompanyMember(company_id="c-fa-rj", user_id="bookkeeper", role="preparer", status="active"))
            session.add(CompanyMember(company_id="c-fa-rj", user_id="chief", role="accountant", status="active"))
            asset = await create_asset(
                session, _asset_payload(), company_id="c-fa-rj", user_id="bookkeeper", request_id="t"
            )
            await submit_asset(session, asset.id, company_id="c-fa-rj", user_id="bookkeeper", request_id="t")
            result = await reject_asset(
                session, asset.id, company_id="c-fa-rj", user_id="chief", request_id="t", reason="缺购置发票"
            )
            assert result["asset"]["status"] == "draft"
            assert asset.source_entry_id is not None
            result = await submit_asset(session, asset.id, company_id="c-fa-rj", user_id="bookkeeper", request_id="t")
            assert result["entryStatus"] == "pending_review"
            assert result["journalEntryId"] == asset.source_entry_id


# ── 3. 尾差兜平 ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schedule_tail_difference(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-tail")
            asset = await create_asset(
                session,
                _asset_payload(
                    originalValue="100.00", usefulLifeMonths=12,
                    acquisitionDate="2026-03-01", availableForUseDate="2026-03-01",
                ),
                company_id="c-fa-tail", user_id="owner", request_id="t",
            )
            await submit_asset(session, asset.id, company_id="c-fa-tail", user_id="owner", request_id="t")
            rows = (
                await session.execute(
                    select(FixedAssetDepreciationSchedule)
                    .where(FixedAssetDepreciationSchedule.asset_id == asset.id)
                    .order_by(FixedAssetDepreciationSchedule.sequence_no)
                )
            ).scalars().all()
            assert len(rows) == 12
            # 次月起提：2026-04 起
            assert rows[0].period_start == "2026-04"
            assert str(rows[0].amount) == "8.33"
            assert str(rows[-1].amount) == "8.37"
            assert sum(Decimal(str(r.amount)) for r in rows) == Decimal("100.00")


# ── 4. 折旧执行：分录、幂等、次月起提、研发分摊、提足自动转态 ────────────

@pytest.mark.asyncio
async def test_run_depreciation_posts_voucher_and_idempotent(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-run")
            asset, _ = await _create_and_submit(session, "c-fa-run")

            # 次月起提：2026-01 执行无计划行
            early = await run_depreciation(session, "2026-01", company_id="c-fa-run", user_id="owner", request_id="t")
            assert early["processedCount"] == 0

            result = await run_depreciation(session, "2026-02", company_id="c-fa-run", user_id="owner", request_id="t")
            assert result["processedCount"] == 1
            assert result["postedCount"] == 1
            assert str(result["totalAmount"]) == "3333.33"

            entry_id = f"depreciation-{asset.id}-2026-02"
            debit, credit = await _entry_lines(session, entry_id)
            assert {l.account_code for l in debit} == {"5602007"}
            assert {l.account_code for l in credit} == {"1602"}
            assert str(credit[0].amount) == "3333.33"

            assert str(asset.accumulated_depreciation) == "3333.33"
            assert asset.last_depreciated_period == "2026-02"

            # 幂等：同期间重跑跳过
            again = await run_depreciation(session, "2026-02", company_id="c-fa-run", user_id="owner", request_id="t")
            assert again["processedCount"] == 0
            assert again["skippedCount"] >= 1
            assert str(asset.accumulated_depreciation) == "3333.33"


@pytest.mark.asyncio
async def test_run_depreciation_dry_run(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-dry")
            await _create_and_submit(session, "c-fa-dry")
            preview = await run_depreciation(
                session, "2026-02", company_id="c-fa-dry", user_id="owner", request_id="t", dry_run=True
            )
            assert preview["dryRun"] is True
            assert preview["processedCount"] == 1
            assert str(preview["totalAmount"]) == "3333.33"
            # 预览不落库
            posted = (
                await session.execute(
                    select(FixedAssetDepreciationSchedule).where(
                        FixedAssetDepreciationSchedule.status == "posted"
                    )
                )
            ).scalars().all()
            assert posted == []


@pytest.mark.asyncio
async def test_run_depreciation_rd_split(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-rd")
            session.add(
                RdProject(
                    company_id="c-fa-rd", code="RD2026-01", name="折旧研发项目",
                    status="active", start_date=date(2026, 1, 1), created_by="owner",
                )
            )
            await session.flush()
            project = (
                await session.execute(select(RdProject).where(RdProject.company_id == "c-fa-rd"))
            ).scalar_one()
            await _create_and_submit(
                session, "c-fa-rd",
                rdProjectId=project.id, rdUseRate="0.6", rdAllocationBasis="工时占比60%",
            )
            result = await run_depreciation(session, "2026-02", company_id="c-fa-rd", user_id="owner", request_id="t")
            assert result["processedCount"] == 1
            rows = (
                await session.execute(select(JournalLine).where(JournalLine.journal_entry_id == result["items"][0]["journalEntryId"]))
            ).scalars().all()
            debit_by_code = {l.account_code: str(l.amount) for l in rows if l.direction == "debit"}
            assert debit_by_code["430100103"] == "2000.00"   # 3333.33 × 0.6
            assert debit_by_code["5602007"] == "1333.33"     # 管理费用部分


@pytest.mark.asyncio
async def test_one_time_depreciation_full_in_first_period(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-one")
            asset, _ = await _create_and_submit(
                session, "c-fa-one",
                name="500 万以内设备", category="machine",
                originalValue="480000.00", usefulLifeMonths=120,
                depreciationMethod="one_time",
            )
            rows = (
                await session.execute(
                    select(FixedAssetDepreciationSchedule)
                    .where(FixedAssetDepreciationSchedule.asset_id == asset.id)
                )
            ).scalars().all()
            assert len(rows) == 1
            assert rows[0].period_start == "2026-02"
            assert str(rows[0].amount) == "480000.00"
            assert asset.depreciation_end_period == "2026-02"

            result = await run_depreciation(session, "2026-02", company_id="c-fa-one", user_id="owner", request_id="t")
            assert result["processedCount"] == 1
            assert asset.status == "fully_depreciated"
            assert str(asset.accumulated_depreciation) == "480000.00"
            # 提足后再执行无新增
            third = await run_depreciation(session, "2026-03", company_id="c-fa-one", user_id="owner", request_id="t")
            assert third["processedCount"] == 0


@pytest.mark.asyncio
async def test_fully_depreciated_transition(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-full")
            asset, _ = await _create_and_submit(
                session, "c-fa-full",
                originalValue="100.00", usefulLifeMonths=12,
                acquisitionDate="2024-02-01", availableForUseDate="2024-02-01",
            )
            # 逐期计提：2024-03 起 12 期 → 2025-02
            from services.fixed_asset_service import _add_months

            for i in range(12):
                period_i = _add_months("2024-03", i)
                await run_depreciation(session, period_i, company_id="c-fa-full", user_id="owner", request_id="t")
            assert asset.status == "fully_depreciated"
            assert str(asset.accumulated_depreciation) == "100.00"
            nxt = await run_depreciation(session, "2025-03", company_id="c-fa-full", user_id="owner", request_id="t")
            assert nxt["processedCount"] == 0


@pytest.mark.asyncio
async def test_closed_period_rejects_depreciation(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-lock")
            asset, _ = await _create_and_submit(session, "c-fa-lock")
            # 首期折旧在 2026-02；关掉 2026-02 期间
            period = AccountingPeriod(
                company_id="c-fa-lock", period_start=date(2026, 2, 1), status="closed",
            )
            session.add(period)
            await session.flush()

            with pytest.raises(AccountingError) as exc:
                await run_depreciation(session, "2026-02", company_id="c-fa-lock", user_id="owner", request_id="t")
            assert exc.value.code == "PERIOD_CLOSED"
            rows = (
                await session.execute(
                    select(FixedAssetDepreciationSchedule).where(
                        FixedAssetDepreciationSchedule.asset_id == asset.id,
                        FixedAssetDepreciationSchedule.period_start == "2026-02",
                    )
                )
            ).scalars().all()
            assert rows[0].status == "planned"

            period.status = "reopened"
            result = await run_depreciation(session, "2026-02", company_id="c-fa-lock", user_id="owner", request_id="t")
            assert result["processedCount"] == 1


# ── 5. 处置：快照、当月停提、1606 中转分录与状态回写 ──────────────────────

@pytest.mark.asyncio
async def test_disposal_sale_flow(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-disp")
            asset, _ = await _create_and_submit(session, "c-fa-disp")
            # 先计提 2026-02 折旧，累计 3333.33
            await run_depreciation(session, "2026-02", company_id="c-fa-disp", user_id="owner", request_id="t")

            disposal = await create_disposal(
                session,
                {
                    "assetId": asset.id, "disposalType": "sale",
                    "disposalDate": "2026-03-31", "proceedsAmount": "118000.00",
                },
                company_id="c-fa-disp", user_id="owner", request_id="t",
            )
            assert disposal.status == "draft"
            assert str(disposal.accumulated_depreciation_snapshot) == "3333.33"
            assert str(disposal.net_book_value_snapshot) == "116666.67"
            assert asset.status == "disposing"
            # 处置当月 planned 行置 skipped（当月不再计提）
            march_rows = (
                await session.execute(
                    select(FixedAssetDepreciationSchedule).where(
                        FixedAssetDepreciationSchedule.asset_id == asset.id,
                        FixedAssetDepreciationSchedule.period_start == "2026-03",
                    )
                )
            ).scalars().all()
            assert march_rows[0].status == "skipped"

            await submit_disposal(session, disposal.id, company_id="c-fa-disp", user_id="owner", request_id="t")
            result = await approve_disposal(session, disposal.id, company_id="c-fa-disp", user_id="owner", request_id="t")
            assert result["status"] == "posted"
            assert asset.status == "disposed"

            debit, credit = await _entry_lines(session, result["journalEntryId"])
            debit_by_code: dict[str, Decimal] = {}
            credit_by_code: dict[str, Decimal] = {}
            for l in debit:
                debit_by_code[l.account_code] = debit_by_code.get(l.account_code, Decimal("0")) + Decimal(str(l.amount))
            for l in credit:
                credit_by_code[l.account_code] = credit_by_code.get(l.account_code, Decimal("0")) + Decimal(str(l.amount))
            # 借：清理净值 + 累计折旧 + 价款 + 清理收益结转
            assert str(debit_by_code["1606"]) == "118000.00"   # 净值 116666.67 + 收益 1333.33
            assert str(debit_by_code["1602"]) == "3333.33"
            assert str(debit_by_code["1002"]) == "118000.00"
            # 贷：原值 + 清理价款 + 处置收益
            assert str(credit_by_code["1601"]) == "120000.00"
            assert str(credit_by_code["1606"]) == "118000.00"
            assert str(credit_by_code["5301001"]) == "1333.33"
            assert sum(debit_by_code.values()) == sum(credit_by_code.values())


@pytest.mark.asyncio
async def test_disposal_scrap_loss_and_cancel_restores(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-scrap")
            asset, _ = await _create_and_submit(
                session, "c-fa-scrap",
                originalValue="1000.00", usefulLifeMonths=12,
            )
            # 不计提直接报废：净损失 = 全额原值
            disposal = await create_disposal(
                session,
                {"assetId": asset.id, "disposalType": "scrap", "disposalDate": "2026-01-31"},
                company_id="c-fa-scrap", user_id="owner", request_id="t",
            )
            await submit_disposal(session, disposal.id, company_id="c-fa-scrap", user_id="owner", request_id="t")
            result = await approve_disposal(session, disposal.id, company_id="c-fa-scrap", user_id="owner", request_id="t")
            assert result["assetStatus"] == "scrapped"
            debit, credit = await _entry_lines(session, result["journalEntryId"])
            debit_codes = {l.account_code: str(l.amount) for l in debit}
            assert debit_codes["5711001"] == "1000.00"  # 净损失进营业外支出
            assert sum(Decimal(str(l.amount)) for l in debit) == sum(Decimal(str(l.amount)) for l in credit)

            # 取消路径：另一台资产处置后取消，恢复 active 与 planned 行
            asset2, _ = await _create_and_submit(session, "c-fa-scrap", name="第二台设备")
            disposal2 = await create_disposal(
                session,
                {"assetId": asset2.id, "disposalType": "scrap", "disposalDate": "2026-02-28"},
                company_id="c-fa-scrap", user_id="owner", request_id="t",
            )
            assert asset2.status == "disposing"
            result = await cancel_disposal(session, disposal2.id, company_id="c-fa-scrap", user_id="owner", request_id="t")
            assert result["assetStatus"] == "active"
            feb_row = (
                await session.execute(
                    select(FixedAssetDepreciationSchedule).where(
                        FixedAssetDepreciationSchedule.asset_id == asset2.id,
                        FixedAssetDepreciationSchedule.period_start == "2026-02",
                    )
                )
            ).scalars().all()
            assert feb_row[0].status == "planned"


@pytest.mark.asyncio
async def test_disposal_requires_disposable_status(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-dd")
            asset = await create_asset(
                session, _asset_payload(), company_id="c-fa-dd", user_id="owner", request_id="t"
            )
            with pytest.raises(AccountingError):
                await create_disposal(
                    session,
                    {"assetId": asset.id, "disposalType": "scrap", "disposalDate": "2026-01-31"},
                    company_id="c-fa-dd", user_id="owner", request_id="t",
                )


# ── 6. 期初衔接：ingest_card 直建卡片 + 幂等 ─────────────────────────────

@pytest.mark.asyncio
async def test_ingest_opening_card(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-open")
            card = await ingest_card(
                session,
                {
                    "name": "期初办公设备", "category": "electronics",
                    "acquisitionDate": "2025-06-01", "originalValue": "36000.00",
                    "residualRate": "0.05", "usefulLifeMonths": 36,
                },
                company_id="c-fa-open", user_id="owner",
                source_entry_id="opening-c-fa-open-2026",
                accumulated_depreciation=Decimal("3420.00"),
                last_depreciated_period="2025-12",
            )
            assert card.status == "active"
            assert str(card.accumulated_depreciation) == "3420.00"
            rows = (
                await session.execute(
                    select(FixedAssetDepreciationSchedule)
                    .where(FixedAssetDepreciationSchedule.asset_id == card.id)
                    .order_by(FixedAssetDepreciationSchedule.sequence_no)
                )
            ).scalars().all()
            # 剩余计划从 2026-01 继续，Σ = (36000 - 1800) - 3420 = 30780
            assert rows[0].period_start == "2026-01"
            total = sum(Decimal(str(r.amount)) for r in rows)
            assert total == Decimal("30780.00")

            # 同一期初凭证重复入卡被拒
            with pytest.raises(AccountingError) as exc:
                await ingest_card(
                    session,
                    {
                        "name": "重复卡片", "category": "electronics",
                        "acquisitionDate": "2025-06-01", "originalValue": "1000.00",
                        "usefulLifeMonths": 36,
                    },
                    company_id="c-fa-open", user_id="owner",
                    source_entry_id="opening-c-fa-open-2026",
                )
            assert exc.value.code == "DUPLICATE_SOURCE"


@pytest.mark.asyncio
async def test_create_asset_from_posted_entry_without_duplicate_voucher(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-entry")
            source = await _post_asset_purchase(
                session, "c-fa-entry", entry_id="entry-fa-source"
            )
            before = (
                await session.execute(
                    select(JournalEntry).where(JournalEntry.company_id == "c-fa-entry")
                )
            ).scalars().all()

            prefill = await get_fixed_asset_entry_prefill(
                session, source.id, company_id="c-fa-entry", user_id="owner"
            )
            assert prefill["originalValue"] == Decimal("16800.00")
            assert prefill["suggestedCategory"] == "electronics"
            assert prefill["existingAssetId"] is None

            card = await create_asset_from_entry(
                session,
                {
                    **_asset_payload(
                        name="购办公电脑及显示器",
                        originalValue="16800.00",
                        usefulLifeMonths=36,
                    ),
                    "sourceEntryId": source.id,
                },
                company_id="c-fa-entry",
                user_id="owner",
                request_id="t",
            )
            after = (
                await session.execute(
                    select(JournalEntry).where(JournalEntry.company_id == "c-fa-entry")
                )
            ).scalars().all()
            assert len(after) == len(before)
            assert card.source_entry_id == source.id
            assert card.status == "active"
            rows = (
                await session.execute(
                    select(FixedAssetDepreciationSchedule)
                    .where(FixedAssetDepreciationSchedule.asset_id == card.id)
                    .order_by(FixedAssetDepreciationSchedule.sequence_no)
                )
            ).scalars().all()
            assert rows[0].period_start == "2026-02"

            linked = await get_fixed_asset_entry_prefill(
                session, source.id, company_id="c-fa-entry", user_id="owner"
            )
            assert linked["existingAssetId"] == card.id

            with pytest.raises(AccountingError) as exc:
                await create_asset_from_entry(
                    session,
                    {
                        **_asset_payload(
                            name="重复电脑",
                            originalValue="16800.00",
                            usefulLifeMonths=36,
                        ),
                        "sourceEntryId": source.id,
                    },
                    company_id="c-fa-entry",
                    user_id="owner",
                    request_id="t",
                )
            assert exc.value.code == "DUPLICATE_SOURCE"
            assert exc.value.context["existingAssetId"] == card.id


@pytest.mark.asyncio
async def test_depreciation_catch_up_posts_each_period_and_is_idempotent(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-catchup")
            source = await _post_asset_purchase(
                session, "c-fa-catchup", entry_id="entry-fa-catchup"
            )
            card = await create_asset_from_entry(
                session,
                {
                    **_asset_payload(
                        name="购办公电脑及显示器",
                        originalValue="16800.00",
                        usefulLifeMonths=36,
                    ),
                    "sourceEntryId": source.id,
                },
                company_id="c-fa-catchup",
                user_id="owner",
                request_id="t",
            )
            due = await list_due_depreciation_periods(
                session, "c-fa-catchup", "2026-03", asset_ids=[card.id]
            )
            assert [item["period"] for item in due] == ["2026-02", "2026-03"]

            result = await run_depreciation_catch_up(
                session,
                "2026-03",
                company_id="c-fa-catchup",
                user_id="owner",
                request_id="catchup",
                asset_ids=[card.id],
            )
            assert [item["period"] for item in result["periods"]] == ["2026-02", "2026-03"]
            assert result["postedCount"] == 2
            rows = (
                await session.execute(
                    select(FixedAssetDepreciationSchedule)
                    .where(FixedAssetDepreciationSchedule.asset_id == card.id)
                    .order_by(FixedAssetDepreciationSchedule.sequence_no)
                )
            ).scalars().all()
            assert [row.status for row in rows[:2]] == ["posted", "posted"]
            assert rows[0].journal_entry_id != rows[1].journal_entry_id

            again = await run_depreciation_catch_up(
                session,
                "2026-03",
                company_id="c-fa-catchup",
                user_id="owner",
                request_id="catchup-again",
                asset_ids=[card.id],
            )
            assert again["processedCount"] == 0


@pytest.mark.asyncio
async def test_depreciation_catch_up_rejects_closed_period_without_posting(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-catchup-lock")
            source = await _post_asset_purchase(
                session, "c-fa-catchup-lock", entry_id="entry-fa-catchup-lock"
            )
            card = await create_asset_from_entry(
                session,
                {
                    **_asset_payload(
                        name="购办公电脑及显示器",
                        originalValue="16800.00",
                        usefulLifeMonths=36,
                    ),
                    "sourceEntryId": source.id,
                },
                company_id="c-fa-catchup-lock",
                user_id="owner",
                request_id="t",
            )
            session.add(
                AccountingPeriod(
                    company_id="c-fa-catchup-lock",
                    period_start=date(2026, 2, 1),
                    status="closed",
                )
            )
            await session.flush()

            with pytest.raises(AccountingError) as exc:
                await run_depreciation_catch_up(
                    session,
                    "2026-03",
                    company_id="c-fa-catchup-lock",
                    user_id="owner",
                    request_id="catchup-lock",
                    asset_ids=[card.id],
                )
            assert exc.value.code == "PERIOD_CLOSED"
            assert exc.value.context["periods"] == ["2026-02"]
            rows = (
                await session.execute(
                    select(FixedAssetDepreciationSchedule).where(
                        FixedAssetDepreciationSchedule.asset_id == card.id,
                        FixedAssetDepreciationSchedule.status == "posted",
                    )
                )
            ).scalars().all()
            assert rows == []


@pytest.mark.asyncio
async def test_asset_soft_delete_and_isolation(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-iso-a", "owner")
            await _seed(session, "c-fa-iso-b", "owner")
            asset, _ = await _create_and_submit(session, "c-fa-iso-a")
            # 其他公司看不到
            result_b = await list_assets(session, company_id="c-fa-iso-b", user_id="owner")
            assert result_b["items"] == []
            result_a = await list_assets(session, company_id="c-fa-iso-a", user_id="owner")
            assert result_a["total"] == 1

            draft = await create_asset(
                session, _asset_payload(name="待删草稿"), company_id="c-fa-iso-a", user_id="owner", request_id="t"
            )
            await delete_asset(session, draft.id, company_id="c-fa-iso-a", user_id="owner", request_id="t")
            with pytest.raises(AccountingError):
                await get_asset_fixed(session, draft.id, company_id="c-fa-iso-a", user_id="owner")


async def get_asset_fixed(session, asset_id, *, company_id, user_id):
    from services.fixed_asset_service import get_asset

    return await get_asset(session, asset_id, company_id=company_id, user_id=user_id)


# ── 7. 核对总账：三方勾稽 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reconciliation_three_way(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-recon")
            # 无卡片时不适用
            empty = await asset_reconciliation(session, "c-fa-recon")
            assert empty["applicable"] is False

            await _create_and_submit(session, "c-fa-recon")
            await run_depreciation(session, "2026-02", company_id="c-fa-recon", user_id="owner", request_id="t")

            recon = await asset_reconciliation(session, "c-fa-recon")
            assert recon["applicable"] is True
            assert recon["balanced"] is True
            assert str(recon["cardCostTotal"]) == str(recon["ledger1601"]) == "120000.00"
            assert str(recon["cardDepreciationTotal"]) == str(recon["ledger1602"]) == "3333.33"
            assert str(recon["schedulePostedTotal"]) == "3333.33"
            assert str(recon["costDiff"]) == "0.00"

            # 卡片与总账脱钩（模拟：手工多记一笔 1601）→ 勾稽不平且带明细
            await create_entry(
                session,
                {
                    "direction": "expense", "amount": "500.00", "title": "手工资产购置",
                    "category": "固定资产", "occurDate": "2026-02-10",
                    "dedupFingerprint": "recon-test:extra", "voucherAuthority": "system",
                    "voucherLines": {
                        "debit": [{"accountCode": "1601", "accountName": "固定资产", "amount": "500.00"}],
                        "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "500.00"}],
                    },
                },
                company_id="c-fa-recon", user_id="owner", request_id="t", confirm=True,
            )
            broken = await asset_reconciliation(session, "c-fa-recon")
            assert broken["balanced"] is False
            assert str(broken["costDiff"]) == "-500.00"
            assert len(broken["details"]) == 1


# ── 8. 月结检查：折旧 pending blocking + 账实子项 ─────────────────────────

@pytest.mark.asyncio
async def test_close_check_fixed_asset(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-ck")
            await _create_and_submit(session, "c-fa-ck")

            checks = await period_close_checks(session, "c-fa-ck", "2026-02")
            dep = next(c for c in checks["checks"] if c["code"] == "FIXED_ASSET_DEPRECIATION")
            assert dep["status"] == "blocking"
            assert dep["suggestedAction"] == "run_depreciation"
            book_actual = next(c for c in checks["checks"] if c["code"] == "BOOK_TO_ACTUAL")
            assert book_actual["fixedAsset"]["applicable"] is True
            assert book_actual["fixedAsset"]["balanced"] is True

            await run_depreciation(session, "2026-02", company_id="c-fa-ck", user_id="owner", request_id="t")
            checks = await period_close_checks(session, "c-fa-ck", "2026-02")
            dep = next(c for c in checks["checks"] if c["code"] == "FIXED_ASSET_DEPRECIATION")
            assert dep["status"] == "passed"


# ── 9. 编辑草稿重建计划 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_draft_rebuilds_schedule(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-upd")
            asset = await create_asset(
                session,
                _asset_payload(originalValue="120000.00", usefulLifeMonths=120),
                company_id="c-fa-upd", user_id="owner", request_id="t",
            )
            await update_asset(
                session,
                asset.id,
                {"usefulLifeMonths": 36},
                company_id="c-fa-upd", user_id="owner", request_id="t",
            )
            assert asset.useful_life_months == 36
            rows = (
                await session.execute(
                    select(FixedAssetDepreciationSchedule).where(
                        FixedAssetDepreciationSchedule.asset_id == asset.id
                    )
                )
            ).scalars().all()
            assert len(rows) == 36
            assert sum(Decimal(str(r.amount)) for r in rows) == Decimal("120000.00")
            assert asset.depreciation_end_period == "2029-01"


@pytest.mark.asyncio
async def test_schedule_query_via_engine(sessions):
    async with sessions() as session:
        async with session.begin():
            await _seed(session, "c-fa-sched")
            asset, _ = await _create_and_submit(session, "c-fa-sched")
            items = await get_schedule(session, asset.id, company_id="c-fa-sched", user_id="owner")
            assert len(items) == 36
            assert items[0]["periodStart"] == "2026-02"


# ── 10. 存量迁移回填（文件库：legacy 表经 get_conn 读写）─────────────────

@pytest.mark.asyncio
async def test_backfill_migrates_legacy_cards():
    """legacy_fixed_assets → ORM 卡片：勾稽过、计划重建、幂等打标。"""
    import sqlite3

    from db.session import database_url, get_engine
    from db.session import session_scope
    from services.fixed_asset_backfill_service import backfill_fixed_assets

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    # 路径还原（conftest 的 DATABASE_URL 是 sqlite+aiosqlite:///绝对路径文件）
    from services.database import _sqlite_target

    db_file = _sqlite_target(database_url())
    conn = sqlite3.connect(db_file)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS legacy_fixed_assets ("
            "id TEXT PRIMARY KEY, user_id TEXT NOT NULL DEFAULT '', company_id TEXT NOT NULL, "
            "payload TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '')"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS legacy_depreciation_schedule ("
            "id TEXT PRIMARY KEY, company_id TEXT NOT NULL, asset_id TEXT NOT NULL, period TEXT NOT NULL, "
            "depreciation_amount NUMERIC NOT NULL DEFAULT 0, accumulated_before NUMERIC NOT NULL DEFAULT 0, "
            "accumulated_after NUMERIC NOT NULL DEFAULT 0, voucher_ref TEXT, created_at TEXT NOT NULL DEFAULT '')"
        )
        conn.execute("DELETE FROM legacy_fixed_assets WHERE company_id = 'c-fa-bf'")
        conn.execute("DELETE FROM legacy_depreciation_schedule WHERE company_id = 'c-fa-bf'")
        cards = [
            {
                "id": "fa-srv-legacy-1", "name": "legacy 电脑", "category": "电子设备",
                "purchaseDate": "2025-01-10", "cost": 120000.0, "accumulatedDepreciation": 5000.0,
                "usefulMonths": 36, "salvageRate": 0.0, "status": "in_use",
                "lastDepreciationDate": "2025-12",
            },
            {
                "id": "fa-srv-legacy-2", "name": "legacy 一次性设备", "category": "机器设备",
                "purchaseDate": "2025-06-01", "cost": 80000.0, "accumulatedDepreciation": 0.0,
                "usefulMonths": 60, "salvageRate": 0.0, "oneTimeDepreciation": True, "status": "in_use",
            },
        ]
        for card in cards:
            conn.execute(
                "INSERT INTO legacy_fixed_assets (id, company_id, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (card["id"], "c-fa-bf", json.dumps(card), "2025-01-01", "2025-12-31"),
            )
        conn.execute(
            "INSERT INTO legacy_depreciation_schedule (id, company_id, asset_id, period, depreciation_amount, "
            "accumulated_before, accumulated_after, voucher_ref, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("dep-1", "c-fa-bf", "fa-srv-legacy-1", "2025-12", 5000.0, 0.0, 5000.0, None, "2025-12-31"),
        )
        conn.commit()
    finally:
        conn.close()

    async with session_scope() as session:
        await ensure_tenant(session, "c-fa-bf", "owner")
        # 勾稽锚：1601 借方 200000 / 1602 贷方 5000（配平行进实收资本）
        await create_entry(
            session,
            {
                "id": "opening-c-fa-bf-2026", "direction": "owner_transfer", "amount": "200000.00",
                "title": "期初余额", "category": "期初余额", "occurDate": "2026-01-01",
                "sourceType": "opening_balance", "dedupFingerprint": "opening:c-fa-bf:2026",
                "entryType": "opening", "voucherAuthority": "system",
                "voucherLines": {
                    "debit": [
                        {"accountCode": "1601", "accountName": "固定资产", "amount": "200000.00"},
                        {"accountCode": "5602007", "accountName": "管理费用-折旧费", "amount": "5000.00"},
                    ],
                    "credit": [
                        {"accountCode": "1602", "accountName": "累计折旧", "amount": "5000.00"},
                        {"accountCode": "3001", "accountName": "实收资本", "amount": "200000.00"},
                    ],
                },
            },
            company_id="c-fa-bf", user_id="owner", request_id="t", confirm=True,
        )

    result = await backfill_fixed_assets("c-fa-bf")
    assert result["ok"] is True
    assert result["migrated"] == 2

    async with session_scope() as session:
        cards_orm = (
            await session.execute(
                select(FixedAsset).where(FixedAsset.company_id == "c-fa-bf").order_by(FixedAsset.id)
            )
        ).scalars().all()
        assert len(cards_orm) == 2
        by_name = {c.name: c for c in cards_orm}
        legacy_pc = by_name["legacy 电脑"]
        assert legacy_pc.id == "fa-mig-fa-srv-legacy-1"
        assert legacy_pc.category == "electronics"
        assert str(legacy_pc.accumulated_depreciation) == "5000.00"
        assert legacy_pc.last_depreciated_period == "2025-12"
        rows = (
            await session.execute(
                select(FixedAssetDepreciationSchedule)
                .where(FixedAssetDepreciationSchedule.asset_id == legacy_pc.id)
                .order_by(FixedAssetDepreciationSchedule.sequence_no)
            )
        ).scalars().all()
        # 1 行 posted（legacy 计划重建）+ 剩余 planned（2026-01 起，2025-12 已提）
        posted = [r for r in rows if r.status == "posted"]
        planned = [r for r in rows if r.status == "planned"]
        assert len(posted) == 1 and posted[0].period_start == "2025-12"
        assert planned[0].period_start == "2026-01"
        assert sum(Decimal(str(r.amount)) for r in rows) == Decimal("120000.00")

        legacy_one = by_name["legacy 一次性设备"]
        assert legacy_one.depreciation_method == "one_time"
        one_rows = (
            await session.execute(
                select(FixedAssetDepreciationSchedule).where(
                    FixedAssetDepreciationSchedule.asset_id == legacy_one.id
                )
            )
        ).scalars().all()
        assert len(one_rows) == 1
        assert str(one_rows[0].amount) == "80000.00"

    # 幂等重跑：已打标，零迁移
    rerun = await backfill_fixed_assets("c-fa-bf")
    assert rerun.get("skipped") is True

    # 清理 legacy 表，避免污染其他用例
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("DELETE FROM legacy_fixed_assets WHERE company_id = 'c-fa-bf'")
        conn.execute("DELETE FROM legacy_depreciation_schedule WHERE company_id = 'c-fa-bf'")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_backfill_refuses_mismatched_ledger():
    """勾稽不平拒绝迁移并打 mismatch 标记。"""
    import sqlite3

    from db.session import database_url, get_engine, session_scope
    from services.fixed_asset_backfill_service import backfill_fixed_assets
    from services.database import _sqlite_target

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    db_file = _sqlite_target(database_url())
    conn = sqlite3.connect(db_file)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS legacy_fixed_assets ("
            "id TEXT PRIMARY KEY, user_id TEXT NOT NULL DEFAULT '', company_id TEXT NOT NULL, "
            "payload TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '')"
        )
        conn.execute("DELETE FROM legacy_fixed_assets WHERE company_id = 'c-fa-bf-mm'")
        conn.execute(
            "INSERT INTO legacy_fixed_assets (id, company_id, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (
                "fa-srv-mm-1", "c-fa-bf-mm",
                json.dumps({
                    "name": "无账卡片", "category": "电子设备", "purchaseDate": "2025-01-10",
                    "cost": 9999.0, "accumulatedDepreciation": 0.0, "usefulMonths": 36, "status": "in_use",
                }),
                "2025-01-01", "2025-01-01",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    async with session_scope() as session:
        await ensure_tenant(session, "c-fa-bf-mm", "owner")

    result = await backfill_fixed_assets("c-fa-bf-mm")
    assert result["ok"] is False
    assert result["status"] == "mismatch"

    async with session_scope() as session:
        count = (
            await session.execute(
                select(FixedAsset.id).where(FixedAsset.company_id == "c-fa-bf-mm")
            )
        ).scalars().all()
        assert count == []

    conn = sqlite3.connect(db_file)
    try:
        conn.execute("DELETE FROM legacy_fixed_assets WHERE company_id = 'c-fa-bf-mm'")
        conn.commit()
    finally:
        conn.close()
