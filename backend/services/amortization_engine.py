# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""无形资产摊销引擎：把到期 planned 计划行过账并生成摊销凭证。

设计方案：《无形资产模块设计方案.md》第 5 章。八条硬规则：
1. 可供使用当月起摊（计划在提交时预生成，引擎只负责按期过账）；
2. 处置/核销当月不再摊销（处置单创建时已把当月 planned 行置 skipped，资产状态
   也不再是 active/idle，双保险）；
3. 直线法：月额 = money((原值 − 残值) / 月数)，尾差在最后一期兜平；
4. 幂等：schedule 唯一键 + 确定性 entry_id + dedupFingerprint + advisory lock；
5. 期间锁：closed/closing 期间 409 拒绝，不静默补记；
6. 上限：accumulated_after ≤ 原值 − 残值（DB CHECK + 计划兜平共同保证）；
7. 摊销凭证 confirm=True：owner_self_post 直接 posted，maker_checker 落
   pending_review——绝不留 draft；
8. 末期过账后资产自动置 fully_amortized。

分录：借 5602008 管理费用-摊销费（非研发部分）/ 430100103 研发支出-折旧与摊销
（研发部分，带 rdDetail）；贷 1702 累计摊销。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError
from core.money import ZERO, money, money_text
from db.models import AccountingPeriod, IntangibleAmortizationSchedule, IntangibleAsset
from services.accounting_core_service import (
    _advisory_lock,
    assert_period_writable,
    create_entry,
    ensure_tenant,
    get_or_create_period,
    record_audit,
)

RD_EXPENSE_ACCOUNT = ("430100103", "研发支出-折旧与摊销")
ADMIN_EXPENSE_ACCOUNT = ("5602008", "管理费用-摊销费")
CREDIT_ACCOUNT = ("1702", "累计摊销")


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


