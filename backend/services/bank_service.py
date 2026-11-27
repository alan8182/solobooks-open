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
from services.account_mapper import get_voucher_hint, lookup as lookup_account
from services.category_service import is_ambiguity_category
from services.audit_service import log_action
from core.errors import AccountingError
from core.money import ZERO, money, money_text
from services.json_utils import json_dumps
from services.platform_bill_common import (
    ALIPAY_CLEARING_CODE,
    LEGACY_PLATFORM_CLEARING_CODE,
    PLATFORM_CLEARING_CODES,
    WECHAT_CLEARING_CODE,
)

logger = logging.getLogger(__name__)

RECONCILIATION_AMOUNT_TOLERANCE = Decimal("0.01")

_NULL_LIKE_DESCRIPTION_VALUES = {"", "null", "undefined", "none", "nil", "-"}

_GENERIC_BANK_MEMO_RE = re.compile(
    r"汇兑|转账|转出|入账|付款|支付|代扣|代付|附言|备注|用途|往来款|往来",
    re.I,
)
_EXPLICIT_IT_SERVICE_RE = re.compile(
    r"(?:(?<![a-z0-9])api(?![a-z0-9])|接口调用|接口服务|调用费|token|saas|云服务|软件订阅|"
    r"信息技术服务|平台技术服务|模型api|大模型服务)",
    re.I,
)
_PREPAYMENT_RE = re.compile(r"预付|预缴|定金|保证金|押金")
_PROCUREMENT_RE = re.compile(r"货款|材料款|采购款|商品尾款|采购订单|采购合同|应付账款")
_BANK_FEE_RE = re.compile(
    r"手续费|转账费|账户管理费|短信费|企业银行各项费用|网上企业银行服务费|网银服务费|"
    r"对公中间业务|小额账户管理费|电子回单费|工本费|密码器|U盾",
    re.I,
)
_COUNTERPARTY_IT_INDUSTRY_RE = re.compile(
    r"人工智能|信息技术|软件开发|软件服务|软件科技|云计算|数据服务|数据科技|网络科技|互联网科技",
    re.I,
)
_REFUND_RE = re.compile(r"退款|退回|冲正|撤销", re.I)
_PENALTY_EXPENSE_RE = re.compile(
    r"罚款|罚金|滞纳金|行政处罚|(?:税务|税局|个税|申报|逾期)[^，。；\n]{0,12}罚"
)
_RULE_PURPOSE_TRAILING_NUMBER_RE = re.compile(r"(?<!\d)\d{4,}(?!\d)")

_RULE_SCHEMA_COLUMNS = {
    "id": "TEXT",
    "counterparty_key": "TEXT NOT NULL DEFAULT ''",
    "purpose_key": "TEXT NOT NULL DEFAULT ''",
    "account_code": "TEXT",
    "confidence": "REAL NOT NULL DEFAULT 0.98",
    "source_statement_id": "TEXT",
    "created_by": "TEXT",
    "enabled": "INTEGER NOT NULL DEFAULT 1",
    "rule_version": "INTEGER NOT NULL DEFAULT 1",
    "hit_count": "INTEGER NOT NULL DEFAULT 0",
    "last_hit_at": "TEXT",
    "last_hit_statement_id": "TEXT",
    "override_count": "INTEGER NOT NULL DEFAULT 0",
    "amount_min": "NUMERIC(20,2)",
    "amount_max": "NUMERIC(20,2)",
    "amount_hit_count": "INTEGER NOT NULL DEFAULT 0",
    "created_at": "TEXT",
}

_RULE_DIRECTIONAL_REVIEW_CATEGORIES = {
    "研发支出-费用化支出",
    "工资发放",
    "社保公积金",
    "个税代缴",
    "微信支付结算",
    "支付宝结算",
    "平台提现结算",
    "往来退款",
}

SALARY_PAYMENT_KEYWORDS = (
    "代发工资", "工资代发", "代发薪资", "代发薪", "薪资", "薪酬",
    "工资", "薪金", "奖金", "代发",
)
PAYROLL_LIABILITY_KEYWORDS = (
    "社保", "社会保险", "公积金", "住房公积金", "社保公积金",
)
# 财税库行/金库通用扣款通道：摘要「税费社保」只是通道名，不代表险种。
# 社保费自 2020 年起由税务代收，税款、社保、个税、罚款、滞纳金在流水上同形。
_TAX_CHANNEL_RE = re.compile(r"税费|税款|财税库行|税库|金库|国库|税务")
# 只有出现明确险种/公积金表述，才足以把流水判给工资模块核销
_EXPLICIT_PAYROLL_LIABILITY_RE = re.compile(
    r"社会保险|社保费|社保缴纳|社保公积金|公积金|医疗保险|养老保险|失业保险|工伤保险|生育保险|社保中心"
)
# 只认明确的个税字样：「税款」「税务」属于通用通道词，留给税费模糊组让用户拍板
PAYROLL_TAX_KEYWORDS = (
    "个人所得税", "个税",
)


def is_salary_payment_description(description: str) -> bool:
    text = str(description or "")
    return any(keyword in text for keyword in SALARY_PAYMENT_KEYWORDS)


def is_ambiguous_tax_channel_description(description: str) -> bool:
    """摘要是否只是财税库行通用通道（如「税费社保」），无法区分税款/社保/罚款。"""
    text = str(description or "")
    return bool(_TAX_CHANNEL_RE.search(text)) and not _EXPLICIT_PAYROLL_LIABILITY_RE.search(text)


def is_payroll_liability_description(description: str) -> bool:
    text = str(description or "")
    if not any(keyword in text for keyword in PAYROLL_LIABILITY_KEYWORDS):
        return False
    # 2026-10 真实误判：「税费社保 (对方: 金库杭州市余杭区支库)」-200 实为个税
    # 逾期申报罚款，只因摘要含「社保」二字就被短路成 221102 社保公积金，
    # 还提示「需工资模块核销」。通道词必须有明确险种/公积金表述才归工资模块。
    if is_ambiguous_tax_channel_description(text):
        return False
    return True


def is_payroll_tax_description(description: str) -> bool:
    """摘要是否明确指向代扣个税（「税款」「税单号」这类通用通道词不算）。"""
    text = str(description or "")
    return any(keyword in text for keyword in PAYROLL_TAX_KEYWORDS)


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
    "个税代缴",
    "微信支付结算",
    "支付宝结算",
    "平台提现结算",
    # 退款对冲是上下文相关的（跟具体哪笔付款配对），沉淀成对手方规则
    # 会把以后真正的收入也吞掉
    "往来退款",
    # 一次性税务罚款与滞纳金属于偶发非经营支出，禁止沉淀为「金库 XX 支库」等对手方常驻规则
    "罚款支出",
    "税收滞纳金",
}


def extract_statement_counterparty(description: str) -> str:
    """Extract counterparty name from statement description like '... (对方: xxx)'.

    银行导出既有半角也有全角括号，个别行把户名写在「对方户名：X」里；三种都要认，
    否则同名单位会被拆成多张往来档案。
    """
    text = str(description or "").strip()
    match = re.search(r"[(（]对方\s*[:：]\s*(.+?)[)）]\s*$", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"对方户名\s*[:：]\s*([^\s|｜,，;；)）]+)", text)
    if match:
        return match.group(1).strip()
    # 招行等导出把户名放进业务摘要末尾括号：「转入-项目尾款(杭州白鹭文化传媒有限公司)」。
    # 末尾括号里还可能是「(个人客户)」「(5元)」这类说明，只有明显是单位/姓名才取。
    match = re.search(r"[（(]\s*([^()（）]{2,40}?)\s*[)）]\s*$", text)
    if match:
        from services.auxiliary_service import counterparty_is_confident

        candidate = match.group(1).strip()
        if counterparty_is_confident(candidate):
            return candidate
    return ""


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


def normalize_category_rule_key(value: Any) -> str:
    """Normalize a tenant rule key without depending on punctuation or ASCII case."""
    return _description_semantic_key(str(value or ""))[:160]


def extract_statement_counterparty_key(description: str) -> str:
    return normalize_category_rule_key(extract_statement_counterparty(description))


def extract_statement_purpose_key(description: str) -> str:
    """Extract reusable business purpose while discarding bank boilerplate.

    Counterparty identity and business purpose are deliberately stored as two
    dimensions. A supplier can sell cloud service one month and rent an office
    the next; collapsing those into one counterparty-only rule silently applies
    the previous accounting decision to unrelated transactions.
    """
    text = re.sub(r"\(对方\s*[:：].*?\)\s*$", "", str(description or ""), flags=re.I)
    if _REFUND_RE.search(text):
        return "退款"
    if _EXPLICIT_IT_SERVICE_RE.search(text):
        return "信息技术服务"
    if _PREPAYMENT_RE.search(text):
        return "预付往来"
    if _PROCUREMENT_RE.search(text):
        return "采购"
    if _BANK_FEE_RE.search(text):
        return "银行手续费"
    if is_payroll_liability_description(text):
        return "社保公积金"
    if is_payroll_tax_description(text):
        return "个税代缴"
    if is_salary_payment_description(text):
        return "工资"

    cleaned = _GENERIC_BANK_MEMO_RE.sub("", text)
    cleaned = _RULE_PURPOSE_TRAILING_NUMBER_RE.sub("", cleaned)
    key = normalize_category_rule_key(cleaned)
    return key if len(key) >= 2 else ""


def _category_rule_match_pattern(counterparty_key: str, purpose_key: str) -> str:
    if counterparty_key and purpose_key:
        return f"{counterparty_key}::purpose={purpose_key}"
    if counterparty_key:
        return counterparty_key
    return f"purpose={purpose_key}"


def _category_rule_account_code(category: str) -> Optional[str]:
    info = lookup_account(category)
    return str(info.get("accountCode") or "") if info else None


def _category_rule_direction_allowed(category: str, direction: str) -> bool:
    info = lookup_account(category)
    if not info:
        return False
    account_type = str(info.get("accountType") or "")
    if direction == "income":
        # Bank inflows can settle a liability (other payables, shareholder
        # advance) as well as book revenue. Only reject expense/cost accounts,
        # with interest income as the explicit expense-account exception.
        return (
            account_type not in {"损益-费用", "损益-成本"}
            or account_type == "损益-收入"
            or category == "利息收入"
        )
    if direction == "expense":
        return account_type != "损益-收入"
    return True


def _serialize_category_rule(row: Any) -> Dict[str, Any]:
    item = dict(row)
    purpose = str(item.get("purpose_key") or "")
    try:
        stored_confidence = float(item.get("confidence") or 0.98)
    except (TypeError, ValueError):
        stored_confidence = 0.98
    # Rules without a reusable purpose remain useful hints, but must never be
    # auto-selected: the same counterparty may carry different businesses.
    effective_confidence = stored_confidence if purpose else min(stored_confidence, 0.78)
    item.update({
        "id": str(item.get("id") or ""),
        "counterpartyKey": str(item.get("counterparty_key") or ""),
        "purposeKey": purpose,
        "matchPattern": str(item.get("match_pattern") or ""),
        "category": str(item.get("category") or ""),
        "accountCode": str(item.get("account_code") or _category_rule_account_code(
            str(item.get("category") or "")
        ) or ""),
        "confidence": effective_confidence,
        "storedConfidence": stored_confidence,
        "enabled": bool(item.get("enabled", 1)),
        "ruleVersion": int(item.get("rule_version") or 1),
        "hitCount": int(item.get("hit_count") or 0),
        "overrideCount": int(item.get("override_count") or 0),
        "amountHitCount": int(item.get("amount_hit_count") or 0),
        "sourceStatementId": item.get("source_statement_id") or "",
        "createdBy": item.get("created_by") or "",
        "lastHitAt": item.get("last_hit_at"),
        "lastHitStatementId": item.get("last_hit_statement_id") or "",
        "amountMin": item.get("amount_min"),
        "amountMax": item.get("amount_max"),
        "createdAt": item.get("created_at") or item.get("updated_at"),
        "updatedAt": item.get("updated_at"),
    })
    return item


async def list_company_category_rules(
    company_id: str,
    *,
    query: str = "",
    direction: str = "",
    enabled: Optional[bool] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    await _ensure_schema()
    clauses = ["company_id = ?"]
    params: list[Any] = [company_id]
    if direction in {"income", "expense"}:
        clauses.append("direction = ?")
        params.append(direction)
    if enabled is not None:
        clauses.append("enabled = ?")
        params.append(1 if enabled else 0)
    keyword = str(query or "").strip()
    if keyword:
        clauses.append(
            "(counterparty_key LIKE ? OR purpose_key LIKE ? OR category LIKE ? OR account_code LIKE ?)"
        )
        like = f"%{normalize_category_rule_key(keyword)}%"
        params.extend([like, like, f"%{keyword}%", f"%{keyword}%"])

    conn = await get_conn()
    try:
        where = " AND ".join(clauses)
        count_row = await (
            await conn.execute(
                f"SELECT COUNT(*) AS total FROM bank_category_rules WHERE {where}",
                tuple(params),
            )
        ).fetchone()
        cursor = await conn.execute(
            "SELECT * FROM bank_category_rules "
            f"WHERE {where} ORDER BY enabled DESC, updated_at DESC, category ASC "
            "LIMIT ? OFFSET ?",
            tuple([*params, max(1, min(limit, 500)), max(0, offset)]),
        )
        rows = [_serialize_category_rule(row) for row in await cursor.fetchall()]
    finally:
        await conn.close()
    return {"items": rows, "total": int(count_row["total"] if count_row else 0)}


async def update_company_category_rule(
    company_id: str,
    rule_id: str,
    updates: Dict[str, Any],
    *,
    user_id: str = "",
) -> Optional[Dict[str, Any]]:
    await _ensure_schema()
    conn = await get_conn()
    try:
        row = await (
            await conn.execute(
                "SELECT * FROM bank_category_rules WHERE company_id = ? AND id = ?",
                (company_id, rule_id),
            )
        ).fetchone()
        if not row:
            return None
        current = _serialize_category_rule(row)
        fields: list[str] = []
        params: list[Any] = []
        category = str(updates.get("category") or current["category"]).strip()
        if not lookup_account(category):
            raise HTTPException(status_code=422, detail="分类不存在")
        if not _category_rule_direction_allowed(category, str(current.get("direction") or "")):
            raise HTTPException(status_code=422, detail="该分类与规则收支方向不一致")
        if "category" in updates:
            fields.extend(["category = ?", "account_code = ?"])
            params.extend([category, _category_rule_account_code(category)])

        if "purposeKey" in updates:
            purpose = normalize_category_rule_key(updates.get("purposeKey"))
            counterparty = str(current.get("counterpartyKey") or "")
            if not counterparty and not purpose:
                raise HTTPException(status_code=422, detail="对方和业务用途不能同时为空")
            fields.extend(["purpose_key = ?", "match_pattern = ?"])
            params.extend([purpose, _category_rule_match_pattern(counterparty, purpose)])

        if "enabled" in updates and updates.get("enabled") is not None:
            fields.append("enabled = ?")
            params.append(1 if updates.get("enabled") else 0)
        if "confidence" in updates and updates.get("confidence") is not None:
            fields.append("confidence = ?")
            params.append(max(0.5, min(float(updates["confidence"]), 1.0)))
        if not fields:
            return current

        fields.extend(["rule_version = rule_version + 1", "updated_at = ?"])
        params.extend([_now_iso(), rule_id, company_id])
        await conn.execute(
            f"UPDATE bank_category_rules SET {', '.join(fields)} "
            "WHERE id = ? AND company_id = ?",
            tuple(params),
        )
        await conn.commit()
        updated = await (
            await conn.execute(
                "SELECT * FROM bank_category_rules WHERE company_id = ? AND id = ?",
                (company_id, rule_id),
            )
        ).fetchone()
    except HTTPException:
        raise
    except Exception as exc:
        await conn.rollback()
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(status_code=409, detail="已有相同对方与业务用途的规则") from exc
        raise
    finally:
        await conn.close()
    result = _serialize_category_rule(updated) if updated else None
    if result:
        await log_action(
            company_id,
            user_id,
            "update",
            "bank_category_rule",
            rule_id,
            {"category": result["category"], "enabled": result["enabled"]},
        )
    return result


async def delete_company_category_rule(
    company_id: str, rule_id: str, *, user_id: str = ""
) -> bool:
    await _ensure_schema()
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "DELETE FROM bank_category_rules WHERE company_id = ? AND id = ?",
            (company_id, rule_id),
        )
        await conn.commit()
    finally:
        await conn.close()
    deleted = bool(cursor.rowcount)
    if deleted:
        await log_action(
            company_id,
            user_id,
            "delete",
            "bank_category_rule",
            rule_id,
        )
    return deleted


async def save_company_category_rule(
    company_id: str,
    description: str,
    stmt_amount: Any,
    category: str,
    *,
    source_statement_id: str = "",
    user_id: str = "",
) -> Optional[str]:
    """Persist a company-level statement classification rule when user opts in."""
    cat = str(category or "").strip()
    if not cat or cat in _NON_RULE_CATEGORIES or is_ambiguity_category(cat):
        return None
    counterparty = extract_statement_counterparty_key(description)
    purpose = extract_statement_purpose_key(description)
    if not counterparty and not purpose:
        return None
    direction = "income" if safe_money(stmt_amount) >= ZERO else "expense"
    if not _category_rule_direction_allowed(cat, direction):
        return None
    pattern = _category_rule_match_pattern(counterparty, purpose)
    now = _now_iso()
    confidence = 0.98 if purpose else 0.78
    amount = abs(safe_money(stmt_amount))
    await _ensure_schema()
    conn = await get_conn()
    try:
        await conn.execute(
            "INSERT INTO bank_category_rules "
            "(id, company_id, direction, match_pattern, counterparty_key, purpose_key, "
            "category, account_code, confidence, source_statement_id, created_by, enabled, "
            "rule_version, hit_count, last_hit_at, last_hit_statement_id, override_count, "
            "amount_min, amount_max, amount_hit_count, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 0, NULL, NULL, 0, ?, ?, 1, ?, ?) "
            "ON CONFLICT (company_id, direction, match_pattern) DO UPDATE SET "
            "counterparty_key = excluded.counterparty_key, purpose_key = excluded.purpose_key, "
            "category = excluded.category, account_code = excluded.account_code, "
            "confidence = excluded.confidence, source_statement_id = excluded.source_statement_id, "
            "created_by = COALESCE(bank_category_rules.created_by, excluded.created_by), "
            "enabled = 1, rule_version = bank_category_rules.rule_version + 1, "
            "override_count = bank_category_rules.override_count + "
            "CASE WHEN bank_category_rules.category != excluded.category THEN 1 ELSE 0 END, "
            "amount_min = CASE WHEN bank_category_rules.amount_min IS NULL "
            "OR excluded.amount_min < bank_category_rules.amount_min "
            "THEN excluded.amount_min ELSE bank_category_rules.amount_min END, "
            "amount_max = CASE WHEN bank_category_rules.amount_max IS NULL "
            "OR excluded.amount_max > bank_category_rules.amount_max "
            "THEN excluded.amount_max ELSE bank_category_rules.amount_max END, "
            "amount_hit_count = bank_category_rules.amount_hit_count + 1, "
            "updated_at = excluded.updated_at",
            (
                f"bcr-{uuid4().hex}",
                company_id,
                direction,
                pattern,
                counterparty,
                purpose,
                cat,
                _category_rule_account_code(cat),
                confidence,
                source_statement_id or None,
                user_id or None,
                amount,
                amount,
                now,
                now,
            ),
        )
        await conn.commit()
    finally:
        await conn.close()
    await log_action(
        company_id,
        user_id,
        "upsert",
        "bank_category_rule",
        pattern,
        {"category": cat, "counterparty": counterparty, "purpose": purpose},
    )
    return extract_statement_rule_pattern(description) or purpose


