# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""核算项目三表查询引擎（余额表 / 明细账 / 组合表）。

口径与法定账表（``ledger_book_service``）完全同源：
1. 只认 ``posted`` / ``reversed``，按记账期间 ``period_start`` 归集；
2. 同期间冲销对（``same_period_reversal_exclusions``）不计入发生额合计，
   明细账逐笔流水仍完整保留审计痕迹；
3. 未挂维度 ID 的分录统一归入「未指定」组并计入合计，保证与科目余额表逐分勾稽；
4. 全程使用 ``core.money``（Decimal 两位小数），禁止浮点运算。
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.chart_policy import auxiliary_kinds_for_account
from core.errors import AccountingError
from core.money import ZERO, money, money_text
from db.models import (
    ChartAccount,
    Department,
    Employee,
    JournalEntry,
    JournalLine,
    Partner,
    Project,
)
from db.session import session_scope
from services.auxiliary_service import ID_KEY, KINDS, MODELS, NAME_KEY
from services.reversal_pairs import same_period_reversal_exclusions

POSTED_STATUSES = ("posted", "reversed")
UNASSIGNED_KEY = "__unassigned__"
COMBINATION_DIMS = ("account", *KINDS)
COMBINATION_METRICS = ("net", "debit", "credit", "closing")

UNASSIGNED_LABELS: dict[str, str] = {
    "partner": "未指定往来单位",
    "department": "未指定部门",
    "project": "未指定项目",
    "employee": "未指定职员",
}

DIMENSION_LABELS: dict[str, str] = {
    "account": "会计科目",
    "partner": "往来单位",
    "department": "部门",
    "project": "项目",
    "employee": "职员",
}


def _month_range(month: str) -> tuple[date, date]:
    try:
        year, month_number = (int(part) for part in str(month or "").split("-", 1))
        return date(year, month_number, 1), date(
            year,
            month_number,
            calendar.monthrange(year, month_number)[1],
        )
    except (TypeError, ValueError) as exc:
        raise AccountingError("INVALID_PERIOD", "账期格式应为 YYYY-MM", 422) from exc


def _account_matches(requested_code: str, actual_code: str) -> bool:
    return actual_code == requested_code or actual_code.startswith(requested_code)


def _direction_of(value: Decimal) -> str:
    if value > ZERO:
        return "debit"
    if value < ZERO:
        return "credit"
    return "flat"


def _abs_text(value: Decimal) -> str:
    return money_text(abs(value))


def _validate_kind(kind: str) -> str:
    if kind not in KINDS:
        raise AccountingError("INVALID_AUXILIARY_KIND", f"不支持的辅助核算维度：{kind}", 422)
    return kind


async def _load_master_maps(session: AsyncSession, company_id: str) -> dict[str, dict[str, dict[str, Any]]]:
    maps: dict[str, dict[str, dict[str, Any]]] = {}
    for kind, model in MODELS.items():
        rows = (
            await session.execute(select(model).where(model.company_id == company_id))
        ).scalars().all()
        maps[kind] = {
            str(row.id): {
                "id": str(row.id),
                "code": str(row.code or ""),
                "name": str(row.name or ""),
                "isActive": bool(row.is_active),
            }
            for row in rows
        }
    return maps


async def _load_chart_names(session: AsyncSession, company_id: str) -> dict[str, str]:
    rows = (
        await session.execute(
            select(ChartAccount.code, ChartAccount.name).where(ChartAccount.company_id == company_id)
        )
    ).all()
    return {str(code): str(name) for code, name in rows}


async def _load_posted_rows(
    session: AsyncSession, company_id: str, end_date: date
) -> tuple[list[tuple[JournalEntry, JournalLine]], set[str]]:
    rows = (
        await session.execute(
            select(JournalEntry, JournalLine)
            .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.status.in_(POSTED_STATUSES),
                JournalEntry.period_start <= end_date,
                JournalLine.company_id == company_id,
            )
            .order_by(
                JournalEntry.period_start,
                JournalEntry.occurred_on,
                JournalEntry.voucher_no,
                JournalLine.line_no,
            )
        )
    ).all()
    excluded = same_period_reversal_exclusions({entry for entry, _ in rows})
    return rows, excluded


