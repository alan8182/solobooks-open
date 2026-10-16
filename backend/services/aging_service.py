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


async def query_aging(company_id: str, account_code: str, as_of: str = "") -> dict[str, Any]:
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

    buckets: dict[str, dict[str, Any]] = {}
    total = ZERO
    count = 0
    for entry, line in rows:
        amount = money(line.amount)
        days = max(0, (reference - entry.occurred_on).days)
        label = _bucket_label(days)
        item = {
            "entryId": entry.id,
            "voucherRef": entry.voucher_no,
            "date": entry.occurred_on.isoformat(),
            "title": entry.summary,
            "amount": money_text(amount),
            "direction": "借" if line.direction == "debit" else "贷",
            "days": days,
            "bucket": label,
        }
        bucket = buckets.setdefault(label, {"label": label, "total": ZERO, "count": 0, "items": []})
        bucket["total"] += amount
        bucket["count"] += 1
        bucket["items"].append(item)
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


async def query_ar_aging(company_id: str, as_of: str = "") -> dict[str, Any]:
    return await query_aging(company_id, "1122", as_of)


async def query_ap_aging(company_id: str, as_of: str = "") -> dict[str, Any]:
    return await query_aging(company_id, "2202", as_of)
