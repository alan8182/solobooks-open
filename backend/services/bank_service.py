"""Bank reconciliation service (async SQLite)."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import re
from datetime import date
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.database import get_conn
from services.account_mapper import get_voucher_hint
from services.audit_service import log_action
from core.money import ZERO, money, money_text
from services.json_utils import json_dumps
from db.models import SourceTransaction

RECONCILIATION_AMOUNT_TOLERANCE = Decimal("0.01")

_NULL_LIKE_DESCRIPTION_VALUES = {"", "null", "undefined", "none", "nil", "-"}


def _clean_description_value(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return "" if text.lower() in _NULL_LIKE_DESCRIPTION_VALUES else text


def _description_semantic_key(value: str) -> str:
    value = re.sub(r"^\s*(?:附言|备注|用途)\s*[:：]\s*", "", value, flags=re.I)
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).lower()


def normalize_statement_description(description: str) -> str:
    """Normalize imported descriptions and remove repeated summary/comment text."""
    text = _clean_description_value(description)
    if not text:
        return "-"

    counterparty = ""
    match = re.search(r"\s*\(对方\s*[:：]\s*(.*?)\)\s*$", text)
    if match:
        name = _clean_description_value(match.group(1))
        counterparty = f"(对方: {name})" if name else ""
        text = text[:match.start()].strip()

    output: List[str] = []
    seen: set[str] = set()
    for raw_segment in re.split(r"[|｜]", text):
        segment = _clean_description_value(raw_segment)
        if not segment:
            continue
        comment = re.match(r"^\s*(?:附言|备注|用途)\s*[:：]\s*(.*)$", segment, flags=re.I)
        body = _clean_description_value(comment.group(1) if comment else segment)
        key = _description_semantic_key(body)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(f"附言: {body}" if comment else body)

    normalized = " | ".join(output)
    return " ".join(part for part in (normalized, counterparty) if part) or "-"


def build_balance_reconciliation(
    statements: List[Dict[str, Any]],
    book_bank_balance: Any,
) -> Dict[str, Any]:
    """Reconcile statement continuity and the book's 1002 ending balance."""
    # Bank exports are imported newest-first. Reversing the generated IDs within
    # each date restores the running-balance order for same-day transactions.
    ordered = sorted(statements, key=lambda item: str(item.get("id") or ""), reverse=True)
    ordered.sort(key=lambda item: str(item.get("transaction_date") or ""))
    if not ordered:
        return {
            "has_data": False,
            "opening_balance": ZERO,
            "income_total": ZERO,
            "expense_total": ZERO,
            "calculated_closing_balance": ZERO,
            "bank_closing_balance": ZERO,
            "flow_difference": ZERO,
            "book_bank_balance": money(book_bank_balance),
            "book_difference": money(book_bank_balance),
            "flow_balanced": False,
        }

    first = ordered[0]
    amounts = [safe_money(item.get("amount")) for item in ordered]
    opening_balance = safe_money(first.get("balance")) - amounts[0]
    income_total = sum((amount for amount in amounts if amount > ZERO), ZERO)
    expense_total = abs(sum((amount for amount in amounts if amount < ZERO), ZERO))
    calculated_closing = opening_balance + income_total - expense_total
    bank_closing = safe_money(ordered[-1].get("balance"))
    flow_difference = bank_closing - calculated_closing
    book_difference = bank_closing - book_bank_balance
    return {
        "has_data": True,
        "opening_balance": money(opening_balance),
        "income_total": money(income_total),
        "expense_total": money(expense_total),
        "calculated_closing_balance": money(calculated_closing),
        "bank_closing_balance": money(bank_closing),
        "flow_difference": money(flow_difference),
        "book_bank_balance": money(book_bank_balance),
        "book_difference": money(book_difference),
        "flow_balanced": abs(flow_difference) <= RECONCILIATION_AMOUNT_TOLERANCE,
    }

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _direction_matches_statement(stmt_amount: Any, direction: str) -> bool:
    """Return whether an entry direction can reconcile to a statement sign."""
    stmt_amount = safe_money(stmt_amount)
    if stmt_amount > ZERO:
        return direction in ("income", "owner_transfer")
    if stmt_amount < ZERO:
        return direction in ("expense", "owner_transfer")
    return True


def _fallback_classification_for_statement(stmt: Dict[str, Any], confidence: float = 0.3) -> Dict[str, Any]:
    stmt_amount = safe_money(stmt.get("amount"))
    is_income = stmt_amount > ZERO
    return {
        "direction": "income" if is_income else "expense",
        "category": "其他业务收入" if is_income else "其他支出",
        "accountCode": "6051" if is_income else "5699",
        "accountName": "其他业务收入" if is_income else "管理费用-其他",
        "title": stmt.get("description", ""),
        "confidence": confidence,
        "reason": "无法自动分类，已按流水收支方向生成草稿",
    }


def _ensure_classification_direction(stmt: Dict[str, Any], classification: Dict[str, Any]) -> Dict[str, Any]:
    stmt_amount = safe_money(stmt.get("amount"))
    direction = str(classification.get("direction") or "")
    if _direction_matches_statement(stmt_amount, direction):
        return classification

    fallback = _fallback_classification_for_statement(stmt)
    original_reason = str(classification.get("reason") or "").strip()
    if original_reason:
        fallback["reason"] = f"{original_reason}; 已按流水收支方向校正"
    return fallback


def _bank_effect_from_voucher(voucher: Dict[str, Any]) -> Decimal:
    """Return the net 1002 bank impact: debit positive, credit negative."""
    bank_effect = ZERO
    for line in voucher.get("debit", []) or []:
        if line.get("accountCode") == "1002":
            bank_effect += safe_money(line.get("amount"))
    for line in voucher.get("credit", []) or []:
        if line.get("accountCode") == "1002":
            bank_effect -= safe_money(line.get("amount"))
    return money(bank_effect)


def _account_effect_from_voucher(voucher: Dict[str, Any], account_code: str) -> Decimal:
    """Return one account's signed debit-positive, credit-negative effect."""
    effect = ZERO
    for line in voucher.get("debit", []) or []:
        if line.get("accountCode") == account_code:
            effect += safe_money(line.get("amount"))
    for line in voucher.get("credit", []) or []:
        if line.get("accountCode") == account_code:
            effect -= safe_money(line.get("amount"))
    return money(effect)

def _entry_bank_effect_from_payload(payload: Dict[str, Any]) -> Decimal:
    """Return the signed bank impact represented by an entry payload."""
    voucher = payload.get("voucherLines") or {}
    if voucher:
        return _bank_effect_from_voucher(voucher)

    amount = safe_money(payload.get("amount"))
    direction = str(payload.get("direction") or "").strip()
    category = str(payload.get("category") or "").strip()

    if direction == "income":
        return abs(amount)
    if direction == "expense":
        return -abs(amount)
    if direction == "owner_transfer":
        if amount < 0 or category in ("股东取现", "老板取现"):
            return -abs(amount)
        return abs(amount)
    return amount


