# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""固定资产折旧引擎：把到期 planned 计划行过账并生成折旧凭证。

照无形资产摊销引擎（amortization_engine）八条硬规则，差异点：
1. 次月起提（计划在提交时预生成，首期 = 可供使用次月；引擎只负责按期过账）；
2. 处置当月停提（处置单创建时已把当月 planned 行置 skipped，与处置快照的
   累计折旧口径一致；教科书「当月减少当月照提」在快照一致性上会让已过账
   处置凭证的 1602 借方与后续计提冲突，故不采用——差异已在 runbook 说明）；
3. 直线法：月额 = money((原值 − 残值) / 月数)，尾差在最后一期兜平；
   一次性折旧（one_time）：≤500 万设备器具仅 1 期全额计提；
4. 幂等：schedule 唯一键 + 确定性 entry_id + dedupFingerprint（沿用 legacy
   格式 depreciation-{asset_id}-{period}）+ advisory lock；
5. 期间锁：closed/closing 期间 409 拒绝，不静默补记；
6. 上限：accumulated_after ≤ 原值 − 残值（DB CHECK + 计划兜平共同保证）；
7. 折旧凭证 confirm=True：owner_self_post 直接 posted，maker_checker 落
   pending_review——绝不留 draft；
8. 末期过账后资产自动置 fully_depreciated。

分录：借 5602007 管理费用-折旧费（非研发部分）/ 430100103 研发支出-折旧与摊销
（研发部分，带 rdDetail，沿用 legacy 口径）；贷 1602 累计折旧。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError
from core.money import ZERO, money, money_text
from db.models import AccountingPeriod, FixedAsset, FixedAssetDepreciationSchedule
from services.accounting_core_service import (
    _advisory_lock,
    assert_period_writable,
    create_entry,
    ensure_tenant,
    get_or_create_period,
    record_audit,
)

RD_EXPENSE_ACCOUNT = ("430100103", "研发支出-折旧与摊销")
ADMIN_EXPENSE_ACCOUNT = ("5602007", "管理费用-折旧费")
CREDIT_ACCOUNT = ("1602", "累计折旧")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_period(period: str) -> str:
    raw = str(period or "").strip()
    if len(raw) != 7 or raw[4] != "-" or not raw[:4].isdigit() or not raw[5:7].isdigit():
        raise AccountingError("INVALID_PERIOD", "期间格式无效，应为 YYYY-MM", 422)
    if not 1 <= int(raw[5:7]) <= 12:
        raise AccountingError("INVALID_PERIOD", "期间月份无效", 422)
    return raw


def _next_period(period: str) -> str:
    year, month = int(period[:4]), int(period[5:7])
    return f"{year + (month // 12):04d}-{month % 12 + 1:02d}"


