# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Authoritative general ledger, sub-ledger, and account summary queries."""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from core.money import ZERO, money, money_text
from db.models import JournalEntry, JournalLine
from db.session import session_scope

POSTED_STATUSES = ("posted", "reversed")
AGGREGATE_PARENT_CODES = {"4301", "5401", "5601", "5602", "5603"}


def _account_matches(requested_code: str, actual_code: str) -> bool:
    if requested_code in AGGREGATE_PARENT_CODES:
        return actual_code == requested_code or actual_code.startswith(requested_code)
    return actual_code == requested_code


def _month_range(month: str) -> tuple[date, date]:
    try:
        year, month_number = (int(part) for part in month.split("-", 1))
        return date(year, month_number, 1), date(
            year,
            month_number,
            calendar.monthrange(year, month_number)[1],
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("账期格式应为 YYYY-MM") from exc


async def query_general_ledger(company_id: str, account_code: str, month: str) -> list[dict[str, Any]]:
    start, end = _month_range(month)
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(JournalEntry, JournalLine)
                .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.status.in_(POSTED_STATUSES),
                    JournalEntry.occurred_on <= end,
                    JournalLine.company_id == company_id,
                    JournalLine.account_code.startswith(account_code)
                    if account_code in AGGREGATE_PARENT_CODES
                    else JournalLine.account_code == account_code,
                )
                .order_by(JournalEntry.occurred_on, JournalEntry.voucher_no, JournalLine.line_no)
            )
        ).all()

    opening = ZERO
    transactions: list[dict[str, Any]] = []
    for entry, line in rows:
        signed = money(line.amount) if line.direction == "debit" else -money(line.amount)
        if entry.occurred_on < start:
            opening += signed
            continue
        transactions.append(
            {
                "entryId": entry.id,
                "voucherRef": entry.voucher_no,
                "date": entry.occurred_on.isoformat(),
                "summary": entry.summary,
                "debit": money(line.amount) if line.direction == "debit" else ZERO,
                "credit": money(line.amount) if line.direction == "credit" else ZERO,
            }
        )

    running = money(opening)
    output: list[dict[str, Any]] = []
    for transaction in transactions:
        running = money(running + transaction["debit"] - transaction["credit"])
        output.append(
            {
                **transaction,
                "debit": money_text(transaction["debit"]),
                "credit": money_text(transaction["credit"]),
                "balance": money_text(running),
            }
        )
    return output


async def query_sub_ledger(company_id: str, account_code: str, month: str) -> list[dict[str, Any]]:
    start, end = _month_range(month)
    async with session_scope() as session:
        entries = (
            await session.execute(
                select(JournalEntry)
                .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.status.in_(POSTED_STATUSES),
                    JournalEntry.occurred_on <= end,
                    JournalLine.company_id == company_id,
                    JournalLine.account_code.startswith(account_code)
                    if account_code in AGGREGATE_PARENT_CODES
                    else JournalLine.account_code == account_code,
                )
                .distinct()
                .order_by(JournalEntry.occurred_on, JournalEntry.voucher_no)
            )
        ).scalars().all()
        entry_ids = [entry.id for entry in entries]
        lines = (
            await session.execute(
                select(JournalLine)
                .where(JournalLine.journal_entry_id.in_(entry_ids))
                .order_by(JournalLine.journal_entry_id, JournalLine.line_no)
            )
        ).scalars().all() if entry_ids else []

    by_entry: dict[str, list[JournalLine]] = defaultdict(list)
    for line in lines:
        by_entry[line.journal_entry_id].append(line)

    opening = ZERO
    output: list[dict[str, Any]] = []
    for entry in entries:
        own_lines = [line for line in by_entry[entry.id] if _account_matches(account_code, line.account_code)]
        debit = sum((money(line.amount) for line in own_lines if line.direction == "debit"), ZERO)
        credit = sum((money(line.amount) for line in own_lines if line.direction == "credit"), ZERO)
        if entry.occurred_on < start:
            opening += debit - credit
            continue
        counterparties = [
            {
                "code": line.account_code,
                "name": line.account_name,
                "amount": money_text(line.amount),
            }
            for line in by_entry[entry.id]
            if not _account_matches(account_code, line.account_code)
        ]
        output.append(
            {
                "entryId": entry.id,
                "voucherRef": entry.voucher_no,
                "date": entry.occurred_on.isoformat(),
                "summary": entry.summary,
                "debit": debit,
                "credit": credit,
                "counterparties": counterparties,
            }
        )

    running = money(opening)
    for transaction in output:
        running = money(running + transaction["debit"] - transaction["credit"])
        transaction["debit"] = money_text(transaction["debit"])
        transaction["credit"] = money_text(transaction["credit"])
        transaction["balance"] = money_text(running)
    return output


async def get_balance_summary(company_id: str, month: str) -> dict[str, Any]:
    start, end = _month_range(month)
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(JournalEntry, JournalLine)
                .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.status.in_(POSTED_STATUSES),
                    JournalEntry.occurred_on <= end,
                    JournalLine.company_id == company_id,
                )
            )
        ).all()

    # 期初取月前所有过账分录的累计净额，期末 = 期初 + 本期借贷（借余为正的符号约定）
    accounts: dict[str, dict[str, Any]] = {}
    for entry, line in rows:
        bucket = accounts.setdefault(
            line.account_code,
            {"name": line.account_name, "opening": ZERO, "debit": ZERO, "credit": ZERO},
        )
        if entry.occurred_on < start:
            bucket["opening"] += money(line.amount) if line.direction == "debit" else -money(line.amount)
        elif line.direction == "debit":
            bucket["debit"] += money(line.amount)
        else:
            bucket["credit"] += money(line.amount)

    items = [
        {
            "accountCode": code,
            "accountName": info["name"],
            "openingBalance": money_text(info["opening"]),
            "debitTotal": money_text(info["debit"]),
            "creditTotal": money_text(info["credit"]),
            "balance": money_text(info["debit"] - info["credit"]),
            "closingBalance": money_text(info["opening"] + info["debit"] - info["credit"]),
        }
        for code, info in sorted(accounts.items())
    ]
    total_debit = sum((money(item["debitTotal"]) for item in items), ZERO)
    total_credit = sum((money(item["creditTotal"]) for item in items), ZERO)
    return {
        "month": month,
        "items": items,
        "totalDebit": money_text(total_debit),
        "totalCredit": money_text(total_credit),
        "isBalanced": total_debit == total_credit,
    }
