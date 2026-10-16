# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""小私账服务 —— 记录未入公账的经营收支"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.database import get_conn
from services.audit_service import log_action
from core.money import ZERO, money, money_text

_PAGE_SIZE_DEFAULT = 20
_DEPOSIT_CATEGORY = "押金保证金"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_schema() -> None:
    conn = await get_conn()
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS private_expenses ("
            "  id TEXT PRIMARY KEY,"
            "  company_id TEXT NOT NULL,"
            "  user_id TEXT NOT NULL,"
            "  title TEXT NOT NULL,"
            "  amount NUMERIC(20,2) NOT NULL,"
            "  direction TEXT DEFAULT 'expense',"
            "  category TEXT DEFAULT '',"
            "  occur_date TEXT DEFAULT '',"
            "  payer TEXT DEFAULT '',"
            "  owner_name TEXT DEFAULT '本人',"
            "  private_account TEXT DEFAULT '',"
            "  is_available INTEGER DEFAULT 1,"
            "  memo TEXT DEFAULT '',"
            "  invoice_status TEXT DEFAULT 'no_invoice',"
            "  substitute_entry_id TEXT DEFAULT '',"
            "  substitute_memo TEXT DEFAULT '',"
            "  created_at TEXT NOT NULL,"
            "  updated_at TEXT NOT NULL"
            ")"
        )
        # Check and dynamically add columns for existing tables
        cursor = await conn.execute("PRAGMA table_info(private_expenses)")
        columns = [row["name"] for row in await cursor.fetchall()]
        if "direction" not in columns:
            await conn.execute("ALTER TABLE private_expenses ADD COLUMN direction TEXT DEFAULT 'expense'")
        if "invoice_status" not in columns:
            await conn.execute("ALTER TABLE private_expenses ADD COLUMN invoice_status TEXT DEFAULT 'no_invoice'")
        if "substitute_entry_id" not in columns:
            await conn.execute("ALTER TABLE private_expenses ADD COLUMN substitute_entry_id TEXT DEFAULT ''")
        if "substitute_memo" not in columns:
            await conn.execute("ALTER TABLE private_expenses ADD COLUMN substitute_memo TEXT DEFAULT ''")
        if "owner_name" not in columns:
            await conn.execute("ALTER TABLE private_expenses ADD COLUMN owner_name TEXT DEFAULT '本人'")
        if "private_account" not in columns:
            await conn.execute("ALTER TABLE private_expenses ADD COLUMN private_account TEXT DEFAULT ''")
        if "is_available" not in columns:
            await conn.execute("ALTER TABLE private_expenses ADD COLUMN is_available INTEGER DEFAULT 1")
            await conn.execute(
                "UPDATE private_expenses SET private_account = '小红书平台保证金', is_available = 0 "
                "WHERE direction = 'income' AND title LIKE '%小红书%'"
            )
        await conn.execute(
            "UPDATE private_expenses SET owner_name = '本人' WHERE owner_name IS NULL OR owner_name = ''"
        )
        await conn.execute(
            "UPDATE private_expenses SET private_account = CASE "
            "WHEN title LIKE '%小红书%' THEN '小红书平台保证金' ELSE '平台保证金' END "
            "WHERE category = ? AND (private_account IS NULL OR private_account = '')",
            (_DEPOSIT_CATEGORY,),
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pe_company ON private_expenses(company_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pe_company_date ON private_expenses(company_id, occur_date DESC)"
        )
        await conn.commit()
    finally:
        await conn.close()


async def list_expenses(
    company_id: str, page: int = 1, page_size: int = _PAGE_SIZE_DEFAULT,
    owner_name: str = "",
) -> Dict[str, Any]:
    """分页查询私账记录，按日期倒序。"""
    await _ensure_schema()
    offset = (page - 1) * page_size
    conn = await get_conn()
    try:
        where = "company_id = ?"
        params: List[Any] = [company_id]
        if owner_name:
            where += " AND owner_name = ?"
            params.append(owner_name)
        cursor = await conn.execute(
            f"SELECT * FROM private_expenses WHERE {where} ORDER BY occur_date DESC, created_at DESC LIMIT ? OFFSET ?",
            (*params, page_size, offset),
        )
        rows = await cursor.fetchall()
        items = [{**dict(r), "amount": money_text(r["amount"])} for r in rows]

        cursor2 = await conn.execute(
            f"SELECT COUNT(*) AS cnt FROM private_expenses WHERE {where}",
            tuple(params),
        )
        total_row = await cursor2.fetchone()
        total = total_row["cnt"] if total_row else 0
    finally:
        await conn.close()

    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_expense(company_id: str, expense_id: str) -> Optional[Dict[str, Any]]:
    """获取单条私账记录。"""
    await _ensure_schema()
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM private_expenses WHERE company_id = ? AND id = ?",
            (company_id, expense_id),
        )
        row = await cursor.fetchone()
        return ({**dict(row), "amount": money_text(row["amount"])}) if row else None
    finally:
        await conn.close()