async def list_due_amortization_periods(
    session: AsyncSession,
    company_id: str,
    through_period: str,
    *,
    asset_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """列出截至指定期间尚未摊销的无形资产期间。"""
    through_period = _validate_period(through_period)
    query = (
        select(
            IntangibleAmortizationSchedule.period_start,
            sa_func.sum(IntangibleAmortizationSchedule.amount),
            sa_func.count(),
        )
        .join(IntangibleAsset, IntangibleAsset.id == IntangibleAmortizationSchedule.asset_id)
        .where(
            IntangibleAmortizationSchedule.company_id == company_id,
            IntangibleAmortizationSchedule.period_start <= through_period,
            IntangibleAmortizationSchedule.status == "planned",
            IntangibleAsset.deleted_at.is_(None),
            IntangibleAsset.status.in_(("active", "idle")),
        )
        .group_by(IntangibleAmortizationSchedule.period_start)
        .order_by(IntangibleAmortizationSchedule.period_start.asc())
    )
    if asset_ids:
        query = query.where(IntangibleAmortizationSchedule.asset_id.in_(asset_ids))
    rows = (await session.execute(query)).all()
    return [
        {
            "period": str(row[0]),
            "amount": money(row[1]),
            "count": int(row[2]),
        }
        for row in rows
    ]


async def _blocked_amortization_periods(
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


async def run_amortization_catch_up(
    session: AsyncSession,
    through_period: str,
    *,
    company_id: str,
    user_id: str,
    request_id: str,
    asset_ids: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """按期间补提无形资产摊销；已关账期间整批拒绝。"""
    await ensure_tenant(session, company_id, user_id)
    through_period = _validate_period(through_period)
    due = await list_due_amortization_periods(
        session, company_id, through_period, asset_ids=asset_ids
    )
    blocked = await _blocked_amortization_periods(
        session, company_id, [item["period"] for item in due]
    )
    if blocked:
        raise AccountingError(
            "PERIOD_CLOSED",
            f"无形资产摊销涉及已关账期间：{'、'.join(blocked)}，请先反结账或调整期间",
            409,
            {"periods": blocked},
        )

    periods: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    total_amount = ZERO
    processed = skipped = posted = pending = 0
    for item in due:
        result = await run_amortization(
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


def _amortization_entry_payload(asset: IntangibleAsset, row: IntangibleAmortizationSchedule) -> dict[str, Any]:
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
                        "notes": f"{row.period_start} {asset.name}研发使用摊销",
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
        "id": f"amortization-{asset.id}-{row.period_start}",
        "direction": "expense",
        "amount": money_text(amount),
        "title": f"摊销-{asset.name} {row.period_start}",
        "category": "摊销",
        "occurDate": f"{row.period_start}-01",
        "sourceType": "intangible_amortization",
        "dedupFingerprint": f"intangible_amortization:{asset.company_id}:{asset.id}:{row.period_start}",
        "voucherAuthority": "system",
        "voucherLines": {
            "debit": debit,
            "credit": [
                {"accountCode": CREDIT_ACCOUNT[0], "accountName": CREDIT_ACCOUNT[1], "amount": money_text(amount)}
            ],
        },
    }


async def run_amortization(
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
    await _advisory_lock(session, f"amortization:{company_id}:{period}")

    query = (
        select(IntangibleAmortizationSchedule, IntangibleAsset)
        .join(IntangibleAsset, IntangibleAsset.id == IntangibleAmortizationSchedule.asset_id)
        .where(
            IntangibleAmortizationSchedule.company_id == company_id,
            IntangibleAmortizationSchedule.period_start == period,
            IntangibleAmortizationSchedule.status == "planned",
            IntangibleAsset.deleted_at.is_(None),
            IntangibleAsset.status.in_(("active", "idle")),
        )
        .order_by(IntangibleAmortizationSchedule.sequence_no.asc())
    )
    if asset_ids:
        query = query.where(IntangibleAmortizationSchedule.asset_id.in_(asset_ids))
    rows = (await session.execute(query)).all()

    # 本期已计提（posted/reversed/skipped）的行数：重复执行时计入 skipped，保证幂等语义可观测
    done_query = (
        select(sa_func.count())
        .select_from(IntangibleAmortizationSchedule)
        .join(IntangibleAsset, IntangibleAsset.id == IntangibleAmortizationSchedule.asset_id)
        .where(
            IntangibleAmortizationSchedule.company_id == company_id,
            IntangibleAmortizationSchedule.period_start == period,
            IntangibleAmortizationSchedule.status.in_(("posted", "reversed", "skipped")),
            IntangibleAsset.deleted_at.is_(None),
            IntangibleAsset.status.in_(("active", "idle", "fully_amortized", "disposing", "disposed", "scrapped")),
        )
    )
    if asset_ids:
        done_query = done_query.where(IntangibleAmortizationSchedule.asset_id.in_(asset_ids))
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
                    IntangibleAmortizationSchedule.asset_id,
                    sa_func.max(IntangibleAmortizationSchedule.sequence_no),
                )
                .where(
                    IntangibleAmortizationSchedule.company_id == company_id,
                    IntangibleAmortizationSchedule.status != "skipped",
                )
                .group_by(IntangibleAmortizationSchedule.asset_id)
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
                _amortization_entry_payload(asset, row),
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
        asset.accumulated_amortization = money(asset.accumulated_amortization + amount)
        asset.last_amortized_period = row.period_start
        asset.updated_at = _now()
        if int(max_seq.get(asset.id, 0)) == int(row.sequence_no):
            asset.status = "fully_amortized"
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
        action="intangible.amortization.run",
        entity_type="intangible_amortization_schedule",
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
            select(IntangibleAsset).where(
                IntangibleAsset.id == asset_id,
                IntangibleAsset.company_id == company_id,
            )
        )
    ).scalar_one_or_none()
    if asset is None:
        raise AccountingError("NOT_FOUND", "无形资产不存在", 404)
    rows = (
        await session.execute(
            select(IntangibleAmortizationSchedule)
            .where(
                IntangibleAmortizationSchedule.company_id == company_id,
                IntangibleAmortizationSchedule.asset_id == asset_id,
            )
            .order_by(IntangibleAmortizationSchedule.sequence_no.asc())
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
    """某期间全部已过账摊销行（摊销执行历史）。"""
    await ensure_tenant(session, company_id, user_id)
    period = _validate_period(period)
    rows = (
        await session.execute(
            select(IntangibleAmortizationSchedule, IntangibleAsset)
            .join(IntangibleAsset, IntangibleAsset.id == IntangibleAmortizationSchedule.asset_id)
            .where(
                IntangibleAmortizationSchedule.company_id == company_id,
                IntangibleAmortizationSchedule.period_start == period,
                IntangibleAmortizationSchedule.status.in_(("posted", "reversed", "skipped")),
            )
            .order_by(IntangibleAmortizationSchedule.sequence_no.asc())
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


async def amortization_summary(
    session: AsyncSession, period_from: str, period_to: str, *, company_id: str, user_id: str
) -> dict[str, Any]:
    """摊销费用汇总（按类别），含与总账科目的勾稽行。"""
    await ensure_tenant(session, company_id, user_id)
    period_from = _validate_period(period_from)
    period_to = _validate_period(period_to)
    rows = (
        await session.execute(
            select(IntangibleAmortizationSchedule, IntangibleAsset)
            .join(IntangibleAsset, IntangibleAsset.id == IntangibleAmortizationSchedule.asset_id)
            .where(
                IntangibleAmortizationSchedule.company_id == company_id,
                IntangibleAmortizationSchedule.status == "posted",
                IntangibleAmortizationSchedule.period_start >= period_from,
                IntangibleAmortizationSchedule.period_start <= period_to,
            )
            .order_by(IntangibleAsset.category.asc())
        )
    ).all()

    grouped: dict[str, dict[str, Any]] = {}
    for row, asset in rows:
        bucket = grouped.setdefault(
            asset.category,
            {"originalValue": ZERO, "amortizationAmount": ZERO, "rdAmount": ZERO, "count": 0},
        )
        bucket["originalValue"] = money(bucket["originalValue"] + money(asset.original_value))
        bucket["amortizationAmount"] = money(bucket["amortizationAmount"] + money(row.amount))
        bucket["count"] += 1
        if asset.rd_project_id and asset.rd_use_rate and asset.rd_use_rate > ZERO:
            bucket["rdAmount"] = money(bucket["rdAmount"] + money(money(row.amount) * asset.rd_use_rate))

    from core.intangible_categories import get_category

    items = []
    total_amount = ZERO
    total_rd = ZERO
    for code, bucket in grouped.items():
        rd = bucket["rdAmount"]
        items.append(
            {
                "category": code,
                "categoryName": get_category(code).name,
                "originalValue": bucket["originalValue"],
                "amortizationAmount": bucket["amortizationAmount"],
                "adminAmount": money(bucket["amortizationAmount"] - rd),
                "rdAmount": rd,
            }
        )
        total_amount = money(total_amount + bucket["amortizationAmount"])
        total_rd = money(total_rd + rd)

    from db.models import JournalEntry, JournalLine

    async def _sum_account(code: str) -> Decimal:
        next_month = f"{_next_period(period_to)}-01"
        value = (
            await session.execute(
                select(sa_func.coalesce(sa_func.sum(JournalLine.amount), 0))
                .select_from(JournalLine)
                .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
                .where(
                    JournalLine.company_id == company_id,
                    JournalLine.account_code == code,
                    JournalEntry.status == "posted",
                    JournalEntry.period_start >= datetime.strptime(f"{period_from}-01", "%Y-%m-%d").date(),
                    JournalEntry.period_start < datetime.strptime(next_month, "%Y-%m-%d").date(),
                )
            )
        ).scalar() or 0
        return money(Decimal(str(value)))

    ledger_admin = await _sum_account(ADMIN_EXPENSE_ACCOUNT[0])
    ledger_rd = await _sum_account(RD_EXPENSE_ACCOUNT[0])
    return {
        "periodFrom": period_from,
        "periodTo": period_to,
        "items": items,
        "reconciliation": {
            "scheduleTotal": total_amount,
            "ledgerAdminAmount": ledger_admin,
            "ledgerRdAmount": ledger_rd,
            "diff": money(total_amount - ledger_admin - ledger_rd),
        },
    }


async def a105080_preview(
    session: AsyncSession, year: int, *, company_id: str, user_id: str
) -> dict[str, Any]:
    """A105080（资产折旧、摊销及纳税调整明细表）数据预览（P3 预留）。

    按资产返回：原值 / 本年会计摊销额 / 累计摊销 / 账面价值 / 本年税收摊销 /
    纳税调整额。口径说明（preview 假设，正式申报以税务师调整为准）：
    - 税收摊销 = (原值 − 残值) / 税法月数 × 本年已摊月数，税法月数取
      `tax_life_months`，缺省 120 个月；
    - 假设税会起摊时点一致（preview 简化，不做税会起点差异调整）；
    - 纳税调整额 = 本年会计摊销 − 本年税收摊销（正数调增、负数调减）。
    """
    await ensure_tenant(session, company_id, user_id)
    year = int(year)
    period_from, period_to = f"{year:04d}-01", f"{year:04d}-12"

    assets = (
        await session.execute(
            select(IntangibleAsset).where(
                IntangibleAsset.company_id == company_id,
                IntangibleAsset.deleted_at.is_(None),
                IntangibleAsset.status.in_(("active", "idle", "fully_amortized", "disposed", "scrapped")),
            )
        )
    ).scalars().all()

    yearly = dict(
        (
            await session.execute(
                select(
                    IntangibleAmortizationSchedule.asset_id,
                    sa_func.coalesce(sa_func.sum(IntangibleAmortizationSchedule.amount), 0),
                    sa_func.count(),
                )
                .where(
                    IntangibleAmortizationSchedule.company_id == company_id,
                    IntangibleAmortizationSchedule.status == "posted",
                    IntangibleAmortizationSchedule.period_start >= period_from,
                    IntangibleAmortizationSchedule.period_start <= period_to,
                )
                .group_by(IntangibleAmortizationSchedule.asset_id)
            )
        ).all()
    )

    items = []
    for asset in assets:
        book_yearly = money(Decimal(str(yearly.get(asset.id, (0, 0))[0])))
        book_months = int(yearly.get(asset.id, (0, 0))[1]) if asset.id in yearly else 0
        tax_life = int(asset.tax_life_months or 120)
        tax_monthly = money((money(asset.original_value) - money(asset.residual_value or ZERO)) / Decimal(tax_life))
        tax_yearly = money(tax_monthly * Decimal(book_months))
        items.append(
            {
                "assetId": asset.id,
                "assetCode": asset.code,
                "assetName": asset.name,
                "category": asset.category,
                "originalValue": money(asset.original_value),
                "amortizationThisYear": book_yearly,
                "amortizedMonthsThisYear": book_months,
                "accumulatedAmortization": money(asset.accumulated_amortization or ZERO),
                "netBookValue": money(money(asset.original_value) - money(asset.accumulated_amortization or ZERO)),
                "taxLifeMonths": tax_life,
                "taxAmortizationThisYear": tax_yearly,
                "adjustmentAmount": money(book_yearly - tax_yearly),
            }
        )

    total_accounting = money(sum((i["amortizationThisYear"] for i in items), ZERO))
    total_tax = money(sum((i["taxAmortizationThisYear"] for i in items), ZERO))
    return {
        "year": year,
        "items": items,
        "totals": {
            "amortizationThisYear": total_accounting,
            "taxAmortizationThisYear": total_tax,
            "adjustmentAmount": money(total_accounting - total_tax),
        },
        "assumptions": [
            "税收摊销按税法月数直线计算（tax_life_months，缺省 120 个月）",
            "假设税会起摊时点一致；正式申报以税务师调整为准",
        ],
    }