def _entry_group_reconciliation_effect(payload: Dict[str, Any]) -> Decimal:
    """Return the cash/settlement effect used when grouping entries to one statement.

    WeChat merchant transactions accrue through 1012 (微信支付待结算) before
    the net receipt reaches the bank, so a gross receipt plus its fee must be
    reconciled from the clearing-account net effect.
    """
    voucher = payload.get("voucherLines") or {}
    if not voucher:
        return _entry_bank_effect_from_payload(payload)
    bank_effect = _bank_effect_from_voucher(voucher)
    if bank_effect != ZERO:
        return bank_effect
    settlement_effect = _account_effect_from_voucher(voucher, "1012")
    if settlement_effect != ZERO:
        return settlement_effect
    return bank_effect


def _entry_direction_for_bank_effect(payload: Dict[str, Any], bank_effect: Decimal) -> str:
    direction = str(payload.get("direction") or "").strip()
    if direction == "owner_transfer":
        return "owner_transfer"
    if bank_effect > 0:
        return "income"
    if bank_effect < 0:
        return "expense"
    return direction


def _amount_difference(statement_amount: Any, entry_bank_effect: Any) -> Decimal:
    return money(safe_money(statement_amount) - safe_money(entry_bank_effect))


def build_reconciliation_check(statement_amount: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    statement_amount = safe_money(statement_amount)
    entry_bank_effect = _entry_bank_effect_from_payload(payload)
    direction = _entry_direction_for_bank_effect(payload, entry_bank_effect)
    difference = _amount_difference(statement_amount, entry_bank_effect)
    sign_matches = (
        abs(statement_amount) <= RECONCILIATION_AMOUNT_TOLERANCE
        or abs(entry_bank_effect) <= RECONCILIATION_AMOUNT_TOLERANCE
        or (statement_amount > 0 and entry_bank_effect > 0)
        or (statement_amount < 0 and entry_bank_effect < 0)
    )
    return {
        "statementAmount": money(statement_amount),
        "entryBankEffect": money(entry_bank_effect),
        "difference": difference,
        "amountMatches": abs(difference) <= RECONCILIATION_AMOUNT_TOLERANCE,
        "directionMatches": sign_matches and _direction_matches_statement(statement_amount, direction),
        "direction": direction,
    }


def build_group_reconciliation_check(
    statement_amount: Any,
    payloads: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Validate one bank statement against the aggregate bank effect of many entries."""
    statement_amount = safe_money(statement_amount)
    direct_bank_effect = money(sum((
        _entry_bank_effect_from_payload(payload)
        for payload in payloads
    ), ZERO))
    entry_bank_effect = direct_bank_effect if direct_bank_effect != ZERO else money(sum((
        _account_effect_from_voucher(payload.get("voucherLines") or {}, "1012")
        for payload in payloads
    ), ZERO))
    difference = _amount_difference(statement_amount, entry_bank_effect)
    direction_matches = (
        abs(statement_amount) <= RECONCILIATION_AMOUNT_TOLERANCE
        or abs(entry_bank_effect) <= RECONCILIATION_AMOUNT_TOLERANCE
        or (statement_amount > 0 and entry_bank_effect > 0)
        or (statement_amount < 0 and entry_bank_effect < 0)
    )
    return {
        "statementAmount": money(statement_amount),
        "entryBankEffect": entry_bank_effect,
        "difference": difference,
        "amountMatches": abs(difference) <= RECONCILIATION_AMOUNT_TOLERANCE,
        "directionMatches": direction_matches,
        "entryEffects": [
            {
                "entryId": str(payload.get("id") or ""),
                "title": str(payload.get("title") or ""),
                "bankEffect": money(_entry_group_reconciliation_effect(payload)),
            }
            for payload in payloads
        ],
    }


def diagnose_matched_statement_effect(
    statement: Dict[str, Any],
    payloads: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Explain why a confirmed match does not reproduce the bank's 1002 effect."""
    statement_amount = safe_money(statement.get("amount"))
    bank_effect = money(sum((
        _bank_effect_from_voucher(payload.get("voucherLines") or {})
        for payload in payloads
    ), ZERO))
    difference = money(statement_amount - bank_effect)
    if abs(difference) <= RECONCILIATION_AMOUNT_TOLERANCE:
        return None

    settlement_effect = money(sum((
        _account_effect_from_voucher(payload.get("voucherLines") or {}, "1012")
        for payload in payloads
    ), ZERO))
    entry_ids = [str(payload.get("id") or "") for payload in payloads if payload.get("id")]
    titles = [str(payload.get("title") or "") for payload in payloads if payload.get("title")]

    issue_type = "bank_effect_mismatch"
    reason = "已匹配账目的银行存款影响与流水金额不一致"
    repairable = False
    repair_label = "查看账目"
    repair_description = "请核对匹配账目的凭证科目和金额"

    if (
        abs(bank_effect) <= RECONCILIATION_AMOUNT_TOLERANCE
        and abs(settlement_effect - statement_amount) <= RECONCILIATION_AMOUNT_TOLERANCE
    ):
        issue_type = "missing_settlement_voucher"
        reason = "缺少微信支付待结算转入银行存款的结算凭证"
        repairable = True
        repair_label = "生成结算凭证"
        repair_description = (
            f"生成银行存款与微信支付待结算之间的 {abs(statement_amount):.2f} 元结算凭证"
        )
    elif abs(bank_effect + statement_amount) <= RECONCILIATION_AMOUNT_TOLERANCE:
        auto_entry = len(payloads) == 1 and (
            str(payloads[0].get("source") or "") == "bank_import"
            or str(payloads[0].get("id") or "").startswith("bank-")
        )
        issue_type = "reversed_bank_direction"
        reason = "银行存款方向与流水方向相反"
        repairable = auto_entry
        repair_label = "修正凭证方向" if auto_entry else "查看账目"
        repair_description = "交换该自动生成凭证的借贷方向，使银行存款方向与流水一致"

    return {
        "statementId": str(statement.get("id") or ""),
        "date": str(statement.get("transaction_date") or "")[:10],
        "description": str(statement.get("description") or ""),
        "statementAmount": statement_amount,
        "bankEffect": bank_effect,
        "settlementEffect": settlement_effect,
        "difference": difference,
        "type": issue_type,
        "reason": reason,
        "repairable": repairable,
        "repairLabel": repair_label,
        "repairDescription": repair_description,
        "entryIds": entry_ids,
        "entryTitles": titles,
    }

async def _ensure_schema() -> None:
    conn = await get_conn()
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS bank_statements ("
            "  id TEXT PRIMARY KEY,"
            "  company_id TEXT NOT NULL,"
            "  transaction_date TEXT NOT NULL,"
            "  description TEXT NOT NULL,"
            "  amount NUMERIC(20,2) NOT NULL,"
            "  balance NUMERIC(20,2) NOT NULL DEFAULT 0,"
            "  statement_id TEXT NOT NULL,"
            "  matched_entry_id TEXT,"
            "  description_normalized INTEGER NOT NULL DEFAULT 0,"
            "  created_at TEXT NOT NULL"
            ")"
        )
        columns = await (await conn.execute("PRAGMA table_info(bank_statements)")).fetchall()
        if "description_normalized" not in {row["name"] for row in columns}:
            await conn.execute(
                "ALTER TABLE bank_statements "
                "ADD COLUMN description_normalized INTEGER NOT NULL DEFAULT 0"
            )
        cursor = await conn.execute(
            "SELECT id, description FROM bank_statements WHERE description_normalized = 0"
        )
        for row in await cursor.fetchall():
            await conn.execute(
                "UPDATE bank_statements SET description = ?, description_normalized = 1 WHERE id = ?",
                (normalize_statement_description(row["description"]), row["id"]),
            )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bank_company ON bank_statements(company_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bank_statement ON bank_statements(statement_id)"
        )
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS bank_statement_matches ("
            "  statement_id TEXT NOT NULL,"
            "  company_id TEXT NOT NULL,"
            "  entry_id TEXT NOT NULL,"
            "  previous_billing_status TEXT,"
            "  previous_payment_date TEXT,"
            "  created_at TEXT NOT NULL,"
            "  PRIMARY KEY (statement_id, entry_id)"
            ")"
        )
        match_columns = await (await conn.execute("PRAGMA table_info(bank_statement_matches)")).fetchall()
        added_payment_date_column = False
        if "previous_payment_date" not in {row["name"] for row in match_columns}:
            await conn.execute(
                "ALTER TABLE bank_statement_matches ADD COLUMN previous_payment_date TEXT"
            )
            added_payment_date_column = True
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_bank_match_entry "
            "ON bank_statement_matches(company_id, entry_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bank_match_statement "
            "ON bank_statement_matches(company_id, statement_id)"
        )
        # Backfill legacy one-to-one links. The unique entry index intentionally
        # keeps corrupt duplicate legacy links visible to the health checker.
        await conn.execute(
            "INSERT OR IGNORE INTO bank_statement_matches "
            "(statement_id, company_id, entry_id, previous_billing_status, previous_payment_date, created_at) "
            "SELECT id, company_id, matched_entry_id, NULL, NULL, created_at "
            "FROM bank_statements "
            "WHERE matched_entry_id IS NOT NULL AND matched_entry_id != ''"
        )
        # Existing confirmed matches also receive the bank transaction date.
        if added_payment_date_column:
            entries_table = await (
                await conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'entries'"
                )
            ).fetchone()
            if entries_table:
                cursor = await conn.execute(
                    "SELECT m.entry_id, s.transaction_date, e.payload "
                    "FROM bank_statement_matches m "
                    "JOIN bank_statements s ON s.id = m.statement_id AND s.company_id = m.company_id "
                    "JOIN entries e ON e.id = m.entry_id AND e.company_id = m.company_id"
                )
                for row in await cursor.fetchall():
                    try:
                        payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(payload, dict) or payload.get("paymentDate"):
                        continue
                    payload["paymentDate"] = str(row["transaction_date"] or "")[:10]
                    await conn.execute(
                        "UPDATE entries SET payload = ? WHERE id = ?",
                        (json_dumps(payload, ensure_ascii=False), row["entry_id"]),
                    )
        await conn.commit()
    finally:
        await conn.close()


