# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""资产核对总账：卡片合计 vs 1601/1602 科目余额 vs 折旧计划累计，三方勾稽。

P1 WP-B 设计第 6 条。口径：
- 卡片侧只统计已入账状态（active/idle/fully_depreciated/disposing/disposed/
  scrapped）——draft/pending_review 尚未生成取得凭证，不占 1601；
- 总账侧 1601 借方余额（原值）、1602 贷方余额（累计折旧），POSTED_STATUSES
  口径（posted + reversed），与科目余额表一致；
- 计划侧 Σposted 折旧行须等于卡片累计折旧合计（卡内自洽）。
差异列出明细（按卡片逐张对不上总账的，列到卡片级）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.money import ZERO, money, money_text
from db.models import FixedAsset, FixedAssetDepreciationSchedule, JournalEntry, JournalLine
from services.ledger_service import POSTED_STATUSES

_TOLERANCE = Decimal("0.05")

_POSTED_CARD_STATUSES = ("active", "idle", "fully_depreciated", "disposing", "disposed", "scrapped")


async def asset_reconciliation(session: AsyncSession, company_id: str, *, month: str | None = None) -> dict[str, Any]:
    """三方勾稽。applicable=False 表示公司无已入账卡片（检查项不适用）。"""
    cards = (
        await session.execute(
            select(FixedAsset).where(
                FixedAsset.company_id == company_id,
                FixedAsset.deleted_at.is_(None),
                FixedAsset.status.in_(_POSTED_CARD_STATUSES),
            )
        )
    ).scalars().all()
    card_cost = money(sum((money(c.original_value) for c in cards), ZERO))
    card_depreciation = money(sum((money(c.accumulated_depreciation or ZERO) for c in cards), ZERO))

    async def _ledger_sides(code: str) -> tuple[Decimal, Decimal]:
        conditions = [
            JournalLine.company_id == company_id,
            JournalLine.account_code == code,
            JournalEntry.status.in_(POSTED_STATUSES),
        ]
        if month:
            y, m = int(month[:4]), int(month[5:7])
            end_of_month = date(y + (m // 12), m % 12 + 1, 1)
            conditions.append(JournalEntry.period_start < end_of_month)
        totals = (
            await session.execute(
                select(
                    sa_func.coalesce(sa_func.sum(JournalLine.amount).filter(JournalLine.direction == "debit"), 0),
                    sa_func.coalesce(sa_func.sum(JournalLine.amount).filter(JournalLine.direction == "credit"), 0),
                )
                .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
                .where(*conditions)
            )
        ).one()
        return money(Decimal(str(totals[0]))), money(Decimal(str(totals[1])))

    debit_1601, credit_1601 = await _ledger_sides("1601")
    debit_1602, credit_1602 = await _ledger_sides("1602")
    ledger_cost = money(debit_1601 - credit_1601)
    ledger_depreciation = money(credit_1602 - debit_1602)

    schedule_posted = money(
        Decimal(str(
            (
                await session.execute(
                    select(sa_func.coalesce(sa_func.sum(FixedAssetDepreciationSchedule.amount), 0)).where(
                        FixedAssetDepreciationSchedule.company_id == company_id,
                        FixedAssetDepreciationSchedule.status == "posted",
                    )
                )
            ).scalar() or 0
        ))
    )

    cost_diff = money(card_cost - ledger_cost)
    depreciation_diff = money(card_depreciation - ledger_depreciation)
    schedule_diff = money(card_depreciation - schedule_posted)
    balanced = (
        abs(cost_diff) <= _TOLERANCE
        and abs(depreciation_diff) <= _TOLERANCE
        and abs(schedule_diff) <= _TOLERANCE
    )

    details: list[dict[str, Any]] = []
    if not balanced:
        # 逐卡列出原值/累计，供与总账明细对账
        details = [
            {
                "assetId": c.id,
                "assetCode": c.code,
                "assetName": c.name,
                "status": c.status,
                "originalValue": money(c.original_value),
                "accumulatedDepreciation": money(c.accumulated_depreciation or ZERO),
                "sourceEntryId": c.source_entry_id,
            }
            for c in cards
        ]

    return {
        "applicable": bool(cards),
        "balanced": balanced,
        "cardCostTotal": card_cost,
        "cardDepreciationTotal": card_depreciation,
        "ledger1601": ledger_cost,
        "ledger1602": ledger_depreciation,
        "schedulePostedTotal": schedule_posted,
        "costDiff": cost_diff,
        "depreciationDiff": depreciation_diff,
        "scheduleDiff": schedule_diff,
        "cardCount": len(cards),
        "details": details,
    }


def reconciliation_summary(reconciliation: dict[str, Any]) -> dict[str, Any]:
    """月结检查 BOOK_TO_ACTUAL 子项用的精简视图。"""
    if not reconciliation.get("applicable"):
        return {"applicable": False, "balanced": True}
    return {
        "applicable": True,
        "balanced": bool(reconciliation.get("balanced")),
        "cardCostTotal": money_text(reconciliation["cardCostTotal"]),
        "ledger1601": money_text(reconciliation["ledger1601"]),
        "cardDepreciationTotal": money_text(reconciliation["cardDepreciationTotal"]),
        "ledger1602": money_text(reconciliation["ledger1602"]),
        "costDiff": money_text(reconciliation["costDiff"]),
        "depreciationDiff": money_text(reconciliation["depreciationDiff"]),
        "scheduleDiff": money_text(reconciliation["scheduleDiff"]),
    }