def build_balance_reconciliation(
    statements: List[Dict[str, Any]],
    book_bank_balance: Any,
) -> Dict[str, Any]:
    """Reconcile statement continuity and the book's 1002 ending balance."""
    # 同一份导出在同一秒内按行号升序排列即真实时序（见 _STATEMENT_ORDER）；
    # 没有行序的历史行退回 id。旧的"id 倒序"tie-break 在 SQLite 的 uuid4
    # 路径下是噪声，会把同秒多笔倒着算，期初/期末/连续差额全部失真。
    ordered = sorted(
        statements,
        key=lambda item: (
            str(item.get("transaction_date") or ""),
            1 if item.get("source_row_index") is None else 0,
            item.get("source_row_index") if item.get("source_row_index") is not None else 0,
            str(item.get("id") or ""),
        ),
    )
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
    """找与这笔流水等额同向、流水当日及之前 3 天内、且尚未对账的银行存款凭证。

    命中即说明账上已经记过这笔钱：应当配对而不是新建凭证，否则 1002 会重复计入。
    晚于流水日的凭证不算重复（那是"先付款、后收票/后补记"，应走往来暂付模型，
    配上去会让流水所在期间的银行核对永远关不上）。
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
            # reversed 不算重复：红冲后重新入账正是用户想要的路径
            JournalEntry.status.in_(("draft", "pending_review", "posted")),
            JournalEntry.entry_type == "standard",
            JournalEntry.occurred_on >= statement_date - timedelta(days=3),
            JournalEntry.occurred_on <= statement_date,
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


def _entry_not_after_statement(entry_occur_date: Any, stmt_date: Any) -> bool:
    """候选凭证日期不得晚于流水日期。

    晚于即"先付款、后收票/后补记"（如 5 月付款、6 月收发票）：应走往来暂付
    模型（当期挂 122199、收票时冲销），而不是把 5 月流水跨期配到 6 月凭证——
    那会让两个月的银行核对都关不上。空日期无法判定时放行。
    """
    entry_day = str(entry_occur_date or "")[:10]
    stmt_day = str(stmt_date or "")[:10]
    if not entry_day or not stmt_day:
        return True
    return entry_day <= stmt_day


async def _load_reversed_entry_ids(company_id: str) -> set:
    """已红冲/作废凭证 id 集合。

    红冲不回写 legacy 投影 payload（无 status 键），候选构建只看投影会继续
    把被冲销凭证当配对候选 → 确认配对必报"已作废或冲销的凭证不能用于新对账"。
    以核心 journal_entries 的状态为准；legacy 夹具库无此表时返回空集。
    """
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT id FROM journal_entries WHERE company_id = ? AND status = 'reversed'",
            (company_id,),
        )
        rows = await cursor.fetchall()
        return {str(row["id"]) for row in rows}
    except Exception:
        # legacy 夹具库可能没有 journal_entries 表（纯投影测试环境）
        return set()
    finally:
        await conn.close()


def _fallback_classification_for_statement(stmt: Dict[str, Any], confidence: float = 0.3) -> Dict[str, Any]:
    stmt_amount = safe_money(stmt.get("amount"))
    if stmt_amount < ZERO and is_ambiguous_tax_channel_description(str(stmt.get("description") or "")):
        # 通道词兜底也给出税费模糊组，别退化成「其他支出」让用户无从选起
        return _tax_channel_classification(stmt)
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


def _statement_business_purpose_text(description: Any) -> str:
    """Remove counterparty and generic bank wording, leaving actual business evidence."""
    text = re.sub(r"\(对方\s*[:：].*?\)\s*$", "", str(description or ""), flags=re.I)
    text = _GENERIC_BANK_MEMO_RE.sub("", text)
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _needs_business_purpose_confirmation(stmt: Dict[str, Any]) -> bool:
    """A bank transfer with only a company name and generic memo needs user input."""
    return len(_statement_business_purpose_text(stmt.get("description"))) < 2


def _business_purpose_confirmation_classification(
    stmt: Dict[str, Any], reason: str = ""
) -> Dict[str, Any]:
    result = _fallback_classification_for_statement(stmt, confidence=0.45)
    date_str = str(stmt.get("transaction_date") or "")[:10]
    result["title"] = f"待确认业务用途-{date_str}" if date_str else "待确认业务用途"
    result["reason"] = reason or (
        "银行摘要只有交易对方和汇兑/往来款等默认备注，无法确定真实业务用途；"
        "请选择实际分类后再入账"
    )
    result["requiresConfirmation"] = True
    return result


def _counterparty_it_industry_evidence(stmt: Dict[str, Any]) -> str:
    """Extract a generic IT-industry cue from the counterparty name only."""
    match = re.search(r"\(对方\s*[:：]\s*(.*?)\)\s*$", str(stmt.get("description") or ""))
    if not match:
        return ""
    industry = _COUNTERPARTY_IT_INDUSTRY_RE.search(match.group(1))
    return industry.group(0) if industry else ""


def _industry_inferred_it_classification(
    stmt: Dict[str, Any], industry: str, model_category: str = ""
) -> Dict[str, Any]:
    date_str = str(stmt.get("transaction_date") or "")[:10]
    source = f"；模型初判为「{model_category}」" if model_category else ""
    return {
        "direction": "expense",
        "category": "信息技术服务费",
        "accountCode": "5602015",
        "accountName": "管理费用-信息技术服务费",
        "title": f"信息技术服务费-{date_str}" if date_str else "信息技术服务费",
        "confidence": 0.72,
        "requiresConfirmation": True,
        "reason": (
            f"交易对手名称包含「{industry}」行业词，银行摘要缺少更具体业务说明{source}；"
            "按低置信度信息技术服务费建议，入账前请核对合同或发票"
        ),
    }


def identify_explicit_it_service_rule(
    stmt: Dict[str, Any], business_context: str = ""
) -> Optional[Dict[str, Any]]:
    """Use explicit API/software/service wording without maintaining vendor allowlists."""
    amount = safe_money(stmt.get("amount"))
    description = " ".join(
        part for part in (
            str(stmt.get("description") or "").strip(),
            business_context.strip(),
        ) if part
    )
    if amount >= ZERO or _PREPAYMENT_RE.search(description):
        return None
    match = _EXPLICIT_IT_SERVICE_RE.search(description)
    if not match:
        return None
    date_str = str(stmt.get("transaction_date") or "")[:10]
    return {
        "type": "new",
        "category": "信息技术服务费",
        "title": f"信息技术服务费-{date_str}" if date_str else "信息技术服务费",
        "confidence": 0.94,
        "rule": "explicit_it_service_rule",
        "reason": f"银行摘要明确包含「{match.group(0)}」，按信息技术服务费入账",
    }


def guard_statement_classification(
    stmt: Dict[str, Any],
    classification: Dict[str, Any],
    business_context: str = "",
) -> Dict[str, Any]:
    """Reject classifications that require evidence absent from the bank statement."""
    if business_context.strip():
        return classification
    category = str(classification.get("category") or "").strip()
    description = str(stmt.get("description") or "")
    # 财税库行通道词不能仅凭「社保」二字判社保：模型偶发归社保时改回税费模糊组
    # （只对支出方向生效，收入侧「税务退费」等不能翻成支出）
    if (
        category == "社保公积金"
        and safe_money(stmt.get("amount")) < ZERO
        and is_ambiguous_tax_channel_description(description)
    ):
        return _tax_channel_classification(stmt)
    needs_confirmation = _needs_business_purpose_confirmation(stmt)
    industry = _counterparty_it_industry_evidence(stmt) if needs_confirmation else ""
    if category == "供应商付款" and not _PROCUREMENT_RE.search(description):
        if industry:
            return _industry_inferred_it_classification(stmt, industry, category)
        return _business_purpose_confirmation_classification(
            stmt,
            "摘要没有货款、采购款或应付账款等采购证据，不能仅凭公司名称记入应付账款；"
            "请确认这是采购挂账、服务费用还是其他往来",
        )
    if category == "银行及金融手续费" and not _BANK_FEE_RE.search(description):
        return _business_purpose_confirmation_classification(
            stmt,
            "摘要只有普通服务费，没有银行手续费、转账费或账户管理费证据；"
            "请先确认实际服务内容",
        )
    if industry and category in {"往来款", "其他支出", "信息技术服务费"}:
        return _industry_inferred_it_classification(stmt, industry, category)
    return classification


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


def _platform_clearing_effect(voucher: Dict[str, Any]) -> Decimal:
    """凭证对平台待结算科目的净影响（借正贷负）。

    微信、支付宝的资金路径相同：先挂各自待结算科目，提现再转银行存款。
    单条凭证只应命中一个平台科目；求和是为了兼容拆分到同一科目的多行。
    """
    return money(sum((
        _account_effect_from_voucher(voucher, code) for code in PLATFORM_CLEARING_CODES
    ), ZERO))


PLATFORM_CLEARING_INFO: Dict[str, Dict[str, str]] = {
    WECHAT_CLEARING_CODE: {
        "platform": "wechat", "clearingCode": WECHAT_CLEARING_CODE,
        "label": "微信支付", "clearingName": "微信支付待结算",
        "category": "微信支付结算", "reconciliationType": "wechat_settlement",
        "titlePrefix": "微信支付净额结算",
    },
    ALIPAY_CLEARING_CODE: {
        "platform": "alipay", "clearingCode": ALIPAY_CLEARING_CODE,
        "label": "支付宝", "clearingName": "支付宝待结算",
        "category": "支付宝结算", "reconciliationType": "alipay_settlement",
        "titlePrefix": "支付宝净额结算",
    },
    LEGACY_PLATFORM_CLEARING_CODE: {
        "platform": "platform", "clearingCode": LEGACY_PLATFORM_CLEARING_CODE,
        "label": "平台", "clearingName": "其他货币资金",
        "category": "平台提现结算", "reconciliationType": "platform_settlement",
        "titlePrefix": "平台净额结算",
    },
}


def _platform_clearing_code(voucher: Dict[str, Any]) -> str:
    for code in PLATFORM_CLEARING_CODES:
        if _account_effect_from_voucher(voucher, code) != ZERO:
            return code
    return ""


def platform_clearing_codes_for_payloads(payloads: List[Dict[str, Any]]) -> set[str]:
    """一组待核销凭证实际命中的平台清算科目集合（空集 = 无平台待结算影响）。"""
    codes = {_platform_clearing_code(payload.get("voucherLines") or {}) for payload in payloads}
    codes.discard("")
    return codes


def platform_clearing_code_for_payloads(payloads: List[Dict[str, Any]]) -> str:
    """单一平台清算科目；混合平台返回空串由调用方拒绝。"""
    codes = platform_clearing_codes_for_payloads(payloads)
    return next(iter(codes)) if len(codes) == 1 else ""


def platform_clearing_info(statement: Dict[str, Any], payloads: List[Dict[str, Any]]) -> Dict[str, str]:
    """清算科目优先取凭证事实；凭证缺失时按流水摘要兜底（存量数据修复用）。"""
    code = platform_clearing_code_for_payloads(payloads)
    if code not in PLATFORM_CLEARING_INFO:
        description = str(statement.get("description") or "")
        code = ALIPAY_CLEARING_CODE if re.search(r"支付宝|Alipay|蚂蚁金服", description, re.I) else WECHAT_CLEARING_CODE
    return PLATFORM_CLEARING_INFO[code]


def _entry_group_reconciliation_effect(payload: Dict[str, Any]) -> Decimal:
    """Return the cash/settlement effect used when grouping entries to one statement.

    Platform merchant transactions accrue through their clearing account
    (1012001 微信 / 1012002 支付宝 / 1012 历史) before the net receipt reaches
    the bank, so a gross receipt plus its fee must be reconciled from the
    clearing-account net effect.
    """
    voucher = payload.get("voucherLines") or {}
    if not voucher:
        return _entry_bank_effect_from_payload(payload)
    bank_effect = _bank_effect_from_voucher(voucher)
    if bank_effect != ZERO:
        return bank_effect
    settlement_effect = _platform_clearing_effect(voucher)
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
    """Validate one bank statement against the aggregate bank effect of many entries.

    组内只要有任何凭证真正动了银行存款（1002），整组就按银行影响对账：平台结算链
    （收入计提/手续费挂 1012001 待结算，结算凭证借 1002 贷 1012001）里，挂账凭证的
    清算户影响已被结算凭证吸收，再按清算净额计入会双重计数——2026-11 生产案例：
    [结算凭证 58.47 + 收入计提 59.00 + 手续费 -0.53] 被算成 116.94，结账向导
    「高风险对账问题」据此误锁关账。清算户净额只在该组没有任何 1002 影响
    （银行到账直接对到计提/手续费凭证）时才作为对账口径（毛收入 - 手续费 = 到账净额）。
    """
    statement_amount = safe_money(statement_amount)
    bank_effect = money(sum((
        _entry_bank_effect_from_payload(payload)
        for payload in payloads
    ), ZERO))
    if bank_effect != ZERO:
        entry_bank_effect = bank_effect
        per_entry_effect = _entry_bank_effect_from_payload
    else:
        entry_bank_effect = money(sum((
            _entry_group_reconciliation_effect(payload)
            for payload in payloads
        ), ZERO))
        per_entry_effect = _entry_group_reconciliation_effect
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
                "bankEffect": money(per_entry_effect(payload)),
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
        _platform_clearing_effect(payload.get("voucherLines") or {})
        for payload in payloads
    ), ZERO))
    clearing_info = platform_clearing_info(statement, payloads)
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
        reason = f"缺少{clearing_info['label']}待结算转入银行存款的结算凭证"
        repairable = True
        repair_label = "生成结算凭证"
        repair_description = (
            f"生成银行存款与{clearing_info['clearingName']}之间的 {abs(statement_amount):.2f} 元结算凭证"
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

# 流水排序的唯一定义：同秒多笔只靠日期分不出先后，靠导入时保存的文件行序
# （同一份银行导出在同一秒内按行号升序排列即真实时序）；没有行序的历史行
# 退回 id。从 "ORDER BY transaction_date" 改成这个片段的地方必须一起改，
# 否则列表顺序与余额核对顺序会各说各话。
_STATEMENT_ORDER = (
    "transaction_date ASC, source_row_index IS NULL, source_row_index ASC, id ASC"
)


def normalize_bank_reference(value: Any) -> str:
    """银行流水号归一化；返回空串表示"该行没有可用的引用号"。

    引用号是银行给的交易身份，可以直接判重；但只有能精确表示的才收：
    JSON 数字超过 2^53 已在浏览器端丢精度（中国银行「记录标识号」25 位就是
    这种，读出来是 259755500999999978999999978 的垃圾值），宁可当作没有。
    """
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float):
        # 超出 2^53 的浮点已经丢精度，int() 出来的是看似完整的假号码
        if not value.is_integer() or abs(value) > 2**53 - 1:
            return ""
        value = int(value)
    text_value = str(value).strip().replace(" ", "").replace("\u3000", "")
    if not text_value:
        return ""
    # 科学计数法：精度已不可逆（Excel 存大数的典型形态）
    if re.search(r"[eE][+-]?\d", text_value):
        return ""
    if re.fullmatch(r"\d+\.0+", text_value):
        text_value = text_value.split(".", 1)[0]
    if re.fullmatch(r"0+", text_value):
        # 全零是「客户业务编号」这类占位列，不是身份
        return ""
    if len(text_value) > 64:
        return ""
    return text_value


async def _ensure_schema() -> None:
    """进程级一次性 schema 守卫。

    建表/补列/历史描述归一化都是幂等迁移动作：首次调用执行，之后整个进程
    短路（此前每个银行接口都重复跑 DDL 判定 + 全表扫 description_normalized=0；
    新写入行在导入时已带 normalized 描述，补记只针对存量数据）。
    PostgreSQL 下这些 DDL 经 database._translate_postgres 翻译为 no-op，
    正式 schema 依赖 Alembic 迁移先行（b7e3d1c8a4f2 补引用号与行序）。
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
            "  created_at TEXT NOT NULL,"
            "  transaction_reference TEXT,"
            "  source_row_index INTEGER"
            ")"
        )
        columns = await (await conn.execute("PRAGMA table_info(bank_statements)")).fetchall()
        column_names = {row["name"] for row in columns}
        if "description_normalized" not in column_names:
            await conn.execute(
                "ALTER TABLE bank_statements "
                "ADD COLUMN description_normalized INTEGER NOT NULL DEFAULT 0"
            )
        if "transaction_reference" not in column_names:
            await conn.execute(
                "ALTER TABLE bank_statements ADD COLUMN transaction_reference TEXT"
            )
        if "source_row_index" not in column_names:
            await conn.execute(
                "ALTER TABLE bank_statements ADD COLUMN source_row_index INTEGER"
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
        # 引用号只做查找加速，不做唯一约束：流水表没有账户列，同公司多账户
        # 理论上可撞号；且唯一冲突会掀掉整批导入事务（导入只按行捕获业务异常）。
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bank_reference "
            "ON bank_statements(company_id, transaction_reference)"
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
            "  id TEXT,"
            "  company_id TEXT NOT NULL,"
            "  direction TEXT NOT NULL,"
            "  match_pattern TEXT NOT NULL,"
            "  counterparty_key TEXT NOT NULL DEFAULT '',"
            "  purpose_key TEXT NOT NULL DEFAULT '',"
            "  category TEXT NOT NULL,"
            "  account_code TEXT,"
            "  confidence REAL NOT NULL DEFAULT 0.98,"
            "  source_statement_id TEXT,"
            "  created_by TEXT,"
            "  enabled INTEGER NOT NULL DEFAULT 1,"
            "  rule_version INTEGER NOT NULL DEFAULT 1,"
            "  hit_count INTEGER NOT NULL DEFAULT 0,"
            "  last_hit_at TEXT,"
            "  last_hit_statement_id TEXT,"
            "  override_count INTEGER NOT NULL DEFAULT 0,"
            "  amount_min NUMERIC(20,2),"
            "  amount_max NUMERIC(20,2),"
            "  amount_hit_count INTEGER NOT NULL DEFAULT 0,"
            "  created_at TEXT,"
            "  updated_at TEXT NOT NULL,"
            "  PRIMARY KEY (company_id, direction, match_pattern)"
            ")"
        )
        rule_columns = {
            row["name"]
            for row in await (await conn.execute("PRAGMA table_info(bank_category_rules)")).fetchall()
        }
        for column, definition in _RULE_SCHEMA_COLUMNS.items():
            if column not in rule_columns:
                await conn.execute(
                    f"ALTER TABLE bank_category_rules ADD COLUMN {column} {definition}"
                )
        await conn.execute(
            "UPDATE bank_category_rules SET "
            "id = 'legacy-' || company_id || '-' || direction || '-' || match_pattern "
            "WHERE id IS NULL OR id = ''"
        )
        await conn.execute(
            "UPDATE bank_category_rules SET "
            "counterparty_key = match_pattern "
            "WHERE counterparty_key IS NULL OR counterparty_key = ''"
        )
        await conn.execute(
            "UPDATE bank_category_rules SET purpose_key = '' "
            "WHERE purpose_key IS NULL"
        )
        await conn.execute(
            "UPDATE bank_category_rules SET created_at = updated_at "
            "WHERE created_at IS NULL OR created_at = ''"
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_bank_category_rule_id "
            "ON bank_category_rules(id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bank_category_rule_lookup "
            "ON bank_category_rules(company_id, direction, enabled, counterparty_key, purpose_key)"
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


async def _load_company_category_rules(company_id: str) -> List[Dict[str, Any]]:
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM bank_category_rules WHERE company_id = ?",
            (company_id,),
        )
        return [_serialize_category_rule(row) for row in await cursor.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 bank_category_rules 失败，本次跳过企业记忆规则: %s", exc)
        return []
    finally:
        await conn.close()


async def _load_closed_bank_months(company_id: str) -> set[str]:
    """Best-effort read of closed periods for rule-only suggestion guards."""
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT period_start, status FROM accounting_periods WHERE company_id = ?",
            (company_id,),
        )
        return {
            str(row["period_start"])[:7]
            for row in await cursor.fetchall()
            if str(row["status"] or "") == "closed"
        }
    except Exception:
        # Legacy fixture databases may not contain accounting_periods. The
        # authoritative posting path still enforces the same rule on confirm.
        return set()
    finally:
        await conn.close()


