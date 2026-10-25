# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Aging analysis sourced only from posted journal lines."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import select

from core.money import ZERO, money, money_text
from db.models import JournalEntry, JournalLine
from db.session import session_scope


def _bucket_label(days: int) -> str:
    if days <= 30:
        return "30天内"
    if days <= 60:
        return "31-60天"
    if days <= 90:
        return "61-90天"
    if days <= 120:
        return "91-120天"
    return "120天以上"


def _bucket_sort_key(label: str) -> int:
    return {"30天内": 0, "31-60天": 1, "61-90天": 2, "91-120天": 3, "120天以上": 4}.get(label, 99)


async def query_aging(
    company_id: str,
    account_code: str,
    as_of: str = "",
    *,
    group_by: str = "none",
) -> dict[str, Any]:
    """账龄分析。

    ``group_by='partner'`` 时按往来单位分组（柠檬云"账龄分析表"的口径）：
    返回 ``groups`` 数组，每组自带 buckets；无往来单位的行归入 ``unassigned``，
    不丢数据。默认 ``none`` 保持原有的凭证粒度返回结构，向后兼容。
    """
    try:
        reference = date.fromisoformat(as_of[:10]) if as_of else datetime.now().date()
    except ValueError:
        reference = datetime.now().date()

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(JournalEntry, JournalLine)
                .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.status.in_(("posted", "reversed")),
                    JournalEntry.occurred_on <= reference,
                    JournalLine.company_id == company_id,
                    JournalLine.account_code == account_code,
                )
                .order_by(JournalEntry.occurred_on, JournalEntry.voucher_no)
            )
        ).all()

    def bucket_row(entry, line) -> dict[str, Any]:
        amount = money(line.amount)
        days = max(0, (reference - entry.occurred_on).days)
        label = _bucket_label(days)
        return {
            "entryId": entry.id,
            "voucherRef": entry.voucher_no,
            "date": entry.occurred_on.isoformat(),
            "title": entry.summary,
            "amount": money_text(amount),
            "direction": "借" if line.direction == "debit" else "贷",
            "days": days,
            "bucket": label,
            "_amount": amount,
        }

    if group_by == "partner":
        grouped: dict[str, dict[str, Any]] = {}
        for entry, line in rows:
            auxiliary = line.auxiliary if isinstance(line.auxiliary, dict) else {}
            partner_id = str(auxiliary.get("partner_id") or "")
            key = partner_id or "__unassigned__"
            group = grouped.setdefault(
                key,
                {
                    "partnerId": partner_id,
                    "partnerName": str(auxiliary.get("partner_name") or "") or "未指定往来单位",
                    "unassigned": partner_id == "",
                    "total": ZERO,
                    "count": 0,
                    "buckets": {},
                },
            )
            row = bucket_row(entry, line)
            amount = row.pop("_amount")
            bucket = group["buckets"].setdefault(
                row["bucket"], {"label": row["bucket"], "total": ZERO, "count": 0, "items": []}
            )
            bucket["total"] += amount
            bucket["count"] += 1
            bucket["items"].append(row)
            group["total"] += amount
            group["count"] += 1

        groups: list[dict[str, Any]] = []
        unassigned: dict[str, Any] | None = None
        total = ZERO
        count = 0
        for group in grouped.values():
            buckets = sorted(group["buckets"].values(), key=lambda bucket: _bucket_sort_key(bucket["label"]))
            for bucket in buckets:
                bucket["total"] = money_text(bucket["total"])
            output = {
                "partnerId": group["partnerId"],
                "partnerName": group["partnerName"],
                "unassigned": group["unassigned"],
                "buckets": buckets,
                "total": money_text(group["total"]),
                "count": group["count"],
            }
            total += group["total"]
            count += group["count"]
            if group["unassigned"]:
                unassigned = output
            else:
                groups.append(output)
        groups.sort(key=lambda item: item["partnerName"])
        return {
            "account_code": account_code,
            "as_of": reference.isoformat(),
            "group_by": "partner",
            "groups": groups,
            "unassigned": unassigned,
            "total": money_text(total),
            "count": count,
        }

    buckets: dict[str, dict[str, Any]] = {}
    total = ZERO
    count = 0
    for entry, line in rows:
        row = bucket_row(entry, line)
        amount = row.pop("_amount")
        label = row["bucket"]
        bucket = buckets.setdefault(label, {"label": label, "total": ZERO, "count": 0, "items": []})
        bucket["total"] += amount
        bucket["count"] += 1
        bucket["items"].append(row)
        total += amount
        count += 1

    output = sorted(buckets.values(), key=lambda bucket: _bucket_sort_key(bucket["label"]))
    for bucket in output:
        bucket["total"] = money_text(bucket["total"])
    return {
        "account_code": account_code,
        "as_of": reference.isoformat(),
        "buckets": output,
        "total": money_text(total),
        "count": count,
    }


async def query_ar_aging(company_id: str, as_of: str = "", *, group_by: str = "none") -> dict[str, Any]:
    return await query_aging(company_id, "1122", as_of, group_by=group_by)


async def query_ap_aging(company_id: str, as_of: str = "", *, group_by: str = "none") -> dict[str, Any]:
    return await query_aging(company_id, "2202", as_of, group_by=group_by)
