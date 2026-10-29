# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Bank reconciliation service (async SQLite)."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
from datetime import date
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.database import get_conn
from services.account_mapper import get_voucher_hint
from services.audit_service import log_action
from core.errors import AccountingError
from core.money import ZERO, money, money_text
from services.json_utils import json_dumps

WECHAT_CLEARING_CODE = "1012001"

logger = logging.getLogger(__name__)

RECONCILIATION_AMOUNT_TOLERANCE = Decimal("0.01")

_NULL_LIKE_DESCRIPTION_VALUES = {"", "null", "undefined", "none", "nil", "-"}

SALARY_PAYMENT_KEYWORDS = (
    "代发工资", "工资代发", "代发薪资", "代发薪", "薪资", "薪酬",
    "工资", "薪金", "奖金", "代发",
)
PAYROLL_LIABILITY_KEYWORDS = (
    "社保", "社会保险", "公积金", "住房公积金", "社保公积金",
)


def is_salary_payment_description(description: str) -> bool:
    text = str(description or "")
    return any(keyword in text for keyword in SALARY_PAYMENT_KEYWORDS)


def is_payroll_liability_description(description: str) -> bool:
    text = str(description or "")
    return any(keyword in text for keyword in PAYROLL_LIABILITY_KEYWORDS)


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


_NON_RULE_CATEGORIES = {
    "研发支出-费用化支出",
    "工资发放",
    "社保公积金",
    "微信支付结算",
    "支付宝结算",
    "平台提现结算",
    # 退款对冲是上下文相关的（跟具体哪笔付款配对），沉淀成对手方规则
    # 会把以后真正的收入也吞掉
    "往来退款",
}


def extract_statement_counterparty(description: str) -> str:
    """Extract counterparty name from statement description like '... (对方: xxx)'."""
    text = str(description or "").strip()
    match = re.search(r"\(对方\s*[:：]\s*(.+?)\)\s*$", text)
    return match.group(1).strip() if match else ""


def extract_statement_rule_pattern(description: str) -> str:
    """Extract counterparty or cleaned summary key for company category rules."""
    norm = normalize_statement_description(description)
    if not norm or norm == "-":
        return ""
    match = re.search(r"\(对方:\s*(.+?)\)\s*$", norm)
    if match:
        return match.group(1).strip()
    cleaned = re.sub(r"\b\d{4,}\b", "", norm).strip(" |-:")
    return cleaned if len(cleaned) >= 2 else ""


async def save_company_category_rule(
    company_id: str,
    description: str,
    stmt_amount: Any,
    category: str,
) -> Optional[str]:
    """Persist a company-level statement classification rule when user opts in."""
    cat = str(category or "").strip()
    if not cat or cat in _NON_RULE_CATEGORIES:
        return None
    pattern = extract_statement_rule_pattern(description)
    if not pattern:
        return None
    direction = "income" if safe_money(stmt_amount) >= ZERO else "expense"
    await _ensure_schema()
    conn = await get_conn()
    try:
        await conn.execute(
            # SQLite/PG 双方言的 upsert：INSERT OR REPLACE 不是 PG 语法，
            # 兼容层也不会翻译它，必须显式写 ON CONFLICT 冲突目标。
            "INSERT INTO bank_category_rules "
            "(company_id, direction, match_pattern, category, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (company_id, direction, match_pattern) DO UPDATE SET "
            "category = excluded.category, updated_at = excluded.updated_at",
            (company_id, direction, pattern, cat, _now_iso()),
        )
        await conn.commit()
    finally:
        await conn.close()
    return pattern


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