async def ensure_bank_schema() -> None:
    """Public schema guard for reconciliation mutations."""
    await _ensure_schema()


async def get_matched_entry_ids(company_id: str) -> set[str]:
    """Return every entry linked to a statement, including legacy primary links."""
    await _ensure_schema()
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT entry_id FROM bank_statement_matches WHERE company_id = ?",
            (company_id,),
        )
        matched = {str(row["entry_id"]) for row in await cursor.fetchall() if row["entry_id"]}
        cursor = await conn.execute(
            "SELECT matched_entry_id FROM bank_statements "
            "WHERE company_id = ? AND matched_entry_id IS NOT NULL AND matched_entry_id != ''",
            (company_id,),
        )
        matched.update(str(row["matched_entry_id"]) for row in await cursor.fetchall() if row["matched_entry_id"])
        return matched
    finally:
        await conn.close()


def safe_money(val: Any) -> Decimal:
    if val is None or val == "":
        return ZERO
    try:
        cleaned = str(val).replace(",", "").strip()
        return money(cleaned)
    except (ValueError, TypeError):
        return ZERO


async def import_statements(company_id: str, csv_content: str, statement_id: str = "") -> dict:
    """Import bank statements from CSV content.

    Expected CSV columns: date, description, amount, balance
    """
    await _ensure_schema()
    now = _now_iso()
    imported = 0
    skipped = 0
    errors = 0

    reader = csv.DictReader(io.StringIO(csv_content))
    conn = await get_conn()
    try:
        for row in reader:
            try:
                txn_date = row.get("date", "").strip()
                desc = normalize_statement_description(row.get("description", ""))
                amount = safe_money(row.get("amount"))
                balance = safe_money(row.get("balance"))

                # Check for duplicates (same date, desc, amount — balance varies by statement)
                cursor = await conn.execute(
                    "SELECT 1 FROM bank_statements WHERE company_id = ? AND transaction_date = ? AND description = ? AND amount = ? LIMIT 1",
                    (company_id, txn_date, desc, amount)
                )
                if await cursor.fetchone():
                    skipped += 1
                    continue

                sid = f"bs-{company_id}-{int(datetime.now(timezone.utc).timestamp() * 1000000)}-{imported}"

                await conn.execute(
                    "INSERT INTO bank_statements "
                    "(id, company_id, transaction_date, description, amount, balance, statement_id, description_normalized, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                    (sid, company_id, txn_date, desc, amount, balance, statement_id or sid, now),
                )
                imported += 1
            except (ValueError, KeyError):
                errors += 1

        if imported > 0:
            await conn.commit()
    finally:
        await conn.close()

    return {"imported": imported, "skipped": skipped, "errors": errors, "statement_id": statement_id}


async def import_statements_json(company_id: str, rows: List[Dict[str, Any]], statement_id: str = "") -> dict:
    """Import bank statements from parsed JSON rows (frontend Excel parsing).

    Expected fields: transaction_date, description, amount, balance
    """
    await _ensure_schema()
    now = _now_iso()
    imported = 0
    skipped = 0
    errors = 0

    conn = await get_conn()
    try:
        for row in rows:
            try:
                txn_date = str(row.get("transaction_date") or row.get("date") or "").strip()
                desc = normalize_statement_description(
                    str(row.get("description") or row.get("desc") or row.get("摘要") or "")
                )
                amount = safe_money(row.get("amount") or row.get("金额"))
                balance = safe_money(row.get("balance") or row.get("余额"))

                # Check for duplicates (same date, desc, amount — balance varies by statement)
                cursor = await conn.execute(
                    "SELECT 1 FROM bank_statements WHERE company_id = ? AND transaction_date = ? AND description = ? AND amount = ? LIMIT 1",
                    (company_id, txn_date, desc, amount)
                )
                if await cursor.fetchone():
                    skipped += 1
                    continue

                sid = f"bs-{company_id}-{int(datetime.now(timezone.utc).timestamp() * 1000000)}-{imported}"

                await conn.execute(
                    "INSERT INTO bank_statements "
                    "(id, company_id, transaction_date, description, amount, balance, statement_id, description_normalized, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                    (sid, company_id, txn_date, desc, amount, balance, statement_id or sid, now),
                )
                imported += 1
            except (ValueError, KeyError, TypeError):
                errors += 1

        if imported > 0:
            await conn.commit()
    finally:
        await conn.close()

    return {"imported": imported, "skipped": skipped, "errors": errors, "statement_id": statement_id}


