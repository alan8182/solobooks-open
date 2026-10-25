# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""资产处理编排：把来源交易建卡与折旧/摊销补提收口成一次确认。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.fixed_asset_categories import get_category as get_fixed_asset_category
from core.intangible_categories import get_category as get_intangible_category
from db.models import AccountingPeriod, FixedAsset, IntangibleAsset
from db.session import session_scope
from services.accounting_core_service import _json_safe, ensure_tenant
from services.amortization_engine import (
    list_due_amortization_periods,
    run_amortization_catch_up,
)
from services.depreciation_engine import (
    list_due_depreciation_periods,
    run_depreciation_catch_up,
)
from services.entry_service import list_entries
from services.fixed_asset_service import (
    create_asset_from_entry,
    get_fixed_asset_entry_prefill,
)
from services.intangible_asset_service import (
    create_intangible_asset_from_entry,
    get_intangible_asset_entry_prefill,
)


def _period_start(period: str) -> date:
    return date(int(period[:4]), int(period[5:7]), 1)


async def _closed_periods(
    session: AsyncSession, company_id: str, periods: list[str]
) -> set[str]:
    if not periods:
        return set()
    starts = [_period_start(period) for period in periods]
    rows = (
        await session.execute(
            select(AccountingPeriod.period_start, AccountingPeriod.status).where(
                AccountingPeriod.company_id == company_id,
                AccountingPeriod.period_start.in_(starts),
                AccountingPeriod.status.in_(("closing", "closed")),
            )
        )
    ).all()
    return {row[0].strftime("%Y-%m") for row in rows}


def _blocked_item(kind: str, entry_id: str, name: str, reason: str) -> dict[str, Any]:
    return {"kind": kind, "entryId": entry_id, "name": name, "reason": reason}


async def _preview_asset_closure(
    session: AsyncSession, company_id: str, user_id: str, month: str
) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    fixed_linked = set(
        (
            await session.execute(
                select(FixedAsset.source_entry_id).where(
                    FixedAsset.company_id == company_id,
                    FixedAsset.source_entry_id.is_not(None),
                    FixedAsset.deleted_at.is_(None),
                )
            )
        ).scalars().all()
    )
    intangible_linked = set(
        (
            await session.execute(
                select(IntangibleAsset.source_entry_id).where(
                    IntangibleAsset.company_id == company_id,
                    IntangibleAsset.source_entry_id.is_not(None),
                    IntangibleAsset.deleted_at.is_(None),
                )
            )
        ).scalars().all()
    )

    fixed_missing: list[dict[str, Any]] = []
    intangible_missing: list[dict[str, Any]] = []
    fixed_blocked: list[dict[str, Any]] = []
    intangible_blocked: list[dict[str, Any]] = []

    entries = await list_entries(user_id, company_id)
    reversed_ids = {
        str(entry.get("reversalOf") or "")
        for entry in entries
        if str(entry.get("entryType") or "") == "reversal"
    }
    for entry in entries:
        entry_id = str(entry.get("id") or "")
        if not entry_id:
            continue
        category = str(entry.get("category") or "")
        account_code = str(entry.get("accountCode") or "")
        entry_type = str(entry.get("entryType") or "")
        entry_title = str(entry.get("title") or "资产交易")
        if entry_id in reversed_ids or entry_type in {"reversal", "opening", "closing"}:
            if category == "固定资产" or account_code.startswith("1601"):
                fixed_blocked.append(_blocked_item("fixed_asset", entry_id, entry_title, "原凭证已冲销或属于系统结转"))
            if category == "无形资产" or account_code.startswith("1701"):
                intangible_blocked.append(_blocked_item("intangible_asset", entry_id, entry_title, "原凭证已冲销或属于系统结转"))
            continue

        if (category == "固定资产" or account_code.startswith("1601")) and entry_id not in fixed_linked:
            prefill = await get_fixed_asset_entry_prefill(
                session, entry_id, company_id=company_id, user_id=user_id
            )
            name = str(prefill["name"] or entry.get("title") or "固定资产")
            if prefill["existingAssetId"]:
                continue
            if prefill["entryStatus"] != "posted":
                fixed_blocked.append(_blocked_item("fixed_asset", entry_id, name, "原凭证尚未过账"))
                continue
            if Decimal(str(prefill["originalValue"])) <= 0:
                fixed_blocked.append(_blocked_item("fixed_asset", entry_id, name, "原凭证没有 1601 借方"))
                continue
            category_info = get_fixed_asset_category(prefill["suggestedCategory"])
            fixed_missing.append(
                {
                    "entryId": entry_id,
                    "sourceEntryId": entry_id,
                    "name": name,
                    "originalValue": float(prefill["originalValue"]),
                    "acquisitionDate": prefill["acquisitionDate"],
                    "availableForUseDate": prefill["availableForUseDate"],
                    "category": category_info.code,
                    "categoryName": category_info.name,
                    "usefulLifeMonths": category_info.default_life_months,
                    "depreciationMethod": "straight_line",
                }
            )

        if (category == "无形资产" or account_code.startswith("1701")) and entry_id not in intangible_linked:
            prefill = await get_intangible_asset_entry_prefill(
                session, entry_id, company_id=company_id, user_id=user_id
            )
            name = str(prefill["name"] or entry.get("title") or "无形资产")
            if prefill["existingAssetId"]:
                continue
            if prefill["entryStatus"] != "posted":
                intangible_blocked.append(_blocked_item("intangible_asset", entry_id, name, "原凭证尚未过账"))
                continue
            if Decimal(str(prefill["originalValue"])) <= 0:
                intangible_blocked.append(_blocked_item("intangible_asset", entry_id, name, "原凭证没有 1701 借方"))
                continue
            category_info = get_intangible_category(prefill["suggestedCategory"])
            intangible_missing.append(
                {
                    "entryId": entry_id,
                    "sourceEntryId": entry_id,
                    "name": name,
                    "originalValue": float(prefill["originalValue"]),
                    "acquisitionDate": prefill["acquisitionDate"],
                    "availableForUseDate": prefill["availableForUseDate"],
                    "category": category_info.code,
                    "categoryName": category_info.name,
                    "usefulLifeMonths": category_info.default_life_months,
                }
            )

    fixed_due = await list_due_depreciation_periods(session, company_id, month)
    intangible_due = await list_due_amortization_periods(session, company_id, month)
    fixed_closed = await _closed_periods(
        session, company_id, [item["period"] for item in fixed_due]
    )
    intangible_closed = await _closed_periods(
        session, company_id, [item["period"] for item in intangible_due]
    )
    for item in fixed_due:
        if item["period"] in fixed_closed:
            fixed_blocked.append(
                {
                    "kind": "fixed_depreciation_period",
                    "period": item["period"],
                    "reason": "期间已关账",
                }
            )
    for item in intangible_due:
        if item["period"] in intangible_closed:
            intangible_blocked.append(
                {
                    "kind": "intangible_amortization_period",
                    "period": item["period"],
                    "reason": "期间已关账",
                }
            )

    return {
        "month": month,
        "fixedAssets": {
            "missingCards": fixed_missing,
            "dueDepreciation": fixed_due,
            "blockedItems": fixed_blocked,
        },
        "intangibles": {
            "missingCards": intangible_missing,
            "dueAmortization": intangible_due,
            "blockedItems": intangible_blocked,
        },
    }