def match_company_category_rule(
    statement: Dict[str, Any],
    rules: List[Dict[str, Any]],
    *,
    closed_months: Optional[set[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Return the most specific enabled tenant rule for one statement."""
    amount = safe_money(statement.get("amount"))
    direction = "income" if amount >= ZERO else "expense"
    counterparty = extract_statement_counterparty_key(str(statement.get("description") or ""))
    purpose = extract_statement_purpose_key(str(statement.get("description") or ""))
    month = str(statement.get("transaction_date") or "")[:7]

    candidates: list[tuple[int, str, Dict[str, Any]]] = []
    for rule in rules:
        if not rule.get("enabled") or str(rule.get("direction") or "") != direction:
            continue
        rule_counterparty = str(rule.get("counterpartyKey") or "")
        rule_purpose = str(rule.get("purposeKey") or "")
        if rule_counterparty:
            if not counterparty or rule_counterparty != counterparty:
                continue
            if rule_purpose and rule_purpose != purpose:
                continue
            specificity = 300 if rule_purpose else 120
        else:
            if not purpose or rule_purpose != purpose:
                continue
            specificity = 220
        candidates.append((
            specificity,
            str(rule.get("updatedAt") or rule.get("createdAt") or ""),
            {**rule, "matchKind": "exact" if rule_purpose else "counterparty_only"},
        ))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    matched = dict(candidates[0][2])
    reasons: list[str] = []
    category = str(matched.get("category") or "")
    if not matched.get("purposeKey"):
        reasons.append("规则只记住了交易对方，本次业务用途仍需确认")
    if _REFUND_RE.search(str(statement.get("description") or "")) and matched.get("purposeKey") != "退款":
        reasons.append("退款/退回业务不能套用普通付款规则")
    if month and month in (closed_months or set()):
        reasons.append(f"{month} 已关账，需先确认补记期间")
    if category in _RULE_DIRECTIONAL_REVIEW_CATEGORIES or is_ambiguity_category(category):
        reasons.append("该分类需要结合账外事实逐笔确认")
    try:
        amount_max = safe_money(matched.get("amountMax"))
    except Exception:
        amount_max = ZERO
    abs_amount = abs(amount)
    if amount_max > ZERO and abs_amount > amount_max * Decimal("5"):
        reasons.append(
            f"本次金额 {abs_amount:.2f} 明显高于历史规则范围 {amount_max:.2f}"
        )
    matched["requiresReview"] = bool(reasons)
    matched["reviewReasons"] = reasons
    if reasons:
        matched["confidence"] = min(float(matched.get("confidence") or 0.5), 0.78)
    return matched


async def _record_company_category_rule_hits(
    company_id: str, hits: List[tuple[Dict[str, Any], str]]
) -> None:
    if not hits:
        return
    now = _now_iso()
    conn = await get_conn()
    try:
        for rule, statement_id in hits:
            rule_id = str(rule.get("id") or "")
            if not rule_id or rule.get("lastHitStatementId") == statement_id:
                continue
            await conn.execute(
                "UPDATE bank_category_rules SET hit_count = hit_count + 1, "
                "amount_hit_count = amount_hit_count + 1, last_hit_at = ?, "
                "last_hit_statement_id = ? WHERE company_id = ? AND id = ?",
                (now, statement_id, company_id, rule_id),
            )
        await conn.commit()
    finally:
        await conn.close()


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
                reference = normalize_bank_reference(row.get("transaction_reference"))

                # 判重同 core：有号只认号；复合键只对"入参无号或库中无号"的行生效。
                # 余额必须进键：交易后余额是逐笔连续值，同日同摘要同金额的多笔手续费
                # （一秒内连发 3 笔 -4.50）余额互异，属不同交易；漏掉余额会把它们
                # 折叠成一笔，静默少记。日期截到日，避免带时分秒与纯日期互不相等。
                cursor = await conn.execute(
                    "SELECT 1 FROM bank_statements WHERE company_id = ? AND ("
                    "  (? <> '' AND transaction_reference = ?)"
                    "  OR (substr(transaction_date, 1, 10) = ? AND description = ? "
                    "      AND amount = ? AND balance = ? AND (? = '' OR transaction_reference IS NULL))"
                    ") LIMIT 1",
                    (company_id, reference, reference, txn_date[:10], desc, amount, balance, reference)
                )
                if await cursor.fetchone():
                    skipped += 1
                    continue

                # uuid4：微秒时间戳+循环下标在同公司并发导入时可撞主键
                sid = f"bs-{company_id}-{uuid4().hex}"

                await conn.execute(
                    "INSERT INTO bank_statements "
                    "(id, company_id, transaction_date, description, amount, balance, statement_id, "
                    "description_normalized, created_at, transaction_reference, source_row_index) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                    (
                        sid, company_id, txn_date, desc, amount, balance, statement_id or sid, now,
                        reference or None, None,
                    ),
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
                reference = normalize_bank_reference(row.get("transaction_reference"))
                raw_row_index = row.get("source_row_index")
                try:
                    source_row_index = int(raw_row_index) if raw_row_index not in (None, "") else None
                except (TypeError, ValueError):
                    source_row_index = None

                # 判重同 core：有号只认号，复合键只对"入参无号或库中无号"的行生效。
                # 理由同 CSV 路径：余额互异即不同交易，不看余额会把同日同额的多笔
                # 手续费折叠成一条。
                cursor = await conn.execute(
                    "SELECT 1 FROM bank_statements WHERE company_id = ? AND ("
                    "  (? <> '' AND transaction_reference = ?)"
                    "  OR (substr(transaction_date, 1, 10) = ? AND description = ? "
                    "      AND amount = ? AND balance = ? AND (? = '' OR transaction_reference IS NULL))"
                    ") LIMIT 1",
                    (company_id, reference, reference, txn_date[:10], desc, amount, balance, reference)
                )
                if await cursor.fetchone():
                    skipped += 1
                    continue

                # uuid4：微秒时间戳+循环下标在同公司并发导入时可撞主键
                sid = f"bs-{company_id}-{uuid4().hex}"

                await conn.execute(
                    "INSERT INTO bank_statements "
                    "(id, company_id, transaction_date, description, amount, balance, statement_id, "
                    "description_normalized, created_at, transaction_reference, source_row_index) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                    (
                        sid, company_id, txn_date, desc, amount, balance, statement_id or sid, now,
                        reference or None, source_row_index,
                    ),
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

    去重键是「日期 + 摘要 + 金额 + 余额」。同日同额同摘要但交易后余额互异的行
    （一秒内连发的多笔手续费就是这种）是不同交易，旧键不看余额会把它们折叠成一笔。
    真正的重复不再静默吞掉：默认返回 skippedDetails 供用户确认，
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
            reference = normalize_bank_reference(row.get("transaction_reference"))
            raw_row_index = row.get("source_row_index")
            try:
                source_row_index = int(raw_row_index) if raw_row_index not in (None, "") else None
            except (TypeError, ValueError):
                source_row_index = None
            duplicate = None
            if not force_duplicates:
                # 判重三支（引用号存在时它是权威身份）：
                # 1) 入参有号 → 只认同号；
                # 2) 复合键回落 → 仅当入参无号，或库中那一行本来就没号（历史数据/
                #    微信这类无号源）。这样"带号重导"既不会重复入账，也不会把
                #    两笔号不同的真实交易按指纹合并；
                # 3) 库中行有号、入参无号 → 仍按复合键，避免无号源重复导入。
                # 复合键 = 日期(substr 到日) + 摘要 + 金额 + 余额：余额逐笔连续，
                # 同秒同额的多笔手续费余额互异；日期截到日是因为流水文本可带时分秒
                # 而历史行只存到日期。
                duplicate = await session.execute(
                    text(
                        "SELECT id, transaction_reference, description, amount "
                        "FROM bank_statements WHERE company_id = :company_id AND ("
                        "  (:reference <> '' AND transaction_reference = :reference)"
                        "  OR (substr(transaction_date, 1, 10) = :transaction_date "
                        "      AND description = :description AND amount = :amount "
                        "      AND balance = :balance "
                        "      AND (:reference = '' OR transaction_reference IS NULL))"
                        ") LIMIT 1"
                    ),
                    {
                        "company_id": company_id,
                        "reference": reference,
                        "transaction_date": txn_date_text[:10],
                        "description": desc,
                        "amount": money_text(signed_amount),
                        "balance": money_text(balance),
                    },
                )
            matched = duplicate.first() if duplicate is not None else None
            if matched is not None:
                matched_reference = str(matched[1] or "")
                matched_by_reference = bool(reference) and matched_reference == reference
                if matched_by_reference and (
                    money_text(money(matched[3])) != money_text(signed_amount)
                    or normalize_statement_description(str(matched[2] or "")) != desc
                ):
                    # 同号却不同内容：多半是银行重导时改了摘要，或者上游把号映射错了，
                    # 必须让人看见，不能只说"重复"。
                    reason = f"同交易流水号（{reference}）已存在但金额/摘要不一致，请核对银行流水号"
                elif matched_by_reference:
                    reason = f"同交易流水号（{reference}）的流水已存在"
                else:
                    reason = "同日期+同摘要+同金额+同余额的流水已存在；如确属两笔真实交易，请使用强制导入"
                skipped += 1
                skipped_details.append({
                    "transactionDate": txn_date_text,
                    "description": desc,
                    "amount": money_text(signed_amount),
                    "balance": money_text(balance),
                    "transactionReference": reference,
                    "sourceRowIndex": source_row_index,
                    "reason": reason,
                })
                continue

            sid = f"bs-{company_id}-{int(datetime.now(timezone.utc).timestamp() * 1000000)}-{imported}"
            await session.execute(
                text(
                    "INSERT INTO bank_statements "
                    "(id, company_id, transaction_date, description, amount, balance, statement_id, "
                    "description_normalized, created_at, transaction_reference, source_row_index) VALUES "
                    "(:id, :company_id, :transaction_date, :description, :amount, :balance, "
                    ":statement_id, 1, :created_at, :transaction_reference, :source_row_index)"
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
                    # 无号必须存 NULL：'' 会让「库中无号」的回落到失效
                    "transaction_reference": reference or None,
                    "source_row_index": source_row_index,
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
                        "transactionReference": reference,
                        "sourceRowIndex": source_row_index,
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
                f"SELECT * FROM bank_statements WHERE company_id = ? AND statement_id = ? ORDER BY {_STATEMENT_ORDER}",
                (company_id, statement_id),
            )
        else:
            cursor = await conn.execute(
                f"SELECT * FROM bank_statements WHERE company_id = ? ORDER BY {_STATEMENT_ORDER}",
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


_COUNTERPARTY_PATTERN = re.compile(r"[(（]对方[:：]\s*([^)）]+)[)）]")


def statement_counterparty(description: str) -> str:
    """流水描述里的结构化对手方：「… (对方: xxx)」中的 xxx；无则空串。"""
    match = _COUNTERPARTY_PATTERN.search(description or "")
    return match.group(1).strip() if match else ""


def _affinity_clean(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fa5]", "", text or "")


def _han_set(text: str) -> set:
    return set(re.findall(r"[\u4e00-\u9fa5]", text or ""))


def entry_seller_name(entry: Dict) -> str:
    """发票类候选的销方名：标题「发票-」前缀之后的部分（导入即以此生成标题）。"""
    title = str(entry.get("title") or "")
    if "发票-" in title:
        return title.rsplit("发票-", 1)[1].strip()
    return ""


def _entry_invoice_counterparty(entry: Dict) -> str:
    """销项发票（收款）核对购方，进项发票（支出）核对销方。"""
    if str(entry.get("direction") or "expense") == "income":
        return str(entry.get("buyer_name") or entry.get("buyerName") or "").strip()
    return entry_seller_name(entry)


def seller_affinity(stmt_desc: str, entry: Dict) -> bool:
    """发票对手方（进项取销方、销项取购方）与流水是否相关——金额+日期之外的第三道证据。

    任一满足即相关：
    - 流水「对方」与发票对手方互相包含（≥2 字：「王翼」＝「王翼」、
      「deepseek调用服务费」的对方就是销方全称）
    - 去掉对方段后的描述与发票对手方有足够汉字交集。报销场景（描述含「报销/垫付」）
      商户名常只有 2 字品牌（百度/智谱），阈值放宽到 2；非报销场景保持 ≥3，
      否则「杭州」这类地名交集会把社保金库配到科技公司发票上
    - 清洗后整段互相包含（≥6 字）

    只有金额和日期接近、对手方毫无关系的配对（社保金库 vs 科技公司
    发票）不构成相关，调用方应降置信或改为人工核实，而不是当建议推。
    非发票类候选（或缺失购方名的销项发票）无从判断对手方，返回 True 走原有规则。
    """
    seller = _affinity_clean(_entry_invoice_counterparty(entry))
    if not seller:
        return True
    desc = str(stmt_desc or "")
    counterparty = _affinity_clean(statement_counterparty(desc))
    if len(counterparty) >= 2 and (
        counterparty == seller or counterparty in seller or seller in counterparty
    ):
        return True
    desc_body = _affinity_clean(_COUNTERPARTY_PATTERN.sub("", desc))
    is_reimbursement = "报销" in desc_body or "垫付" in desc_body
    threshold = 2 if is_reimbursement else 3
    if len(_han_set(desc_body) & _han_set(seller)) >= threshold:
        return True
    if len(desc_body) >= 6 and (desc_body in seller or seller in desc_body):
        return True
    return False


def _build_match_candidates(
    rows: List[Any],
    already_matched_ids,
    *,
    allow_advancement: bool,
    reversed_ids: frozenset | set = frozenset(),
) -> List[Dict]:
    """把 entries 行装配成对账候选（auto_match 与 ai_suggest 共用，防口径漂移）。

    allow_advancement：股东/员工个人垫付凭证银行效果为零，也允许作为报销流水
    的配对候选（ai_suggest 需要该能力；auto_match 规则匹配不需要）。
    待核销发票（贷2241/借1122）挂账分录不含银行科目，配对金额取票面金额。
    reversed_ids：已红冲凭证不参与配对（核心状态，投影 payload 无 status 键）。
    """
    entries: List[Dict] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
            entry_id = payload.get("id", row["id"])
            if entry_id in already_matched_ids or entry_id in reversed_ids:
                continue
            if payload.get("is_archive_only") or payload.get("isArchiveOnly"):
                # 仅归档不记账的底稿凭证（如"先付款后收票"时发票自动归档）不是可核销
                # 凭证：voucherLines 带 1002 行但余额核对按同口径排除它，配上去银行
                # 账永远关不上。候选层不推荐，确认层（bank_settlement_service）再硬拒。
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
            if (
                is_advancement
                and abs(bank_effect) < RECONCILIATION_AMOUNT_TOLERANCE
                and str(payload.get("billingStatus") or "") == "reconciled"
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
                "categorySource": str(payload.get("categorySource") or ""),
                "categoryReason": str(payload.get("categoryReason") or ""),
                "item_name": str(payload.get("item_name") or ""),
                "occurDate": str(payload.get("occurDate", ""))[:10],
                "source": payload.get("source", ""),
                "merchant": payload.get("merchant", ""),
                "buyer_name": str(payload.get("buyer_name") or payload.get("buyerName") or ""),
                "payer": str(payer or ""),
                "isAdvancement": bool(is_advancement and abs(bank_effect) < RECONCILIATION_AMOUNT_TOLERANCE),
                "isPendingInvoice": bool(is_invoice_pending and abs(bank_effect) < RECONCILIATION_AMOUNT_TOLERANCE),
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


    reversed_ids = await _load_reversed_entry_ids(company_id)
    entries = _build_match_candidates(rows, already_matched_ids, allow_advancement=False, reversed_ids=reversed_ids)

    suggestions = []
    used_entry_ids = set(already_matched_ids)
    for stmt in statements:
        available_entries = [
            e for e in entries
            if e.get("id") not in used_entry_ids
            and _direction_matches_statement(stmt["amount"], e.get("direction", "expense"))
            and _entry_not_after_statement(e.get("occurDate"), stmt.get("transaction_date"))
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
            f"AND substr(transaction_date, 1, 7) = ? ORDER BY {_STATEMENT_ORDER}",
            (company_id, month),
        )
        bank_only = [dict(r) for r in await cursor.fetchall()]
        cursor = await conn.execute(
            "SELECT * FROM bank_statements WHERE company_id = ? "
            f"AND substr(transaction_date, 1, 7) = ? ORDER BY {_STATEMENT_ORDER}",
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
    parsed_entries: List[Dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        parsed_entries.append(payload)

    # 红冲凭证与原凭证成对抵消，不能再作为“账本无流水”催办。与待办、
    # 经营报表及结账向导保持同一口径：红字凭证本身和已被红冲的原凭证都退出
    # 待匹配范围。
    reversed_entry_ids = {
        str(payload.get("reversalOf") or "")
        for payload in parsed_entries
        if str(payload.get("entryType") or "") == "reversal" or payload.get("isReversal")
    } - {""}

    for payload in parsed_entries:
        payload_id = str(payload.get("id") or "")
        if payload_id:
            entries_by_id[payload_id] = payload

        voucher_status = str(payload.get("voucherStatus") or "")
        entry_type = str(payload.get("entryType") or "")
        if voucher_status in {"draft", "pending_review", "void"}:
            continue
        if entry_type == "reversal" or payload.get("isReversal") or voucher_status == "reversed":
            continue
        if payload_id and payload_id in reversed_entry_ids:
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
            clearing_info = platform_clearing_info(statement, payloads)
            clearing_code = clearing_info["clearingCode"]
            if statement_amount > 0:
                voucher_lines = {
                    "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": amount}],
                    "credit": [{"accountCode": clearing_code, "accountName": clearing_info["clearingName"], "amount": amount}],
                }
                direction = "income"
            else:
                voucher_lines = {
                    "debit": [{"accountCode": clearing_code, "accountName": clearing_info["clearingName"], "amount": amount}],
                    "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": amount}],
                }
                direction = "expense"
            payload = {
                "id": repair_entry_id,
                "companyId": company_id,
                "company_id": company_id,
                "userId": user_id,
                "user_id": user_id,
                "title": f"{clearing_info['label']}结算到账-{transaction_date}",
                "amount": amount,
                "direction": direction,
                "category": clearing_info["category"],
                "occurDate": transaction_date,
                "paymentDate": transaction_date,
                "billingStatus": "reconciled",
                "source": "bank_reconciliation_repair",
                "reconciliationType": clearing_info["reconciliationType"],
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


# 银行摘要 → 平台账单来源与订单号字段。提现到账先挂各自待结算科目，
# 候选匹配只在该平台的账单凭证里找原收入/手续费，不能跨平台混配。
_PLATFORM_CANDIDATE_RULES = (
    ("alipay", re.compile(r"支付宝|Alipay|蚂蚁金服", re.I), "alipay_bill", "platform_order_no"),
    ("wechat", re.compile(r"微信|财付通|WeChat|Tenpay", re.I), "wechat_bill", "wechat_order_no"),
)
_PLATFORM_WITHDRAWAL_RULES = {"wechat_withdrawal", "alipay_withdrawal"}


async def get_platform_settlement_candidates(
    company_id: str, statement: Dict[str, Any], platform: str = "",
) -> List[List[Dict[str, Any]]]:
    """Read complete order/day groups; a candidate is never an automatic match."""
    description = str(statement.get("description") or "")
    rule = next((item for item in _PLATFORM_CANDIDATE_RULES if item[0] == platform), None)
    if rule is None:
        rule = next((item for item in _PLATFORM_CANDIDATE_RULES if item[1].search(description)), None)
    # 显式传入平台时也要核对摘要：调用方（快捷规则）已判定，但兼容入口不能
    # 把“其他客户收款”配到微信/支付宝账单上。
    if rule is None or not rule[1].search(description):
        return []
    _, _, source, order_field = rule
    try:
        statement_date = date.fromisoformat(str(statement.get("transaction_date") or "")[:10])
    except ValueError:
        return []
    matched_ids = await get_matched_entry_ids(company_id)
    conn = await get_conn()
    try:
        rows = await (await conn.execute(
            "SELECT id, payload FROM entries WHERE company_id = ? "
            "AND json_extract(payload, '$.source') = ? "
            "AND json_extract(payload, '$.occurDate') >= ? "
            "AND json_extract(payload, '$.occurDate') < ? ORDER BY id",
            (company_id, source, (statement_date - timedelta(days=5)).isoformat(),
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
        key = (str(payload.get("occurDate") or "")[:10], str(payload.get(order_field) or ""))
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


async def get_wechat_settlement_candidates(
    company_id: str, statement: Dict[str, Any],
) -> List[List[Dict[str, Any]]]:
    """兼容旧调用：只查微信账单候选。新代码用 get_platform_settlement_candidates。"""
    return await get_platform_settlement_candidates(company_id, statement, "wechat")


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
        "pairedStatementId": other["id"],
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
            # entries 投影表缺失只降级为无候选，不能让 ai-suggest 整体 500
            logger.warning("读取 entries 失败，AI 对账候选为空 company=%s: %s", company_id, exc)
            rows = []
    finally:
        await conn.close()

    company_rules = await _load_company_category_rules(company_id)
    closed_months = await _load_closed_bank_months(company_id)
    reversed_ids = await _load_reversed_entry_ids(company_id)
    entries = _build_match_candidates(rows, already_matched_ids, allow_advancement=True, reversed_ids=reversed_ids)

    suggestions = []
    used_suggested_entry_ids = set(already_matched_ids)
    recorded_rule_hits: list[tuple[Dict[str, Any], str]] = []

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
            and _entry_not_after_statement(e.get("occurDate"), stmt.get("transaction_date"))
        ]
        rule_match = _try_rule_match(stmt, available_entries)
        multi_match = None if (rule_match and rule_match.get("type") == "match") else _try_multi_invoice_match(stmt, available_entries)
        if multi_match:
            ids = multi_match["entry_ids"]
            matched_group = multi_match["entries"]
            used_suggested_entry_ids.update(ids)
            suggestions.append({
                "statementId": stmt["id"],
                "statement": {
                    "date": stmt["transaction_date"],
                    "description": stmt["description"],
                    "amount": stmt["amount"],
                    "balance": stmt["balance"],
                },
                "type": "match",
                "matchedEntryId": ids[0],
                "matchedEntryIds": ids,
                "matchedEntry": matched_group[0],
                "matchedEntries": matched_group,
                "confidence": multi_match["confidence"],
                "rule": multi_match["rule"],
                "reason": multi_match["reason"],
                "amountDifference": multi_match.get("amount_difference", 0),
            })
            continue
        if rule_match:
            if rule_match.get("type") == "match":
                used_suggested_entry_ids.add(rule_match["entry_id"])
            mismatch = bool(rule_match.get("counterparty_mismatch"))
            sug_item: Dict[str, Any] = {
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
            }
            if mismatch:
                sug_item["counterpartyMismatch"] = True
                sug_item["requiresReview"] = True
            suggestions.append(sug_item)
            continue

        # C. 企业记忆规则（用户确认过的规则压过系统关键词启发式——否则
        #    「记住此规则」在快规则命中的流水上永远不生效）
        stmt_dir = "income" if safe_money(stmt["amount"]) > ZERO else "expense"
        remembered_rule = match_company_category_rule(
            stmt, company_rules, closed_months=closed_months
        )
        if remembered_rule:
            remembered_category = str(remembered_rule.get("category") or "")
            counterparty = str(remembered_rule.get("counterpartyKey") or "")
            purpose = str(remembered_rule.get("purposeKey") or "")
            rule_label = counterparty or purpose
            if counterparty and purpose:
                rule_label = f"{counterparty} / {purpose}"
            date_str = str(stmt.get("transaction_date") or "")[:10]
            confidence = float(remembered_rule.get("confidence") or 0.5)
            requires_review = bool(remembered_rule.get("requiresReview"))
            review_reasons = list(remembered_rule.get("reviewReasons") or [])
            match_kind = str(remembered_rule.get("matchKind") or "")
            if match_kind == "exact":
                reason = f"命中企业规则：{rule_label}，建议归入{remembered_category}"
            else:
                reason = f"企业规则只记住了交易对方「{rule_label}」，本次仍需确认业务用途"
            if review_reasons:
                reason = f"{reason}；{'；'.join(review_reasons)}"
            account_info = lookup_account(remembered_category) or {}
            account_name = str(account_info.get("accountName") or "")
            account_code = str(
                remembered_rule.get("accountCode")
                or account_info.get("accountCode")
                or ""
            )
            recorded_rule_hits.append((remembered_rule, str(stmt["id"])))
            suggestions.append({
                "statementId": stmt["id"],
                "statement": {
                    "date": stmt["transaction_date"],
                    "description": stmt["description"],
                    "amount": stmt["amount"],
                    "balance": stmt["balance"],
                },
                "type": "new",
                "confidence": confidence,
                "rule": "company_memory_rule",
                "ruleId": remembered_rule.get("id"),
                "rulePattern": rule_label,
                "ruleMatch": match_kind,
                "requiresReview": requires_review,
                "reviewReasons": review_reasons,
                "reason": reason,
                "suggestedEntry": {
                    "title": f"{remembered_category}-{rule_label[:16]}",
                    "category": remembered_category,
                    "accountCode": account_code,
                    "accountName": account_name,
                    "amount": abs(safe_money(stmt["amount"])),
                    "direction": stmt_dir,
                    "occurDate": date_str,
                    "confidence": confidence,
                    "requiresConfirmation": requires_review,
                    "reason": reason,
                },
            })
            continue

        # A. 系统关键词快规则（已收窄：退款/验证类不再误中平台结算）
        fast_rule = identify_explicit_it_service_rule(stmt) or identify_statement_fast_rule(stmt)
        if fast_rule and fast_rule["rule"] not in _PLATFORM_WITHDRAWAL_RULES:
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

        if fast_rule and fast_rule["rule"] in _PLATFORM_WITHDRAWAL_RULES:
            platform = "alipay" if fast_rule["rule"] == "alipay_withdrawal" else "wechat"
            platform_label = "支付宝" if platform == "alipay" else "微信"
            matched_group = None
            try:
                settlement_candidates = await get_platform_settlement_candidates(company_id, stmt, platform)
                available_groups = [group for group in settlement_candidates
                                    if len(group) <= 500
                                    and not any(entry["id"] in used_suggested_entry_ids for entry in group)]
                if len(available_groups) == 1:
                    matched_group = available_groups[0]
            except Exception:
                logger.exception("get_platform_settlement_candidates failed for %s", stmt.get("id"))

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
                    "rule": f"{platform}_detail_match",
                    "reason": f"{platform_label}原账目净额与到账金额一致，可一键合并对账",
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

        stmt_amount_val = safe_money(stmt["amount"])
        if stmt_amount_val < ZERO and is_payroll_liability_description(str(stmt.get("description") or "")):
            suggestions.append(_build_payroll_classification_suggestion(stmt, "liability"))
            continue
        if stmt_amount_val < ZERO and is_payroll_tax_description(str(stmt.get("description") or "")):
            suggestions.append(_build_payroll_classification_suggestion(stmt, "tax"))
            continue
        if stmt_amount_val < ZERO and is_salary_payment_description(str(stmt.get("description") or "")):
            suggestions.append(_build_payroll_classification_suggestion(stmt, "salary"))
            continue
        # 财税库行通道（「税费社保」）归税费缴纳模糊组：不猜社保/个税/罚款。
        # 放在工资/个税关键词之后：摘要若明确写了「工资」「个人所得税」仍先走工资模块。
        # 金额与已计提工资表精确一致时，_enrich_payroll_settlement_suggestions
        # 会在后面把它纠正为工资模块核销，自动化不丢。
        if stmt_amount_val < ZERO and is_ambiguous_tax_channel_description(str(stmt.get("description") or "")):
            suggestions.append(_build_tax_channel_suggestion(stmt))
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
                    and _entry_not_after_statement(e.get("occurDate"), stmt.get("transaction_date"))
                ][:10]  # Max 10 candidates for token efficiency

                if candidates:
                    user_msg = f"银行流水：\n- 日期：{stmt['transaction_date']}\n- 摘要：{stmt['description']}\n- 金额：{stmt['amount']}\n\n候选账目记录：\n{json_dumps(candidates, ensure_ascii=False, indent=2)}"
                    
                    # Parallel thread executor for synchronous chat
                    match_result = await asyncio.to_thread(
                        safe_chat_text_json,
                        BANK_MATCH_PROMPT, user_msg,
                        fallback={"matchedEntryId": None, "confidence": 0, "reason": "无法判断"},
                        max_retries=1,
                        temperature=0,
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
                    temperature=0,
                    scene="bank_statement",
                    user_id=user_id,
                    company_id=company_id,
                )
                classify_result = _ensure_classification_direction(stmt, classify_result)
                classify_result = guard_statement_classification(stmt, classify_result)

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

    await _enrich_payroll_settlement_suggestions(company_id, suggestions)
    await _enrich_advance_payable_suggestions(company_id, suggestions)
    await _enrich_invoice_reimbursement_suggestions(company_id, suggestions)
    _mark_fallback_category_matches(suggestions)
    await _record_company_category_rule_hits(company_id, recorded_rule_hits)
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

        # 发票销方 ↔ 流水对手方守门：金额+日期吻合但销方与「对方」毫无关系时
        # （社保金库扣款 vs 科技公司发票），金额巧合不构成配对证据——
        # 降置信并在理由里写明，让人工核实而不是当成高置信建议推。
        if entry.get("source") == "invoice_import":
            affine = seller_affinity(str(stmt.get("description", "")), entry)
            party_label = "购方" if entry_dir == "income" else "销方"
            affinity_note = "" if affine else f"；流水对方与发票{party_label}不一致，请人工核实"
        else:
            affine = True
            affinity_note = ""

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
                    confidence = max(0.95, 1.0 - date_diff * 0.01)
                    if not affine:
                        confidence = min(confidence, 0.60)
                    best = {
                        "entry_id": entry["id"],
                        "entry": entry,
                        "type": "match",
                        "amount_difference": ZERO,
                        "confidence": confidence,
                        "counterparty_mismatch": not affine,
                        "reason": f"金额完全匹配，日期相差{date_diff}天（高置信）{affinity_note}",
                    }

            # Tier 2: exact amount + within 30 days
            elif amount_diff < RECONCILIATION_AMOUNT_TOLERANCE and date_diff <= 30:
                score = 100 + date_diff
                if score < best_score:
                    best_score = score
                    confidence = max(0.75, 0.90 - date_diff * 0.005)
                    if not affine:
                        confidence = min(confidence, 0.60)
                    best = {
                        "entry_id": entry["id"],
                        "entry": entry,
                        "type": "match",
                        "amount_difference": ZERO,
                        "confidence": confidence,
                        "counterparty_mismatch": not affine,
                        "reason": f"金额完全匹配，日期相差{date_diff}天（发票与付款日期差，中置信）{affinity_note}",
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
                            "counterparty_mismatch": not affine,
                            "reason": f"金额接近（差额{amount_diff:.2f}元），日期相差{date_diff}天（低置信，建议人工核实）{affinity_note}",
                        }
        else:
            # ponytail: 无日期时仅按金额精确匹配，置信度降为 0.8
            if amount_diff < RECONCILIATION_AMOUNT_TOLERANCE:
                score = 500
                if score < best_score:
                    best_score = score
                    confidence = 0.80
                    if not affine:
                        confidence = min(confidence, 0.60)
                    best = {
                        "entry_id": entry["id"],
                        "entry": entry,
                        "type": "match",
                        "amount_difference": ZERO,
                        "confidence": confidence,
                        "counterparty_mismatch": not affine,
                        "reason": f"金额完全匹配（流水无日期，建议人工核实）{affinity_note}",
                    }

    return best


def _try_multi_invoice_match(stmt: Dict, entries: List[Dict]) -> Optional[Dict]:
    """为大额/汇总付款流水寻找唯一的 1:N 同池子集和报销匹配（精确到分，±1 分容差）。

    设计守门：
    - 仅作用于付款流出且金额 >= 1 元；
    - 严格分池（按单一垫付人 `adv:{payer}` 或待付款发票 `pending_invoice`），绝不混池；
    - 仅纳入流水日及之前 60 天内、单笔金额小于流水的候选，每池最多前 20 笔；
    - 子集大小 2..8；全池恰好仅存 1 组唯一解才推「合并配对」，存在多解或无解均放弃。
    """
    stmt_amount = safe_money(stmt.get("amount"))
    if stmt_amount >= ZERO:
        return None
    stmt_abs = abs(stmt_amount)
    if stmt_abs < Decimal("1.00"):
        return None

    stmt_date = None
    raw_stmt_date = str(stmt.get("transaction_date") or "").strip()[:10]
    if raw_stmt_date:
        try:
            stmt_date = datetime.strptime(raw_stmt_date, "%Y-%m-%d")
        except (ValueError, IndexError):
            stmt_date = None

    pools: Dict[str, List[tuple[int, int, Dict]]] = {}
    for entry in entries:
        if entry.get("direction", "expense") != "expense":
            continue
        amt = abs(safe_money(entry.get("amount")))
        if amt <= ZERO or amt >= stmt_abs:
            continue
        amt_cents = int((amt * 100).to_integral_value())
        if amt_cents <= 0:
            continue

        day_diff = 0
        if stmt_date is not None:
            raw_entry_date = str(entry.get("occurDate") or "").strip()[:10]
            try:
                entry_date = datetime.strptime(raw_entry_date, "%Y-%m-%d")
            except (ValueError, IndexError, TypeError):
                continue
            day_diff = (stmt_date - entry_date).days
            if day_diff < 0 or day_diff > 60:
                continue

        if entry.get("isAdvancement"):
            payer_key = str(entry.get("payer") or "").strip() or "personal"
            pool_key = f"adv:{payer_key}"
        elif entry.get("isPendingInvoice") or entry.get("source") == "invoice_import":
            pool_key = "pending_invoice"
        else:
            continue
        pools.setdefault(pool_key, []).append((day_diff, amt_cents, entry))

    target_cents = int((stmt_abs * 100).to_integral_value())
    tol_cents = 1
    solutions: List[tuple[str, List[Dict]]] = []

    for pool_key, raw_items in pools.items():
        if len(raw_items) < 2:
            continue
        raw_items.sort(key=lambda item: (item[0], -item[1], str(item[2].get("id") or "")))
        capped = [(amt_c, entry) for _, amt_c, entry in raw_items[:20]]
        # 按金额降序搜索，配合后缀和剪枝快速收敛
        capped.sort(key=lambda item: (-item[0], str(item[1].get("id") or "")))
        n = len(capped)
        suffix_sum = [0] * (n + 1)
        for i in range(n - 1, -1, -1):
            suffix_sum[i] = suffix_sum[i + 1] + capped[i][0]
        if suffix_sum[0] < target_cents - tol_cents:
            continue

        chosen: List[Dict] = []

        def dfs(idx: int, cur_sum: int) -> bool:
            if len(chosen) >= 2 and abs(cur_sum - target_cents) <= tol_cents:
                solutions.append((pool_key, list(chosen)))
                return len(solutions) > 1
            if idx >= n or len(chosen) >= 8:
                return False
            if cur_sum > target_cents + tol_cents:
                return False
            if cur_sum + suffix_sum[idx] < target_cents - tol_cents:
                return False

            amt_c, entry = capped[idx]
            if cur_sum + amt_c <= target_cents + tol_cents:
                chosen.append(entry)
                if dfs(idx + 1, cur_sum + amt_c):
                    chosen.pop()
                    return True
                chosen.pop()
            if dfs(idx + 1, cur_sum):
                return True
            return False

        if dfs(0, 0) or len(solutions) > 1:
            return None

    if len(solutions) != 1:
        return None

    pool_key, matched_items = solutions[0]
    matched_items = sorted(
        matched_items,
        key=lambda e: (str(e.get("occurDate") or ""), str(e.get("id") or "")),
    )
    total_amt = money(sum((abs(safe_money(item.get("amount"))) for item in matched_items), ZERO))
    diff = abs(stmt_abs - total_amt)
    if pool_key == "adv:boss_personal":
        pool_label = "老板垫付"
    elif pool_key.startswith("adv:employee_personal:"):
        pool_label = f"员工({pool_key.split(':', 2)[2]})垫付"
    elif pool_key.startswith("adv:"):
        pool_label = "个人垫付"
    else:
        pool_label = "待付款发票"

    return {
        "entries": matched_items,
        "entry_ids": [item["id"] for item in matched_items],
        "type": "match",
        "rule": "reimbursement_multi_match",
        "amount_difference": ZERO if diff == ZERO else money(diff),
        "confidence": 0.90,
        "reason": f"合并报销匹配 {len(matched_items)} 笔{pool_label}，合计 ¥{total_amt:.2f} 与流水一致",
    }


def _normalize_statement_classification(classification: Dict[str, Any]) -> Dict[str, Any]:
    """统一分类别名，按科目映射补齐缺失科目代码/名称及薪酬标记。"""
    result = dict(classification)
    category = str(result.get("category") or "").strip()
    alias_reset = category == "税费支出"
    if alias_reset:
        category = "税费缴纳"
        result["category"] = category
    info = lookup_account(category) if category else None
    if info:
        if alias_reset or not str(result.get("accountCode") or "").strip():
            result["accountCode"] = info["accountCode"]
        if alias_reset or not str(result.get("accountName") or "").strip():
            result["accountName"] = info["accountName"]
    if category == "社保公积金":
        result["requiresSalarySettlement"] = True
        result["requiresPayrollLiability"] = True
    elif category == "个税代缴":
        result["requiresSalarySettlement"] = True
    elif category == "工资发放":
        result["requiresSalarySettlement"] = True
    return result


def _context_fallback_classification(stmt: Dict[str, Any], context: str) -> Dict[str, Any]:
    """有用户补充说明但 LLM 不可用时，按说明关键词生成兜底草稿。"""
    stmt_amount = safe_money(stmt.get("amount"))
    if stmt_amount < ZERO and context:
        if "滞纳金" in context:
            return {
                "direction": "expense",
                "category": "税收滞纳金",
                "accountCode": "5711010",
                "accountName": "营业外支出-税收滞纳金",
                "title": "税收滞纳金",
                "confidence": 0.95,
                "reason": "用户补充说明表明该支出为税收滞纳金，计入营业外支出-税收滞纳金（企业所得税不得税前扣除）",
            }
        if _PENALTY_EXPENSE_RE.search(context):
            return {
                "direction": "expense",
                "category": "罚款支出",
                "accountCode": "5711011",
                "accountName": "营业外支出-罚款支出",
                "title": "税务/行政罚款支出",
                "confidence": 0.95,
                "reason": "用户补充说明表明该支出为罚款/行政处罚，计入营业外支出-罚款支出（企业所得税不得税前扣除）",
            }
        if "研发" in context:
            return {
                "direction": "expense",
                "category": "研发支出-费用化支出",
                "accountCode": "4301",
                "accountName": "研发支出-费用化支出",
                "title": "研发相关支出",
                "confidence": 0.9,
                "reason": "用户补充说明表明该支出用于研发，应优先按研发支出处理",
            }
        explicit_it_rule = identify_explicit_it_service_rule(stmt, context)
        if explicit_it_rule:
            return {
                "direction": "expense",
                "category": explicit_it_rule["category"],
                "accountCode": "5602015",
                "accountName": "管理费用-信息技术服务费",
                "title": explicit_it_rule["title"],
                "confidence": 0.95,
                "reason": f"用户补充说明明确包含信息技术服务用途：{context}",
            }
    return _fallback_classification_for_statement(stmt)


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

    stmt_amount = safe_money(stmt["amount"])
    context = (business_context or "").strip()

    if not context:
        if stmt_amount < ZERO and is_payroll_liability_description(stmt["description"]):
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
        if stmt_amount < ZERO and is_payroll_tax_description(stmt["description"]):
            return {
                "direction": "expense",
                "category": "个税代缴",
                "accountCode": "2221012",
                "accountName": "应交税费-应交个人所得税",
                "title": "个税缴纳",
                "confidence": 0.95,
                "requiresSalarySettlement": True,
                "reason": "识别为个税代缴：应在工资模块按流水核销应交个人所得税，不能记所得税费用",
            }
        if stmt_amount < ZERO and is_salary_payment_description(stmt["description"]):
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
        if stmt_amount < ZERO and is_ambiguous_tax_channel_description(stmt["description"]):
            # 无业务说明时也只给税费模糊组，交用户在候选里拍板；补充说明后走 LLM
            return _normalize_statement_classification(_tax_channel_classification(stmt))
        fast_rule = identify_explicit_it_service_rule(stmt) or identify_statement_fast_rule(stmt)
        if fast_rule:
            return _normalize_statement_classification({
                "direction": "income" if stmt_amount > ZERO else "expense",
                "category": fast_rule["category"],
                "title": fast_rule["title"],
                "confidence": fast_rule["confidence"],
                "reason": fast_rule["reason"],
            })

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
        fallback=_context_fallback_classification(stmt, context),
        max_retries=1,
        retry_on_parse=True,
        temperature=0,
        scene="bank_statement",
        user_id=user_id,
        company_id=company_id,
    )
    classify_result = _ensure_classification_direction(stmt, classify_result)
    return _normalize_statement_classification(
        guard_statement_classification(stmt, classify_result, context)
    )


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


_SALARY_PAYMENT_CATEGORIES = {"工资", "工资支出", "工资发放", "职工薪酬", "薪资", "薪酬"}
_SALARY_PAYMENT_CODES = {"5602001", "221101"}
_PAYROLL_LIABILITY_CATEGORIES = {"社保公积金", "社保", "公积金", "住房公积金"}
_PAYROLL_LIABILITY_CODES = {"221102", "221104", "221105", "5602016", "5602017"}
_PAYROLL_TAX_CATEGORIES = {"个税代缴", "个人所得税"}
_PAYROLL_TAX_CODES = {"2221012"}


def _has_explicit_non_payroll_override(entry_data: Dict[str, Any], keyword_checker) -> bool:
    """用户补充了明确非工资/社保的业务说明时，允许按用户选择的非工资科目确认。"""
    remark = str(entry_data.get("remark") or "").strip()
    return "业务说明：" in remark and not keyword_checker(remark)


def _looks_like_salary_payment(entry_data: Dict[str, Any], description: str) -> bool:
    """工资流水不得在银行页直接记费用，否则与工资计提重复。"""
    category = str(entry_data.get("category") or "").strip()
    account_code = str(entry_data.get("accountCode") or "").strip()
    if category in _SALARY_PAYMENT_CATEGORIES or account_code in _SALARY_PAYMENT_CODES:
        return True
    if is_salary_payment_description(description):
        if category and _has_explicit_non_payroll_override(entry_data, is_salary_payment_description):
            return False
        return True
    return False


def _looks_like_payroll_liability(entry_data: Dict[str, Any], description: str) -> bool:
    """社保/公积金流水必须走工资模块核销，不能直接记费用导致重复列支。"""
    category = str(entry_data.get("category") or "").strip()
    account_code = str(entry_data.get("accountCode") or "").strip()
    if category in _PAYROLL_LIABILITY_CATEGORIES or account_code in _PAYROLL_LIABILITY_CODES:
        return True
    if is_payroll_liability_description(description):
        # 银行摘要常写为「税费社保」，实际可能缴税或税务罚款，用户改选其他科目或填写业务说明时放行
        if category and ("税" in description or _has_explicit_non_payroll_override(entry_data, is_payroll_liability_description)):
            return False
        return True
    return False


def _looks_like_payroll_tax(entry_data: Dict[str, Any]) -> bool:
    """个税代缴必须走工资模块核销 2221012，不能记所得税费用/税费缴纳。"""
    category = str(entry_data.get("category") or "").strip()
    account_code = str(entry_data.get("accountCode") or "").strip()
    return category in _PAYROLL_TAX_CATEGORIES or account_code in _PAYROLL_TAX_CODES


_OPENING_PAYROLL_ACCOUNT_NAMES: Dict[str, str] = {
    "221101": "应付职工薪酬-工资",
    "221102": "应付职工薪酬-单位社保",
    "221104": "应付职工薪酬-代扣社保公积金",
    "221105": "应付职工薪酬-住房公积金",
    "2221012": "应交税费-应交个人所得税",
}


def _extract_payroll_month_hint(description: str, stmt_date_str: str) -> str:
    """从银行摘要提取工资归属月（如「26年9月工资」→ 2026-09），缺失时回落到流水上月。"""
    text_val = str(description or "")
    m_cn = re.search(r"(?:20)?(\d{2})\s*年\s*(\d{1,2})\s*月", text_val)
    if m_cn:
        year = 2000 + int(m_cn.group(1))
        month = int(m_cn.group(2))
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}"
    m_iso = re.search(r"(20\d{2})[-/.年](\d{1,2})", text_val)
    if m_iso:
        year = int(m_iso.group(1))
        month = int(m_iso.group(2))
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}"
    try:
        dt = date.fromisoformat(str(stmt_date_str or "")[:10])
    except ValueError:
        return ""
    if dt.day <= 24:
        prev_year = dt.year - 1 if dt.month == 1 else dt.year
        prev_month = 12 if dt.month == 1 else dt.month - 1
        return f"{prev_year:04d}-{prev_month:02d}"
    return f"{dt.year:04d}-{dt.month:02d}"


def _tax_channel_classification(stmt: Dict[str, Any]) -> Dict[str, Any]:
    """财税库行通道扣款：只给『税费缴纳』并交用户拍板，不猜社保/个税/罚款。

    税费缴纳在 ``category_service.AMBIGUITY_GROUPS`` 里，前端会展开
    社保公积金 / 个税代缴 / 罚款支出 / 税收滞纳金 / 所得税费用 候选。
    """
    info = lookup_account("税费缴纳") or {}
    date_str = str(stmt.get("transaction_date") or "")[:10]
    return {
        "direction": "expense",
        "category": "税费缴纳",
        "accountCode": str(info.get("accountCode") or "2221026"),
        "accountName": str(info.get("accountName") or "应交增值税（小规模纳税人专用）"),
        "title": f"税费缴纳-{date_str}" if date_str else "税费缴纳",
        "confidence": 0.5,
        "requiresConfirmation": True,
        "requiresReview": True,
        "reason": (
            "摘要走财税库行通用扣款通道（税费/社保/罚款在流水上同形），"
            "无法区分正常税款、社保、个税或税务罚款；"
            "请点选实际分类（税务罚款选罚款支出，逾期缴税加收的滞纳金选税收滞纳金）"
        ),
    }


def _build_tax_channel_suggestion(stmt: Dict[str, Any]) -> Dict[str, Any]:
    classification = _tax_channel_classification(stmt)
    return {
        "statementId": stmt["id"],
        "statement": {
            "date": stmt["transaction_date"],
            "description": stmt["description"],
            "amount": stmt["amount"],
            "balance": stmt["balance"],
        },
        "type": "new",
        "confidence": classification["confidence"],
        "reason": classification["reason"],
        "suggestedEntry": classification,
    }


def _build_payroll_classification_suggestion(stmt: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """为工资发放或社保公积金扣款流水生成标准分类建议。"""
    abs_amount = abs(safe_money(stmt.get("amount")))
    desc = str(stmt.get("description") or "")
    date_str = str(stmt.get("transaction_date") or "")[:10]
    if kind == "tax":
        reason = "识别为个税代缴：个税属于代扣员工税款，应在工资模块按流水核销应交个人所得税，不能记所得税费用或税费缴纳"
        return {
            "statementId": stmt["id"],
            "statement": {
                "date": stmt["transaction_date"],
                "description": stmt["description"],
                "amount": stmt["amount"],
                "balance": stmt["balance"],
            },
            "type": "new",
            "confidence": 0.95,
            "reason": reason,
            "suggestedEntry": {
                "direction": "expense",
                "category": "个税代缴",
                "accountCode": "2221012",
                "accountName": _OPENING_PAYROLL_ACCOUNT_NAMES["2221012"],
                "title": "个税缴纳",
                "amount": abs_amount,
                "occurDate": date_str,
                "confidence": 0.95,
                "requiresSalarySettlement": True,
                "reason": reason,
            },
        }
    if kind == "liability":
        is_hf = "公积金" in desc and "社保" not in desc
        account_code = "221105" if is_hf else "221102"
        account_name = _OPENING_PAYROLL_ACCOUNT_NAMES[account_code]
        reason = "识别为社保/公积金缴纳：已计提时应在工资模块按流水核销应付职工薪酬，不能直接记费用"
        return {
            "statementId": stmt["id"],
            "statement": {
                "date": stmt["transaction_date"],
                "description": stmt["description"],
                "amount": stmt["amount"],
                "balance": stmt["balance"],
            },
            "type": "new",
            "confidence": 0.95,
            "reason": reason,
            "suggestedEntry": {
                "direction": "expense",
                "category": "社保公积金",
                "accountCode": account_code,
                "accountName": account_name,
                "title": "社保公积金缴纳",
                "amount": abs_amount,
                "occurDate": date_str,
                "confidence": 0.95,
                "requiresSalarySettlement": True,
                "requiresPayrollLiability": True,
                "reason": reason,
            },
        }
    reason = "识别为工资发放：工资费用应在所属月计提，此处只核销应付职工薪酬，请到工资模块按流水确认发放"
    return {
        "statementId": stmt["id"],
        "statement": {
            "date": stmt["transaction_date"],
            "description": stmt["description"],
            "amount": stmt["amount"],
            "balance": stmt["balance"],
        },
        "type": "new",
        "confidence": 0.95,
        "reason": reason,
        "suggestedEntry": {
            "direction": "expense",
            "category": "工资发放",
            "accountCode": "221101",
            "accountName": _OPENING_PAYROLL_ACCOUNT_NAMES["221101"],
            "title": "工资发放",
            "amount": abs_amount,
            "occurDate": date_str,
            "confidence": 0.95,
            "requiresSalarySettlement": True,
            "reason": reason,
        },
    }


def _public_opening_payroll_ctx(ctx: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if ctx is None:
        return None
    return {k: v for k, v in ctx.items() if k != "debitAllocations"}


async def _evaluate_opening_payroll_settlement(
    session: AsyncSession,
    company_id: str,
    stmt: Dict[str, Any],
    kind: str,
) -> Dict[str, Any]:
    """评估开账首月发放前期工资/社保公积金/个税时，对应应付科目期初贷方可用余额。"""
    from sqlalchemy import func, select
    from db.models import Company, JournalEntry, JournalLine, SourceTransaction
    from services.accounting_core_service import book_start_date
    from services.opening_balance_service import get_opening_balances

    desc = str(stmt.get("description") or "")
    abs_amount = abs(money(stmt.get("amount") or ZERO))
    if kind == "salary":
        target_codes: tuple[str, ...] = ("221101",)
    elif kind == "tax":
        target_codes = ("2221012",)
    elif "公积金" in desc:
        target_codes = ("221105", "221104", "221101", "221102")
    else:
        target_codes = ("221102", "221104", "221101", "221105")
    primary_code = target_codes[0]
    primary_name = _OPENING_PAYROLL_ACCOUNT_NAMES[primary_code]

    try:
        stmt_date = date.fromisoformat(str(stmt.get("transaction_date") or "")[:10])
    except ValueError:
        return {
            "eligible": False,
            "canSettle": False,
            "reason": "invalid_date",
            "bookStartDate": "",
            "accountCode": primary_code,
            "accountName": primary_name,
            "availableOpeningCredit": "0.00",
            "primaryAvailableCredit": "0.00",
            "remainingOpeningCredit": "0.00",
            "requiredAmount": money_text(abs_amount),
            "allocations": [],
            "debitAllocations": [],
        }

    company = await session.get(Company, company_id)
    start_date = book_start_date(company) if company is not None else None
    if start_date is None or stmt_date < start_date:
        return {
            "eligible": False,
            "canSettle": False,
            "reason": "not_after_book_start",
            "bookStartDate": start_date.isoformat() if start_date else "",
            "accountCode": primary_code,
            "accountName": primary_name,
            "availableOpeningCredit": "0.00",
            "primaryAvailableCredit": "0.00",
            "remainingOpeningCredit": "0.00",
            "requiredAmount": money_text(abs_amount),
            "allocations": [],
            "debitAllocations": [],
        }

    opening_items = await get_opening_balances(company_id, stmt_date.year, session=session)
    names_by_code = dict(_OPENING_PAYROLL_ACCOUNT_NAMES)
    opening_by_code: Dict[str, Decimal] = {code: ZERO for code in target_codes}
    for item in opening_items:
        code = str(item.get("account_code") or "")
        if code in opening_by_code:
            net_credit = max(
                money(item.get("credit_balance") or ZERO) - money(item.get("debit_balance") or ZERO),
                ZERO,
            )
            opening_by_code[code] = net_credit
            if item.get("account_name"):
                names_by_code[code] = str(item["account_name"])
    primary_name = names_by_code.get(primary_code, primary_name)

    settled_rows = (
        await session.execute(
            select(JournalLine.account_code, func.coalesce(func.sum(JournalLine.amount), 0))
            .join(JournalEntry, JournalLine.journal_entry_id == JournalEntry.id)
            .join(SourceTransaction, JournalEntry.source_transaction_id == SourceTransaction.id)
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.status.in_(("draft", "pending_review", "posted")),
                JournalEntry.occurred_on >= date(stmt_date.year, 1, 1),
                JournalEntry.occurred_on <= date(stmt_date.year, 12, 31),
                SourceTransaction.source_type == "opening_payroll_settlement",
                JournalLine.company_id == company_id,
                JournalLine.direction == "debit",
                JournalLine.account_code.in_(target_codes),
            )
            .group_by(JournalLine.account_code)
        )
    ).all()
    settled_by_code: Dict[str, Decimal] = {
        str(code): money(total or ZERO) for code, total in settled_rows
    }

    available_by_code: Dict[str, Decimal] = {}
    available_total = ZERO
    for code in target_codes:
        avail = max(opening_by_code[code] - settled_by_code.get(code, ZERO), ZERO)
        available_by_code[code] = avail
        available_total = money(available_total + avail)

    is_opening_month = stmt_date.year == start_date.year and stmt_date.month == start_date.month
    eligible = is_opening_month or available_total > ZERO
    can_settle = eligible and abs_amount > ZERO and available_total >= abs_amount

    debit_allocations: list[Dict[str, Any]] = []
    if can_settle:
        remaining = abs_amount
        for code in target_codes:
            if remaining <= ZERO:
                break
            avail = available_by_code[code]
            take = min(avail, remaining)
            if take > ZERO:
                debit_allocations.append({
                    "accountCode": code,
                    "accountName": names_by_code.get(code, _OPENING_PAYROLL_ACCOUNT_NAMES[code]),
                    "amount": money(take),
                })
                remaining = money(remaining - take)

    if debit_allocations:
        primary_code = debit_allocations[0]["accountCode"]
        primary_name = debit_allocations[0]["accountName"]

    allocations = [
        {
            "accountCode": item["accountCode"],
            "accountName": item["accountName"],
            "amount": money_text(item["amount"]),
        }
        for item in debit_allocations
    ]
    primary_available = available_by_code.get(primary_code, ZERO)
    if can_settle and len(debit_allocations) == 1:
        remaining_after = max(primary_available - debit_allocations[0]["amount"], ZERO)
    elif can_settle:
        remaining_after = max(available_total - abs_amount, ZERO)
    else:
        remaining_after = available_total

    return {
        "eligible": eligible,
        "canSettle": can_settle,
        "reason": "" if can_settle else ("opening_balance_insufficient" if eligible else "outside_opening_period"),
        "bookStartDate": start_date.isoformat(),
        "accountCode": primary_code,
        "accountName": primary_name,
        "availableOpeningCredit": money_text(available_total),
        "primaryAvailableCredit": money_text(primary_available),
        "remainingOpeningCredit": money_text(remaining_after),
        "requiredAmount": money_text(abs_amount),
        "allocations": allocations,
        "debitAllocations": debit_allocations,
    }


def _payroll_kind_label(kind: str) -> str:
    if kind == "tax":
        return "个税"
    if kind == "salary":
        return "工资"
    return "社保公积金"


_TAX_AMBIGUITY_CATEGORIES = {"税费缴纳", "罚款支出", "税收滞纳金", "所得税费用"}


def _build_opening_payroll_settlement(
    kind: str,
    opening_ctx: Optional[Dict[str, Any]],
    stmt_meta: Dict[str, Any],
) -> tuple[Dict[str, Any], str, bool]:
    """构造工资/社保/个税在无匹配工资表时的期初冲销或工资模块引导上下文。"""
    if opening_ctx and opening_ctx.get("canSettle"):
        pub = _public_opening_payroll_ctx(opening_ctx) or {}
        settlement = {
            "mode": "opening_balance",
            "kind": kind,
            **pub,
        }
        allocs = opening_ctx.get("allocations") or []
        if len(allocs) > 1:
            parts = " + ".join(
                f"「{a['accountCode']} {a['accountName']}」¥{a['amount']}"
                for a in allocs
            )
            reason = (
                f"建账启用日（{opening_ctx['bookStartDate']}）前遗留应付，"
                f"将合并冲销期初贷方余额：{parts}，不重复计入本月费用"
            )
        elif opening_ctx.get("remainingOpeningCredit") != "0.00":
            avail_str = opening_ctx.get("primaryAvailableCredit") or opening_ctx["availableOpeningCredit"]
            rem_str = opening_ctx["remainingOpeningCredit"]
            suffix = (
                f"（当前可用 ¥{avail_str}，冲销后剩余 ¥{rem_str} 可用于后续冲抵未单独拆分的个人代扣社保/公积金）"
                if kind == "salary"
                else f"（当前可用 ¥{avail_str}，冲销后剩余 ¥{rem_str}）"
            )
            reason = (
                f"建账启用日（{opening_ctx['bookStartDate']}）前遗留应付，"
                f"将冲销「{opening_ctx['accountCode']} {opening_ctx['accountName']}」期初贷方余额 "
                f"¥{opening_ctx['requiredAmount']}{suffix}，不重复计入本月费用"
            )
        else:
            reason = (
                f"建账启用日（{opening_ctx['bookStartDate']}）前遗留应付，"
                f"将直接冲销「{opening_ctx['accountCode']} {opening_ctx['accountName']}」期初贷方余额 "
                f"¥{opening_ctx['requiredAmount']}（冲销后该科目期初余额清零），不重复计入本月费用"
            )
        return settlement, reason, False

    if opening_ctx and opening_ctx.get("eligible"):
        pub = _public_opening_payroll_ctx(opening_ctx) or {}
        settlement = {
            "mode": "opening_balance_missing",
            "kind": kind,
            **pub,
        }
        reason = (
            f"开账首月发放前期{_payroll_kind_label(kind)}需冲销"
            f"「{opening_ctx['accountCode']} {opening_ctx['accountName']}」期初贷方余额，"
            f"当前可用 ¥{opening_ctx['availableOpeningCredit']}（需 ¥{opening_ctx['requiredAmount']}），请先到「期初设置」补录"
        )
        return settlement, reason, True

    suggested_month = _extract_payroll_month_hint(
        str(stmt_meta.get("description") or ""),
        str(stmt_meta.get("date") or ""),
    )
    settlement = {
        "mode": "salary_module_required",
        "kind": kind,
        "suggestedMonth": suggested_month,
    }
    target_label = _payroll_kind_label(kind)
    reason = (
        f"未找到金额完全匹配的已计提{target_label}记录，请前往工资模块核对或计提后再核销（若非{target_label}支出请点「改分类」）"
    )
    return settlement, reason, True


async def _enrich_payroll_settlement_suggestions(
    company_id: str,
    suggestions: List[Dict[str, Any]],
) -> None:
    """为工资/社保/个税类流水建议补充原地核销或期初冲销上下文，消除「点了必报 409」的死按钮。"""
    payroll_items: list[tuple[Dict[str, Any], str]] = []
    tax_ambiguity_items: list[Dict[str, Any]] = []
    for sug in suggestions:
        if sug.get("type") != "new":
            continue
        stmt_meta = sug.get("statement") or {}
        if safe_money(stmt_meta.get("amount")) >= ZERO:
            continue
        entry_meta = sug.get("suggestedEntry") or {}
        desc = str(stmt_meta.get("description") or "")
        cat = str(entry_meta.get("category") or "").strip()
        if _looks_like_payroll_tax(entry_meta):
            payroll_items.append((sug, "tax"))
        elif _looks_like_payroll_liability(entry_meta, desc):
            payroll_items.append((sug, "liability"))
        elif _looks_like_salary_payment(entry_meta, desc):
            payroll_items.append((sug, "salary"))
        elif cat in _TAX_AMBIGUITY_CATEGORIES:
            # 税务通道/税费多义组扣款（社保费自 2020 年起由税务代收，摘要与增值税缴款同形）：
            # 1) 金额命中已计提工资表应缴额时直接纠偏为工资模块核销；
            # 2) 未命中时预评估期初应付余额附在 payrollLiabilitySettlement / payrollTaxSettlement，
            #    不改默认分类，供用户在前端点选「社保公积金 / 个税代缴」后一键冲销期初或引导补录。
            tax_ambiguity_items.append(sug)

    if not payroll_items and not tax_ambiguity_items:
        return

    import services.salary_service as salary_service

    salary_res = await salary_service.list_salary_payment_suggestions(company_id)
    liability_res = await salary_service.list_payroll_liability_payment_suggestions(company_id)
    salary_by_stmt = {
        str((item.get("statement") or {}).get("id") or ""): item
        for item in (salary_res.get("suggestions") or [])
    }
    liability_by_stmt = {
        str((item.get("statement") or {}).get("id") or ""): item
        for item in (liability_res.get("suggestions") or [])
    }

    need_opening_eval: list[tuple[Dict[str, Any], str]] = []
    for sug, kind in payroll_items:
        stmt_id = str(sug.get("statementId") or "")
        stmt_meta = sug.get("statement") or {}
        entry_meta = sug.setdefault("suggestedEntry", {})
        hit = salary_by_stmt.get(stmt_id) if kind == "salary" else liability_by_stmt.get(stmt_id)
        if hit and kind in ("tax", "liability"):
            hit_kind = str(hit.get("kind") or "")
            if (kind == "tax") != (hit_kind == "tax"):
                hit = None
        if hit:
            if kind == "salary":
                settlement = {
                    "mode": "salary_sheet",
                    "kind": "salary",
                    "month": hit.get("month") or "",
                    "label": "工资发放",
                    "amount": hit.get("netSalary") or "",
                }
                reason = f"已匹配 {settlement['month']} 工资表实发金额 ¥{settlement['amount']}，点击即可核销并生成发放凭证"
            else:
                settlement = {
                    "mode": "salary_sheet",
                    "kind": hit.get("kind") or ("tax" if kind == "tax" else "social_security"),
                    "month": hit.get("month") or "",
                    "label": hit.get("label") or _payroll_kind_label(kind),
                    "amount": hit.get("amount") or "",
                }
                reason = f"已匹配 {settlement['month']} {settlement['label']}应付金额 ¥{settlement['amount']}，点击即可核销并生成付款凭证"
            sug["payrollSettlement"] = settlement
            sug["requiresReview"] = False
            sug["reason"] = reason
            entry_meta["reason"] = reason
            entry_meta["requiresSalarySettlement"] = True
        else:
            need_opening_eval.append((sug, kind))

    # 税务通道流水：金额命中已计提应缴额时，把候选从「税类多选」纠正为工资模块核销。
    # 顺序在工资类流水之后，互不重叠（_TAX_AMBIGUITY_CATEGORIES 不在 payroll 集合里）。
    pending_tax_items: list[Dict[str, Any]] = []
    for sug in tax_ambiguity_items:
        stmt_id = str(sug.get("statementId") or "")
        entry_meta = sug.setdefault("suggestedEntry", {})
        cat = str(entry_meta.get("category") or "").strip()
        hit = liability_by_stmt.get(stmt_id) if cat == "税费缴纳" else None
        if not hit:
            pending_tax_items.append(sug)
            continue
        hit_kind = str(hit.get("kind") or "")
        if hit_kind == "housing_fund":
            category, code = "社保公积金", "221105"
        elif hit_kind == "tax":
            category, code = "个税代缴", "2221012"
        else:
            category, code = "社保公积金", "221102"
        settlement = {
            "mode": "salary_sheet",
            "kind": hit_kind or "social_security",
            "month": hit.get("month") or "",
            "label": hit.get("label") or _payroll_kind_label("tax" if hit_kind == "tax" else "liability"),
            "amount": hit.get("amount") or "",
        }
        reason = (
            f"摘要走税务通道无法区分险种，但金额与 {settlement['month']} "
            f"{settlement['label']}应缴额 ¥{settlement['amount']} 精确一致，点击即可按工资表核销"
        )
        sug["payrollSettlement"] = settlement
        sug["requiresReview"] = False
        sug["reason"] = reason
        entry_meta.update({
            "category": category,
            "accountCode": code,
            "accountName": _OPENING_PAYROLL_ACCOUNT_NAMES[code],
            "requiresSalarySettlement": True,
            "reason": reason,
        })

    if not need_opening_eval and not pending_tax_items:
        return

    opening_results: Dict[str, Optional[Dict[str, Any]]] = {}
    tax_opening_results: Dict[str, tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]] = {}
    try:
        from db.session import session_scope

        async with session_scope() as session:
            for sug, kind in need_opening_eval:
                stmt_id = str(sug.get("statementId") or "")
                stmt_meta = sug.get("statement") or {}
                stmt_proxy = {
                    "transaction_date": stmt_meta.get("date") or "",
                    "description": stmt_meta.get("description") or "",
                    "amount": stmt_meta.get("amount") or 0,
                }
                opening_results[stmt_id] = await _evaluate_opening_payroll_settlement(
                    session, company_id, stmt_proxy, kind
                )
            for sug in pending_tax_items:
                stmt_id = str(sug.get("statementId") or "")
                stmt_meta = sug.get("statement") or {}
                stmt_proxy = {
                    "transaction_date": stmt_meta.get("date") or "",
                    "description": stmt_meta.get("description") or "",
                    "amount": stmt_meta.get("amount") or 0,
                }
                liab_ctx = await _evaluate_opening_payroll_settlement(
                    session, company_id, stmt_proxy, "liability"
                )
                tax_ctx = await _evaluate_opening_payroll_settlement(
                    session, company_id, stmt_proxy, "tax"
                )
                tax_opening_results[stmt_id] = (liab_ctx, tax_ctx)
    except Exception:
        logger.warning("评估期初应付工资可用余额失败，回落到工资表引导 company=%s", company_id, exc_info=True)

    for sug, kind in need_opening_eval:
        stmt_id = str(sug.get("statementId") or "")
        stmt_meta = sug.get("statement") or {}
        entry_meta = sug.setdefault("suggestedEntry", {})
        opening_ctx = opening_results.get(stmt_id)
        settlement, reason, requires_review = _build_opening_payroll_settlement(
            kind, opening_ctx, stmt_meta
        )
        sug["payrollSettlement"] = settlement
        sug["requiresReview"] = requires_review
        sug["reason"] = reason
        entry_meta["reason"] = reason
        entry_meta["requiresSalarySettlement"] = True
        if opening_ctx and (opening_ctx.get("canSettle") or opening_ctx.get("eligible")):
            entry_meta["accountCode"] = opening_ctx["accountCode"]
            entry_meta["accountName"] = opening_ctx["accountName"]

    for sug in pending_tax_items:
        stmt_id = str(sug.get("statementId") or "")
        stmt_meta = sug.get("statement") or {}
        liab_ctx, tax_ctx = tax_opening_results.get(stmt_id, (None, None))
        liab_settlement, liab_reason, _ = _build_opening_payroll_settlement(
            "liability", liab_ctx, stmt_meta
        )
        tax_settlement, tax_reason, _ = _build_opening_payroll_settlement(
            "tax", tax_ctx, stmt_meta
        )
        sug["payrollLiabilitySettlement"] = {**liab_settlement, "reason": liab_reason}
        sug["payrollTaxSettlement"] = {**tax_settlement, "reason": tax_reason}


def _person_name_key(value: Any) -> str:
    """往来人姓名归一：只去空白，不改字序——同名不同字的两个人不能合并。"""
    return re.sub(r"\s+", "", str(value or ""))


async def _person_payable_balances(company_id: str) -> Dict[str, Dict[str, Any]]:
    """按往来人汇总 2241 其他应付款的可用贷方余额与来源。

    ``journal_entries`` 同时取 posted 与 reversed：被冲销原凭证的明细仍在，冲销凭证
    已经以反方向入账，两者相加才是当前真实余额。期初已入账的 2241 也天然包含在内，
    这正是「建账前已入账、建账后才付款」场景需要的证据。

    老板不需要看明细账，但需要知道「这笔欠款是哪来的」：sources 给出构成余额的
    凭证（期初显示「建账时带入」），kind=opening 表示余额仍是未动过的建账带入数。
    """
    raw: Dict[str, Dict[str, Any]] = {}
    try:
        from db.session import session_scope

        async with session_scope() as session:
            result = await session.execute(
                text(
                    "SELECT l.direction AS direction, l.amount AS amount, l.auxiliary AS auxiliary, "
                    "e.id AS entry_id, e.entry_type AS entry_type, e.voucher_no AS voucher_no, "
                    "e.occurred_on AS occurred_on, e.summary AS summary "
                    "FROM journal_lines l "
                    "JOIN journal_entries e ON e.id = l.journal_entry_id "
                    "WHERE e.company_id = :company_id "
                    "AND e.status IN ('posted', 'reversed') "
                    "AND l.account_code LIKE '2241%'"
                ),
                {"company_id": company_id},
            )
            rows = result.fetchall()
    except Exception:
        logger.warning("汇总个人其他应付款余额失败 company=%s", company_id, exc_info=True)
        return {}

    for row in rows:
        auxiliary = row.auxiliary
        if isinstance(auxiliary, str):
            try:
                auxiliary = json.loads(auxiliary) if auxiliary else {}
            except (TypeError, ValueError):
                auxiliary = {}
        if not isinstance(auxiliary, dict):
            continue
        name = str(auxiliary.get("partner_name") or auxiliary.get("employee_name") or "").strip()
        key = _person_name_key(name)
        if not key:
            continue
        amount = money(row.amount or ZERO)
        is_credit = str(row.direction or "").lower() == "credit"
        entry = raw.setdefault(key, {
            "name": name,
            "credit": ZERO,
            "debit": ZERO,
            "opening_credit": ZERO,
            "sources": {},
        })
        if is_credit:
            entry["credit"] = money(entry["credit"] + amount)
            if str(row.entry_type or "") == "opening":
                entry["opening_credit"] = money(entry["opening_credit"] + amount)
            source = entry["sources"].setdefault(
                str(row.entry_id),
                {
                    "entryId": str(row.entry_id),
                    "voucherRef": str(row.voucher_no or row.entry_id or ""),
                    "date": row.occurred_on.isoformat() if hasattr(row.occurred_on, "isoformat") else str(row.occurred_on or ""),
                    "entryType": str(row.entry_type or ""),
                    "amount": ZERO,
                },
            )
            source["amount"] = money(source["amount"] + amount)
        else:
            entry["debit"] = money(entry["debit"] + amount)

    balances: Dict[str, Dict[str, Any]] = {}
    for key, entry in raw.items():
        available = money(entry["credit"] - entry["debit"])
        opening_kind = (
            entry["opening_credit"] > ZERO
            and entry["debit"] == ZERO
            and money(entry["credit"]) == money(entry["opening_credit"])
        )
        positive_sources = [s for s in entry["sources"].values() if s["amount"] > ZERO]
        positive_sources.sort(key=lambda s: s["date"], reverse=True)
        if opening_kind:
            positive_sources.sort(key=lambda s: s["entryType"] != "opening")
        sources = [
            {**source, "label": "建账时带入" if source["entryType"] == "opening" else "账上垫付"}
            for source in positive_sources[:3]
        ]
        balances[key] = {
            "name": entry["name"],
            "available": available,
            "kind": "opening" if opening_kind else "booked",
            "sources": sources,
        }
    return balances


_ADVANCE_PAYABLE_CATEGORIES = {
    "员工报销",
    "员工垫付报销",
    "股东垫付报销",
    "其他应收款",
    "其他应付款",
    "往来款",
}
_ADVANCE_PAYABLE_DESC_RE = re.compile(r"报销|垫付")


def _match_payable_person(description: str, balances: Dict[str, Dict[str, Any]]) -> str:
    """从摘要里认出已有垫付余额的人：优先「对方:」字段，其次按姓名包含匹配。"""
    counterparty = _person_name_key(extract_statement_counterparty(description))
    if counterparty:
        # 摘要明确写了对方：只认精确同名，绝不再拿姓名去猜——「杭州杨云飞科技」
        # 不能因为包含「杨云飞」就冲减到个人应付。
        return counterparty if counterparty in balances else ""
    text_key = _person_name_key(description)
    hits = [key for key in balances if len(key) >= 2 and key in text_key]
    if not hits:
        return ""
    # 姓名互为子串时取最长（「杨云飞」优先于「云飞」）；仍并列则放弃，宁可不出建议。
    hits.sort(key=len, reverse=True)
    if len(hits) > 1 and len(hits[0]) == len(hits[1]):
        return ""
    return hits[0]


async def _enrich_advance_payable_suggestions(
    company_id: str,
    suggestions: List[Dict[str, Any]],
) -> None:
    """垫付报销流水：账上已有本人 2241 余额时，把建议从「记其他应收款」纠正为冲减应付。

    只在有实据时改：摘要能认出往来人、该人 2241 贷方余额为正且足以覆盖本笔付款、金额
    不超过余额。没有余额证据的报销仍走原建议，交给用户确认——不能凭「报销」二字就把
    一笔新费用直接冲成负债。
    """
    candidates: list[tuple[Dict[str, Any], str]] = []
    for sug in suggestions:
        if sug.get("type") != "new" or sug.get("payrollSettlement"):
            continue
        stmt_meta = sug.get("statement") or {}
        amount = safe_money(stmt_meta.get("amount"))
        if amount >= ZERO:
            continue
        desc = str(stmt_meta.get("description") or "")
        category = str((sug.get("suggestedEntry") or {}).get("category") or "").strip()
        if category not in _ADVANCE_PAYABLE_CATEGORIES and not _ADVANCE_PAYABLE_DESC_RE.search(desc):
            continue
        candidates.append((sug, desc))

    if not candidates:
        return

    balances = await _person_payable_balances(company_id)
    if not balances:
        return

    for sug, desc in candidates:
        person_key = _match_payable_person(desc, balances)
        if not person_key:
            continue
        hit = balances[person_key]
        available = money(hit["available"])
        amount = abs(safe_money((sug.get("statement") or {}).get("amount")))
        # 余额只够覆盖部分付款时不自动改分类：多出的部分会被记成负债借方，需要用户决定。
        if amount <= ZERO or amount > available:
            continue

        person = str(hit["name"] or "").strip()
        entry_meta = sug.setdefault("suggestedEntry", {})
        # 「不是还旧账，是这次新支出」要按 AI 原建议落费用：先留底再改分类。
        original = {
            "direction": entry_meta.get("direction"),
            "category": entry_meta.get("category"),
            "accountCode": entry_meta.get("accountCode"),
            "accountName": entry_meta.get("accountName"),
            "title": entry_meta.get("title"),
        }
        # 已是股东/员工垫付分类就保留原标签（同一 2241 科目，别把股东改叫员工）；
        # 其他分类（员工报销、其他应收款、办公费等）统一纠正为员工垫付报销。
        category = str(entry_meta.get("category") or "").strip()
        if category not in ("员工垫付报销", "股东垫付报销"):
            category = "员工垫付报销"
        entry_meta.update({
            "direction": "owner_transfer",
            "category": category,
            "accountCode": "2241",
            "accountName": "其他应付款",
            "counterparty": person,
        })
        reason = (
            f"「{person}」其他应付款已有垫付余额 ¥{money_text(available)}，"
            f"本笔为垫付报销付款，冲减「2241 其他应付款-{person}」而非重复计入费用"
        )
        entry_meta["reason"] = reason
        sug["advancePayable"] = {
            "counterparty": person,
            "accountCode": "2241",
            "accountName": "其他应付款",
            "available": money_text(available),
            "kind": hit.get("kind") or "booked",
            "sources": hit.get("sources") or [],
            "original": original,
        }
        sug["reason"] = reason
        sug["rule"] = "advance_payable"
        # 改的是入账科目（资产→负债），必须逐笔确认，且不能沉淀成自动规则。
        sug["requiresReview"] = True
        hit["available"] = money(available - amount)


async def _enrich_invoice_reimbursement_suggestions(
    company_id: str,
    suggestions: List[Dict[str, Any]],
) -> None:
    """当月发票草稿（默认 company_account）遇上付给个人的报销/垫付流水时，引导两步入账。

    防止「收票挂账 + 公户报销个人」被规则匹配压成 1 笔直付凭证：
    1) 仅处理单笔 match、付款流出（amount < 0）、来源为 invoice_import 支出且仍为草稿、
       当前 payer 为空或 company_account 的建议；
    2) 结合流水对方与销方不一致、摘要含报销/垫付或对方命中在职员工，标出
       ``invoiceReimbursement`` 与 ``counterpartyMismatch``，并强制 ``requiresReview=True``。
    """
    from services.auxiliary_service import counterparty_partner_type

    candidates: list[tuple[Dict[str, Any], str, Dict[str, Any], Dict[str, Any]]] = []
    for sug in suggestions:
        if sug.get("type") != "match":
            continue
        raw_ids = sug.get("matchedEntryIds") or [sug.get("matchedEntryId")]
        ids = [str(item).strip() for item in raw_ids if str(item or "").strip()]
        if len(ids) != 1:
            continue
        stmt_meta = sug.get("statement") or {}
        if safe_money(stmt_meta.get("amount")) >= ZERO:
            continue
        entry = sug.get("matchedEntry") or {}
        if str(entry.get("source") or "") != "invoice_import":
            continue
        if str(entry.get("direction") or "expense") != "expense":
            continue
        payer = str(entry.get("payer") or "").strip()
        if payer not in ("", "company_account"):
            continue
        candidates.append((sug, ids[0], stmt_meta, entry))

    if not candidates:
        return

    entry_ids = list({eid for _, eid, _, _ in candidates})
    journal_statuses: dict[str, str] = {}
    emp_map: dict[str, bool] = {}

    try:
        from sqlalchemy import select
        from db.models import Employee, JournalEntry
        from db.session import session_scope

        async with session_scope() as session:
            j_rows = (
                await session.execute(
                    select(JournalEntry.id, JournalEntry.status).where(
                        JournalEntry.company_id == company_id,
                        JournalEntry.id.in_(entry_ids),
                    )
                )
            ).all()
            journal_statuses = {str(row[0]): str(row[1] or "") for row in j_rows}

            e_rows = (
                await session.execute(
                    select(Employee.name, Employee.is_owner).where(
                        Employee.company_id == company_id,
                        Employee.is_active == 1,
                    )
                )
            ).all()
            for emp_name, is_owner in e_rows:
                clean_name = str(emp_name or "").strip()
                if clean_name:
                    emp_map[clean_name] = bool(is_owner)
    except Exception:
        # SQLite legacy test fixtures may not have journal_entries/employees initialized.
        pass

    for sug, entry_id, stmt_meta, entry in candidates:
        desc = str(stmt_meta.get("description") or "")
        counterparty = extract_statement_counterparty(desc)
        seller_raw = str(entry.get("merchant") or entry_seller_name(entry) or "").strip()
        seller_clean = _affinity_clean(seller_raw)
        cp_clean = _affinity_clean(counterparty)

        if len(cp_clean) >= 2 and seller_clean:
            cp_differs_from_seller = (
                cp_clean != seller_clean
                and cp_clean not in seller_clean
                and seller_clean not in cp_clean
            )
        else:
            cp_differs_from_seller = bool(sug.get("counterpartyMismatch")) or not seller_affinity(desc, entry)

        if not cp_differs_from_seller:
            continue

        sug["counterpartyMismatch"] = True
        sug["requiresReview"] = True

        status = journal_statuses.get(entry_id, "")
        if status and status not in ("draft", "pending_review"):
            continue

        has_reim_kw = bool(_ADVANCE_PAYABLE_DESC_RE.search(desc))
        is_known_emp = bool(counterparty and counterparty in emp_map)
        is_person_cp = bool(counterparty and counterparty_partner_type(counterparty) == "person")

        if not (is_known_emp or (has_reim_kw and (not counterparty or is_person_cp))):
            continue

        if is_known_emp and not emp_map[counterparty]:
            suggested_payer = f"employee_personal:{counterparty}"
            payer_label = f"员工垫付（{counterparty}）"
        else:
            suggested_payer = "boss_personal"
            payer_label = "老板垫付"

        seller_label = seller_raw or str(entry.get("title") or "")
        sug["invoiceReimbursement"] = {
            "counterparty": counterparty,
            "sellerName": seller_label,
            "suggestedPayer": suggested_payer,
            "payerLabel": payer_label,
            "entryId": entry_id,
        }
        sug["reason"] = (
            f"付款对象为「{counterparty or '个人'}」（报销/垫付），发票销方为「{seller_label}」，"
            f"建议按「{payer_label}」分两步入账（保留垫付发票并生成报销还款凭证）"
        )


_FALLBACK_INVOICE_CATEGORIES = ("", "其他支出", "管理费用-其他")


def _is_fallback_invoice_entry(entry: Dict[str, Any]) -> bool:
    """判断配对候选是否仍处于发票导入兜底科目，需由用户确认具体用途后再核销。"""
    if not isinstance(entry, dict):
        return False
    if str(entry.get("source") or "") != "invoice_import":
        return False
    if str(entry.get("direction") or "expense") != "expense":
        return False
    if str(entry.get("categorySource") or "").strip() == "fallback":
        return True
    return str(entry.get("category") or "").strip() in _FALLBACK_INVOICE_CATEGORIES


def _mark_fallback_category_matches(suggestions: List[Dict[str, Any]]) -> None:
    """拦截归入兜底科目的已配对发票，避免在结账对账中被静默批量核销。"""
    for sug in suggestions:
        if sug.get("type") != "match":
            continue
        matched_entries = sug.get("matchedEntries")
        if not isinstance(matched_entries, list) or not matched_entries:
            single = sug.get("matchedEntry")
            matched_entries = [single] if isinstance(single, dict) else []
        if any(_is_fallback_invoice_entry(entry) for entry in matched_entries):
            sug["fallbackCategory"] = True
            sug["requiresReview"] = True


async def _handle_payroll_guard_in_confirm(
    *,
    kind: str,
    req: Any,
    session: Optional[AsyncSession],
    company_id: str,
    user_id: str,
    stmt: Any,
    entry_data: Dict[str, Any],
    salary_service: Any,
    respond: Any,
    sync_core: Any,
) -> Dict[str, Any]:
    """处理银行确认入口命中工资/社保流水：满足开账期初条件且显式请求时冲销期初负债，否则返回 409 指引。"""
    from services.accounting_core_service import assert_bank_reconciliable
    from services.entry_service import save_entry

    if kind == "tax":
        res = await salary_service.list_payroll_liability_payment_suggestions(company_id)
        code = "PAYROLL_TAX_REQUIRES_PAYROLL"
        default_msg = "识别为个税代缴，不能在银行页直接记费用。请到工资模块按流水确认缴纳，系统会生成应交个人所得税（2221012）核销凭证。"
        default_title = "个税缴纳（冲销期初）"
        category = "个税代缴"
    elif kind == "liability":
        res = await salary_service.list_payroll_liability_payment_suggestions(company_id)
        code = "PAYROLL_LIABILITY_REQUIRES_PAYROLL"
        default_msg = "识别为社保/公积金缴纳，不能在银行页直接记费用。请到工资模块按流水确认缴纳，系统会生成应付职工薪酬核销凭证。"
        default_title = "社保公积金缴纳（冲销期初）"
        category = "社保公积金"
    else:
        res = await salary_service.list_salary_payment_suggestions(company_id)
        code = "SALARY_REQUIRES_PAYROLL"
        default_msg = "识别为工资发放，不能在银行页直接记费用。请到工资模块先计提，再按流水确认发放，系统会生成应付职工薪酬核销凭证。"
        default_title = "工资发放（冲销期初）"
        category = "工资发放"

    matched_sheet_ids = {
        str((item.get("statement") or {}).get("id") or "")
        for item in (res.get("suggestions") or [])
        if kind != "tax" or str(item.get("kind") or "") == "tax"
    }
    matched_sheet_hit = str(req.statement_id) in matched_sheet_ids

    opening_ctx: Optional[Dict[str, Any]] = None
    if isinstance(session, AsyncSession):
        opening_ctx = await _evaluate_opening_payroll_settlement(
            session, company_id, dict(stmt), kind
        )

    if (
        bool(getattr(req, "settleOpeningPayroll", False))
        and not matched_sheet_hit
        and opening_ctx
        and opening_ctx.get("canSettle")
    ):
        stmt_date = str(stmt["transaction_date"][:10])
        abs_amount = abs(money(stmt["amount"] or ZERO))
        entry_id = f"bank-{req.statement_id}"
        debit_allocations = opening_ctx["debitAllocations"]
        first_alloc = debit_allocations[0]
        alloc_summary = " + ".join(
            f"{a['accountCode']} ¥{money_text(a['amount'])}" for a in debit_allocations
        )
        remark = (
            f"冲销期初应付余额（{alloc_summary}，开账日 {opening_ctx['bookStartDate']} 前遗留）"
            if len(debit_allocations) > 1
            else f"冲销期初应付余额（开账日 {opening_ctx['bookStartDate']} 前遗留）"
        )
        settled_entry = {
            **entry_data,
            "id": entry_id,
            "source": "opening_payroll_settlement",
            "sourceType": "opening_payroll_settlement",
            "direction": "expense",
            "category": category,
            "accountCode": first_alloc["accountCode"],
            "accountName": first_alloc["accountName"],
            "payer": "company_account",
            "billingStatus": "reconciled",
            "occurDate": stmt_date,
            "paymentDate": stmt_date,
            "amount": abs_amount,
            "title": str(entry_data.get("title") or "").strip() or default_title,
            "remark": remark,
            "voucherLines": {
                "debit": debit_allocations,
                "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": abs_amount}],
            },
        }
        await save_entry(
            settled_entry,
            user_id=user_id,
            company_id=company_id,
            trusted_voucher=True,
            allow_backfill=bool(req.allowBackfill),
            backfill_reason=req.backfillReason,
        )
        await assert_bank_reconciliable(session, company_id=company_id, entry_ids=[entry_id])

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

        await log_action(
            company_id,
            user_id,
            "create",
            "entry",
            entry_id,
            {
                "source": "opening_payroll_settlement",
                "title": settled_entry["title"],
                "accountCode": first_alloc["accountCode"],
                "accountCodes": [a["accountCode"] for a in debit_allocations],
            },
        )
        await sync_core(stmt, [entry_id])
        return await respond({
            "status": "created",
            "entryId": entry_id,
            "settledOpeningPayroll": True,
        })

    message = default_msg
    if opening_ctx and opening_ctx.get("eligible") and not opening_ctx.get("canSettle"):
        kind_label = _payroll_kind_label(kind)
        message = (
            f"开账首月发放前期{kind_label}需冲销「{opening_ctx['accountCode']} {opening_ctx['accountName']}」期初贷方余额，"
            f"当前可用 ¥{opening_ctx['availableOpeningCredit']}（需 ¥{opening_ctx['requiredAmount']}）。"
            f"请先到「我的 → 期初设置」补录贷方期初余额。"
        )
    detail: Dict[str, Any] = {
        "code": code,
        "message": message,
        "suggestions": res.get("suggestions", []),
    }
    if opening_ctx is not None:
        detail["openingPayroll"] = _public_opening_payroll_ctx(opening_ctx)
    raise HTTPException(status_code=409, detail=detail)


async def _load_reim_payload_fallback(
    conn: Any, company_id: str, entry_id: str,
) -> Optional[Dict[str, Any]]:
    """读取 entries 表的 payload；若隔离测例中 save_entry 写入了 session_scope，则回退读取。"""
    cursor = await conn.execute(
        "SELECT payload FROM entries WHERE id = ? AND company_id = ?",
        (entry_id, company_id),
    )
    row = await cursor.fetchone()
    if row is not None:
        raw = row["payload"]
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return parsed if isinstance(parsed, dict) else None
    if not entry_id.startswith("reim-"):
        return None
    from db.models import JournalEntry
    from db.session import session_scope
    from services.accounting_core_service import serialize_journal

    async with session_scope() as session:
        journal = await session.get(JournalEntry, entry_id)
        if not journal or journal.company_id != company_id:
            return None
        return await serialize_journal(session, journal)


async def _delete_reim_entry_fallback(
    conn: Any, company_id: str, user_id: str, entry_id: str,
) -> None:
    """删除未过账的 reim-* 衍生条目（同步清理投影与 session_scope 草稿）。"""
    await conn.execute(
        "DELETE FROM entries WHERE id = ? AND company_id = ?",
        (entry_id, company_id),
    )
    from sqlalchemy import delete as sa_delete
    from db.models import JournalEntry, JournalLine, ReconciliationLink
    from db.session import session_scope
    from services.accounting_core_service import delete_draft

    async with session_scope() as session:
        journal = await session.get(JournalEntry, entry_id)
        if journal and journal.company_id == company_id and journal.status in {"draft", "pending_review"}:
            await session.execute(sa_delete(ReconciliationLink).where(
                ReconciliationLink.company_id == company_id,
                ReconciliationLink.journal_entry_id == entry_id,
            ))
            await delete_draft(
                session, entry_id, company_id=company_id, user_id=user_id, request_id=f"unmatch-{entry_id}",
            )
            await session.execute(sa_delete(JournalLine).where(JournalLine.journal_entry_id == entry_id))


async def _restore_entry_status_sqlite(
    conn: Any,
    *,
    company_id: str,
    entry_id: str,
    payload: Any,
    previous_status: Optional[str],
    previous_payment_date: Optional[str],
) -> None:
    """回滚单笔条目的 billingStatus 与 paymentDate（保留 Postgres 方言兼容性）。"""
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

    注意：assert_bank_reconciliable 只在 AsyncSession 分支调用（单测路径跳过），
    测试捕获不了它的缺失——7bb56c5 下沉时漏了导入，真实 HTTP 路径每笔确认都在
    save_entry/对账回写之间炸 NameError，造成"已入账但流水未关联"的幽灵待配对。
    is_ambiguity_category（category_service）同理：只在勾选"记住此规则"分支调用，
    漏导入会让首次确认 500、第二次点击因已匹配而"看起来正常"。
    """
    from services.accounting_core_service import assert_bank_reconciliable
    from services.category_service import is_ambiguity_category
    from services.entry_service import save_entry

    from services.accounting_core_service import assert_bank_reconciliable

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

        reim_payer = str(getattr(req, "reimbursementPayer", None) or "").strip()
        if reim_payer:
            valid_reim_payer = (
                reim_payer in {"boss_personal", "employee_personal"}
                or (reim_payer.startswith("employee_personal:") and bool(reim_payer.split(":", 1)[1].strip()))
            )
            if not valid_reim_payer:
                raise HTTPException(status_code=400, detail="无效的个人垫付人类型")
            if not requested_ids:
                raise HTTPException(status_code=400, detail="请先选择要按垫付报销核销的单据")

        if isinstance(session, AsyncSession):
            from sqlalchemy import select, text

            from db.models import JournalEntry, LegacyEntryProjection
            from db.session import session_scope
            from services.accounting_core_service import update_draft
            from services.bank_settlement_service import confirm_statement_match
            # 批量两次查询替代逐条 get_journal+serialize_journal（500 笔上限时原为
            # 千级串行查询）。判定所需的 payer 可能来自投影回填（与 serialize_journal
            # 同优先级：metadata 打底、投影覆盖），voucherLines 仅存于 metadata。
            personal = False
            payloads: Dict[str, Dict[str, Any]] = {}
            entry_statuses: Dict[str, str] = {}
            for row in (await session.execute(
                select(JournalEntry.id, JournalEntry.metadata_json, JournalEntry.status).where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.id.in_(requested_ids),
                )
            )).all():
                payloads[row[0]] = dict(row[1] or {})
                entry_statuses[row[0]] = str(row[2] or "")
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

            if reim_payer:
                missing_core = [eid for eid in requested_ids if eid not in payloads]
                if missing_core:
                    raise HTTPException(status_code=404, detail={"message": "账目记录不存在", "entryIds": missing_core})
                stmt_row = (await session.execute(
                    text("SELECT amount FROM bank_statements WHERE id = :sid AND company_id = :cid"),
                    {"sid": req.statement_id, "cid": company_id},
                )).fetchone()
                if not stmt_row:
                    raise HTTPException(status_code=404, detail="流水不存在")
                stmt_amt = money(stmt_row[0] or ZERO)
                if stmt_amt >= ZERO:
                    raise HTTPException(status_code=400, detail="垫付报销流水必须为付款流出")
                total_adv = money(sum((abs(money(payloads[eid].get("amount") or ZERO)) for eid in requested_ids), ZERO))
                if abs(abs(stmt_amt) - total_adv) > money("0.05"):
                    raise HTTPException(status_code=400, detail="金额不一致，请核对垫付合计金额与打款金额")

                req_id = (request.headers.get("X-Request-ID") if request else None) or str(uuid4())
                need_convert = [
                    eid for eid in requested_ids
                    if str(payloads[eid].get("payer") or "").strip() != reim_payer
                ]
                for eid in need_convert:
                    if entry_statuses.get(eid) not in {"draft", "pending_review"}:
                        raise HTTPException(
                            status_code=409,
                            detail="原凭证已过账，不能原地改为个人垫付，请先红冲重记或单独处理",
                        )
                if need_convert:
                    async with session_scope() as convert_session:
                        for eid in need_convert:
                            await update_draft(
                                convert_session,
                                eid,
                                {"payer": reim_payer},
                                company_id=company_id,
                                user_id=user_id,
                                request_id=req_id,
                            )
                    for eid in need_convert:
                        payloads[eid]["payer"] = reim_payer
                        payloads[eid].pop("voucherLines", None)

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
                    backfill_reason=req.backfillReason,
                    reverse_posted_bank_entries=bool(getattr(req, "reversePostedBankEntries", False))))

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

                    payload = await _load_reim_payload_fallback(conn, company_id, entry_id)
                    if payload is None:
                        continue
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
                    is_reim = entry_id.startswith(f"reim-{req.statement_id}-") or bool(
                        isinstance(payload, dict) and payload.get("reimbursedEntryIds")
                    )
                    if is_reim:
                        raw_orig_ids = payload.get("reimbursedEntryIds") if isinstance(payload, dict) else None
                        if isinstance(raw_orig_ids, list) and raw_orig_ids:
                            orig_ids = [str(item).strip() for item in raw_orig_ids if str(item).strip()]
                        elif entry_id.startswith(f"reim-{req.statement_id}-") and not entry_id.endswith("-batch"):
                            orig_ids = [entry_id.removeprefix(f"reim-{req.statement_id}-")]
                        else:
                            orig_ids = []
                        saved_states = payload.get("reimbursedEntryStates") if isinstance(payload, dict) else {}
                        await _delete_reim_entry_fallback(conn, company_id, user_id, entry_id)
                        for orig_id in orig_ids:
                            cursor = await conn.execute(
                                "SELECT 1 FROM bank_statement_matches "
                                "WHERE company_id = ? AND entry_id = ? AND statement_id != ? LIMIT 1",
                                (company_id, orig_id, req.statement_id),
                            )
                            orig_matched = await cursor.fetchone()
                            if not orig_matched:
                                cursor = await conn.execute(
                                    "SELECT 1 FROM bank_statements "
                                    "WHERE company_id = ? AND matched_entry_id = ? AND id != ? LIMIT 1",
                                    (company_id, orig_id, req.statement_id),
                                )
                                orig_matched = await cursor.fetchone()
                            if orig_matched:
                                continue
                            cursor = await conn.execute(
                                "SELECT payload FROM entries WHERE id = ? AND company_id = ?",
                                (orig_id, company_id),
                            )
                            orig_row = await cursor.fetchone()
                            if not orig_row:
                                continue
                            orig_payload = json.loads(orig_row["payload"]) if isinstance(orig_row["payload"], str) else orig_row["payload"]
                            prev_state = saved_states.get(orig_id) if isinstance(saved_states, dict) else None
                            prev_st = prev_state.get("billingStatus") if isinstance(prev_state, dict) else None
                            prev_pd = prev_state.get("paymentDate") if isinstance(prev_state, dict) else None
                            await _restore_entry_status_sqlite(
                                conn,
                                company_id=company_id,
                                entry_id=orig_id,
                                payload=orig_payload,
                                previous_status=prev_st,
                                previous_payment_date=prev_pd,
                            )
                        continue
                    await _restore_entry_status_sqlite(
                        conn,
                        company_id=company_id,
                        entry_id=entry_id,
                        payload=payload,
                        previous_status=previous_statuses.get(entry_id),
                        previous_payment_date=previous_payment_dates.get(entry_id),
                    )
                await conn.commit()
                await sync_core(stmt, [])
                return await respond({"status": "unmatched", "entryId": None, "entryIds": existing_ids})

            idempotent_reim_id: Optional[str] = None
            if existing_ids and set(existing_ids) != set(requested_ids):
                # 同一批个人垫付幂等重试：已生成的 reim-{stmt_id}-batch / reim-{stmt_id}-{entry_id}
                # 记录着本次的原始垫付 ID 集合时，允许幂等重入而不是抛 409。
                if len(existing_ids) == 1 and existing_ids[0].startswith(f"reim-{req.statement_id}-"):
                    reim_payload = await _load_reim_payload_fallback(conn, company_id, existing_ids[0])
                    recorded_ids = (
                        reim_payload.get("reimbursedEntryIds")
                        if isinstance(reim_payload, dict)
                        else None
                    )
                    expected_single = (
                        len(requested_ids) == 1
                        and existing_ids[0] == f"reim-{req.statement_id}-{requested_ids[0]}"
                    )
                    if expected_single or (
                        isinstance(recorded_ids, list) and set(map(str, recorded_ids)) == set(requested_ids)
                    ):
                        idempotent_reim_id = existing_ids[0]
                if not idempotent_reim_id:
                    raise HTTPException(status_code=409, detail="该流水已匹配其他账目记录")

            placeholders = ",".join("?" for _ in requested_ids)
            if idempotent_reim_id:
                await conn.execute(
                    f"UPDATE entries SET payload = json_set(payload, '$.billingStatus', 'reconciled') "
                    f"WHERE company_id = ? AND id IN ({placeholders})",
                    (company_id, *requested_ids),
                )
                await conn.commit()
                await sync_core(stmt, [idempotent_reim_id])
                res_idem: Dict[str, Any] = {"status": "already_matched", "entryId": idempotent_reim_id}
                if len(requested_ids) > 1:
                    res_idem["reimbursedEntryIds"] = list(requested_ids)
                return await respond(res_idem)

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

            stmt_amount = money(stmt["amount"] or ZERO)
            if reim_payer:
                if stmt_amount >= ZERO:
                    raise HTTPException(status_code=400, detail="垫付报销流水必须为付款流出")
                if any(p.get("billingStatus") == "reconciled" for p in payloads):
                    raise HTTPException(status_code=409, detail="其中一笔垫付账目已报销核销")
                total_reim_amount = money(sum((abs(money(p.get("amount") or ZERO)) for p in payloads), ZERO))
                if abs(abs(stmt_amount) - total_reim_amount) > money("0.05"):
                    raise HTTPException(status_code=400, detail="金额不一致，请核对垫付合计金额与打款金额")
                for entry_id, payload in zip(requested_ids, payloads):
                    if str(payload.get("payer") or "").strip() == reim_payer:
                        continue
                    v_status = str(payload.get("voucherStatus") or payload.get("status") or "").strip()
                    if v_status and v_status not in ("draft", "pending_review"):
                        raise HTTPException(
                            status_code=409,
                            detail="原凭证已过账，不能原地改为个人垫付，请先红冲重记或单独处理",
                        )
                    payload["payer"] = reim_payer
                    await conn.execute(
                        "UPDATE entries SET payload = json_set(payload, '$.payer', ?) WHERE company_id = ? AND id = ?",
                        (reim_payer, company_id, entry_id),
                    )

            advancement_items = []
            for entry_id, payload in zip(requested_ids, payloads):
                payer = str(payload.get("payer") or "")
                voucher = payload.get("voucherLines") or {}
                bank_effect = _bank_effect_from_voucher(voucher) if voucher else ZERO
                is_personal_payer = payer in ("boss_personal", "employee_personal") or payer.startswith("employee_personal:")
                if is_personal_payer and abs(bank_effect) < money("0.01"):
                    advancement_items.append((entry_id, payload, payer))
            if advancement_items and len(advancement_items) != len(payloads):
                raise HTTPException(status_code=400, detail="个人垫付报销暂不支持与非垫付条目合并对账")

            if advancement_items:
                distinct_payers = {p for _, _, p in advancement_items}
                if len(distinct_payers) > 1:
                    raise HTTPException(status_code=400, detail="多笔垫付合并报销仅支持同一垫付人，请按垫付人分别核销")
                if any(p.get("billingStatus") == "reconciled" for _, p, _ in advancement_items):
                    raise HTTPException(status_code=409, detail="其中一笔垫付账目已报销核销")
                if stmt_amount >= 0:
                    raise HTTPException(status_code=400, detail="垫付报销流水必须为付款流出")
                total_adv_amount = money(sum((abs(money(p.get("amount") or ZERO)) for _, p, _ in advancement_items), ZERO))
                if abs(abs(stmt_amount) - total_adv_amount) > money("0.05"):
                    raise HTTPException(status_code=400, detail="金额不一致，请核对垫付合计金额与打款金额")

                payer = next(iter(distinct_payers))
                is_batch = len(advancement_items) > 1
                reim_entry_id = (
                    f"reim-{req.statement_id}-batch"
                    if is_batch
                    else f"reim-{req.statement_id}-{requested_ids[0]}"
                )
                occupied_reim = False
                if isinstance(session, AsyncSession):
                    occupied_reim = await session.get(JournalEntry, reim_entry_id) is not None
                else:
                    try:
                        cur_reim = await conn.execute(
                            "SELECT 1 FROM journal_entries WHERE id = ? AND company_id = ?",
                            (reim_entry_id, company_id),
                        )
                        occupied_reim = await cur_reim.fetchone() is not None
                    except Exception:
                        occupied_reim = False
                if occupied_reim:
                    reim_entry_id = f"{reim_entry_id}-{uuid4().hex[:6]}"
                first_title = advancement_items[0][1].get("title", "")
                reim_title = (
                    f"合并报销垫付款-{first_title}等{len(advancement_items)}笔"
                    if is_batch
                    else f"报销垫付款-{first_title}"
                )
                reim_category = "股东垫付报销" if payer == "boss_personal" else "员工垫付报销"
                reimbursed_states = {
                    eid: {
                        "billingStatus": p.get("billingStatus"),
                        "paymentDate": p.get("paymentDate"),
                    }
                    for eid, p, _ in advancement_items
                }
                reim_data = {
                    "id": reim_entry_id,
                    "title": reim_title,
                    "amount": abs(stmt_amount),
                    "direction": "owner_transfer",
                    "category": reim_category,
                    "payer": payer,
                    "billingStatus": "reconciled",
                    "occurDate": str(stmt["transaction_date"][:10]),
                    "paymentDate": str(stmt["transaction_date"][:10]),
                    "source": "bank_import",
                    "reimbursedEntryIds": list(requested_ids),
                    "reimbursedEntryStates": reimbursed_states,
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
                    f"UPDATE entries SET payload = json_set(payload, '$.billingStatus', 'reconciled', '$.paymentDate', ?) "
                    f"WHERE company_id = ? AND id IN ({placeholders})",
                    (str(stmt["transaction_date"][:10]), company_id, *requested_ids),
                )
                await conn.commit()
                await sync_core(stmt, [reim_entry_id])
                res_body: Dict[str, Any] = {"status": "matched", "entryId": reim_entry_id}
                if is_batch:
                    res_body["reimbursedEntryIds"] = list(requested_ids)
                return await respond(res_body)

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

        # salary_service 需在两个工资/社保分支之前导入（pyflakes 曾抓到漏导入：
        # 社保缴纳流水走第一分支时 NameError 500）
        import services.salary_service as salary_service

        if money(stmt["amount"] or ZERO) < ZERO and _looks_like_payroll_tax(entry_data):
            return await _handle_payroll_guard_in_confirm(
                kind="tax",
                req=req,
                session=session,
                company_id=company_id,
                user_id=user_id,
                stmt=stmt,
                entry_data=entry_data,
                salary_service=salary_service,
                respond=respond,
                sync_core=sync_core,
            )

        if money(stmt["amount"] or ZERO) < ZERO and _looks_like_payroll_liability(entry_data, str(stmt["description"] or "")):
            return await _handle_payroll_guard_in_confirm(
                kind="liability",
                req=req,
                session=session,
                company_id=company_id,
                user_id=user_id,
                stmt=stmt,
                entry_data=entry_data,
                salary_service=salary_service,
                respond=respond,
                sync_core=sync_core,
            )

        if money(stmt["amount"] or ZERO) < ZERO and _looks_like_salary_payment(entry_data, str(stmt["description"] or "")):
            return await _handle_payroll_guard_in_confirm(
                kind="salary",
                req=req,
                session=session,
                company_id=company_id,
                user_id=user_id,
                stmt=stmt,
                entry_data=entry_data,
                salary_service=salary_service,
                respond=respond,
                sync_core=sync_core,
            )

        if entry_data.get("category") not in ("微信支付结算", "支付宝结算", "平台提现结算"):
            settlement_candidates = await get_platform_settlement_candidates(company_id, dict(stmt))
            if settlement_candidates:
                raise HTTPException(status_code=409, detail={
                    "code": "EXISTING_WECHAT_ENTRIES",
                    "message": "该平台结算已有收入及手续费账目，请合并核销原账目，不能再次记为收入或支出",
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

        # 交易对手是银行流水里最可靠的身份信息：往来科目（应收/应付/其他应收付/
        # 实收资本）要靠它挂到具体单位，否则账龄表和股东实缴只能看到一个总数。
        counterparty = extract_statement_counterparty(str(stmt["description"] or ""))
        if counterparty and not str(entry_data.get("counterparty") or "").strip():
            entry_data["counterparty"] = counterparty

        entry_id = f"bank-{req.statement_id}"
        occupied_row = None
        conn = await get_conn()
        try:
            try:
                cursor = await conn.execute(
                    "SELECT status FROM journal_entries WHERE id = ? AND company_id = ?",
                    (entry_id, company_id),
                )
                occupied_row = await cursor.fetchone()
            except Exception:
                # 单测/legacy 夹具可能没有权威 journal 表：按未占用处理，交由后续路径校验
                occupied_row = None
        finally:
            await conn.close()
        if occupied_row is not None and str(occupied_row["status"] or "") not in {"draft", "pending_review"}:
            # 确定性 id 已被已过账/已冲销凭证占用：save_entry 对已存在凭证只返回不报错，
            # 复用会把冲销凭证重新挂回流水并谎报「created」（2026-11-23 事故）。
            # 重新认可入账必须落一笔新凭证。
            entry_id = f"bank-{req.statement_id}-{uuid4().hex[:8]}"
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
        rule_warning = ""
        # 模糊分类组的正确科目取决于账外事实（认缴进度、缴款性质），同一附言
        # 未必每次同科目；沉淀自动规则会把本月的判断错误套用到未来流水。
        if req.rememberRule and not is_ambiguity_category(str(entry_data.get("category") or "")):
            try:
                saved_rule_pattern = await save_company_category_rule(
                    company_id,
                    str(stmt["description"] or ""),
                    stmt["amount"],
                    str(entry_data.get("category") or ""),
                    source_statement_id=req.statement_id,
                    user_id=user_id,
                )
            except Exception:
                # 流水已成功入账和匹配；规则只是后续效率优化，不能在此时
                # 返回 500 让用户误以为第一次确认失败并重复操作。
                logger.exception(
                    "保存银行分类规则失败，已保留成功入账 statement=%s company=%s",
                    req.statement_id,
                    company_id,
                )
                rule_warning = "流水已入账，但分类规则保存失败，可在流水规则中重试"
        response_data: Dict[str, Any] = {"status": "created", "entryId": entry_id}
        if saved_rule_pattern:
            response_data["savedRulePattern"] = saved_rule_pattern
        if rule_warning:
            response_data["ruleWarning"] = rule_warning
        return await respond(response_data)

    return await respond({"status": "error", "message": "无效的请求"})
