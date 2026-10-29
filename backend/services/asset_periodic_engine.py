# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""固定资产折旧 / 无形资产摊销共用按期计提引擎。

两个引擎此前是约 500 行的逐行复制（仅模型、科目、id 前缀不同），修 bug 需要双写。
本模块把公共流程参数化：期间校验、到期计划列表、关账期拦截、按期过账（幂等 +
advisory lock + 研发拆分 + 末期自动完结）、计划查询、执行历史。差异全部收进
`PeriodicEngineSpec`，depreciation_engine / amortization_engine 各持一份 spec 并
对外保持原函数签名（routers/tests 零改动）。

八条硬规则（两引擎共同遵守，差异见各自模块 docstring）：
1. 计划在提交时预生成，引擎只负责按期过账；
2. 处置当月停提（处置单创建时已把当月 planned 行置 skipped）；
3. 直线法，尾差最后一期兜平；
4. 幂等：schedule 唯一键 + 确定性 entry_id + dedupFingerprint + advisory lock；
5. 期间锁：closed/closing 期间 409 拒绝，不静默补记；
6. 上限：accumulated_after ≤ 原值 − 残值（DB CHECK + 计划兜平共同保证）；
7. 计提凭证 confirm=True：owner_self_post 直接 posted，maker_checker 落
   pending_review——绝不留 draft；
8. 末期过账后资产自动置 fully_*。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError
from core.money import ZERO, money, money_text
from db.models import AccountingPeriod
from services.accounting_core_service import (
    _advisory_lock,
    assert_period_writable,
    create_entry,
    ensure_tenant,
    get_or_create_period,
    record_audit,
)

RD_EXPENSE_ACCOUNT = ("430100103", "研发支出-折旧与摊销")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def validate_period(period: str) -> str:
    raw = str(period or "").strip()
    if len(raw) != 7 or raw[4] != "-" or not raw[:4].isdigit() or not raw[5:7].isdigit():
        raise AccountingError("INVALID_PERIOD", "期间格式无效，应为 YYYY-MM", 422)
    if not 1 <= int(raw[5:7]) <= 12:
        raise AccountingError("INVALID_PERIOD", "期间月份无效", 422)
    return raw


@dataclass(frozen=True)
class PeriodicEngineSpec:
    """折旧/摊销引擎的全部差异点。

    asset_label / verb_label 只用于报错与审计文案（如「固定资产折旧涉及已关账期间」）；
    accumulated_attr / last_period_attr 是资产模型上按期累加与回写的字段名；
    done_asset_statuses 是 done_query 的额外资产状态过滤（摊销侧有、折旧侧无）。
    """

    asset_label: str
    verb_label: str
    schedule_model: Any
    asset_model: Any
    rd_expense_account: tuple[str, str]
    admin_expense_account: tuple[str, str]
    credit_account: tuple[str, str]
    entry_id_prefix: str
    title_prefix: str
    category: str
    source_type: str
    fingerprint: Callable[[Any, Any], str]
    accumulated_attr: str
    last_period_attr: str
    done_status: str
    lock_prefix: str
    audit_action: str
    audit_entity_type: str
    done_asset_statuses: tuple[str, ...] | None = None
    rd_expense_type: str = field(default="depreciation_amortization")