async def create_expense(
    company_id: str, user_id: str, data: Dict[str, Any]
) -> Dict[str, Any]:
    """创建私账记录。"""
    await _ensure_schema()
    now = _now_iso()
    import random
    eid = f"pe-{company_id}-{int(datetime.now(timezone.utc).timestamp() * 1000)}-{random.randint(1000, 9999)}"
    title = str(data.get("title", "")).strip()
    amount = money(data.get("amount") or ZERO, allow_negative=False)
    direction = str(data.get("direction") or "expense").strip()
    if direction not in ("income", "expense"):
        direction = "expense"
    category = str(data.get("category", "")).strip()
    occur_date = str(data.get("occur_date") or data.get("occurDate") or "")[:10]
    payer = str(data.get("payer", "")).strip()
    owner_name = str(data.get("owner_name") or "本人").strip() or "本人"
    private_account = str(data.get("private_account") or "").strip()
    is_available = 1 if bool(data.get("is_available", True)) else 0
    memo = str(data.get("memo", "")).strip()
    invoice_status = str(data.get("invoice_status") or data.get("invoiceStatus") or "no_invoice").strip()
    substitute_entry_id = str(data.get("substitute_entry_id") or data.get("substituteEntryId") or "").strip()
    substitute_memo = str(data.get("substitute_memo") or data.get("substituteMemo") or "").strip()

    conn = await get_conn()
    try:
        await conn.execute(
            "INSERT INTO private_expenses "
            "(id, company_id, user_id, title, amount, direction, category, occur_date, payer, owner_name, private_account, is_available, memo, invoice_status, substitute_entry_id, substitute_memo, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, company_id, user_id, title, amount, direction, category, occur_date, payer, owner_name, private_account, is_available, memo, invoice_status, substitute_entry_id, substitute_memo, now, now),
        )
        await conn.commit()
    finally:
        await conn.close()

    await log_action(company_id, user_id, "create", "private_expense", eid,
                     {"title": title, "amount": amount, "direction": direction})
    return {"id": eid, "title": title, "amount": money_text(amount), "direction": direction, "category": category,
            "occur_date": occur_date, "payer": payer, "owner_name": owner_name,
            "private_account": private_account, "is_available": bool(is_available), "memo": memo,
            "invoice_status": invoice_status, "substitute_entry_id": substitute_entry_id,
            "substitute_memo": substitute_memo,
            "company_id": company_id, "user_id": user_id, "created_at": now, "updated_at": now}


async def update_expense(
    company_id: str, expense_id: str, user_id: str, data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """更新私账记录。"""
    existing = await get_expense(company_id, expense_id)
    if not existing:
        return None

    now = _now_iso()
    title = str(data.get("title", existing["title"])).strip()
    amount = money(data.get("amount", existing["amount"]), allow_negative=False)
    direction = str(data.get("direction") or existing.get("direction", "expense")).strip()
    if direction not in ("income", "expense"):
        direction = "expense"
    category = str(data.get("category", existing.get("category", ""))).strip()
    occur_date = str(data.get("occur_date") or data.get("occurDate") or existing.get("occur_date", ""))[:10]
    payer = str(data.get("payer", existing.get("payer", ""))).strip()
    owner_name = str(data.get("owner_name", existing.get("owner_name", "本人"))).strip() or "本人"
    private_account = str(data.get("private_account", existing.get("private_account", ""))).strip()
    is_available = 1 if bool(data.get("is_available", existing.get("is_available", True))) else 0
    memo = str(data.get("memo", existing.get("memo", ""))).strip()
    invoice_status = str(data.get("invoice_status") or data.get("invoiceStatus") or existing.get("invoice_status", "no_invoice")).strip()
    substitute_entry_id = str(data.get("substitute_entry_id") or data.get("substituteEntryId") or existing.get("substitute_entry_id", "")).strip()
    substitute_memo = str(data.get("substitute_memo") or data.get("substituteMemo") or existing.get("substitute_memo", "")).strip()

    conn = await get_conn()
    try:
        await conn.execute(
            "UPDATE private_expenses SET title=?, amount=?, direction=?, category=?, occur_date=?, payer=?, owner_name=?, private_account=?, is_available=?, memo=?, invoice_status=?, substitute_entry_id=?, substitute_memo=?, updated_at=? "
            "WHERE company_id=? AND id=?",
            (title, amount, direction, category, occur_date, payer, owner_name, private_account, is_available, memo, invoice_status, substitute_entry_id, substitute_memo, now, company_id, expense_id),
        )
        await conn.commit()
    finally:
        await conn.close()

    await log_action(company_id, user_id, "update", "private_expense", expense_id,
                     {"title": title, "amount": amount, "direction": direction})
    return {**existing, "title": title, "amount": money_text(amount), "direction": direction, "category": category,
            "occur_date": occur_date, "payer": payer, "owner_name": owner_name,
            "private_account": private_account, "is_available": bool(is_available), "memo": memo,
            "invoice_status": invoice_status, "substitute_entry_id": substitute_entry_id,
            "substitute_memo": substitute_memo, "updated_at": now}


async def delete_expense(company_id: str, expense_id: str, user_id: str) -> bool:
    """删除私账记录。"""
    existing = await get_expense(company_id, expense_id)
    if not existing:
        return False

    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "DELETE FROM private_expenses WHERE company_id = ? AND id = ?",
            (company_id, expense_id),
        )
        await conn.commit()
        deleted = cursor.rowcount > 0
    finally:
        await conn.close()

    if deleted:
        await log_action(company_id, user_id, "delete", "private_expense", expense_id,
                         {"title": existing.get("title")})
    return deleted