async def import_statements_core(
    session: AsyncSession,
    *,
    company_id: str,
    user_id: str,
    rows: List[Dict[str, Any]],
    statement_id: str = "",
    request_id: str,
) -> dict:
    """Atomically persist bank rows, source facts and audit data in PostgreSQL.

    The legacy import helpers above remain for SQLite migration fixtures. All HTTP
    imports use this function so a failed source/audit write rolls back the bank row.
    """
    from services.accounting_core_service import ensure_tenant, record_audit

    await ensure_tenant(session, company_id, user_id)
    imported = 0
    skipped = 0
    errors = 0
    batch_id = statement_id.strip() or f"bank-batch-{company_id}-{int(datetime.now(timezone.utc).timestamp() * 1000000)}"
    now = _now_iso()

    for row in rows:
        try:
            txn_date_text = str(row.get("transaction_date") or row.get("date") or "").strip()[:10]
            txn_date = date.fromisoformat(txn_date_text)
            desc = normalize_statement_description(
                str(row.get("description") or row.get("desc") or row.get("摘要") or "")
            )
            signed_amount = safe_money(row.get("amount") if row.get("amount") is not None else row.get("金额"))
            balance = safe_money(row.get("balance") if row.get("balance") is not None else row.get("余额"))
            duplicate = await session.execute(
                text(
                    "SELECT id FROM bank_statements WHERE company_id = :company_id "
                    "AND transaction_date = :transaction_date AND description = :description "
                    "AND amount = :amount LIMIT 1"
                ),
                {
                    "company_id": company_id,
                    "transaction_date": txn_date_text,
                    "description": desc,
                    "amount": money_text(signed_amount),
                },
            )
            if duplicate.first() is not None:
                skipped += 1
                continue

            sid = f"bs-{company_id}-{int(datetime.now(timezone.utc).timestamp() * 1000000)}-{imported}"
            await session.execute(
                text(
                    "INSERT INTO bank_statements "
                    "(id, company_id, transaction_date, description, amount, balance, statement_id, "
                    "description_normalized, created_at) VALUES "
                    "(:id, :company_id, :transaction_date, :description, :amount, :balance, "
                    ":statement_id, 1, :created_at)"
                ),
                {
                    "id": sid,
                    "company_id": company_id,
                    "transaction_date": txn_date_text,
                    "description": desc,
                    "amount": money_text(signed_amount),
                    "balance": money_text(balance),
                    "statement_id": batch_id,
                    "created_at": now,
                },
            )
            session.add(
                SourceTransaction(
                    id=f"source-{sid}",
                    company_id=company_id,
                    source_type="bank",
                    external_key=sid,
                    occurred_on=txn_date,
                    description=desc or "银行流水",
                    amount=abs(signed_amount),
                    currency="CNY",
                    original_amount=abs(signed_amount),
                    exchange_rate=None,
                    raw_payload={
                        "bankStatementId": sid,
                        "statementBatchId": batch_id,
                        "signedAmount": money_text(signed_amount),
                        "balance": money_text(balance),
                        "original": json.loads(json_dumps(row)),
                    },
                    status="candidate",
                    created_by=user_id,
                )
            )
            imported += 1
        except (ValueError, TypeError, KeyError):
            errors += 1

    await session.flush()
    if imported:
        await record_audit(
            session,
            company_id=company_id,
            user_id=user_id,
            action="import_bank_statements",
            entity_type="bank_statement_batch",
            entity_id=batch_id,
            request_id=request_id,
            after={"imported": imported, "skipped": skipped, "errors": errors},
        )
    return {"imported": imported, "skipped": skipped, "errors": errors, "statement_id": batch_id}