async def preview_asset_closure(
    company_id: str, user_id: str, month: str
) -> dict[str, Any]:
    async with session_scope() as session:
        return await _preview_asset_closure(session, company_id, user_id, month)


def build_asset_closure_payload(preview: dict[str, Any]) -> dict[str, Any]:
    fixed = preview["fixedAssets"]
    intangible = preview["intangibles"]
    fixed_cards = [
        {
            "sourceEntryId": item["sourceEntryId"],
            "name": item["name"],
            "category": item["category"],
            "acquisitionDate": item["acquisitionDate"],
            "availableForUseDate": item["availableForUseDate"],
            "originalValue": item["originalValue"],
            "usefulLifeMonths": item["usefulLifeMonths"],
            "depreciationMethod": item["depreciationMethod"],
        }
        for item in fixed["missingCards"]
    ]
    intangible_cards = [
        {
            "sourceEntryId": item["sourceEntryId"],
            "name": item["name"],
            "category": item["category"],
            "acquisitionDate": item["acquisitionDate"],
            "availableForUseDate": item["availableForUseDate"],
            "originalValue": item["originalValue"],
            "usefulLifeMonths": item["usefulLifeMonths"],
        }
        for item in intangible["missingCards"]
    ]
    fixed_closed = {
        blocked["period"] for blocked in fixed["blockedItems"] if "period" in blocked
    }
    intangible_closed = {
        blocked["period"] for blocked in intangible["blockedItems"] if "period" in blocked
    }
    fixed_due = [item for item in fixed["dueDepreciation"] if item["period"] not in fixed_closed]
    intangible_due = [
        item for item in intangible["dueAmortization"] if item["period"] not in intangible_closed
    ]
    return {
        "kind": "asset_closure",
        "month": preview["month"],
        "throughPeriod": preview["month"],
        "fixedCards": fixed_cards,
        "intangibleCards": intangible_cards,
        "runFixedDepreciation": bool(fixed_due) and not fixed_closed,
        "runIntangibleAmortization": bool(intangible_due) and not intangible_closed,
        "preview": preview,
    }


async def execute_asset_closure(
    session: AsyncSession,
    payload: dict[str, Any],
    *,
    company_id: str,
    user_id: str,
    request_id: str,
) -> dict[str, Any]:
    fixed_cards: list[dict[str, Any]] = []
    for index, card in enumerate(payload.get("fixedCards") or []):
        asset = await create_asset_from_entry(
            session,
            dict(card),
            company_id=company_id,
            user_id=user_id,
            request_id=f"{request_id}:fixed-card:{index}",
        )
        fixed_cards.append({"assetId": asset.id, "name": asset.name, "status": "created"})

    intangible_cards: list[dict[str, Any]] = []
    for index, card in enumerate(payload.get("intangibleCards") or []):
        asset = await create_intangible_asset_from_entry(
            session,
            dict(card),
            company_id=company_id,
            user_id=user_id,
            request_id=f"{request_id}:intangible-card:{index}",
        )
        intangible_cards.append({"assetId": asset.id, "name": asset.name, "status": "created"})

    through_period = str(payload.get("throughPeriod") or payload.get("month") or "")
    fixed_depreciation = None
    if payload.get("runFixedDepreciation") and through_period:
        fixed_depreciation = await run_depreciation_catch_up(
            session,
            through_period,
            company_id=company_id,
            user_id=user_id,
            request_id=f"{request_id}:fixed-depreciation",
        )

    intangible_amortization = None
    if payload.get("runIntangibleAmortization") and through_period:
        intangible_amortization = await run_amortization_catch_up(
            session,
            through_period,
            company_id=company_id,
            user_id=user_id,
            request_id=f"{request_id}:intangible-amortization",
        )

    return _json_safe(
        {
            "fixedCards": fixed_cards,
            "intangibleCards": intangible_cards,
            "fixedDepreciation": fixed_depreciation,
            "intangibleAmortization": intangible_amortization,
        }
    )
