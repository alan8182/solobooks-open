# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Close checks against authoritative journals, in the caller's transaction."""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.money import ZERO, money, money_text
from db.models import JournalEntry, JournalLine, ReconciliationLink, SourceTransaction


async def possible_duplicate_wechat_income(session: AsyncSession, entry: JournalEntry) -> list[str]:
    """Flag a bank import booked as revenue when original settlement groups exist.

    This is a review blocker, not an automatic pairing or data repair.
    """
    from services.bank_service import RECONCILIATION_AMOUNT_TOLERANCE

    if str((entry.metadata_json or {}).get("source") or (entry.metadata_json or {}).get("sourceType") or "") != "bank_import":
        return []
    if not re.search(r"微信|财付通|WeChat|Tenpay", entry.summary, re.I):
        return []
    own_lines = (await session.execute(select(JournalLine).where(
        JournalLine.journal_entry_id == entry.id, JournalLine.company_id == entry.company_id,
    ))).scalars().all()
    bank_effect = sum((money(line.amount) * (1 if line.direction == "debit" else -1)
                       for line in own_lines if line.account_code == "1002"), ZERO)
    if bank_effect <= ZERO or not any(line.account_code in {"5001", "5051"} for line in own_lines):
        return []
    candidates = (await session.execute(select(JournalEntry, JournalLine).join(
        JournalLine, JournalLine.journal_entry_id == JournalEntry.id,
    ).where(
        JournalEntry.company_id == entry.company_id,
        JournalLine.company_id == entry.company_id,
        JournalEntry.occurred_on >= entry.occurred_on - timedelta(days=5),
        JournalEntry.occurred_on <= entry.occurred_on,
        JournalEntry.status.in_(("draft", "pending_review", "posted")),
        JournalLine.account_code.in_(("1012001", "1012")),
    ))).all()
    groups: dict[tuple, dict[str, Any]] = {}
    days: dict[date, dict[str, Any]] = {}
    for candidate, line in candidates:
        metadata = candidate.metadata_json or {}
        if metadata.get("source") != "wechat_bill" or metadata.get("is_archive_only") or metadata.get("isArchiveOnly"):
            continue
        key = (candidate.occurred_on, metadata.get("wechat_order_no") or candidate.id)
        group = groups.setdefault(key, {"amount": ZERO, "ids": set()})
        group["amount"] += money(line.amount) * (1 if line.direction == "debit" else -1)
        group["ids"].add(candidate.id)
    # Exclude fully refunded orders before forming a complete settlement day.
    for (day, _), group in groups.items():
        if group["amount"] == ZERO:
            continue
        daily = days.setdefault(day, {"amount": ZERO, "ids": set()})
        daily["amount"] += group["amount"]
        daily["ids"].update(group["ids"])
    ids: set[str] = set()
    for group in [*groups.values(), *days.values()]:
        if abs(group["amount"] - bank_effect) <= RECONCILIATION_AMOUNT_TOLERANCE:
            ids.update(group["ids"])
    return sorted(ids)