async def get_statements(company_id: str, statement_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List bank statements with match status."""
    await _ensure_schema()
    conn = await get_conn()
    try:
        if statement_id:
            cursor = await conn.execute(
                "SELECT * FROM bank_statements WHERE company_id = ? AND statement_id = ? ORDER BY transaction_date ASC",
                (company_id, statement_id),
            )
        else:
            cursor = await conn.execute(
                "SELECT * FROM bank_statements WHERE company_id = ? ORDER BY transaction_date ASC",
                (company_id,),
            )
        rows = [dict(r) for r in await cursor.fetchall()]
        cursor = await conn.execute(
            "SELECT statement_id, entry_id FROM bank_statement_matches "
            "WHERE company_id = ? ORDER BY created_at ASC, statement_id ASC, entry_id ASC",
            (company_id,),
        )
        match_map: Dict[str, List[str]] = {}
        for match_row in await cursor.fetchall():
            match_map.setdefault(str(match_row["statement_id"]), []).append(str(match_row["entry_id"]))

        for row in rows:
            ids = list(match_map.get(str(row["id"]), []))
            legacy_id = str(row.get("matched_entry_id") or "").strip()
            if legacy_id:
                # The primary match is part of the API contract and must stay
                # first even when the link rows share the same timestamp.
                ids = [legacy_id, *(entry_id for entry_id in ids if entry_id != legacy_id)]
            row["matched_entry_ids"] = ids
        return rows
    finally:
        await conn.close()


def _has_text_similarity(desc: str, title: str) -> bool:
    """如果金额不完全相等，检测摘要和标题的相似度，防止将不相关的流水匹配在一起"""
    if not desc or not title:
        return False
    import re
    clean_desc = re.sub(r"[^\w\u4e00-\u9fa5]", "", desc)
    clean_title = re.sub(r"[^\w\u4e00-\u9fa5]", "", title)
    
    # 提取其中的汉字
    desc_han = set(re.findall(r"[\u4e00-\u9fa5]", clean_desc))
    title_han = set(re.findall(r"[\u4e00-\u9fa5]", clean_title))
    
    # 求汉字交集
    intersect = desc_han.intersection(title_han)
    # 如果汉字重合大于等于 2 个，或者其中一个完全包含另一个，则认为具有文本相关性
    if len(intersect) >= 2 or clean_desc in clean_title or clean_title in clean_desc:
        return True
        
    # 如果包含共同的订单号（排除单位数）
    desc_nums = set(re.findall(r"\d{3,}", clean_desc))
    title_nums = set(re.findall(r"\d{3,}", clean_title))
    if desc_nums.intersection(title_nums):
        return True
        
    return False


async def auto_match(company_id: str) -> dict:
    """Return rule-based match suggestions without changing reconciliation state."""
    await _ensure_schema()

    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM bank_statements WHERE company_id = ? "
            "AND (matched_entry_id IS NULL OR matched_entry_id = '') "
            "ORDER BY transaction_date ASC",
            (company_id,),
        )
        statements = [dict(r) for r in await cursor.fetchall()]
    finally:
        await conn.close()

    already_matched_ids = await get_matched_entry_ids(company_id)

    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT id, payload FROM entries WHERE company_id = ? ORDER BY created_at ASC",
            (company_id,),
        )
        rows = await cursor.fetchall()
    finally:
        await conn.close()

    entries: List[Dict] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
            entry_id = payload.get("id", row["id"])
            if entry_id in already_matched_ids:
                continue

            voucher = payload.get("voucherLines") or {}
            bank_effect = _bank_effect_from_voucher(voucher) if voucher else ZERO
            if voucher and abs(bank_effect) < RECONCILIATION_AMOUNT_TOLERANCE:
                continue

            orig_amount = bank_effect if voucher else safe_money(payload.get("amount"))
            direction = "income" if bank_effect > 0 else "expense" if bank_effect < 0 else payload.get("direction", "expense")

            if not voucher and orig_amount < 0:
                if direction == "income":
                    direction = "expense"
                elif direction == "expense":
                    direction = "income"

            entries.append({
                "id": entry_id,
                "occurDate": str(payload.get("occurDate", ""))[:10],
                "amount": abs(orig_amount),
                "direction": direction,
                "title": payload.get("title", ""),
                "category": payload.get("category", ""),
            })
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    suggestions = []
    used_entry_ids = set(already_matched_ids)
    for stmt in statements:
        available_entries = [
            e for e in entries
            if e.get("id") not in used_entry_ids
            and _direction_matches_statement(stmt["amount"], e.get("direction", "expense"))
        ]
        rule_match = _try_rule_match(stmt, available_entries)
        if not rule_match:
            continue

        if rule_match.get("type") == "match":
            used_entry_ids.add(rule_match["entry_id"])
        suggestions.append({
            "statementId": stmt["id"],
            "statement": {
                "date": stmt["transaction_date"],
                "description": stmt["description"],
                "amount": stmt["amount"],
                "balance": stmt["balance"],
            },
            "type": rule_match.get("type", "match"),
            "matchedEntryId": rule_match["entry_id"],
            "matchedEntry": rule_match["entry"],
            "confidence": rule_match["confidence"],
            "reason": rule_match["reason"],
            "amountDifference": rule_match.get("amount_difference", 0),
        })

    return {
        "total": len(statements),
        "suggested": len(suggestions),
        "matched": 0,
        "unmatched": len(statements) - len(suggestions),
        "suggestions": suggestions,
    }

async def get_difference_report(company_id: str, month: str) -> dict:
    """Generate difference report: bank has but books don't, and vice versa."""
    await _ensure_schema()

    # Unmatched bank statements
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM bank_statements WHERE company_id = ? AND (matched_entry_id IS NULL OR matched_entry_id = '') "
            "AND substr(transaction_date, 1, 7) = ? ORDER BY transaction_date ASC",
            (company_id, month),
        )
        bank_only = [dict(r) for r in await cursor.fetchall()]
        cursor = await conn.execute(
            "SELECT * FROM bank_statements WHERE company_id = ? "
            "AND substr(transaction_date, 1, 7) = ? ORDER BY transaction_date ASC, id ASC",
            (company_id, month),
        )
        month_statements = [dict(r) for r in await cursor.fetchall()]
        cursor = await conn.execute(
            "SELECT m.statement_id, m.entry_id FROM bank_statement_matches m "
            "JOIN bank_statements s ON s.id = m.statement_id AND s.company_id = m.company_id "
            "WHERE m.company_id = ? AND substr(s.transaction_date, 1, 7) = ? "
            "ORDER BY m.created_at ASC, m.statement_id ASC, m.entry_id ASC",
            (company_id, month),
        )
        month_match_rows = [dict(r) for r in await cursor.fetchall()]
    finally:
        await conn.close()

    # Entries with 1002 that have no match
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT payload FROM entries WHERE company_id = ? ORDER BY created_at ASC",
            (company_id,),
        )
        rows = await cursor.fetchall()
    finally:
        await conn.close()

    matched_entry_ids = await get_matched_entry_ids(company_id)
    book_only = []
    book_bank_balance = ZERO
    entries_by_id: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        try:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        payload_id = str(payload.get("id") or "")
        if payload_id:
            entries_by_id[payload_id] = payload

        voucher = payload.get("voucherLines", {})
        if not voucher:
            direction = payload.get("direction", "expense")
            category = payload.get("category", "其他支出")
            amount = safe_money(payload.get("amount"))
            payer = payload.get("payer", "company_account")
            voucher = get_voucher_hint(direction, category, amount, payer)

        occur_date = payload.get("occurDate", "")
        bank_date = str(payload.get("paymentDate") or occur_date)
        if bank_date and bank_date[:7] <= month:
            book_bank_balance += _account_effect_from_voucher(voucher, "1002")
        if not occur_date.startswith(month):
            continue

        has_bank = any(
            l.get("accountCode") == "1002"
            for entry_type in ("debit", "credit")
            for l in voucher.get(entry_type, [])
        )
        if has_bank:
            if str(payload.get("id") or "") not in matched_entry_ids:
                book_only.append({
                    "date": occur_date,
                    "title": payload.get("title", ""),
                    "amount": money(_account_effect_from_voucher(voucher, "1002")),
                })

    match_ids_by_statement: Dict[str, List[str]] = {}
    for match_row in month_match_rows:
        match_ids_by_statement.setdefault(str(match_row["statement_id"]), []).append(
            str(match_row["entry_id"])
        )
    matched_effect_issues = []
    for statement in month_statements:
        statement_id = str(statement.get("id") or "")
        entry_ids = match_ids_by_statement.get(statement_id, [])
        legacy_id = str(statement.get("matched_entry_id") or "").strip()
        if legacy_id and legacy_id not in entry_ids:
            entry_ids.insert(0, legacy_id)
        payloads = [entries_by_id[entry_id] for entry_id in entry_ids if entry_id in entries_by_id]
        if not payloads:
            continue
        issue = diagnose_matched_statement_effect(statement, payloads)
        if issue:
            matched_effect_issues.append(issue)

    balance_reconciliation = build_balance_reconciliation(month_statements, book_bank_balance)
    balance_reconciliation["fully_reconciled"] = (
        balance_reconciliation["has_data"]
        and balance_reconciliation["flow_balanced"]
        and abs(balance_reconciliation["book_difference"]) <= RECONCILIATION_AMOUNT_TOLERANCE
        and not bank_only
        and not book_only
        and not matched_effect_issues
    )

    return {
        "month": month,
        "bank_only": [
            {"date": s["transaction_date"], "description": s["description"], "amount": s["amount"]}
            for s in bank_only
        ],
        "book_only": book_only,
        "bank_only_count": len(bank_only),
        "book_only_count": len(book_only),
        "matched_effect_issues": matched_effect_issues,
        "matched_effect_issue_count": len(matched_effect_issues),
        "balance_reconciliation": balance_reconciliation,
    }


