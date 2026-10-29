# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""无形资产摊销引擎：把到期 planned 计划行过账并生成摊销凭证。

设计方案：《无形资产模块设计方案.md》第 5 章。公共流程（期间校验/补提/过账/
计划/执行历史）在 asset_periodic_engine，本模块提供摊销侧差异配置、对外签名，
以及摊销独有的汇总/勾稽（amortization_summary）与 A105080 预览。

摊销侧差异点：
1. 可供使用当月起摊（计划在提交时预生成，引擎只负责按期过账）；
2. 处置/核销当月不再摊销（处置单创建时已把当月 planned 行置 skipped，资产状态
   也不再是 active/idle，双保险）；
8. 末期过账后资产自动置 fully_amortized。

分录：借 5602008 管理费用-摊销费（非研发部分）/ 430100103 研发支出-折旧与摊销
（研发部分，带 rdDetail）；贷 1702 累计摊销。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError
from core.money import ZERO, money
from db.models import IntangibleAmortizationSchedule, IntangibleAsset
from services.accounting_core_service import ensure_tenant
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

ADMIN_EXPENSE_ACCOUNT = ("5602008", "管理费用-摊销费")
CREDIT_ACCOUNT = ("1702", "累计摊销")

SPEC = PeriodicEngineSpec(
    asset_label="无形资产",
    verb_label="摊销",
    schedule_model=IntangibleAmortizationSchedule,
    asset_model=IntangibleAsset,
    rd_expense_account=RD_EXPENSE_ACCOUNT,
    admin_expense_account=ADMIN_EXPENSE_ACCOUNT,
    credit_account=CREDIT_ACCOUNT,
    entry_id_prefix="amortization",
    title_prefix="摊销",
    category="摊销",
    source_type="intangible_amortization",
    fingerprint=lambda asset, row: f"intangible_amortization:{asset.company_id}:{asset.id}:{row.period_start}",
    accumulated_attr="accumulated_amortization",
    last_period_attr="last_amortized_period",
    done_status="fully_amortized",
    lock_prefix="amortization",
    audit_action="intangible.amortization.run",
    audit_entity_type="intangible_amortization_schedule",
    # done_query 的资产状态过滤（含已完结/处置中，历史口径如此）
    done_asset_statuses=("active", "idle", "fully_amortized", "disposing", "disposed", "scrapped"),
)

__all__ = [
    "ADMIN_EXPENSE_ACCOUNT",
    "CREDIT_ACCOUNT",
    "RD_EXPENSE_ACCOUNT",
    "SPEC",
    "a105080_preview",
    "amortization_summary",
    "get_runs",
    "get_schedule",
    "list_due_amortization_periods",
    "run_amortization",
    "run_amortization_catch_up",
    "validate_period",
]


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
    return await _list_due_periods(session, SPEC, company_id, through_period, asset_ids=asset_ids)


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
    return await _run_catch_up(
        session, SPEC, through_period,
        company_id=company_id, user_id=user_id, request_id=request_id,
        asset_ids=asset_ids, dry_run=dry_run,
    )


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
    """某期间全部已过账摊销行（摊销执行历史）。"""
    return await _get_runs(session, SPEC, period, company_id=company_id, user_id=user_id)


async def amortization_summary(
    session: AsyncSession, period_from: str, period_to: str, *, company_id: str, user_id: str
) -> dict[str, Any]:
    """摊销费用汇总（按类别），含与总账科目的勾稽行。"""
    await ensure_tenant(session, company_id, user_id)
    period_from = validate_period(period_from)
    period_to = validate_period(period_to)
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
