# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""固定资产折旧引擎：把到期 planned 计划行过账并生成折旧凭证。

公共流程（期间校验/补提/过账/计划/执行历史）在 asset_periodic_engine，
本模块只提供折旧侧的差异配置与对外函数签名。

折旧侧差异点：
1. 次月起提（计划在提交时预生成，首期 = 可供使用次月；引擎只负责按期过账）；
2. 处置当月停提（处置单创建时已把当月 planned 行置 skipped，与处置快照的
   累计折旧口径一致；教科书「当月减少当月照提」在快照一致性上会让已过账
   处置凭证的 1602 借方与后续计提冲突，故不采用——差异已在 runbook 说明）；
3. 一次性折旧（one_time）：≤500 万设备器具仅 1 期全额计提；
8. 末期过账后资产自动置 fully_depreciated。

分录：借 5602007 管理费用-折旧费（非研发部分）/ 430100103 研发支出-折旧与摊销
（研发部分，带 rdDetail，沿用 legacy 口径）；贷 1602 累计折旧。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import FixedAsset, FixedAssetDepreciationSchedule
from services.asset_periodic_engine import (
    RD_EXPENSE_ACCOUNT,
    PeriodicEngineSpec,
    get_schedule as _get_schedule,
    get_runs as _get_runs,
    list_due_periods as _list_due_periods,
    run_catch_up as _run_catch_up,
    run_period as _run_period,
    validate_period,
)

ADMIN_EXPENSE_ACCOUNT = ("5602007", "管理费用-折旧费")
CREDIT_ACCOUNT = ("1602", "累计折旧")

SPEC = PeriodicEngineSpec(
    asset_label="固定资产",
    verb_label="折旧",
    schedule_model=FixedAssetDepreciationSchedule,
    asset_model=FixedAsset,
    rd_expense_account=RD_EXPENSE_ACCOUNT,
    admin_expense_account=ADMIN_EXPENSE_ACCOUNT,
    credit_account=CREDIT_ACCOUNT,
    entry_id_prefix="depreciation",
    title_prefix="计提折旧",
    category="折旧",
    source_type="fixed_asset_depreciation",
    fingerprint=lambda asset, row: f"depreciation-{asset.id}-{row.period_start}",
    accumulated_attr="accumulated_depreciation",
    last_period_attr="last_depreciated_period",
    done_status="fully_depreciated",
    lock_prefix="depreciation",
    audit_action="fixed_asset.depreciation.run",
    audit_entity_type="fixed_asset_depreciation_schedule",
    done_asset_statuses=None,
)

__all__ = [
    "ADMIN_EXPENSE_ACCOUNT",
    "CREDIT_ACCOUNT",
    "RD_EXPENSE_ACCOUNT",
    "SPEC",
    "validate_period",
    "depreciation_summary_by_period",
    "get_runs",
    "get_schedule",
    "list_due_depreciation_periods",
    "run_depreciation",
    "run_depreciation_catch_up",
]


async def list_due_depreciation_periods(
    session: AsyncSession,
    company_id: str,
    through_period: str,
    *,
    asset_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """列出截至指定期间尚未计提的折旧期间，供建档提示与补提预览复用。"""
    return await _list_due_periods(session, SPEC, company_id, through_period, asset_ids=asset_ids)


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
    return await _run_catch_up(
        session, SPEC, through_period,
        company_id=company_id, user_id=user_id, request_id=request_id,
        asset_ids=asset_ids, dry_run=dry_run,
    )


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
    return await _run_period(
        session, SPEC, period,
        company_id=company_id, user_id=user_id, request_id=request_id,
        asset_ids=asset_ids, dry_run=dry_run,
    )


async def get_schedule(
    session: AsyncSession, asset_id: str, *, company_id: str, user_id: str
) -> list[dict[str, Any]]:
    return await _get_schedule(session, SPEC, asset_id, company_id=company_id, user_id=user_id)


async def get_runs(
    session: AsyncSession, period: str, *, company_id: str, user_id: str
) -> dict[str, Any]:
    """某期间全部已过账折旧行（折旧执行历史）。"""
    return await _get_runs(session, SPEC, period, company_id=company_id, user_id=user_id)


async def depreciation_summary_by_period(session: AsyncSession, company_id: str, period: str) -> dict[str, Any]:
    """供月结检查/核对使用：某期间 posted 折旧合计与挂账研发拆分。"""
    from decimal import Decimal

    from sqlalchemy import func as sa_func, select

    period = validate_period(period)
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
    from core.money import money

    return {"totalAmount": money(Decimal(str(value[0]))), "count": int(value[1])}