def _line_has_kind(line: JournalLine, kind: str) -> bool:
    if kind in auxiliary_kinds_for_account(line.account_code):
        return True
    aux = line.auxiliary if isinstance(line.auxiliary, dict) else {}
    return bool(str(aux.get(ID_KEY[kind]) or "").strip())


def _resolve_dim_identity(
    line: JournalLine,
    dim: str,
    master_maps: dict[str, dict[str, dict[str, Any]]],
    chart_names: dict[str, str],
) -> tuple[str, str, str, bool]:
    """返回 ``(key, name, code, unassigned)``。"""
    if dim == "account":
        code = str(line.account_code or "")
        name = chart_names.get(code) or str(line.account_name or "") or code
        return code, name, code, False

    aux = line.auxiliary if isinstance(line.auxiliary, dict) else {}
    item_id = str(aux.get(ID_KEY[dim]) or "").strip()
    if not item_id:
        return UNASSIGNED_KEY, UNASSIGNED_LABELS[dim], "", True
    master = master_maps.get(dim, {}).get(item_id)
    if master is not None:
        return item_id, master["name"], master["code"], False
    fallback_name = str(aux.get(NAME_KEY[dim]) or "").strip() or item_id
    return item_id, fallback_name, "", False


def _serialize_balance_row(
    item_id: str,
    item_code: str,
    item_name: str,
    *,
    is_active: bool,
    unassigned: bool,
    opening: Decimal,
    debit: Decimal,
    credit: Decimal,
    ytd_debit: Decimal,
    ytd_credit: Decimal,
) -> dict[str, Any]:
    closing = money(opening + debit - credit)
    return {
        "itemId": "" if unassigned else item_id,
        "itemCode": item_code,
        "itemName": item_name,
        "isActive": is_active,
        "unassigned": unassigned,
        "opening": money_text(opening),
        "openingAbs": _abs_text(opening),
        "openingDirection": _direction_of(opening),
        "debit": money_text(debit),
        "credit": money_text(credit),
        "ytdDebit": money_text(ytd_debit),
        "ytdCredit": money_text(ytd_credit),
        "closing": money_text(closing),
        "closingAbs": _abs_text(closing),
        "closingDirection": _direction_of(closing),
    }