async def get_expense_summary(company_id: str, owner_name: str = "") -> Dict[str, Any]:
    """汇总：收支金额、总笔数与简单冲销结果。"""
    await _ensure_schema()
    conn = await get_conn()
    try:
        where = "company_id = ?"
        params: List[Any] = [_DEPOSIT_CATEGORY, _DEPOSIT_CATEGORY, company_id]
        if owner_name:
            where += " AND owner_name = ?"
            params.append(owner_name)
        cursor = await conn.execute(
            "SELECT "
            "  COALESCE(NULLIF(owner_name, ''), '本人') AS owner_name,"
            "  COUNT(*) AS cnt,"
            "  COALESCE(SUM(CASE WHEN direction = 'income' THEN amount ELSE 0 END), 0) AS income_amount,"
            "  COALESCE(SUM(CASE WHEN direction = 'income' AND COALESCE(is_available, 1) = 1 THEN amount ELSE 0 END), 0) AS available_income_amount,"
            "  COALESCE(SUM(CASE WHEN (direction IS NULL OR direction = '' OR direction = 'expense') AND category NOT IN (?) THEN amount ELSE 0 END), 0) AS expense_amount,"
            "  COALESCE(SUM(CASE WHEN (direction IS NULL OR direction = '' OR direction = 'expense') AND category IN (?) THEN amount ELSE 0 END), 0) AS deposit_amount "
            f"FROM private_expenses WHERE {where} GROUP BY COALESCE(NULLIF(owner_name, ''), '本人') ORDER BY owner_name",
            tuple(params),
        )
        rows = await cursor.fetchall()
        if not rows:
            return {
                "count": 0,
                "total_amount": "0.00",
                "income_amount": "0.00",
                "expense_amount": "0.00",
                "deposit_amount": "0.00",
                "available_income_amount": "0.00",
                "offset_amount": "0.00",
                "balance_amount": "0.00",
                "owners": [],
            }
        owners = []
        for row in rows:
            income_amount = money(row["income_amount"] or ZERO)
            expense_amount = money(row["expense_amount"] or ZERO)
            deposit_amount = money(row["deposit_amount"] or ZERO)
            available_income_amount = money(row["available_income_amount"] or ZERO)
            offset_amount = min(available_income_amount, expense_amount)
            owners.append({
                "owner_name": row["owner_name"],
                "count": row["cnt"],
                "income_amount": money_text(income_amount),
                "expense_amount": money_text(expense_amount),
                "deposit_amount": money_text(deposit_amount),
                "available_income_amount": money_text(available_income_amount),
                "offset_amount": money_text(offset_amount),
                "balance_amount": money_text(available_income_amount - expense_amount),
            })
        return {
            "count": sum(item["count"] for item in owners),
            "total_amount": money_text(sum((money(item["expense_amount"]) for item in owners), ZERO)),
            "income_amount": money_text(sum((money(item["income_amount"]) for item in owners), ZERO)),
            "expense_amount": money_text(sum((money(item["expense_amount"]) for item in owners), ZERO)),
            "deposit_amount": money_text(sum((money(item["deposit_amount"]) for item in owners), ZERO)),
            "available_income_amount": money_text(sum((money(item["available_income_amount"]) for item in owners), ZERO)),
            "offset_amount": money_text(sum((money(item["offset_amount"]) for item in owners), ZERO)),
            "balance_amount": money_text(sum((money(item["balance_amount"]) for item in owners), ZERO)),
            "owners": owners,
        }
    finally:
        await conn.close()