async def repair_matched_statement_effect(
    company_id: str,
    user_id: str,
    statement_id: str,
) -> Dict[str, Any]:
    """Apply one deterministic repair after re-diagnosing the current match."""
    await _ensure_schema()
    conn = await get_conn()
    repair_entry_id = ""
    repair_type = ""
    try:
        cursor = await conn.execute(
            "SELECT * FROM bank_statements WHERE id = ? AND company_id = ?",
            (statement_id, company_id),
        )
        statement_row = await cursor.fetchone()
        if not statement_row:
            raise ValueError("银行流水不存在")
        statement = dict(statement_row)

        cursor = await conn.execute(
            "SELECT m.entry_id, e.payload FROM bank_statement_matches m "
            "JOIN entries e ON e.id = m.entry_id AND e.company_id = m.company_id "
            "WHERE m.statement_id = ? AND m.company_id = ? "
            "ORDER BY m.created_at ASC, m.statement_id ASC, m.entry_id ASC",
            (statement_id, company_id),
        )
        payloads = []
        for row in await cursor.fetchall():
            try:
                payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(payload, dict):
                payloads.append(payload)

        issue = diagnose_matched_statement_effect(statement, payloads)
        if not issue:
            return {"status": "already_repaired", "statementId": statement_id}
        if not issue["repairable"]:
            raise ValueError("该差异无法安全自动修复，请人工核对凭证")

        now = _now_iso()
        statement_amount = safe_money(statement["amount"])
        transaction_date = str(statement["transaction_date"] or "")[:10]
        repair_type = str(issue["type"])

        if repair_type == "missing_settlement_voucher":
            repair_entry_id = f"bank-settlement-{statement_id}"
            amount = abs(statement_amount)
            if statement_amount > 0:
                voucher_lines = {
                    "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": amount}],
                    "credit": [{"accountCode": "1012", "accountName": "微信支付待结算", "amount": amount}],
                }
                direction = "income"
            else:
                voucher_lines = {
                    "debit": [{"accountCode": "1012", "accountName": "微信支付待结算", "amount": amount}],
                    "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": amount}],
                }
                direction = "expense"
            payload = {
                "id": repair_entry_id,
                "companyId": company_id,
                "company_id": company_id,
                "userId": user_id,
                "user_id": user_id,
                "title": f"微信支付结算到账-{transaction_date}",
                "amount": amount,
                "direction": direction,
                "category": "微信支付结算",
                "occurDate": transaction_date,
                "paymentDate": transaction_date,
                "billingStatus": "reconciled",
                "source": "bank_reconciliation_repair",
                "reconciliationType": "wechat_settlement",
                "linkedStatementId": statement_id,
                "voucherLines": voucher_lines,
                "created_at": now,
                "updated_at": now,
            }
            await conn.execute(
                "INSERT INTO entries (id, user_id, company_id, payload, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at",
                (repair_entry_id, user_id, company_id, json_dumps(payload, ensure_ascii=False), now, now),
            )
            await conn.execute(
                "INSERT OR IGNORE INTO bank_statement_matches "
                "(statement_id, company_id, entry_id, previous_billing_status, previous_payment_date, created_at) "
                "VALUES (?, ?, ?, NULL, NULL, ?)",
                (statement_id, company_id, repair_entry_id, now),
            )
        elif repair_type == "reversed_bank_direction":
            if len(payloads) != 1:
                raise ValueError("该差异涉及多笔账目，无法安全自动修正方向")
            payload = payloads[0]
            repair_entry_id = str(payload.get("id") or "")
            voucher = payload.get("voucherLines") or {}
            corrected_voucher = {
                "debit": list(voucher.get("credit") or []),
                "credit": list(voucher.get("debit") or []),
            }
            payload = {
                **payload,
                "direction": "income" if statement_amount > 0 else "expense",
                "paymentDate": transaction_date,
                "voucherLines": corrected_voucher,
                "reconciliationRepair": {
                    "type": repair_type,
                    "statementId": statement_id,
                    "repairedAt": now,
                },
                "updated_at": now,
            }
            await conn.execute(
                "UPDATE entries SET payload = ?, updated_at = ? WHERE id = ? AND company_id = ?",
                (json_dumps(payload, ensure_ascii=False), now, repair_entry_id, company_id),
            )
        else:
            raise ValueError("暂不支持自动修复该差异")

        await conn.commit()
    finally:
        await conn.close()

    await log_action(
        company_id,
        user_id,
        "repair",
        "bank_reconciliation",
        statement_id,
        {"type": repair_type, "entryId": repair_entry_id},
    )
    return {
        "status": "repaired",
        "statementId": statement_id,
        "repairType": repair_type,
        "entryId": repair_entry_id,
    }


async def get_reconciliation_health(company_id: str, month: str) -> dict:
    """Return read-only reconciliation integrity issues for a month."""
    target_month = str(month or "").strip()
    all_statements = [
        statement
        for statement in await get_statements(company_id)
        if statement.get("matched_entry_ids")
    ]

    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT id, payload FROM entries WHERE company_id = ?",
            (company_id,),
        )
        entry_rows = await cursor.fetchall()
    finally:
        await conn.close()

    entries_by_id: Dict[str, Dict[str, Any]] = {}
    for row in entry_rows:
        try:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        row_id = str(row["id"] or "")
        payload_id = str(payload.get("id") or row_id)
        entries_by_id[row_id] = payload
        entries_by_id[payload_id] = payload

    def in_month(statement: Dict[str, Any]) -> bool:
        if not target_month:
            return True
        return str(statement.get("transaction_date") or "")[:7] == target_month

    def statement_item(statement: Dict[str, Any], matched_entry_ids: List[str]) -> Dict[str, Any]:
        return {
            "statementId": statement.get("id"),
            "matchedEntryId": matched_entry_ids[0] if matched_entry_ids else "",
            "matchedEntryIds": matched_entry_ids,
            "date": statement.get("transaction_date"),
            "description": statement.get("description"),
            "statementAmount": safe_money(statement.get("amount")),
        }

    broken_links: List[Dict[str, Any]] = []
    amount_mismatches: List[Dict[str, Any]] = []
    direction_mismatches: List[Dict[str, Any]] = []
    duplicate_entry_matches: List[Dict[str, Any]] = []
    pending_confirmations: List[Dict[str, Any]] = []
    statements_by_entry: Dict[str, List[Dict[str, Any]]] = {}

    for statement in all_statements:
        matched_entry_ids = [
            str(entry_id).strip()
            for entry_id in statement.get("matched_entry_ids", [])
            if str(entry_id).strip()
        ]
        if not matched_entry_ids:
            continue
        for entry_id in matched_entry_ids:
            statements_by_entry.setdefault(entry_id, []).append(statement)
        if not in_month(statement):
            continue

        missing_entry_ids = [entry_id for entry_id in matched_entry_ids if entry_id not in entries_by_id]
        if missing_entry_ids:
            broken_links.append({
                **statement_item(statement, matched_entry_ids),
                "missingEntryIds": missing_entry_ids,
            })
            continue

        entries = [entries_by_id[entry_id] for entry_id in matched_entry_ids]
        stmt_amount = safe_money(statement.get("amount"))
        check = build_group_reconciliation_check(stmt_amount, entries)
        item = {
            **statement_item(statement, matched_entry_ids),
            "entryTitle": " + ".join(str(entry.get("title") or "") for entry in entries),
            "entryTitles": [str(entry.get("title") or "") for entry in entries],
            "entryBankEffect": check["entryBankEffect"],
            "difference": check["difference"],
        }
        if not check["directionMatches"]:
            direction_mismatches.append(item)
        if abs(abs(stmt_amount) - abs(check["entryBankEffect"])) > RECONCILIATION_AMOUNT_TOLERANCE:
            amount_mismatches.append(item)
        if any(entry.get("billingStatus") != "reconciled" for entry in entries):
            pending_confirmations.append(item)

    for entry_id, statements in statements_by_entry.items():
        if len(statements) <= 1 or not any(in_month(s) for s in statements):
            continue
        duplicate_entry_matches.append({
            "matchedEntryId": entry_id,
            "statementIds": [s.get("id") for s in statements],
            "dates": [s.get("transaction_date") for s in statements],
            "descriptions": [s.get("description") for s in statements],
        })

    high_risk_count = (
        len(broken_links)
        + len(amount_mismatches)
        + len(direction_mismatches)
        + len(duplicate_entry_matches)
    )
    warning_count = len(pending_confirmations)

    return {
        "month": target_month,
        "broken_links": broken_links,
        "amount_mismatches": amount_mismatches,
        "direction_mismatches": direction_mismatches,
        "duplicate_entry_matches": duplicate_entry_matches,
        "pending_confirmations": pending_confirmations,
        "summary": {
            "highRiskCount": high_risk_count,
            "warningCount": warning_count,
        },
    }