async def query_auxiliary_balances(
    company_id: str,
    kind: str,
    month: str,
    *,
    account_code: str | None = None,
    include_zero: bool = False,
) -> dict[str, Any]:
    """核算项目余额表：按指定辅助维度汇总期初 / 本期发生 / 本年累计 / 期末。"""
    _validate_kind(kind)
    start, end = _month_range(month)
    year_start = date(end.year, 1, 1)
    account_filter = str(account_code or "").strip() or None

    async with session_scope() as session:
        rows, excluded_entry_ids = await _load_posted_rows(session, company_id, end)
        master_maps = await _load_master_maps(session, company_id)

    buckets: dict[str, dict[str, Any]] = {}
    for entry, line in rows:
        if account_filter:
            if not _account_matches(account_filter, line.account_code):
                continue
        elif not _line_has_kind(line, kind):
            continue

        key, item_name, item_code, unassigned = _resolve_dim_identity(line, kind, master_maps, {})
        master = None if unassigned else master_maps[kind].get(key)
        bucket = buckets.setdefault(
            key,
            {
                "itemId": "" if unassigned else key,
                "itemCode": item_code,
                "itemName": item_name,
                "isActive": True if unassigned or master is None else master["isActive"],
                "unassigned": unassigned,
                "opening": ZERO,
                "debit": ZERO,
                "credit": ZERO,
                "ytdDebit": ZERO,
                "ytdCredit": ZERO,
            },
        )
        amount = money(line.amount)
        is_excluded = entry.id in excluded_entry_ids
        if entry.period_start < start:
            bucket["opening"] += amount if line.direction == "debit" else -amount
        elif start <= entry.period_start <= end and not is_excluded:
            if line.direction == "debit":
                bucket["debit"] += amount
            else:
                bucket["credit"] += amount
        if year_start <= entry.period_start <= end and not is_excluded:
            if line.direction == "debit":
                bucket["ytdDebit"] += amount
            else:
                bucket["ytdCredit"] += amount

    if include_zero:
        for item_id, master in master_maps[kind].items():
            if not master["isActive"] or item_id in buckets:
                continue
            buckets[item_id] = {
                "itemId": item_id,
                "itemCode": master["code"],
                "itemName": master["name"],
                "isActive": True,
                "unassigned": False,
                "opening": ZERO,
                "debit": ZERO,
                "credit": ZERO,
                "ytdDebit": ZERO,
                "ytdCredit": ZERO,
            }

    items: list[dict[str, Any]] = []
    unassigned_row: dict[str, Any] | None = None
    total_opening = ZERO
    total_debit = ZERO
    total_credit = ZERO
    ytd_total_debit = ZERO
    ytd_total_credit = ZERO

    for key, bucket in buckets.items():
        closing = money(bucket["opening"] + bucket["debit"] - bucket["credit"])
        has_values = any(
            v != ZERO
            for v in (
                bucket["opening"],
                bucket["debit"],
                bucket["credit"],
                bucket["ytdDebit"],
                bucket["ytdCredit"],
                closing,
            )
        )
        if not include_zero and not has_values:
            continue
        serialized = _serialize_balance_row(
            bucket["itemId"],
            bucket["itemCode"],
            bucket["itemName"],
            is_active=bucket["isActive"],
            unassigned=bucket["unassigned"],
            opening=bucket["opening"],
            debit=bucket["debit"],
            credit=bucket["credit"],
            ytd_debit=bucket["ytdDebit"],
            ytd_credit=bucket["ytdCredit"],
        )
        if bucket["unassigned"]:
            unassigned_row = serialized
        else:
            items.append(serialized)
        total_opening += bucket["opening"]
        total_debit += bucket["debit"]
        total_credit += bucket["credit"]
        ytd_total_debit += bucket["ytdDebit"]
        ytd_total_credit += bucket["ytdCredit"]

    items.sort(key=lambda r: (r["itemCode"] or "~", r["itemName"]))
    total_closing = money(total_opening + total_debit - total_credit)
    return {
        "kind": kind,
        "kindLabel": DIMENSION_LABELS[kind],
        "accountCode": account_filter,
        "month": month,
        "items": items,
        "unassigned": unassigned_row,
        "totalOpening": money_text(total_opening),
        "totalOpeningAbs": _abs_text(total_opening),
        "totalOpeningDirection": _direction_of(total_opening),
        "totalDebit": money_text(total_debit),
        "totalCredit": money_text(total_credit),
        "ytdTotalDebit": money_text(ytd_total_debit),
        "ytdTotalCredit": money_text(ytd_total_credit),
        "totalClosing": money_text(total_closing),
        "totalClosingAbs": _abs_text(total_closing),
        "totalClosingDirection": _direction_of(total_closing),
    }