async def bank_close_validation(session: AsyncSession, company_id: str, month_start: date) -> dict:
    from services.bank_service import build_balance_reconciliation, diagnose_matched_statement_effect, RECONCILIATION_AMOUNT_TOLERANCE
    from services.accounting_core_service import serialize_journal

    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    sources = (await session.execute(select(SourceTransaction).where(
        SourceTransaction.company_id == company_id,
        SourceTransaction.source_type.in_(("bank", "bank_statement")),
        SourceTransaction.status != "void",
        SourceTransaction.occurred_on >= month_start, SourceTransaction.occurred_on < next_month,
    ))).scalars().all()
    all_links = (await session.execute(select(ReconciliationLink, SourceTransaction).join(
        SourceTransaction, SourceTransaction.id == ReconciliationLink.source_transaction_id,
    ).where(
        ReconciliationLink.company_id == company_id, ReconciliationLink.status == "confirmed",
        SourceTransaction.company_id == company_id, SourceTransaction.status != "void",
        SourceTransaction.source_type.in_(("bank", "bank_statement")),
    ))).all()
    by_source: dict[str, list] = {}
    by_entry: dict[str, set[str]] = {}
    for link, source in all_links:
        by_source.setdefault(source.id, []).append(link)
        by_entry.setdefault(link.journal_entry_id, set()).add(source.id)
    issues = []
    for source in sources:
        links = by_source.get(source.id, [])
        base = {"statementId": source.external_key, "date": source.occurred_on.isoformat(), "description": source.description}
        if source.status != "reconciled" or not links:
            issues.append({**base, "type": "unreconciled", "reason": "流水尚未关联有效核销凭证"})
            continue
        payloads = []
        for link in links:
            entry = await session.get(JournalEntry, link.journal_entry_id)
            if entry is None or entry.company_id != company_id:
                issues.append({**base, "type": "broken_link", "reason": "核销凭证不存在", "entryId": link.journal_entry_id})
                continue
            if len(by_entry[entry.id]) > 1:
                issues.append({**base, "type": "duplicate_match", "reason": "同一凭证重复核销多笔流水", "entryId": entry.id})
            if entry.status not in {"posted", "reversed"}:
                issues.append({**base, "type": "unposted_match", "reason": "核销凭证尚未过账", "entryId": entry.id})
                continue
            payloads.append(await serialize_journal(session, entry, api_money=False))
            duplicates = await possible_duplicate_wechat_income(session, entry)
            if duplicates:
                issues.append({**base, "type": "possible_duplicate_income", "reason": "银行到账可能重复记为微信收入，请核对原收入与结算凭证", "entryId": entry.id, "candidateEntryIds": duplicates})
        signed = (source.raw_payload or {}).get("signedAmount")
        if signed is None:
            issues.append({**base, "type": "missing_direction", "reason": "缺少流水收支方向，不能验证银行科目影响"})
        elif payloads:
            effect_issue = diagnose_matched_statement_effect({
                "id": source.external_key, "transaction_date": source.occurred_on.isoformat(),
                "description": source.description, "amount": signed,
            }, payloads)
            if effect_issue:
                issues.append(effect_issue)

    # Read actual statement balances in this transaction; never trust projection
    # billing flags or a separate connection's possibly stale view at close time.
    statements = [dict(row) for row in (await session.execute(text(
        "SELECT id, transaction_date, amount, balance FROM bank_statements "
        "WHERE company_id = :company AND transaction_date >= :start AND transaction_date < :end"
    ), {"company": company_id, "start": month_start.isoformat(), "end": next_month.isoformat()})).mappings()]
    source_keys = {source.external_key for source in sources}
    for statement in statements:
        if statement["id"] not in source_keys:
            issues.append({"statementId": statement["id"], "type": "missing_source", "reason": "银行流水缺少有效权威来源记录"})
    raw_bank_lines = (await session.execute(select(JournalLine, JournalEntry).join(
        JournalEntry, JournalEntry.id == JournalLine.journal_entry_id,
    ).where(
        JournalEntry.company_id == company_id, JournalLine.company_id == company_id,
        JournalEntry.period_start <= month_start, JournalEntry.status.in_(("posted", "reversed")),
        JournalLine.account_code == "1002",
    ))).all()
    # ponytail: 仅归档不记账条目不参与银行对公存款余额核对；
    # 冲销对（红字凭证 + 被冲销原凭证）的 1002 行相互抵消，不作为"未关联流水"问题上报
    reversal_ids = {
        str(entry.reversal_of_id) for _, entry in raw_bank_lines
        if entry.entry_type == "reversal" and entry.reversal_of_id
    }
    bank_lines = [
        (line, entry) for line, entry in raw_bank_lines
        if not ((entry.metadata_json or {}).get("is_archive_only") or (entry.metadata_json or {}).get("isArchiveOnly"))
        and entry.entry_type != "reversal" and str(entry.id) not in reversal_ids
    ]
    balance = sum((money(line.amount) * (1 if line.direction == "debit" else -1) for line, _ in bank_lines), ZERO)
    for line, entry in bank_lines:
        if entry.period_start == month_start and entry.entry_type != "opening" and entry.id not in by_entry:
            issues.append({"entryId": entry.id, "type": "book_only", "reason": "银行存款分录未关联银行流水"})
    reconciliation = build_balance_reconciliation(statements, balance)
    if reconciliation["has_data"]:
        if not reconciliation["flow_balanced"]:
            issues.append({"type": "statement_balance_mismatch", "reason": "流水金额与银行期末余额不连续"})
        if abs(reconciliation["book_difference"]) > RECONCILIATION_AMOUNT_TOLERANCE:
            issues.append({"type": "bank_balance_mismatch", "reason": "账面银行存款与银行期末余额不一致", "difference": money_text(reconciliation["book_difference"])})
    return {"issues": issues, "issueCount": len(issues), "balanceReconciliation": reconciliation}