async def get_statement_ids(company_id: str) -> List[str]:
    """Get distinct statement IDs for selection."""
    await _ensure_schema()
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT DISTINCT statement_id FROM bank_statements WHERE company_id = ? ORDER BY statement_id DESC",
            (company_id,),
        )
        rows = await cursor.fetchall()
        return [r["statement_id"] for r in rows]
    finally:
        await conn.close()


async def ai_suggest_matches(company_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """AI-powered matching suggestions for unmatched bank statements.

    Improvements:
    - First tries rule-based matching with direction-aware tiered logic
    - Filters already-matched entries from AI candidates
    - Falls back to AI classification for genuinely unrecorded transactions
    """
    from services.bank_ai_prompts import BANK_MATCH_PROMPT, BANK_CLASSIFY_PROMPT
    from services.llm import safe_chat_text_json

    await _ensure_schema()

    # Get unmatched statements
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM bank_statements WHERE company_id = ? AND (matched_entry_id IS NULL OR matched_entry_id = '') "
            "ORDER BY transaction_date DESC LIMIT ?",
            (company_id, limit),
        )
        statements = [dict(r) for r in await cursor.fetchall()]
    finally:
        await conn.close()

    if not statements:
        return []

    # Get IDs of entries already matched by auto_match (to avoid duplicate suggestions)
    already_matched_ids = await get_matched_entry_ids(company_id)

    # Get recent entries, excluding already-matched ones
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT id, payload FROM entries WHERE company_id = ? ORDER BY updated_at DESC LIMIT 100",
            (company_id,),
        )
        rows = await cursor.fetchall()
    finally:
        await conn.close()

    entries = []
    for row in rows:
        try:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
            entry_id = payload.get("id", row["id"])
            # Skip entries already matched to a bank statement
            if entry_id in already_matched_ids:
                continue

            voucher = payload.get("voucherLines") or {}
            bank_effect = _bank_effect_from_voucher(voucher) if voucher else ZERO
            payer = payload.get("payer") or ""
            is_advancement = payer == "boss_personal" or payer == "employee_personal" or payer.startswith("employee_personal:")
            
            # ponytail: allow advancements (zero bank effect) to match reimbursement outflow
            if voucher and abs(bank_effect) < RECONCILIATION_AMOUNT_TOLERANCE and not is_advancement:
                continue

            orig_amount = safe_money(payload.get("amount")) if is_advancement else (bank_effect if voucher else safe_money(payload.get("amount")))
            direction = "income" if bank_effect > 0 else "expense" if bank_effect < 0 else payload.get("direction", "")

            # 方向修正（负数冲减时对冲方向）
            if not voucher and orig_amount < 0:
                if direction == "income":
                    direction = "expense"
                elif direction == "expense":
                    direction = "income"

            entries.append({
                "id": entry_id,
                "title": payload.get("title", ""),
                "amount": abs(orig_amount),
                "direction": direction,
                "category": payload.get("category", ""),
                "occurDate": payload.get("occurDate", ""),
            })
        except (json.JSONDecodeError, TypeError):
            continue

    suggestions = []
    used_suggested_entry_ids = set(already_matched_ids)
    
    need_ai_statements = []
    for stmt in statements:
        # First try rule-based match (fast path)
        available_entries = [
            e for e in entries
            if e.get("id") not in used_suggested_entry_ids
            and _direction_matches_statement(stmt["amount"], e.get("direction", "expense"))
        ]
        rule_match = _try_rule_match(stmt, available_entries)
        if rule_match:
            if rule_match.get("type") == "match":
                used_suggested_entry_ids.add(rule_match["entry_id"])
            suggestions.append({
                "statementId": stmt["id"],
                "statement": {
                    "date": stmt["transaction_date"],
                    "description": stmt["description"],
                    "amount": stmt["amount"],
                    "balance": stmt["balance"],
                },
                "type": rule_match.get("type", "match"),
                "matchedEntryId": rule_match["entry_id"],
                "matchedEntry": rule_match["entry"],
                "confidence": rule_match["confidence"],
                "reason": rule_match["reason"],
                "amountDifference": rule_match.get("amount_difference", 0),
            })
        else:
            need_ai_statements.append(stmt)

    if need_ai_statements:
        async def _ai_suggest_single(stmt: dict, cand_entries: list) -> dict:
            try:
                stmt_amount = abs(stmt["amount"])
                candidates = [
                    e for e in cand_entries
                    if _direction_matches_statement(stmt["amount"], e.get("direction", "expense"))
                    and abs(abs(e["amount"]) - stmt_amount) < stmt_amount * 0.5 + 10
                ][:10]  # Max 10 candidates for token efficiency

                if candidates:
                    user_msg = f"银行流水：\n- 日期：{stmt['transaction_date']}\n- 摘要：{stmt['description']}\n- 金额：{stmt['amount']}\n\n候选账目记录：\n{json_dumps(candidates, ensure_ascii=False, indent=2)}"
                    
                    # Parallel thread executor for synchronous chat
                    match_result = await asyncio.to_thread(
                        safe_chat_text_json,
                        BANK_MATCH_PROMPT, user_msg,
                        fallback={"matchedEntryId": None, "confidence": 0, "reason": "无法判断"},
                        max_retries=1,
                    )

                    if match_result.get("matchedEntryId") and match_result.get("confidence", 0) >= 0.5:
                        matched_entry = next((e for e in candidates if e["id"] == match_result["matchedEntryId"]), None)
                        if matched_entry:
                            amount_diff = abs(abs(safe_money(matched_entry.get("amount"))) - abs(safe_money(stmt["amount"])))
                            match_type = "match" if amount_diff < RECONCILIATION_AMOUNT_TOLERANCE else "difference"
                            return {
                                "statementId": stmt["id"],
                                "statement": {
                                    "date": stmt["transaction_date"],
                                    "description": stmt["description"],
                                    "amount": stmt["amount"],
                                    "balance": stmt["balance"],
                                },
                                "type": match_type,
                                "matchedEntryId": matched_entry["id"],
                                "matchedEntry": matched_entry,
                                "confidence": match_result["confidence"],
                                "reason": match_result["reason"],
                                "amountDifference": money(amount_diff),
                            }

                classify_msg = f"银行流水：\n- 日期：{stmt['transaction_date']}\n- 摘要：{stmt['description']}\n- 金额：{stmt['amount']}\n- 余额：{stmt['balance']}"
                classify_result = await asyncio.to_thread(
                    safe_chat_text_json,
                    BANK_CLASSIFY_PROMPT, classify_msg,
                    fallback=_fallback_classification_for_statement(stmt),
                    max_retries=1,
                )
                classify_result = _ensure_classification_direction(stmt, classify_result)

                return {
                    "statementId": stmt["id"],
                    "statement": {
                        "date": stmt["transaction_date"],
                        "description": stmt["description"],
                        "amount": stmt["amount"],
                        "balance": stmt["balance"],
                    },
                    "type": "new",
                    "suggestedEntry": classify_result,
                    "confidence": classify_result.get("confidence", 0.5),
                    "reason": classify_result.get("reason", ""),
                }
            except Exception:
                fb = _fallback_classification_for_statement(stmt, confidence=0.2)
                return {
                    "statementId": stmt["id"],
                    "statement": {
                        "date": stmt["transaction_date"],
                        "description": stmt["description"],
                        "amount": stmt["amount"],
                        "balance": stmt["balance"],
                    },
                    "type": "new",
                    "suggestedEntry": fb,
                    "confidence": fb.get("confidence", 0.2),
                    "reason": fb.get("reason", ""),
                }

        # Concurrently gather suggestions
        tasks = [
            _ai_suggest_single(stmt, entries)
            for stmt in need_ai_statements
        ]
        ai_results = await asyncio.gather(*tasks)

        # Merge suggestions with conflict de-duplication
        for res in ai_results:
            if res.get("type") == "match":
                eid = res["matchedEntryId"]
                if eid in used_suggested_entry_ids:
                    # Fallback to new classification if candidate is already taken
                    stmt = {
                        "transaction_date": res["statement"]["date"],
                        "description": res["statement"]["description"],
                        "amount": res["statement"]["amount"],
                        "balance": res["statement"]["balance"],
                        "id": res["statementId"]
                    }
                    fb = _fallback_classification_for_statement(stmt)
                    suggestions.append({
                        "statementId": res["statementId"],
                        "statement": res["statement"],
                        "type": "new",
                        "suggestedEntry": fb,
                        "confidence": 0.3,
                        "reason": "配对推荐已被其他优先流水占用，已自动分类为新凭证",
                    })
                else:
                    used_suggested_entry_ids.add(eid)
                    suggestions.append(res)
            else:
                suggestions.append(res)

    return suggestions