async def query_auxiliary_sub_ledger(
    company_id: str,
    kind: str,
    item_id: str,
    month: str,
    *,
    account_code: str | None = None,
) -> dict[str, Any]:
    """核算项目明细账：指定维度与档案项的期初、逐笔发生、滚动余额与期末。"""
    _validate_kind(kind)
    target_id = str(item_id or "").strip()
    if not target_id:
        raise AccountingError("INVALID_AUXILIARY", "请指定核算项目", 422)
    start, end = _month_range(month)
    year_start = date(end.year, 1, 1)
    account_filter = str(account_code or "").strip() or None
    is_unassigned = target_id == UNASSIGNED_KEY

    async with session_scope() as session:
        rows, excluded_entry_ids = await _load_posted_rows(session, company_id, end)
        master_maps = await _load_master_maps(session, company_id)
        chart_names = await _load_chart_names(session, company_id)

    master = None if is_unassigned else master_maps[kind].get(target_id)
    if not is_unassigned and master is None:
        raise AccountingError("AUXILIARY_NOT_FOUND", "辅助核算档案不存在", 404)

    by_entry: dict[str, list[JournalLine]] = defaultdict(list)
    for entry, line in rows:
        by_entry[entry.id].append(line)

    opening = ZERO
    period_debit = ZERO
    period_credit = ZERO
    ytd_debit = ZERO
    ytd_credit = ZERO
    period_rows: list[tuple[JournalEntry, JournalLine]] = []

    id_key = ID_KEY[kind]
    for entry, line in rows:
        if account_filter:
            if not _account_matches(account_filter, line.account_code):
                continue
        elif is_unassigned and not _line_has_kind(line, kind):
            continue

        aux = line.auxiliary if isinstance(line.auxiliary, dict) else {}
        line_item_id = str(aux.get(id_key) or "").strip()
        if is_unassigned:
            if line_item_id:
                continue
        elif line_item_id != target_id:
            continue

        amount = money(line.amount)
        is_excluded = entry.id in excluded_entry_ids
        if entry.period_start < start:
            opening += amount if line.direction == "debit" else -amount
        elif start <= entry.period_start <= end:
            period_rows.append((entry, line))
            if not is_excluded:
                if line.direction == "debit":
                    period_debit += amount
                else:
                    period_credit += amount
        if year_start <= entry.period_start <= end and not is_excluded:
            if line.direction == "debit":
                ytd_debit += amount
            else:
                ytd_credit += amount

    running = money(opening)
    rendered_lines: list[dict[str, Any]] = []
    for entry, line in period_rows:
        amount = money(line.amount)
        signed = amount if line.direction == "debit" else -amount
        running = money(running + signed)
        aux = line.auxiliary if isinstance(line.auxiliary, dict) else {}
        counterparties = [
            {
                "code": other.account_code,
                "name": chart_names.get(other.account_code) or other.account_name,
                "amount": money_text(money(other.amount)),
            }
            for other in by_entry.get(entry.id, [])
            if other.id != line.id
        ]
        rendered_lines.append(
            {
                "entryId": entry.id,
                "voucherRef": entry.voucher_no,
                "date": entry.occurred_on.isoformat(),
                "period": entry.period_start.isoformat(),
                "summary": entry.summary,
                "accountCode": line.account_code,
                "accountName": chart_names.get(line.account_code) or line.account_name,
                "counterparties": counterparties,
                "auxiliary": {
                    k: v
                    for k, v in {
                        "partnerName": aux.get("partner_name"),
                        "departmentName": aux.get("department_name"),
                        "projectName": aux.get("project_name"),
                        "employeeName": aux.get("employee_name"),
                    }.items()
                    if v
                },
                "debit": money_text(amount) if line.direction == "debit" else "0.00",
                "credit": money_text(amount) if line.direction == "credit" else "0.00",
                "balance": money_text(running),
                "balanceAbs": _abs_text(running),
                "balanceDirection": _direction_of(running),
                "backfill": entry.period_start != entry.occurred_on.replace(day=1),
                "isReversal": entry.entry_type == "reversal",
                "isReversed": entry.status == "reversed",
            }
        )

    closing = money(opening + period_debit - period_credit)
    return {
        "kind": kind,
        "kindLabel": DIMENSION_LABELS[kind],
        "itemId": UNASSIGNED_KEY if is_unassigned else target_id,
        "itemCode": "" if is_unassigned or master is None else master["code"],
        "itemName": UNASSIGNED_LABELS[kind] if is_unassigned or master is None else master["name"],
        "unassigned": is_unassigned,
        "accountCode": account_filter,
        "month": month,
        "opening": money_text(opening),
        "openingAbs": _abs_text(opening),
        "openingDirection": _direction_of(opening),
        "lines": rendered_lines,
        "periodDebit": money_text(period_debit),
        "periodCredit": money_text(period_credit),
        "ytdDebit": money_text(ytd_debit),
        "ytdCredit": money_text(ytd_credit),
        "closing": money_text(closing),
        "closingAbs": _abs_text(closing),
        "closingDirection": _direction_of(closing),
    }