async def list_due_depreciation_periods(
    session: AsyncSession,
    company_id: str,
    through_period: str,
    *,
    asset_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """列出截至指定期间尚未计提的折旧期间，供建档提示与补提预览复用。"""
    through_period = _validate_period(through_period)
    query = (
        select(
            FixedAssetDepreciationSchedule.period_start,
            sa_func.sum(FixedAssetDepreciationSchedule.amount),
            sa_func.count(),
        )
        .join(FixedAsset, FixedAsset.id == FixedAssetDepreciationSchedule.asset_id)
        .where(
            FixedAssetDepreciationSchedule.company_id == company_id,
            FixedAssetDepreciationSchedule.period_start <= through_period,
            FixedAssetDepreciationSchedule.status == "planned",
            FixedAsset.deleted_at.is_(None),
            FixedAsset.status.in_(("active", "idle")),
        )
        .group_by(FixedAssetDepreciationSchedule.period_start)
        .order_by(FixedAssetDepreciationSchedule.period_start.asc())
    )
    if asset_ids:
        query = query.where(FixedAssetDepreciationSchedule.asset_id.in_(asset_ids))
    rows = (await session.execute(query)).all()
    return [
        {
            "period": str(row[0]),
            "amount": money(row[1]),
            "count": int(row[2]),
        }
        for row in rows
    ]


async def _blocked_depreciation_periods(
    session: AsyncSession, company_id: str, periods: list[str]
) -> list[str]:
    if not periods:
        return []
    starts = [date(int(period[:4]), int(period[5:7]), 1) for period in periods]
    rows = (
        await session.execute(
            select(AccountingPeriod.period_start, AccountingPeriod.status).where(
                AccountingPeriod.company_id == company_id,
                AccountingPeriod.period_start.in_(starts),
                AccountingPeriod.status.in_(("closing", "closed")),
            )
        )
    ).all()
    return [row[0].strftime("%Y-%m") for row in rows]


async def run_depreciation_catch_up(
    session: AsyncSession,
    through_period: str,
    *,
    company_id: str,
    user_id: str,
    request_id: str,
    asset_ids: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """按期间补提折旧；已有 closing/closed 期间时整批拒绝，避免静默跨期入账。"""
    await ensure_tenant(session, company_id, user_id)
    through_period = _validate_period(through_period)
    due = await list_due_depreciation_periods(
        session, company_id, through_period, asset_ids=asset_ids
    )
    blocked = await _blocked_depreciation_periods(
        session, company_id, [item["period"] for item in due]
    )
    if blocked:
        raise AccountingError(
            "PERIOD_CLOSED",
            f"固定资产折旧涉及已关账期间：{'、'.join(blocked)}，请先反结账或调整期间",
            409,
            {"periods": blocked},
        )

    periods: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    total_amount = ZERO
    processed = skipped = posted = pending = 0
    for item in due:
        result = await run_depreciation(
            session,
            item["period"],
            company_id=company_id,
            user_id=user_id,
            request_id=f"{request_id}:{item['period']}",
            asset_ids=asset_ids,
            dry_run=dry_run,
        )
        periods.append(
            {
                "period": item["period"],
                "totalAmount": money(result["totalAmount"]),
                "processedCount": int(result["processedCount"]),
                "skippedCount": int(result["skippedCount"]),
                "postedCount": int(result["postedCount"]),
                "pendingReviewCount": int(result["pendingReviewCount"]),
            }
        )
        items.extend(result["items"])
        total_amount = money(total_amount + money(result["totalAmount"]))
        processed += int(result["processedCount"])
        skipped += int(result["skippedCount"])
        posted += int(result["postedCount"])
        pending += int(result["pendingReviewCount"])

    return {
        "throughPeriod": through_period,
        "dryRun": dry_run,
        "periods": periods,
        "totalAmount": total_amount,
        "processedCount": processed,
        "skippedCount": skipped,
        "postedCount": posted,
        "pendingReviewCount": pending,
        "items": items,
    }


def _depreciation_entry_payload(asset: FixedAsset, row: FixedAssetDepreciationSchedule) -> dict[str, Any]:
    amount = money(row.amount)
    rd_amount = ZERO
    debit: list[dict[str, Any]] = []
    if asset.rd_project_id and asset.rd_use_rate and asset.rd_use_rate > ZERO:
        rd_amount = money(amount * asset.rd_use_rate)
        if rd_amount > ZERO:
            debit.append(
                {
                    "accountCode": RD_EXPENSE_ACCOUNT[0],
                    "accountName": RD_EXPENSE_ACCOUNT[1],
                    "amount": money_text(rd_amount),
                    "rdDetail": {
                        "projectId": asset.rd_project_id,
                        "expenseType": "depreciation_amortization",
                        "originalAmount": amount,
                        "allocationMethod": "usage",
                        "allocationBasis": asset.rd_allocation_basis,
                        "notes": f"{row.period_start} {asset.name}研发使用折旧",
                    },
                }
            )
    admin_amount = money(amount - rd_amount)
    if admin_amount > ZERO:
        debit.append(
            {
                "accountCode": ADMIN_EXPENSE_ACCOUNT[0],
                "accountName": ADMIN_EXPENSE_ACCOUNT[1],
                "amount": money_text(admin_amount),
            }
        )
    return {
        "id": f"depreciation-{asset.id}-{row.period_start}",
        "direction": "expense",
        "amount": money_text(amount),
        "title": f"计提折旧-{asset.name} {row.period_start}",
        "category": "折旧",
        "occurDate": f"{row.period_start}-01",
        "sourceType": "fixed_asset_depreciation",
        "dedupFingerprint": f"depreciation-{asset.id}-{row.period_start}",
        "voucherAuthority": "system",
        "voucherLines": {
            "debit": debit,
            "credit": [
                {"accountCode": CREDIT_ACCOUNT[0], "accountName": CREDIT_ACCOUNT[1], "amount": money_text(amount)}
            ],
        },
    }


async def run_depreciation(
    session: AsyncSession,
    period: str,
    *,
    company_id: str,
    user_id: str,
    request_id: str,
    asset_ids: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    period = _validate_period(period)
    period_row = await get_or_create_period(session, company_id, f"{period}-01", lock=True)
    assert_period_writable(period_row)
    # 同公司同期间串行，保证幂等与累计数一致
    await _advisory_lock(session, f"depreciation:{company_id}:{period}")

    query = (
        select(FixedAssetDepreciationSchedule, FixedAsset)
        .join(FixedAsset, FixedAsset.id == FixedAssetDepreciationSchedule.asset_id)
        .where(
            FixedAssetDepreciationSchedule.company_id == company_id,
            FixedAssetDepreciationSchedule.period_start == period,
            FixedAssetDepreciationSchedule.status == "planned",
            FixedAsset.deleted_at.is_(None),
            FixedAsset.status.in_(("active", "idle")),
        )
        .order_by(FixedAssetDepreciationSchedule.sequence_no.asc())
    )
    if asset_ids:
        query = query.where(FixedAssetDepreciationSchedule.asset_id.in_(asset_ids))
    rows = (await session.execute(query)).all()

    # 本期已计提（posted/reversed/skipped）的行数：重复执行时计入 skipped，保证幂等语义可观测
    done_query = (
        select(sa_func.count())
        .select_from(FixedAssetDepreciationSchedule)
        .join(FixedAsset, FixedAsset.id == FixedAssetDepreciationSchedule.asset_id)
        .where(
            FixedAssetDepreciationSchedule.company_id == company_id,
            FixedAssetDepreciationSchedule.period_start == period,
            FixedAssetDepreciationSchedule.status.in_(("posted", "reversed", "skipped")),
            FixedAsset.deleted_at.is_(None),
        )
    )
    if asset_ids:
        done_query = done_query.where(FixedAssetDepreciationSchedule.asset_id.in_(asset_ids))
    already_done = int((await session.execute(done_query)).scalar_one())

    items: list[dict[str, Any]] = []
    total_amount = ZERO
    processed = skipped = posted_count = pending_review_count = 0

    if dry_run:
        for row, asset in rows:
            items.append(
                {
                    "assetId": asset.id,
                    "assetName": asset.name,
                    "periodStart": row.period_start,
                    "sequenceNo": row.sequence_no,
                    "amount": money(row.amount),
                    "status": "planned",
                    "journalEntryId": None,
                }
            )
            total_amount = money(total_amount + money(row.amount))
        return {
            "period": period,
            "dryRun": True,
            "totalAmount": total_amount,
            "processedCount": len(rows),
            "skippedCount": 0,
            "postedCount": 0,
            "pendingReviewCount": 0,
            "items": items,
        }

    max_seq = dict(
        (
            await session.execute(
                select(
                    FixedAssetDepreciationSchedule.asset_id,
                    sa_func.max(FixedAssetDepreciationSchedule.sequence_no),
                )
                .where(
                    FixedAssetDepreciationSchedule.company_id == company_id,
                    FixedAssetDepreciationSchedule.status != "skipped",
                )
                .group_by(FixedAssetDepreciationSchedule.asset_id)
            )
        ).all()
    )

    for row, asset in rows:
        amount = money(row.amount)
        if amount <= ZERO:
            skipped += 1
            continue
        try:
            entry = await create_entry(
                session,
                _depreciation_entry_payload(asset, row),
                company_id=company_id,
                user_id=user_id,
                request_id=request_id,
                confirm=True,
            )
        except AccountingError as exc:
            # 并发兜底：同 id 凭证已存在说明本期已计提，幂等跳过
            if exc.code == "DUPLICATE_SOURCE":
                skipped += 1
                items.append(
                    {
                        "assetId": asset.id,
                        "assetName": asset.name,
                        "periodStart": row.period_start,
                        "sequenceNo": row.sequence_no,
                        "amount": amount,
                        "status": "skipped",
                        "journalEntryId": None,
                    }
                )
                continue
            raise
        row.status = "posted"
        row.journal_entry_id = entry.id
        row.posted_at = _now()
        asset.accumulated_depreciation = money(asset.accumulated_depreciation + amount)
        asset.last_depreciated_period = row.period_start
        asset.updated_at = _now()
        if int(max_seq.get(asset.id, 0)) == int(row.sequence_no):
            asset.status = "fully_depreciated"
        item_status = "posted" if entry.status == "posted" else "pending_review"
        if item_status == "posted":
            posted_count += 1
        else:
            pending_review_count += 1
        processed += 1
        total_amount = money(total_amount + amount)
        items.append(
            {
                "assetId": asset.id,
                "assetName": asset.name,
                "periodStart": row.period_start,
                "sequenceNo": row.sequence_no,
                "amount": amount,
                "status": item_status,
                "journalEntryId": entry.id,
            }
        )

    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="fixed_asset.depreciation.run",
        entity_type="fixed_asset_depreciation_schedule",
        entity_id=None,
        request_id=request_id,
        after={"period": period, "processed": processed, "totalAmount": money_text(total_amount)},
    )
    return {
        "period": period,
        "dryRun": False,
        "totalAmount": total_amount,
        "processedCount": processed,
        "skippedCount": already_done + skipped,
        "postedCount": posted_count,
        "pendingReviewCount": pending_review_count,
        "items": items,
    }


async def get_schedule(
    session: AsyncSession, asset_id: str, *, company_id: str, user_id: str
) -> list[dict[str, Any]]:
    await ensure_tenant(session, company_id, user_id)
    asset = (
        await session.execute(
            select(FixedAsset).where(
                FixedAsset.id == asset_id,
                FixedAsset.company_id == company_id,
            )
        )
    ).scalar_one_or_none()
    if asset is None:
        raise AccountingError("NOT_FOUND", "固定资产不存在", 404)
    rows = (
        await session.execute(
            select(FixedAssetDepreciationSchedule)
            .where(
                FixedAssetDepreciationSchedule.company_id == company_id,
                FixedAssetDepreciationSchedule.asset_id == asset_id,
            )
            .order_by(FixedAssetDepreciationSchedule.sequence_no.asc())
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "assetId": r.asset_id,
            "assetName": asset.name,
            "periodStart": r.period_start,
            "sequenceNo": r.sequence_no,
            "amount": money(r.amount),
            "accumulatedBefore": money(r.accumulated_before),
            "accumulatedAfter": money(r.accumulated_after),
            "status": r.status,
            "journalEntryId": r.journal_entry_id,
            "planVersion": r.plan_version,
        }
        for r in rows
    ]


async def get_runs(
    session: AsyncSession, period: str, *, company_id: str, user_id: str
) -> dict[str, Any]:
    """某期间全部已过账折旧行（折旧执行历史）。"""
    await ensure_tenant(session, company_id, user_id)
    period = _validate_period(period)
    rows = (
        await session.execute(
            select(FixedAssetDepreciationSchedule, FixedAsset)
            .join(FixedAsset, FixedAsset.id == FixedAssetDepreciationSchedule.asset_id)
            .where(
                FixedAssetDepreciationSchedule.company_id == company_id,
                FixedAssetDepreciationSchedule.period_start == period,
                FixedAssetDepreciationSchedule.status.in_(("posted", "reversed", "skipped")),
            )
            .order_by(FixedAssetDepreciationSchedule.sequence_no.asc())
        )
    ).all()
    items = []
    total = ZERO
    for row, asset in rows:
        items.append(
            {
                "assetId": asset.id,
                "assetCode": asset.code,
                "assetName": asset.name,
                "periodStart": row.period_start,
                "sequenceNo": row.sequence_no,
                "amount": money(row.amount),
                "status": row.status,
                "journalEntryId": row.journal_entry_id,
            }
        )
        if row.status == "posted":
            total = money(total + money(row.amount))
    return {"period": period, "totalAmount": total, "items": items}


async def depreciation_summary_by_period(session: AsyncSession, company_id: str, period: str) -> dict[str, Any]:
    """供月结检查/核对使用：某期间 posted 折旧合计与挂账研发拆分。"""
    period = _validate_period(period)
    value = (
        await session.execute(
            select(
                sa_func.coalesce(sa_func.sum(FixedAssetDepreciationSchedule.amount), 0),
                sa_func.count(),
            ).where(
                FixedAssetDepreciationSchedule.company_id == company_id,
                FixedAssetDepreciationSchedule.period_start == period,
                FixedAssetDepreciationSchedule.status == "posted",
            )
        )
    ).one()
    return {"totalAmount": money(Decimal(str(value[0]))), "count": int(value[1])}