def _try_rule_match(stmt: Dict, entries: List[Dict]) -> Optional[Dict]:
    """Try to match a bank statement against accounting entries using tiered rules.

    Tiers:
    - Tier 1 (score 0-5): Exact amount + date diff ≤ 5 days   → confidence 0.95-1.00
    - Tier 2 (score 100-130): Exact amount + date diff ≤ 30 days → confidence 0.75-0.90
    - Tier 3 (score 200+): Amount diff ≤5% + date diff ≤ 10 days  → confidence 0.65

    Direction-aware: positive statement only matches income; negative matches expense/owner_transfer.
    """
    stmt_amount = safe_money(stmt["amount"])
    stmt_abs = abs(stmt_amount)
    has_date = bool(stmt.get("transaction_date", "").strip())
    stmt_date = None
    if has_date:
        try:
            stmt_date = datetime.strptime(stmt["transaction_date"][:10], "%Y-%m-%d")
        except (ValueError, IndexError):
            pass

    best = None
    best_score = 9999

    for entry in entries:
        # Direction guard
        entry_dir = entry.get("direction", "expense")
        if not _direction_matches_statement(stmt_amount, entry_dir):
            continue

        entry_amount = abs(safe_money(entry.get("amount")))
        amount_diff = abs(stmt_abs - entry_amount)

        if stmt_date is not None:
            try:
                entry_date = datetime.strptime(str(entry.get("occurDate", ""))[:10], "%Y-%m-%d")
            except (ValueError, IndexError, TypeError):
                continue

            date_diff = abs((stmt_date - entry_date).days)

            # Tier 1: exact amount + within 5 days
            if amount_diff < RECONCILIATION_AMOUNT_TOLERANCE and date_diff <= 5:
                score = date_diff
                if score < best_score:
                    best_score = score
                    best = {
                        "entry_id": entry["id"],
                        "entry": entry,
                        "type": "match",
                        "amount_difference": ZERO,
                        "confidence": max(0.95, 1.0 - date_diff * 0.01),
                        "reason": f"金额完全匹配，日期相差{date_diff}天（高置信）",
                    }

            # Tier 2: exact amount + within 30 days
            elif amount_diff < RECONCILIATION_AMOUNT_TOLERANCE and date_diff <= 30:
                score = 100 + date_diff
                if score < best_score:
                    best_score = score
                    best = {
                        "entry_id": entry["id"],
                        "entry": entry,
                        "type": "match",
                        "amount_difference": ZERO,
                        "confidence": max(0.75, 0.90 - date_diff * 0.005),
                        "reason": f"金额完全匹配，日期相差{date_diff}天（发票与付款日期差，中置信）",
                    }

            # Tier 3: close amount within 5% + within 10 days
            # 仅限 100 元以上大额，且通过文本关键词比对，防止乱匹配
            elif stmt_abs >= Decimal("100.00") and amount_diff <= max(stmt_abs * Decimal("0.05"), Decimal("5.00")) and date_diff <= 10:
                if _has_text_similarity(stmt.get("description", ""), entry.get("title", "")):
                    score = 200 + date_diff * 10 + amount_diff
                    if score < best_score:
                        best_score = score
                        best = {
                            "entry_id": entry["id"],
                            "entry": entry,
                            "type": "difference",
                            "amount_difference": money(amount_diff),
                            "confidence": 0.65,
                            "reason": f"金额接近（差额{amount_diff:.2f}元），日期相差{date_diff}天（低置信，建议人工核实）",
                        }
        else:
            # ponytail: 无日期时仅按金额精确匹配，置信度降为 0.8
            if amount_diff < RECONCILIATION_AMOUNT_TOLERANCE:
                score = 500
                if score < best_score:
                    best_score = score
                    best = {
                        "entry_id": entry["id"],
                        "entry": entry,
                        "type": "match",
                        "amount_difference": ZERO,
                        "confidence": 0.80,
                        "reason": "金额完全匹配（流水无日期，建议人工核实）",
                    }

    return best


async def classify_single_statement(company_id: str, statement_id: str, business_context: str = "") -> Optional[Dict[str, Any]]:
    """Classify a single bank statement using LLM on-demand."""
    from services.bank_ai_prompts import BANK_CLASSIFY_PROMPT
    from services.llm import safe_chat_text_json

    await _ensure_schema()
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM bank_statements WHERE company_id = ? AND id = ?",
            (company_id, statement_id),
        )
        stmt = await cursor.fetchone()
        if not stmt:
            return None
        stmt = dict(stmt)
    finally:
        await conn.close()

    context = (business_context or "").strip()
    if safe_money(stmt["amount"]) < ZERO and "研发" in context:
        return {
            "direction": "expense",
            "category": "研发支出-费用化支出",
            "accountCode": "5301",
            "accountName": "研发支出-费用化支出",
            "title": "研发相关支出",
            "confidence": 0.9,
            "reason": "用户补充说明表明该支出用于研发，应优先按研发支出处理",
        }

    classify_msg = f"""银行流水：
- 日期：{stmt['transaction_date']}
- 摘要：{stmt['description']}
- 金额：{stmt['amount']}
- 余额：{stmt['balance']}"""
    if context:
        classify_msg += f"""
- 用户补充说明：{context}

请优先根据用户补充说明判断业务用途；当补充说明与摘要、发票项目或银行流水描述冲突时，以用户补充说明为准。"""

    classify_result = safe_chat_text_json(
        BANK_CLASSIFY_PROMPT, classify_msg,
        fallback={"direction": "expense" if stmt["amount"] < 0 else "income",
                  "category": "其他支出" if stmt["amount"] < 0 else "其他业务收入",
                  "accountCode": "5699" if stmt["amount"] < 0 else "6051",
                  "accountName": "管理费用-其他" if stmt["amount"] < 0 else "其他业务收入",
                  "title": stmt["description"],
                  "confidence": 0.3, "reason": "无法自动分类"},
        max_retries=1,
    )
    return classify_result