async def query_auxiliary_combination(
    company_id: str,
    row_dim: str,
    col_dim: str,
    month: str,
    *,
    metric: str = "net",
    account_code: str | None = None,
) -> dict[str, Any]:
    """核算项目组合表：行维度 × 列维度的二维交叉矩阵。"""
    if row_dim not in COMBINATION_DIMS or col_dim not in COMBINATION_DIMS:
        raise AccountingError("INVALID_COMBINATION_DIM", "不支持的组合维度", 422)
    if row_dim == col_dim:
        raise AccountingError("INVALID_COMBINATION_DIM", "行维度与列维度不能相同", 422)
    if metric not in COMBINATION_METRICS:
        raise AccountingError("INVALID_COMBINATION_METRIC", f"不支持的统计口径：{metric}", 422)

    start, end = _month_range(month)
    account_filter = str(account_code or "").strip() or None

    async with session_scope() as session:
        rows, excluded_entry_ids = await _load_posted_rows(session, company_id, end)
        master_maps = await _load_master_maps(session, company_id)
        chart_names = await _load_chart_names(session, company_id)

    row_meta: dict[str, dict[str, Any]] = {}
    col_meta: dict[str, dict[str, Any]] = {}
    matrix: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)

    for entry, line in rows:
        if account_filter:
            if not _account_matches(account_filter, line.account_code):
                continue
        else:
            aux_dims = [dim for dim in (row_dim, col_dim) if dim != "account"]
            if not any(_line_has_kind(line, dim) for dim in aux_dims):
                continue

        is_excluded = entry.id in excluded_entry_ids
        amount = money(line.amount)
        delta = ZERO
        if metric == "closing":
            delta = amount if line.direction == "debit" else -amount
        elif start <= entry.period_start <= end and not is_excluded:
            if metric == "debit":
                delta = amount if line.direction == "debit" else ZERO
            elif metric == "credit":
                delta = amount if line.direction == "credit" else ZERO
            else:
                delta = amount if line.direction == "debit" else -amount

        if delta == ZERO:
            continue

        r_key, r_name, r_code, r_unassigned = _resolve_dim_identity(line, row_dim, master_maps, chart_names)
        c_key, c_name, c_code, c_unassigned = _resolve_dim_identity(line, col_dim, master_maps, chart_names)
        row_meta.setdefault(
            r_key,
            {"id": r_key, "code": r_code, "name": r_name, "unassigned": r_unassigned},
        )
        col_meta.setdefault(
            c_key,
            {"id": c_key, "code": c_code, "name": c_name, "unassigned": c_unassigned},
        )
        matrix[(r_key, c_key)] += delta

    active_rows = {
        r_key
        for r_key in row_meta
        if any(matrix.get((r_key, c_key), ZERO) != ZERO for c_key in col_meta)
    }
    active_cols = {
        c_key
        for c_key in col_meta
        if any(matrix.get((r_key, c_key), ZERO) != ZERO for r_key in active_rows)
    }

    sorted_cols = sorted(
        (col_meta[c_key] for c_key in active_cols),
        key=lambda item: (1 if item["unassigned"] else 0, item["code"] or "~", item["name"]),
    )
    sorted_rows = sorted(
        (row_meta[r_key] for r_key in active_rows),
        key=lambda item: (1 if item["unassigned"] else 0, item["code"] or "~", item["name"]),
    )

    col_totals: dict[str, Decimal] = {col["id"]: ZERO for col in sorted_cols}
    rendered_rows: list[dict[str, Any]] = []
    grand_total = ZERO

    for r_info in sorted_rows:
        r_key = r_info["id"]
        cells: dict[str, str] = {}
        row_total = ZERO
        for col in sorted_cols:
            c_key = col["id"]
            val = money(matrix.get((r_key, c_key), ZERO))
            if val != ZERO:
                cells[c_key] = money_text(val)
                row_total += val
                col_totals[c_key] += val
        grand_total += row_total
        rendered_rows.append(
            {
                **r_info,
                "cells": cells,
                "rowTotal": money_text(row_total),
            }
        )

    return {
        "rowDim": row_dim,
        "rowDimLabel": DIMENSION_LABELS[row_dim],
        "colDim": col_dim,
        "colDimLabel": DIMENSION_LABELS[col_dim],
        "metric": metric,
        "accountCode": account_filter,
        "month": month,
        "columns": sorted_cols,
        "rows": rendered_rows,
        "colTotals": {k: money_text(v) for k, v in col_totals.items()},
        "grandTotal": money_text(grand_total),
    }