def identify_statement_fast_rule(stmt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Fast rule classifier for routine bank statement records (100% confidence).

    Identifies:
    1. 微信/财付通提现 -> 微信支付结算 (1012001)
    2. 支付宝提现 -> 支付宝结算 (1012002)
    3. 银行存款结息 -> 利息收入 (5603005)
    4. 企业实名打款验证微额 (<= 1.00 RMB) -> 营业外收入 (5301)
    """
    desc = str(stmt.get("description") or "").strip()
    amount = safe_money(stmt.get("amount"))
    date_str = str(stmt.get("transaction_date") or "")[:10]

    # 「退款/退回」是自有资金退回，绝不能进平台结算或营业外收入——
    # 应冲减前期付款挂的往来（见 _detect_refund_pairs），交给记忆规则/LLM。
    is_refund = bool(re.search(r"退款|退回|退还", desc))
    # 微额打款验证走第 4 条 micro_auth，不能被平台关键词截胡
    is_verification = bool(re.search(r"验证|认证|核验|测试", desc))

    # 1. 微信/财付通提现到账（收入方向，平台划转）。
    #    描述形如「财付通」「财付通支付科技有限公司 | 附言: 提现」等，
    #    平台名命中即视为划转；退款/验证两类已显式排除。
    if (
        re.search(r"财付通|微信|WeChat|Tenpay", desc, re.I)
        and amount > ZERO
        and not is_refund
        and not is_verification
    ):
        return {
            "type": "new",
            "category": "微信支付结算",
            "title": f"微信支付提现到账-{date_str}" if date_str else "微信支付提现到账",
            "confidence": 1.0,
            "rule": "wechat_withdrawal",
            "reason": "检测到财付通/微信支付提现到账，自动计入平台划转（借:银行存款 贷:微信支付待结算）",
        }

    # 2. 支付宝提现到账（收入方向，平台划转）
    if (
        re.search(r"支付宝|蚂蚁金服|Alipay", desc, re.I)
        and amount > ZERO
        and not is_refund
        and not is_verification
    ):
        return {
            "type": "new",
            "category": "支付宝结算",
            "title": f"支付宝提现到账-{date_str}" if date_str else "支付宝提现到账",
            "confidence": 1.0,
            "rule": "alipay_withdrawal",
            "reason": "检测到支付宝提现到账，自动计入平台划转（借:银行存款 贷:支付宝待结算）",
        }

    # 3. 银行利息/结息
    if any(k in desc for k in ["结息", "利息", "存款利息"]) and amount > ZERO:
        return {
            "type": "new",
            "category": "利息收入",
            "title": f"银行结息-{date_str}" if date_str else "银行结息",
            "confidence": 1.0,
            "rule": "bank_interest",
            "reason": "检测到银行存款利息，自动计入财务费用-利息收入",
        }

    # 4. 微额收款兜底（<= 1.00 元）：验证/测试类打款，无对价不构成经营收入。
    #    ponytail: 金额极小本身就是强信号——银行摘要常不写「验证」二字；退款已排除，
    #    微信/支付宝/利息在第 1-3 条先拦截，故不再要求关键词。
    if amount > ZERO and amount <= Decimal("1.00") and not is_refund:
        return {
            "type": "new",
            "category": "营业外收入",
            "title": f"微额验证款-{date_str}" if date_str else "微额验证款",
            "confidence": 1.0,
            "rule": "micro_auth",
            "reason": "金额极小（≤1.00 元）且非退款，判定为验证/测试类小额打款，计入营业外收入",
        }

    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def find_duplicate_bank_entries(
    session: AsyncSession,
    company_id: str,
    statement_date: date,
    signed_amount: Decimal,
) -> list[Dict[str, Any]]:
    """找与这笔流水等额同向、日期 ±3 天、且尚未对账的银行存款凭证。

    命中即说明账上已经记过这笔钱：应当配对而不是新建凭证，否则 1002 会重复计入。
    """
    from sqlalchemy import select, text

    from db.models import JournalEntry, JournalLine, ReconciliationLink

    if safe_money(signed_amount) == ZERO:
        return []
    line_direction = "debit" if safe_money(signed_amount) > ZERO else "credit"
    rows = (await session.execute(
        select(JournalEntry.id, JournalEntry.summary, JournalEntry.occurred_on)
        .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
        .where(
            JournalEntry.company_id == company_id,
            JournalEntry.status.in_(("draft", "pending_review", "posted", "reversed")),
            JournalEntry.entry_type == "standard",
            JournalEntry.occurred_on >= statement_date - timedelta(days=3),
            JournalEntry.occurred_on <= statement_date + timedelta(days=3),
            JournalLine.company_id == company_id,
            JournalLine.account_code == "1002",
            JournalLine.direction == line_direction,
            JournalLine.amount == abs(safe_money(signed_amount)),
        )
        .limit(10)
    )).all()
    if not rows:
        return []

    ids = [row[0] for row in rows]
    linked_ids = set((await session.execute(
        select(ReconciliationLink.journal_entry_id).where(
            ReconciliationLink.company_id == company_id,
            ReconciliationLink.status == "confirmed",
            ReconciliationLink.journal_entry_id.in_(ids),
        )
    )).scalars().all())
    legacy_matched_ids = set((await session.execute(
        text(
            "SELECT matched_entry_id FROM bank_statements "
            "WHERE company_id = :company AND matched_entry_id IS NOT NULL"
        ),
        {"company": company_id},
    )).scalars().all())

    return [
        {"id": row[0], "title": row[1], "date": str(row[2])}
        for row in rows
        if row[0] not in linked_ids and row[0] not in legacy_matched_ids
    ]


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
        "accountCode": "5051" if is_income else "5602999",
        "accountName": "其他业务收入" if is_income else "管理费用-其他",
        "title": stmt.get("description", ""),
        "confidence": confidence,
        "reason": "无法自动分类，已按流水收支方向生成草稿",
    }


_DIRECTION_SYNONYMS = {
    "income": "income", "收入": "income", "进账": "income", "收款": "income",
    "转入": "income", "inflow": "income", "credit": "income",
    "expense": "expense", "支出": "expense", "付款": "expense", "转出": "expense",
    "outflow": "expense", "debit": "expense",
    "owner_transfer": "owner_transfer", "ownertransfer": "owner_transfer",
    "股东投入": "owner_transfer", "股东垫付": "owner_transfer", "往来": "owner_transfer",
}


def _normalize_statement_direction(value: Any) -> Optional[str]:
    """把模型返回的方向（中英文/同义词/大小写）归一为 income|expense|owner_transfer。"""
    if value is None:
        return None
    key = str(value).strip().lower().replace("-", "_").replace(" ", "")
    if key in _DIRECTION_SYNONYMS:
        return _DIRECTION_SYNONYMS[key]
    # "owner transfer" 去空格后是 ownertransfer，已在映射表里；其余未知值返回 None
    return None


def _classification_confidence(classification: Dict[str, Any], default: float = 0.5) -> float:
    try:
        value = float(classification.get("confidence"))
    except (TypeError, ValueError):
        return default
    return min(max(value, 0.0), 1.0)


def _ensure_classification_direction(stmt: Dict[str, Any], classification: Dict[str, Any]) -> Dict[str, Any]:
    """校正模型分类的收支方向。

    - 方向缺失/无法识别：方向由流水正负决定，保留模型的分类，置信度封顶 0.8。
    - 方向自相矛盾：整条退回兜底（分类很可能也判错，不能沿用）。
    """
    stmt_amount = safe_money(stmt.get("amount"))
    cleaned = dict(classification or {})
    category = str(cleaned.get("category") or "").strip()
    if not category:
        fallback = _fallback_classification_for_statement(stmt)
        original_reason = str(cleaned.get("reason") or "").strip()
        fallback["reason"] = f"{original_reason}；模型未给出分类" if original_reason else "模型未给出分类，已按流水收支方向生成草稿"
        return fallback

    direction = _normalize_statement_direction(cleaned.get("direction"))
    if direction is None:
        direction = "income" if stmt_amount > ZERO else "expense"
        cleaned["direction"] = direction
        cleaned["confidence"] = min(_classification_confidence(cleaned, 0.8), 0.8)
        reason = str(cleaned.get("reason") or "").strip()
        cleaned["reason"] = f"{reason}（方向按流水收支推断）" if reason else "方向按流水收支推断"
        cleaned.setdefault("title", stmt.get("description", ""))
        return cleaned

    if _direction_matches_statement(stmt_amount, direction):
        cleaned["direction"] = direction
        cleaned["confidence"] = _classification_confidence(cleaned)
        if not str(cleaned.get("title") or "").strip():
            cleaned["title"] = stmt.get("description", "")
        return cleaned

    fallback = _fallback_classification_for_statement(stmt)
    original_reason = str(cleaned.get("reason") or "").strip()
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

    WeChat merchant transactions accrue through the WeChat clearing account (1012001) before
    the net receipt reaches the bank, so a gross receipt plus its fee must be
    reconciled from the clearing-account net effect.
    """
    voucher = payload.get("voucherLines") or {}
    if not voucher:
        return _entry_bank_effect_from_payload(payload)
    bank_effect = _bank_effect_from_voucher(voucher)
    if bank_effect != ZERO:
        return bank_effect
    settlement_effect = _account_effect_from_voucher(voucher, WECHAT_CLEARING_CODE)
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
        _account_effect_from_voucher(payload.get("voucherLines") or {}, WECHAT_CLEARING_CODE)
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
        _account_effect_from_voucher(payload.get("voucherLines") or {}, WECHAT_CLEARING_CODE)
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

_schema_ready = False


async def _ensure_schema() -> None:
    """进程级一次性 schema 守卫。

    建表/补列/历史描述归一化都是幂等迁移动作：首次调用执行，之后整个进程
    短路（此前每个银行接口都重复跑 DDL 判定 + 全表扫 description_normalized=0；
    新写入行在导入时已带 normalized 描述，补记只针对存量数据）。
    PostgreSQL 下这些 DDL 经 database._translate_postgres 翻译为 no-op，
    正式 schema 依赖 Alembic 迁移先行。
    """
    global _schema_ready
    if _schema_ready:
        return
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
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS bank_category_rules ("
            "  company_id TEXT NOT NULL,"
            "  direction TEXT NOT NULL,"
            "  match_pattern TEXT NOT NULL,"
            "  category TEXT NOT NULL,"
            "  updated_at TEXT NOT NULL,"
            "  PRIMARY KEY (company_id, direction, match_pattern)"
            ")"
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
        _schema_ready = True
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

                # uuid4：微秒时间戳+循环下标在同公司并发导入时可撞主键
                sid = f"bs-{company_id}-{uuid4().hex}"

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

    返回值里的 ``errors`` / ``firstError`` 是排查用的：过去这里把单行异常静默
    计数，调用方只看到 ``imported=0``，整批流水凭空消失却没有任何提示。
    """
    await _ensure_schema()
    now = _now_iso()
    imported = 0
    skipped = 0
    errors = 0
    first_error = ""

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

                # uuid4：微秒时间戳+循环下标在同公司并发导入时可撞主键
                sid = f"bs-{company_id}-{uuid4().hex}"

                await conn.execute(
                    "INSERT INTO bank_statements "
                    "(id, company_id, transaction_date, description, amount, balance, statement_id, description_normalized, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                    (sid, company_id, txn_date, desc, amount, balance, statement_id or sid, now),
                )
                imported += 1
            except (ValueError, KeyError, TypeError) as exc:
                errors += 1
                first_error = first_error or f"{type(exc).__name__}: {exc}"

        if imported > 0:
            await conn.commit()
    finally:
        await conn.close()

    if errors:
        logger.warning(
            "银行流水导入有 %s 行失败（company=%s, imported=%s, skipped=%s, first=%s）",
            errors, company_id, imported, skipped, first_error,
        )

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "firstError": first_error,
        "statement_id": statement_id,
    }


async def import_statements_core(
    session: AsyncSession,
    *,
    company_id: str,
    user_id: str,
    rows: List[Dict[str, Any]],
    statement_id: str = "",
    request_id: str,
    force_duplicates: bool = False,
) -> dict:
    """Atomically persist bank rows, source facts and audit data in PostgreSQL.

    The legacy import helpers above remain for SQLite migration fixtures. All HTTP
    imports use this function so a failed source/audit write rolls back the bank row.

    去重键是「日期 + 摘要 + 金额」，同日同额同摘要的两笔真实交易会被误判为重复。
    静默跳过等于漏记，所以重复不再吞掉：默认返回 skippedDetails 供用户确认，
    带 ``force_duplicates=True`` 才强制导入。
    """
    from services.accounting_core_service import ensure_tenant, record_audit

    await ensure_tenant(session, company_id, user_id)
    imported = 0
    skipped = 0
    errors = 0
    skipped_details: list[dict[str, Any]] = []
    batch_id = statement_id.strip() or f"bank-batch-{company_id}-{int(datetime.now(timezone.utc).timestamp() * 1000000)}"
    now = _now_iso()

    for row in rows:
        try:
            # 保留完整时间：同日多笔流水的先后（时分秒）决定余额连续性与排序，
            # 截成纯日期会让同日流水顺序错乱，期初/期末余额反推全错
            txn_date_text = str(row.get("transaction_date") or row.get("date") or "").strip()
            txn_date = date.fromisoformat(txn_date_text[:10])
            desc = normalize_statement_description(
                str(row.get("description") or row.get("desc") or row.get("摘要") or "")
            )
            signed_amount = safe_money(row.get("amount") if row.get("amount") is not None else row.get("金额"))
            balance = safe_money(row.get("balance") if row.get("balance") is not None else row.get("余额"))
            duplicate = None
            if not force_duplicates:
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
            if duplicate is not None and duplicate.first() is not None:
                skipped += 1
                skipped_details.append({
                    "transactionDate": txn_date_text,
                    "description": desc,
                    "amount": money_text(signed_amount),
                    "reason": "同日期+同摘要+同金额的流水已存在；如确属两笔真实交易，请使用强制导入",
                })
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
            from db.models import SourceTransaction

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
    return {
        "imported": imported,
        "skipped": skipped,
        "skippedDetails": skipped_details,
        "errors": errors,
        "statement_id": batch_id,
    }


async def delete_statements(company_id: str, ids: List[str], user_id: str = "") -> int:
    """Delete bank statements together with their matches and source facts.

    Match rows must go too: the unique index on (company_id, entry_id) would
    otherwise keep blocking those entries from re-matching after a re-import.
    All deletes run in one transaction on the raw connection.
    """
    unique_ids = list(dict.fromkeys(str(item).strip() for item in ids if str(item or "").strip()))
    if not unique_ids:
        return 0
    await _ensure_schema()
    conn = await get_conn()
    try:
        placeholders = ",".join("?" for _ in unique_ids)
        cursor = await conn.execute(
            f"SELECT id FROM bank_statements WHERE company_id = ? AND id IN ({placeholders})",
            (company_id, *unique_ids),
        )
        existing = [str(row["id"]) for row in await cursor.fetchall()]
        if not existing:
            return 0
        for start in range(0, len(existing), 500):
            chunk = existing[start:start + 500]
            chunk_placeholders = ",".join("?" for _ in chunk)
            await conn.execute(
                f"DELETE FROM bank_statement_matches "
                f"WHERE company_id = ? AND statement_id IN ({chunk_placeholders})",
                (company_id, *chunk),
            )
            await conn.execute(
                f"DELETE FROM source_transactions "
                f"WHERE company_id = ? AND source_type = 'bank' AND external_key IN ({chunk_placeholders})",
                (company_id, *chunk),
            )
            await conn.execute(
                f"DELETE FROM bank_statements WHERE company_id = ? AND id IN ({chunk_placeholders})",
                (company_id, *chunk),
            )
        await conn.commit()
    finally:
        await conn.close()
    if user_id:
        await log_action(
            company_id, user_id, "delete", "bank_statement_batch",
            entity_id=existing[0] if len(existing) == 1 else None,
            details={"ids": existing, "deleted": len(existing)},
        )
    return len(existing)


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
            row["counterparty"] = extract_statement_counterparty(row.get("description", ""))
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


def _build_match_candidates(
    rows: List[Any],
    already_matched_ids,
    *,
    allow_advancement: bool,
) -> List[Dict]:
    """把 entries 行装配成对账候选（auto_match 与 ai_suggest 共用，防口径漂移）。

    allow_advancement：股东/员工个人垫付凭证银行效果为零，也允许作为报销流水
    的配对候选（ai_suggest 需要该能力；auto_match 规则匹配不需要）。
    待核销发票（贷2241/借1122）挂账分录不含银行科目，配对金额取票面金额。
    """
    entries: List[Dict] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
            entry_id = payload.get("id", row["id"])
            if entry_id in already_matched_ids:
                continue

            voucher = payload.get("voucherLines") or {}
            bank_effect = _bank_effect_from_voucher(voucher) if voucher else ZERO
            payer = payload.get("payer") or ""
            is_advancement = payer == "boss_personal" or payer == "employee_personal" or payer.startswith("employee_personal:")
            is_invoice_pending = bool(voucher) and (
                any(str(line.get("accountCode")) == "2241" for line in (voucher.get("credit") or []))
                or any(str(line.get("accountCode")) == "1122" for line in (voucher.get("debit") or []))
            )

            if (
                voucher
                and abs(bank_effect) < RECONCILIATION_AMOUNT_TOLERANCE
                and not (allow_advancement and is_advancement)
                and not is_invoice_pending
            ):
                continue

            orig_amount = (
                safe_money(payload.get("amount"))
                if (allow_advancement and is_advancement)
                else (bank_effect if voucher else safe_money(payload.get("amount")))
            )
            if is_invoice_pending and abs(bank_effect) < RECONCILIATION_AMOUNT_TOLERANCE:
                # 待核销发票的挂账分录不含银行科目，配对金额取票面金额
                orig_amount = safe_money(payload.get("amount"))
            direction = "income" if bank_effect > 0 else "expense" if bank_effect < 0 else payload.get("direction", "expense")

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
                "occurDate": str(payload.get("occurDate", ""))[:10],
            })
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return entries


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


    entries = _build_match_candidates(rows, already_matched_ids, allow_advancement=False)

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

        if payload.get("voucherStatus") in {"draft", "pending_review", "void"}:
            continue

        voucher = payload.get("voucherLines", {})
        if not voucher:
            direction = payload.get("direction", "expense")
            category = payload.get("category", "其他支出")
            amount = safe_money(payload.get("amount"))
            payer = payload.get("payer", "company_account")
            voucher = get_voucher_hint(direction, category, amount, payer)

        occur_date = payload.get("occurDate", "")
        bank_date = str(occur_date if payload.get("voucherStatus") else payload.get("paymentDate") or occur_date)
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
        unposted = [p for p in payloads if p.get("voucherStatus") in {"draft", "pending_review"}]
        if unposted:
            posted_effect = money(sum((_entry_bank_effect_from_payload(p) for p in payloads
                if p.get("voucherStatus") not in {"draft", "pending_review"}), ZERO))
            matched_effect_issues.append({
                "type": "unposted_match", "statementId": statement_id,
                "description": statement.get("description"), "date": statement.get("transaction_date"),
                "statementAmount": safe_money(statement.get("amount")),
                "entryIds": [p.get("id") for p in unposted], "repairable": False,
                "entryTitles": [p.get("title") for p in unposted],
                "bankEffect": posted_effect,
                "difference": money(safe_money(statement.get("amount")) - posted_effect),
                "reason": "关联凭证尚未过账，请到凭证页确认或提交复核",
                "message": "关联凭证尚未过账，请到凭证页确认或提交复核",
                "repairDescription": "关联凭证尚未过账，请到凭证页确认或提交复核",
            })
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
    """Legacy projection fixture repair; HTTP repairs use bank_settlement_service.

    Kept for isolated legacy compatibility tests only. Never call this helper
    from a production financial write: it has no authoritative journal session.
    """
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
                    "credit": [{"accountCode": WECHAT_CLEARING_CODE, "accountName": "微信支付待结算", "amount": amount}],
                }
                direction = "income"
            else:
                voucher_lines = {
                    "debit": [{"accountCode": WECHAT_CLEARING_CODE, "accountName": "微信支付待结算", "amount": amount}],
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
        if any(entry.get("billingStatus") != "reconciled" or
               entry.get("voucherStatus") in {"draft", "pending_review"} for entry in entries):
            item["unpostedEntryIds"] = [entry.get("id") for entry in entries
                if entry.get("voucherStatus") in {"draft", "pending_review"}]
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


async def get_wechat_settlement_candidates(
    company_id: str, statement: Dict[str, Any],
) -> List[List[Dict[str, Any]]]:
    """Read complete order/day groups; a candidate is never an automatic match."""
    if not re.search(r"微信|财付通|WeChat|Tenpay", str(statement.get("description") or ""), re.I):
        return []
    try:
        statement_date = date.fromisoformat(str(statement.get("transaction_date") or "")[:10])
    except ValueError:
        return []
    matched_ids = await get_matched_entry_ids(company_id)
    conn = await get_conn()
    try:
        rows = await (await conn.execute(
            "SELECT id, payload FROM entries WHERE company_id = ? "
            "AND json_extract(payload, '$.source') = 'wechat_bill' "
            "AND json_extract(payload, '$.occurDate') >= ? "
            "AND json_extract(payload, '$.occurDate') < ? ORDER BY id",
            (company_id, (statement_date - timedelta(days=5)).isoformat(),
             (statement_date + timedelta(days=1)).isoformat()),
        )).fetchall()
    finally:
        await conn.close()

    orders: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
    unavailable: set[tuple[str, str]] = set()
    for row in rows:
        try:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        payload = {**payload, "id": str(row["id"])}
        key = (str(payload.get("occurDate") or "")[:10], str(payload.get("wechat_order_no") or ""))
        orders.setdefault(key, []).append(payload)
        if (payload["id"] in matched_ids or payload.get("billingStatus") == "reconciled"
                or payload.get("is_archive_only") or payload.get("isArchiveOnly")):
            unavailable.add(key)

    days: Dict[str, List[Dict[str, Any]]] = {}
    groups = []
    for key, entries in orders.items():
        if key in unavailable:
            continue
        # A same-day complete refund must not become part of a bank receipt.
        if sum((_entry_group_reconciliation_effect(entry) for entry in entries), ZERO) == ZERO:
            continue
        groups.append(entries)
        days.setdefault(key[0], []).extend(entries)
    groups.extend(days.values())
    candidates = []
    seen = set()
    for entries in groups:
        ids = frozenset(entry["id"] for entry in entries)
        if ids in seen:
            continue
        check = build_group_reconciliation_check(statement.get("amount"), entries)
        if check["amountMatches"] and check["directionMatches"]:
            seen.add(ids)
            candidates.append(entries)
    return candidates


_REFUND_KW_PATTERN = re.compile(r"退款|退回|退还")


def _statement_counterparty(desc: Any) -> str:
    match = re.search(r"\(对方[:：]\s*(.+?)\)", str(desc or ""))
    return match.group(1).strip() if match else ""


def _detect_refund_pairs(statements: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """识别「一付一退」对：同对手方、等额、反向、30 天内、任一侧含退款字样。

    返回 {statement_id: {"other": 对侧流水, "is_refund": 本侧是否退款侧}}。
    认证/注册类验证款先付后退：两侧都应挂往来对冲（付款 122199 暂付、
    收款冲减 122199），而不是退款进平台结算、付款挂往来永远悬空。
    """
    candidates = []
    for stmt in statements:
        counterparty = _statement_counterparty(stmt.get("description"))
        amount = safe_money(stmt.get("amount"))
        date_str = str(stmt.get("transaction_date") or "")[:10]
        if not counterparty or amount == ZERO:
            continue
        candidates.append((stmt, counterparty, amount, date_str))

    pairs: Dict[str, Dict[str, Any]] = {}
    consumed: set[str] = set()
    for index, (stmt_a, cp_a, amt_a, date_a) in enumerate(candidates):
        if stmt_a["id"] in consumed:
            continue
        for stmt_b, cp_b, amt_b, date_b in candidates[index + 1:]:
            if stmt_b["id"] in consumed or cp_a != cp_b or amt_a != -amt_b:
                continue
            if not (_REFUND_KW_PATTERN.search(str(stmt_a.get("description") or ""))
                    or _REFUND_KW_PATTERN.search(str(stmt_b.get("description") or ""))):
                continue
            try:
                gap_days = abs((date.fromisoformat(date_a) - date.fromisoformat(date_b)).days)
            except ValueError:
                continue
            if gap_days > 30:
                continue
            a_is_refund = bool(_REFUND_KW_PATTERN.search(str(stmt_a.get("description") or "")))
            pairs[stmt_a["id"]] = {"other": stmt_b, "is_refund": a_is_refund}
            pairs[stmt_b["id"]] = {"other": stmt_a, "is_refund": not a_is_refund}
            consumed.update({stmt_a["id"], stmt_b["id"]})
            break
    return pairs


def _refund_pair_suggestion(stmt: Dict[str, Any], pair: Dict[str, Any]) -> Dict[str, Any]:
    is_refund = pair["is_refund"]
    other = pair["other"]
    amount = abs(safe_money(stmt.get("amount")))
    date_str = str(stmt.get("transaction_date") or "")[:10]
    other_date = str(other.get("transaction_date") or "")[:10]
    other_amount = abs(safe_money(other.get("amount")))
    if is_refund:
        category = "往来退款"
        direction = "income"
        title = f"往来退款对冲-{date_str}" if date_str else "往来退款对冲"
        reason = (
            f"与本笔对应的 {other_date} 付款 ¥{other_amount} 构成一付一退，"
            "收款冲减其他应收款-待收款项，不进平台结算或损益"
        )
    else:
        category = "往来款"
        direction = "expense"
        title = f"往来暂付款-{date_str}" if date_str else "往来暂付款"
        reason = (
            f"与本笔对应的 {other_date} 退款 ¥{other_amount} 构成一付一退，"
            "付款暂挂其他应收款-待收款项，与退款对冲"
        )
    return {
        "statementId": stmt["id"],
        "statement": {
            "date": stmt["transaction_date"],
            "description": stmt["description"],
            "amount": stmt["amount"],
            "balance": stmt["balance"],
        },
        "type": "new",
        "confidence": 0.9,
        "rule": "refund_pair",
        "reason": reason,
        "suggestedEntry": {
            "title": title,
            "category": category,
            "accountCode": "122199",
            "accountName": "其他应收款-待收款项",
            "amount": amount,
            "direction": direction,
            "occurDate": date_str,
            "confidence": 0.9,
            "reason": reason,
        },
    }


async def ai_suggest_matches(
    company_id: str, limit: int = 20, month: str | None = None, *, user_id: str = ""
) -> List[Dict[str, Any]]:
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
        sql = "SELECT * FROM bank_statements WHERE company_id = ? AND (matched_entry_id IS NULL OR matched_entry_id = '') "
        params: list[Any] = [company_id]
        if month:
            start = date.fromisoformat(f"{month}-01")
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            sql += "AND transaction_date >= ? AND transaction_date < ? "
            params.extend([start.isoformat(), end.isoformat()])
        sql += "ORDER BY transaction_date DESC LIMIT ?"
        params.append(limit)
        cursor = await conn.execute(sql, tuple(params))
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
        try:
            cursor = await conn.execute(
                "SELECT id, payload FROM entries WHERE company_id = ? ORDER BY updated_at DESC LIMIT 100",
                (company_id,),
            )
            rows = await cursor.fetchall()
        except Exception as exc:  # noqa: BLE001
            # 同下方 rules：表缺失只降级为无候选，不能让 ai-suggest 整体 500
            logger.warning("读取 entries 失败，AI 对账候选为空 company=%s: %s", company_id, exc)
            rows = []
        company_rules = {}
        try:
            rule_cursor = await conn.execute(
                "SELECT direction, match_pattern, category FROM bank_category_rules WHERE company_id = ?",
                (company_id,),
            )
            company_rules = {
                (str(r["direction"]), str(r["match_pattern"])): str(r["category"])
                for r in await rule_cursor.fetchall()
            }
        except Exception as exc:  # noqa: BLE001
            # 表缺失（如 Postgres 未跑迁移）只降级为无记忆规则，不能让整个
            # ai-suggest 500——否则对账建议全灭且前端无提示。
            logger.warning("读取 bank_category_rules 失败，本次跳过企业记忆规则: %s", exc)
    finally:
        await conn.close()

    entries = _build_match_candidates(rows, already_matched_ids, allow_advancement=True)

    suggestions = []
    used_suggested_entry_ids = set(already_matched_ids)

    # B. 一付一退对冲：退款与原付款互相挂钩，双双挂往来不进损益/平台结算
    refund_pairs = _detect_refund_pairs(statements)

    need_ai_statements = []
    for stmt in statements:
        pair = refund_pairs.get(stmt["id"])
        if pair:
            suggestions.append(_refund_pair_suggestion(stmt, pair))
            continue

        # 与已入账凭证配对（优先于一切新建建议：账上记过的钱要核销而不是重记）
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
            continue

        # C. 企业记忆规则（用户确认过的规则压过系统关键词启发式——否则
        #    「记住此规则」在快规则命中的流水上永远不生效）
        stmt_dir = "income" if safe_money(stmt["amount"]) > ZERO else "expense"
        stmt_pattern = extract_statement_rule_pattern(str(stmt.get("description") or ""))
        remembered_category = company_rules.get((stmt_dir, stmt_pattern)) if stmt_pattern else None
        if remembered_category:
            date_str = str(stmt.get("transaction_date") or "")[:10]
            reason = f"命中企业记忆规则：「{stmt_pattern}」自动归入{remembered_category}"
            suggestions.append({
                "statementId": stmt["id"],
                "statement": {
                    "date": stmt["transaction_date"],
                    "description": stmt["description"],
                    "amount": stmt["amount"],
                    "balance": stmt["balance"],
                },
                "type": "new",
                "confidence": 0.98,
                "rule": "company_memory_rule",
                "rulePattern": stmt_pattern,
                "reason": reason,
                "suggestedEntry": {
                    "title": f"{remembered_category}-{stmt_pattern[:16]}",
                    "category": remembered_category,
                    "amount": abs(safe_money(stmt["amount"])),
                    "direction": stmt_dir,
                    "occurDate": date_str,
                    "confidence": 0.98,
                    "reason": reason,
                },
            })
            continue

        # A. 系统关键词快规则（已收窄：退款/验证类不再误中平台结算）
        fast_rule = identify_statement_fast_rule(stmt)
        if fast_rule and fast_rule["rule"] != "wechat_withdrawal":
            suggestions.append({
                "statementId": stmt["id"],
                "statement": {
                    "date": stmt["transaction_date"],
                    "description": stmt["description"],
                    "amount": stmt["amount"],
                    "balance": stmt["balance"],
                },
                "type": "new",
                "confidence": fast_rule["confidence"],
                "rule": fast_rule["rule"],
                "reason": fast_rule["reason"],
                "suggestedEntry": {
                    "title": fast_rule["title"],
                    "category": fast_rule["category"],
                    "amount": abs(safe_money(stmt["amount"])),
                    "direction": "income" if safe_money(stmt["amount"]) > ZERO else "expense",
                    "occurDate": stmt["transaction_date"][:10] if stmt.get("transaction_date") else "",
                    "confidence": fast_rule["confidence"],
                    "reason": fast_rule["reason"],
                },
            })
            continue

        if fast_rule and fast_rule["rule"] == "wechat_withdrawal":
            matched_group = None
            try:
                settlement_candidates = await get_wechat_settlement_candidates(company_id, stmt)
                available_groups = [group for group in settlement_candidates
                                    if len(group) <= 500
                                    and not any(entry["id"] in used_suggested_entry_ids for entry in group)]
                if len(available_groups) == 1:
                    matched_group = available_groups[0]
            except Exception:
                logger.exception("get_wechat_settlement_candidates failed for %s", stmt.get("id"))

            if matched_group:
                ids = [entry["id"] for entry in matched_group]
                used_suggested_entry_ids.update(ids)
                suggestions.append({
                    "statementId": stmt["id"],
                    "statement": {"date": stmt["transaction_date"], "description": stmt["description"],
                                  "amount": stmt["amount"], "balance": stmt["balance"]},
                    "type": "match", "matchedEntryId": ids[0], "matchedEntryIds": ids,
                    "matchedEntry": matched_group[0], "matchedEntries": matched_group,
                    "confidence": 0.95,
                    "rule": "wechat_detail_match",
                    "reason": "微信原账目净额与到账金额一致，可一键合并对账",
                })
            else:
                # 汇总记账模式或无订单明细时：直接走资金划转（借:1002 贷:1012001）
                suggestions.append({
                    "statementId": stmt["id"],
                    "statement": {
                        "date": stmt["transaction_date"],
                        "description": stmt["description"],
                        "amount": stmt["amount"],
                        "balance": stmt["balance"],
                    },
                    "type": "new",
                    "confidence": fast_rule["confidence"],
                    "rule": fast_rule["rule"],
                    "reason": fast_rule["reason"],
                    "suggestedEntry": {
                        "title": fast_rule["title"],
                        "category": fast_rule["category"],
                        "amount": abs(safe_money(stmt["amount"])),
                        "direction": "income" if safe_money(stmt["amount"]) > ZERO else "expense",
                        "occurDate": stmt["transaction_date"][:10] if stmt.get("transaction_date") else "",
                        "confidence": fast_rule["confidence"],
                        "reason": fast_rule["reason"],
                    },
                })
            continue

        need_ai_statements.append(stmt)

    if need_ai_statements:
        async def _ai_suggest_single(stmt: dict, cand_entries: list) -> dict:
            try:
                stmt_amount = abs(safe_money(stmt["amount"]))
                # ponytail: 金额阈值必须用 Decimal，混合 float 会 TypeError → 整条建议退化成兜底
                amount_gap = stmt_amount * Decimal("0.5") + Decimal("10")
                candidates = [
                    e for e in cand_entries
                    if _direction_matches_statement(stmt["amount"], e.get("direction", "expense"))
                    and abs(abs(safe_money(e["amount"])) - stmt_amount) < amount_gap
                ][:10]  # Max 10 candidates for token efficiency

                if candidates:
                    user_msg = f"银行流水：\n- 日期：{stmt['transaction_date']}\n- 摘要：{stmt['description']}\n- 金额：{stmt['amount']}\n\n候选账目记录：\n{json_dumps(candidates, ensure_ascii=False, indent=2)}"
                    
                    # Parallel thread executor for synchronous chat
                    match_result = await asyncio.to_thread(
                        safe_chat_text_json,
                        BANK_MATCH_PROMPT, user_msg,
                        fallback={"matchedEntryId": None, "confidence": 0, "reason": "无法判断"},
                        max_retries=1,
                        scene="bank_statement",
                        user_id=user_id,
                        company_id=company_id,
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
                    retry_on_parse=True,
                    scene="bank_statement",
                    user_id=user_id,
                    company_id=company_id,
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
            except AccountingError:
                raise  # 配额超限必须对上层可见（402），不能伪装成单条兜底建议
            except Exception:
                logger.exception("ai_suggest_single 兜底：statement=%s", stmt.get("id"))
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


async def classify_single_statement(
    company_id: str, statement_id: str, business_context: str = "", *, user_id: str = ""
) -> Optional[Dict[str, Any]]:
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
    if safe_money(stmt["amount"]) < ZERO and is_payroll_liability_description(stmt["description"]):
        return {
            "direction": "expense",
            "category": "社保公积金",
            "accountCode": "221102",
            "accountName": "应付职工薪酬-单位社保",
            "title": "社保公积金缴纳",
            "confidence": 0.95,
            "requiresSalarySettlement": True,
            "requiresPayrollLiability": True,
            "reason": "识别为社保/公积金缴纳：已计提时应在工资模块按流水核销应付职工薪酬，不能直接记费用",
        }
    if safe_money(stmt["amount"]) < ZERO and is_salary_payment_description(stmt["description"]):
        return {
            "direction": "expense",
            "category": "工资发放",
            "accountCode": "221101",
            "accountName": "应付职工薪酬-工资",
            "title": "工资发放",
            "confidence": 0.95,
            "requiresSalarySettlement": True,
            "reason": "识别为工资发放：工资费用应在所属月计提，此处只核销应付职工薪酬，请到工资模块按流水确认发放",
        }
    if safe_money(stmt["amount"]) < ZERO and "研发" in context:
        return {
            "direction": "expense",
            "category": "研发支出-费用化支出",
            "accountCode": "4301",
            "accountName": "研发支出-费用化支出",
            "title": "研发相关支出",
            "confidence": 0.9,
            "reason": "用户补充说明表明该支出用于研发，应优先按研发支出处理",
        }

    if not context:
        fast_rule = identify_statement_fast_rule(stmt)
        if fast_rule:
            return {
                "direction": "income" if safe_money(stmt["amount"]) > ZERO else "expense",
                "category": fast_rule["category"],
                "title": fast_rule["title"],
                "confidence": fast_rule["confidence"],
                "reason": fast_rule["reason"],
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

    classify_result = await asyncio.to_thread(
        safe_chat_text_json,
        BANK_CLASSIFY_PROMPT, classify_msg,
        fallback={"direction": "expense" if stmt["amount"] < 0 else "income",
                  "category": "其他支出" if stmt["amount"] < 0 else "其他业务收入",
                  "accountCode": "5602999" if stmt["amount"] < 0 else "5051",
                  "accountName": "管理费用-其他" if stmt["amount"] < 0 else "其他业务收入",
                  "title": stmt["description"],
                  "confidence": 0.3, "reason": "无法自动分类"},
        max_retries=1,
        retry_on_parse=True,
        scene="bank_statement",
        user_id=user_id,
        company_id=company_id,
    )
    return _ensure_classification_direction(stmt, classify_result)


async def apply_statement_match(
    company_id: str,
    user_id: str,
    statement_id: str,
    entry_ids: List[str],
    *,
    session: AsyncSession,
    request_id: str = "",
) -> dict:
    """Agent 对账建议的"简单匹配"应用器（仅覆盖常规正向匹配）。

    与 HTTP 确认共用权威事务，同时创建结算草稿和原始业务关联。
    垫付报销合并等特殊场景在此拒绝，指引用户走银行流水页手动确认（单一权威路径）。
    """

    from services.bank_settlement_service import confirm_statement_match
    if not entry_ids:
        raise ValueError("至少需要一笔要匹配的账目记录")
    return await confirm_statement_match(session, company_id=company_id, user_id=user_id,
        statement_id=statement_id, entry_ids=entry_ids, request_id=request_id or _now_iso())


def _looks_like_salary_payment(entry_data: Dict[str, Any], description: str) -> bool:
    """工资流水不得在银行页直接记费用，否则与工资计提重复。"""
    if is_salary_payment_description(description):
        return True
    category = str(entry_data.get("category") or "")
    if category in {"工资", "工资支出", "工资发放", "职工薪酬", "薪资", "薪酬"}:
        return True
    account_code = str(entry_data.get("accountCode") or "")
    return account_code in {"5602001", "221101"}


def _looks_like_payroll_liability(entry_data: Dict[str, Any], description: str) -> bool:
    """社保/公积金流水必须走工资模块核销，不能直接记费用导致重复列支。"""
    if is_payroll_liability_description(description):
        return True
    category = str(entry_data.get("category") or "")
    if category in {"社保公积金", "社保", "公积金", "住房公积金"}:
        return True
    return str(entry_data.get("accountCode") or "") in {"221102", "221104"}


async def confirm_suggestion_flow(
    *,
    req: Any,
    session: Optional[AsyncSession],
    company_id: str,
    user_id: str,
    request: Any = None,
    respond,
    sync_core,
) -> Dict[str, Any]:
    """确认 AI 建议的业务体（自 routers/bank.py 下沉）。

    生产路径（session 为 AsyncSession）走 bank_settlement_service 的权威事务；
    单测/legacy 夹具（session 非会话）走本函数内的 legacy 裸 SQL 路径。原样保留
    HTTPException 错误契约：前端按 detail.code 分流（EXISTING_WECHAT_ENTRIES /
    PAYROLL_LIABILITY_REQUIRES_PAYROLL / SALARY_REQUIRES_PAYROLL / EXISTING_ENTRY_MATCH）。
    """
    from services.entry_service import save_entry

    if req.type == "match":
        await ensure_bank_schema()
        requested_raw = req.matched_entry_ids if req.matched_entry_ids else [req.matched_entry_id]
        requested_ids: List[str] = []
        for raw_id in requested_raw:
            entry_id = str(raw_id or "").strip()
            if entry_id and entry_id not in requested_ids:
                requested_ids.append(entry_id)
        if len([entry_id for entry_id in requested_ids
                if entry_id != f"bank-settlement-{req.statement_id}"]) > 500:
            raise HTTPException(status_code=400, detail="一次最多合并 500 笔账目记录")

        if isinstance(session, AsyncSession):
            from sqlalchemy import select

            from db.models import JournalEntry, LegacyEntryProjection
            from services.bank_settlement_service import confirm_statement_match
            # 批量两次查询替代逐条 get_journal+serialize_journal（500 笔上限时原为
            # 千级串行查询）。判定所需的 payer 可能来自投影回填（与 serialize_journal
            # 同优先级：metadata 打底、投影覆盖），voucherLines 仅存于 metadata。
            personal = False
            payloads: Dict[str, Dict[str, Any]] = {}
            for row in (await session.execute(
                select(JournalEntry.id, JournalEntry.metadata_json).where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.id.in_(requested_ids),
                )
            )).all():
                payloads[row[0]] = dict(row[1] or {})
            for row in (await session.execute(
                select(LegacyEntryProjection.id, LegacyEntryProjection.payload).where(
                    LegacyEntryProjection.company_id == company_id,
                    LegacyEntryProjection.id.in_(requested_ids),
                )
            )).all():
                try:
                    legacy = json.loads(row[1]) if isinstance(row[1], str) else (row[1] or {})
                except (json.JSONDecodeError, TypeError):
                    legacy = {}
                if isinstance(legacy, dict) and legacy.get("payer") is not None and row[0] in payloads:
                    payloads[row[0]]["payer"] = legacy["payer"]
            for payload in payloads.values():
                payer = str(payload.get("payer") or "")
                if (payer in {"boss_personal", "employee_personal"} or payer.startswith("employee_personal:")) and \
                        _bank_effect_from_voucher(payload.get("voucherLines") or {}) == ZERO:
                    personal = True
            if not personal:
                return await respond(await confirm_statement_match(session,
                    company_id=company_id, user_id=user_id, statement_id=req.statement_id,
                    entry_ids=requested_ids,
                    request_id=(request.headers.get("X-Request-ID") if request else None) or str(uuid4()),
                    auto_post=bool(req.auto_post),
                    allow_backfill=bool(req.allowBackfill),
                    backfill_reason=req.backfillReason))

        conn = await get_conn()
        try:
            cursor = await conn.execute(
                "SELECT id, amount, matched_entry_id, transaction_date, description "
                "FROM bank_statements WHERE id = ? AND company_id = ?",
                (req.statement_id, company_id),
            )
            stmt = await cursor.fetchone()
            if not stmt:
                raise HTTPException(status_code=404, detail="流水不存在")

            cursor = await conn.execute(
                "SELECT entry_id, previous_billing_status, previous_payment_date FROM bank_statement_matches "
                "WHERE statement_id = ? AND company_id = ? ORDER BY created_at ASC, entry_id ASC",
                (req.statement_id, company_id),
            )
            existing_rows = [dict(row) for row in await cursor.fetchall()]
            existing_ids = [str(row["entry_id"]) for row in existing_rows]
            legacy_id = str(stmt["matched_entry_id"] or "").strip()
            if legacy_id and legacy_id not in existing_ids:
                existing_ids.insert(0, legacy_id)

            if not requested_ids:
                previous_statuses = {
                    str(row["entry_id"]): row.get("previous_billing_status")
                    for row in existing_rows
                }
                previous_payment_dates = {
                    str(row["entry_id"]): row.get("previous_payment_date")
                    for row in existing_rows
                }
                await conn.execute(
                    "DELETE FROM bank_statement_matches WHERE statement_id = ? AND company_id = ?",
                    (req.statement_id, company_id),
                )
                await conn.execute(
                    "UPDATE bank_statements SET matched_entry_id = NULL WHERE id = ? AND company_id = ?",
                    (req.statement_id, company_id),
                )

                for entry_id in existing_ids:
                    cursor = await conn.execute(
                        "SELECT 1 FROM bank_statement_matches "
                        "WHERE company_id = ? AND entry_id = ? AND statement_id != ? LIMIT 1",
                        (company_id, entry_id, req.statement_id),
                    )
                    still_matched = await cursor.fetchone()
                    if not still_matched:
                        cursor = await conn.execute(
                            "SELECT 1 FROM bank_statements "
                            "WHERE company_id = ? AND matched_entry_id = ? AND id != ? LIMIT 1",
                            (company_id, entry_id, req.statement_id),
                        )
                        still_matched = await cursor.fetchone()
                    if still_matched:
                        continue

                    cursor = await conn.execute(
                        "SELECT payload FROM entries WHERE id = ? AND company_id = ?",
                        (entry_id, company_id),
                    )
                    entry_row = await cursor.fetchone()
                    if not entry_row:
                        continue
                    payload = json.loads(entry_row["payload"]) if isinstance(entry_row["payload"], str) else entry_row["payload"]
                    if (
                        isinstance(payload, dict)
                        and payload.get("source") == "bank_reconciliation_repair"
                        and payload.get("linkedStatementId") == req.statement_id
                    ):
                        await conn.execute(
                            "DELETE FROM entries WHERE id = ? AND company_id = ?",
                            (entry_id, company_id),
                        )
                        continue
                    previous_status = previous_statuses.get(entry_id)
                    current_status = payload.get("billingStatus") if isinstance(payload, dict) else None
                    if previous_status is None and current_status != "reconciled":
                        previous_status = current_status
                    if previous_status is None and isinstance(payload, dict):
                        if payload.get("source") == "invoice_import" or payload.get("invoiceNumber"):
                            previous_status = "has_invoice"
                    if previous_status:
                        await conn.execute(
                            "UPDATE entries SET payload = json_set(payload, '$.billingStatus', ?) "
                            "WHERE id = ? AND company_id = ?",
                            (previous_status, entry_id, company_id),
                        )
                    else:
                        await conn.execute(
                            "UPDATE entries SET payload = json_remove(payload, '$.billingStatus') "
                            "WHERE id = ? AND company_id = ?",
                            (entry_id, company_id),
                        )
                    previous_payment_date = previous_payment_dates.get(entry_id)
                    if previous_payment_date:
                        await conn.execute(
                            "UPDATE entries SET payload = json_set(payload, '$.paymentDate', ?) "
                            "WHERE id = ? AND company_id = ?",
                            (previous_payment_date, entry_id, company_id),
                        )
                    else:
                        await conn.execute(
                            "UPDATE entries SET payload = json_remove(payload, '$.paymentDate') "
                            "WHERE id = ? AND company_id = ?",
                            (entry_id, company_id),
                        )
                await conn.commit()
                await sync_core(stmt, [])
                return await respond({"status": "unmatched", "entryId": None, "entryIds": existing_ids})

            if existing_ids and set(existing_ids) != set(requested_ids):
                raise HTTPException(status_code=409, detail="该流水已匹配其他账目记录")

            placeholders = ",".join("?" for _ in requested_ids)
            cursor = await conn.execute(
                f"SELECT id, payload FROM entries WHERE company_id = ? AND id IN ({placeholders})",
                (company_id, *requested_ids),
            )
            entry_rows = await cursor.fetchall()
            payloads_by_id: Dict[str, Dict[str, Any]] = {}
            for entry_row in entry_rows:
                payload = json.loads(entry_row["payload"]) if isinstance(entry_row["payload"], str) else entry_row["payload"]
                if isinstance(payload, dict):
                    payloads_by_id[str(entry_row["id"])] = payload
            missing_ids = [entry_id for entry_id in requested_ids if entry_id not in payloads_by_id]
            if missing_ids:
                raise HTTPException(status_code=404, detail={"message": "账目记录不存在", "entryIds": missing_ids})
            payloads = [payloads_by_id[entry_id] for entry_id in requested_ids]

            cursor = await conn.execute(
                f"SELECT entry_id FROM bank_statement_matches "
                f"WHERE company_id = ? AND statement_id != ? AND entry_id IN ({placeholders}) LIMIT 1",
                (company_id, req.statement_id, *requested_ids),
            )
            conflict = await cursor.fetchone()
            if not conflict:
                cursor = await conn.execute(
                    f"SELECT matched_entry_id AS entry_id FROM bank_statements "
                    f"WHERE company_id = ? AND id != ? AND matched_entry_id IN ({placeholders}) LIMIT 1",
                    (company_id, req.statement_id, *requested_ids),
                )
                conflict = await cursor.fetchone()
            if conflict:
                raise HTTPException(status_code=409, detail="其中一笔账目已匹配其他银行流水")

            advancement_payloads = []
            for payload in payloads:
                payer = str(payload.get("payer") or "")
                voucher = payload.get("voucherLines") or {}
                bank_effect = _bank_effect_from_voucher(voucher) if voucher else ZERO
                is_personal_payer = payer in ("boss_personal", "employee_personal") or payer.startswith("employee_personal:")
                if is_personal_payer and abs(bank_effect) < money("0.01"):
                    advancement_payloads.append(payload)
            if advancement_payloads and len(payloads) > 1:
                raise HTTPException(status_code=400, detail="垫付报销暂不支持与其他条目合并对账")

            stmt_amount = money(stmt["amount"] or ZERO)
            if advancement_payloads:
                payload = advancement_payloads[0]
                entry_id = requested_ids[0]
                payer = str(payload.get("payer") or "")
                if stmt_amount >= 0:
                    raise HTTPException(status_code=400, detail="垫付报销流水必须为付款流出")
                if abs(abs(stmt_amount) - money(payload.get("amount") or ZERO)) > money("0.05"):
                    raise HTTPException(status_code=400, detail="金额不一致，请核对垫付金额与打款金额")

                reim_entry_id = f"reim-{req.statement_id}-{entry_id}"
                reim_category = "股东垫付报销" if payer == "boss_personal" else "员工垫付报销"
                reim_data = {
                    "id": reim_entry_id,
                    "title": f"报销垫付款-{payload.get('title', '')}",
                    "amount": abs(stmt_amount),
                    "direction": "owner_transfer",
                    "category": reim_category,
                    "payer": payer,
                    "billingStatus": "reconciled",
                    "occurDate": str(stmt["transaction_date"][:10]),
                    "paymentDate": str(stmt["transaction_date"][:10]),
                    "source": "bank_import",
                }
                await save_entry(reim_data, user_id=user_id, company_id=company_id)
                if isinstance(session, AsyncSession):
                    await assert_bank_reconciliable(session, company_id=company_id, entry_ids=[reim_entry_id])
                now = datetime.now(timezone.utc).isoformat()
                await conn.execute(
                    "INSERT OR IGNORE INTO bank_statement_matches "
                    "(statement_id, company_id, entry_id, previous_billing_status, previous_payment_date, created_at) "
                    "VALUES (?, ?, ?, NULL, NULL, ?)",
                    (req.statement_id, company_id, reim_entry_id, now),
                )
                await conn.execute(
                    "UPDATE bank_statements SET matched_entry_id = ? WHERE id = ? AND company_id = ?",
                    (reim_entry_id, req.statement_id, company_id),
                )
                await conn.execute(
                    "UPDATE entries SET payload = json_set(payload, '$.billingStatus', 'reconciled') "
                    "WHERE id = ? AND company_id = ?",
                    (entry_id, company_id),
                )
                await conn.commit()
                await sync_core(stmt, [reim_entry_id])
                return await respond({"status": "matched", "entryId": reim_entry_id})

            check = build_group_reconciliation_check(stmt_amount, payloads)
            if not check["directionMatches"]:
                raise HTTPException(status_code=400, detail={
                    "message": "所选账目合计方向与银行流水方向不一致",
                    **check,
                })
            if not check["amountMatches"]:
                raise HTTPException(status_code=400, detail={
                    "message": "所选账目合计与银行流水金额不一致",
                    **check,
                })

            if existing_ids and set(existing_ids) == set(requested_ids):
                if isinstance(session, AsyncSession):
                    await assert_bank_reconciliable(session, company_id=company_id, entry_ids=requested_ids)
                await conn.execute(
                    f"UPDATE entries SET payload = json_set(payload, '$.billingStatus', 'reconciled') "
                    f"WHERE company_id = ? AND id IN ({placeholders})",
                    (company_id, *requested_ids),
                )
                await conn.commit()
                await sync_core(stmt, requested_ids)
                result: Dict[str, Any] = {"status": "already_matched", "entryId": requested_ids[0]}
                if len(requested_ids) > 1:
                    result["entryIds"] = requested_ids
                return await respond(result)

            if isinstance(session, AsyncSession):
                await assert_bank_reconciliable(session, company_id=company_id, entry_ids=requested_ids)
            now = datetime.now(timezone.utc).isoformat()
            for entry_id, payload in zip(requested_ids, payloads):
                await conn.execute(
                    "INSERT INTO bank_statement_matches "
                    "(statement_id, company_id, entry_id, previous_billing_status, previous_payment_date, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        req.statement_id,
                        company_id,
                        entry_id,
                        payload.get("billingStatus"),
                        payload.get("paymentDate"),
                        now,
                    ),
                )
            await conn.execute(
                "UPDATE bank_statements SET matched_entry_id = ? WHERE id = ? AND company_id = ?",
                (requested_ids[0], req.statement_id, company_id),
            )
            await conn.execute(
                f"UPDATE entries SET payload = json_set(payload, '$.billingStatus', 'reconciled', '$.paymentDate', ?) "
                f"WHERE company_id = ? AND id IN ({placeholders})",
                (str(stmt["transaction_date"][:10]), company_id, *requested_ids),
            )
            await conn.commit()
        finally:
            await conn.close()

        result = {"status": "matched", "entryId": requested_ids[0]}
        await sync_core(stmt, requested_ids)
        if len(requested_ids) > 1:
            result["entryIds"] = requested_ids
        return await respond(result)

    elif req.type == "new" and req.new_entry:
        entry_data = req.new_entry.copy()
        entry_data.pop("voucherLines", None)
        entry_data.pop("voucherHint", None)
        if entry_data.get("category") == "研发支出-费用化支出" and not (
            entry_data.get("rdProjectId") and entry_data.get("rdExpenseType")
        ):
            raise HTTPException(status_code=422, detail="研发费用必须选择研发项目和费用类别")
        conn = await get_conn()
        try:
            cursor = await conn.execute(
                "SELECT amount, matched_entry_id, transaction_date, description FROM bank_statements WHERE id = ? AND company_id = ?",
                (req.statement_id, company_id),
            )
            stmt = await cursor.fetchone()
            if not stmt:
                raise HTTPException(status_code=404, detail="流水不存在")

            existing_match = str(stmt["matched_entry_id"] or "").strip()
            if existing_match:
                if isinstance(session, AsyncSession):
                    await assert_bank_reconciliable(session, company_id=company_id, entry_ids=[existing_match])
                await conn.execute(
                    "UPDATE entries SET payload = json_set(payload, '$.billingStatus', 'reconciled') WHERE id = ? AND company_id = ?",
                    (existing_match, company_id),
                )
                await conn.commit()
                await sync_core(stmt, [existing_match])
                return await respond({"status": "already_matched", "entryId": existing_match})
        finally:
            await conn.close()

        if money(stmt["amount"] or ZERO) < ZERO and _looks_like_payroll_liability(entry_data, str(stmt["description"] or "")):
            liability = await salary_service.list_payroll_liability_payment_suggestions(company_id)
            raise HTTPException(status_code=409, detail={
                "code": "PAYROLL_LIABILITY_REQUIRES_PAYROLL",
                "message": "识别为社保/公积金缴纳，不能在银行页直接记费用。请到工资模块按流水确认缴纳，系统会生成应付职工薪酬核销凭证。",
                "suggestions": liability.get("suggestions", []),
            })

        if money(stmt["amount"] or ZERO) < ZERO and _looks_like_salary_payment(entry_data, str(stmt["description"] or "")):
            import services.salary_service as salary_service

            payroll = await salary_service.list_salary_payment_suggestions(company_id)
            raise HTTPException(status_code=409, detail={
                "code": "SALARY_REQUIRES_PAYROLL",
                "message": "识别为工资发放，不能在银行页直接记费用。请到工资模块先计提，再按流水确认发放，系统会生成应付职工薪酬核销凭证。",
                "suggestions": payroll.get("suggestions", []),
            })

        if entry_data.get("category") not in ("微信支付结算", "支付宝结算", "平台提现结算"):
            settlement_candidates = await get_wechat_settlement_candidates(company_id, dict(stmt))
            if settlement_candidates:
                raise HTTPException(status_code=409, detail={
                    "code": "EXISTING_WECHAT_ENTRIES",
                    "message": "该微信结算已有收入及手续费账目，请合并核销原账目，不能再次记为收入或支出",
                    "candidateEntryGroups": [[entry["id"] for entry in group] for group in settlement_candidates],
                })

        # 防重复记账：账上已有等额同向、尚未对账的银行凭证时，应直接配对而不是再记一笔
        if isinstance(session, AsyncSession) and not req.skipDuplicateCheck:
            try:
                stmt_day = date.fromisoformat(str(stmt["transaction_date"])[:10])
            except (TypeError, ValueError):
                stmt_day = None
            if stmt_day is not None:
                duplicates = await find_duplicate_bank_entries(
                    session, company_id, stmt_day, money(stmt["amount"] or ZERO)
                )
                if duplicates:
                    raise HTTPException(status_code=409, detail={
                        "code": "EXISTING_ENTRY_MATCH",
                        "message": "账上已有一笔金额相同的凭证，直接配对即可，避免重复记账",
                        "candidateEntryIds": [item["id"] for item in duplicates],
                        "entries": duplicates,
                    })

        stmt_amount = money(stmt["amount"] or ZERO)
        direction = str(entry_data.get("direction") or "")
        if entry_data.get("category") in ("微信支付结算", "支付宝结算", "平台提现结算") and not direction:
            direction = "income" if stmt_amount > ZERO else "expense"
            entry_data["direction"] = direction
        if not _direction_matches_statement(stmt_amount, direction):
            if stmt_amount > 0:
                raise HTTPException(status_code=400, detail="收款流水只能记为收入或股东投入/往来")
            if stmt_amount < 0:
                raise HTTPException(status_code=400, detail="付款流水只能记为支出或资本/往来")

        entry_id = f"bank-{req.statement_id}"
        entry_data["id"] = entry_id
        entry_data["source"] = "bank_import"
        entry_data["payer"] = entry_data.get("payer", "company_account")
        entry_data["billingStatus"] = "reconciled"
        entry_data["paymentDate"] = str(stmt["transaction_date"][:10])
        if "amount" in entry_data:
            raw_amount = money(entry_data["amount"])
            # income/expense 用方向表意，取绝对值；owner_transfer 保留原始符号表达现金流向
            if entry_data.get("direction") in ("income", "expense"):
                entry_data["amount"] = abs(raw_amount)
            else:
                entry_data["amount"] = raw_amount

        await save_entry(
            entry_data, user_id=user_id, company_id=company_id,
            allow_backfill=bool(req.allowBackfill), backfill_reason=req.backfillReason,
        )
        if isinstance(session, AsyncSession):
            await assert_bank_reconciliable(session, company_id=company_id, entry_ids=[entry_id])

        # Link statement to new entry
        conn = await get_conn()
        try:
            await conn.execute(
                "INSERT OR IGNORE INTO bank_statement_matches "
                "(statement_id, company_id, entry_id, previous_billing_status, previous_payment_date, created_at) "
                "VALUES (?, ?, ?, NULL, NULL, ?)",
                (req.statement_id, company_id, entry_id, datetime.now(timezone.utc).isoformat()),
            )
            await conn.execute(
                "UPDATE bank_statements SET matched_entry_id = ? WHERE id = ? AND company_id = ?",
                (entry_id, req.statement_id, company_id),
            )
            await conn.commit()
        finally:
            await conn.close()

        await log_action(company_id, user_id, "create", "entry", entry_id,
                         {"source": "bank_ai_suggest", "title": entry_data.get("title", "")})
        await sync_core(stmt, [entry_id])
        saved_rule_pattern = None
        # 模糊分类组的正确科目取决于账外事实（认缴进度、缴款性质），同一附言
        # 未必每次同科目；沉淀自动规则会把本月的判断错误套用到未来流水。
        if req.rememberRule and not is_ambiguity_category(str(entry_data.get("category") or "")):
            saved_rule_pattern = await save_company_category_rule(
                company_id,
                str(stmt["description"] or ""),
                stmt["amount"],
                str(entry_data.get("category") or ""),
            )
        response_data: Dict[str, Any] = {"status": "created", "entryId": entry_id}
        if saved_rule_pattern:
            response_data["savedRulePattern"] = saved_rule_pattern
        return await respond(response_data)

    return await respond({"status": "error", "message": "无效的请求"})