async def list_due_periods(
    session: AsyncSession,
    spec: PeriodicEngineSpec,
    company_id: str,
    through_period: str,
    *,
    asset_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """列出截至指定期间尚未计提的期间，供建档提示与补提预览复用。"""
    through_period = validate_period(through_period)
    schedule, asset = spec.schedule_model, spec.asset_model
    query = (
        select(
            schedule.period_start,
            sa_func.sum(schedule.amount),
            sa_func.count(),
        )
        .join(asset, asset.id == schedule.asset_id)
        .where(
            schedule.company_id == company_id,
            schedule.period_start <= through_period,
            schedule.status == "planned",
            asset.deleted_at.is_(None),
            asset.status.in_(("active", "idle")),
        )
        .group_by(schedule.period_start)
        .order_by(schedule.period_start.asc())
    )
    if asset_ids:
        query = query.where(schedule.asset_id.in_(asset_ids))
    rows = (await session.execute(query)).all()
    return [
        {
            "period": str(row[0]),
            "amount": money(row[1]),
            "count": int(row[2]),
        }
        for row in rows
    ]


async def _blocked_periods(
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


async def run_catch_up(
    session: AsyncSession,
    spec: PeriodicEngineSpec,
    through_period: str,
    *,
    company_id: str,
    user_id: str,
    request_id: str,
    asset_ids: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """按期间补提；已有 closing/closed 期间时整批拒绝，避免静默跨期入账。"""
    await ensure_tenant(session, company_id, user_id)
    through_period = validate_period(through_period)
    due = await list_due_periods(session, spec, company_id, through_period, asset_ids=asset_ids)
    blocked = await _blocked_periods(session, company_id, [item["period"] for item in due])
    if blocked:
        raise AccountingError(
            "PERIOD_CLOSED",
            f"{spec.asset_label}{spec.verb_label}涉及已关账期间：{'、'.join(blocked)}，请先反结账或调整期间",
            409,
            {"periods": blocked},
        )

    periods: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    total_amount = ZERO
    processed = skipped = posted = pending = 0
    for item in due:
        result = await run_period(
            session,
            spec,
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


def _entry_payload(spec: PeriodicEngineSpec, asset: Any, row: Any) -> dict[str, Any]:
    amount = money(row.amount)
    rd_amount = ZERO
    debit: list[dict[str, Any]] = []
    if asset.rd_project_id and asset.rd_use_rate and asset.rd_use_rate > ZERO:
        rd_amount = money(amount * asset.rd_use_rate)
        if rd_amount > ZERO:
            debit.append(
                {
                    "accountCode": spec.rd_expense_account[0],
                    "accountName": spec.rd_expense_account[1],
                    "amount": money_text(rd_amount),
                    "rdDetail": {
                        "projectId": asset.rd_project_id,
                        "expenseType": spec.rd_expense_type,
                        "originalAmount": amount,
                        "allocationMethod": "usage",
                        "allocationBasis": asset.rd_allocation_basis,
                        "notes": f"{row.period_start} {asset.name}研发使用{spec.verb_label}",
                    },
                }
            )
    admin_amount = money(amount - rd_amount)
    if admin_amount > ZERO:
        debit.append(
            {
                "accountCode": spec.admin_expense_account[0],
                "accountName": spec.admin_expense_account[1],
                "amount": money_text(admin_amount),
            }
        )
    return {
        "id": f"{spec.entry_id_prefix}-{asset.id}-{row.period_start}",
        "direction": "expense",
        "amount": money_text(amount),
        "title": f"{spec.title_prefix}-{asset.name} {row.period_start}",
        "category": spec.category,
        "occurDate": f"{row.period_start}-01",
        "sourceType": spec.source_type,
        "dedupFingerprint": spec.fingerprint(asset, row),
        "voucherAuthority": "system",
        "voucherLines": {
            "debit": debit,
            "credit": [
                {"accountCode": spec.credit_account[0], "accountName": spec.credit_account[1], "amount": money_text(amount)}
            ],
        },
    }


async def run_period(
    session: AsyncSession,
    spec: PeriodicEngineSpec,
    period: str,
    *,
    company_id: str,
    user_id: str,
    request_id: str,
    asset_ids: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    period = validate_period(period)
    schedule, asset_model = spec.schedule_model, spec.asset_model
    period_row = await get_or_create_period(session, company_id, f"{period}-01", lock=True)
    assert_period_writable(period_row)
    # 同公司同期间串行，保证幂等与累计数一致
    await _advisory_lock(session, f"{spec.lock_prefix}:{company_id}:{period}")

    query = (
        select(schedule, asset_model)
        .join(asset_model, asset_model.id == schedule.asset_id)
        .where(
            schedule.company_id == company_id,
            schedule.period_start == period,
            schedule.status == "planned",
            asset_model.deleted_at.is_(None),
            asset_model.status.in_(("active", "idle")),
        )
        .order_by(schedule.sequence_no.asc())
    )
    if asset_ids:
        query = query.where(schedule.asset_id.in_(asset_ids))
    rows = (await session.execute(query)).all()

    # 本期已计提（posted/reversed/skipped）的行数：重复执行时计入 skipped，保证幂等语义可观测
    done_conditions = [
        schedule.company_id == company_id,
        schedule.period_start == period,
        schedule.status.in_(("posted", "reversed", "skipped")),
        asset_model.deleted_at.is_(None),
    ]
    if spec.done_asset_statuses is not None:
        done_conditions.append(asset_model.status.in_(spec.done_asset_statuses))
    done_query = (
        select(sa_func.count())
        .select_from(schedule)
        .join(asset_model, asset_model.id == schedule.asset_id)
        .where(*done_conditions)
    )
    if asset_ids:
        done_query = done_query.where(schedule.asset_id.in_(asset_ids))
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
                    schedule.asset_id,
                    sa_func.max(schedule.sequence_no),
                )
                .where(
                    schedule.company_id == company_id,
                    schedule.status != "skipped",
                )
                .group_by(schedule.asset_id)
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
                _entry_payload(spec, asset, row),
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
        setattr(asset, spec.accumulated_attr, money(getattr(asset, spec.accumulated_attr) + amount))
        setattr(asset, spec.last_period_attr, row.period_start)
        asset.updated_at = _now()
        if int(max_seq.get(asset.id, 0)) == int(row.sequence_no):
            asset.status = spec.done_status
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
        action=spec.audit_action,
        entity_type=spec.audit_entity_type,
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
    session: AsyncSession,
    spec: PeriodicEngineSpec,
    asset_id: str,
    *,
    company_id: str,
    user_id: str,
) -> list[dict[str, Any]]:
    await ensure_tenant(session, company_id, user_id)
    schedule, asset_model = spec.schedule_model, spec.asset_model
    asset = (
        await session.execute(
            select(asset_model).where(
                asset_model.id == asset_id,
                asset_model.company_id == company_id,
            )
        )
    ).scalar_one_or_none()
    if asset is None:
        raise AccountingError("NOT_FOUND", f"{spec.asset_label}不存在", 404)
    rows = (
        await session.execute(
            select(schedule)
            .where(
                schedule.company_id == company_id,
                schedule.asset_id == asset_id,
            )
            .order_by(schedule.sequence_no.asc())
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
    session: AsyncSession,
    spec: PeriodicEngineSpec,
    period: str,
    *,
    company_id: str,
    user_id: str,
) -> dict[str, Any]:
    """某期间全部已过账计提行（执行历史）。"""
    await ensure_tenant(session, company_id, user_id)
    period = validate_period(period)
    schedule, asset_model = spec.schedule_model, spec.asset_model
    rows = (
        await session.execute(
            select(schedule, asset_model)
            .join(asset_model, asset_model.id == schedule.asset_id)
            .where(
                schedule.company_id == company_id,
                schedule.period_start == period,
                schedule.status.in_(("posted", "reversed", "skipped")),
            )
            .order_by(schedule.sequence_no.asc())
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
