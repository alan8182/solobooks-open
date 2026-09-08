"""Server-derived todo list with persisted handling state (async SQLite)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from decimal import Decimal

from services.database import get_conn
from services.entry_service import list_entries
from services.fixed_asset_service import list_fixed_assets
from core.money import ZERO, money, money_text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_schema() -> None:
    conn = await get_conn()
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS todo_states ("
            "  todo_key TEXT PRIMARY KEY,"
            "  user_id TEXT NOT NULL,"
            "  company_id TEXT NOT NULL,"
            "  done INTEGER NOT NULL DEFAULT 0,"
            "  dismissed INTEGER NOT NULL DEFAULT 0,"
            "  created_at TEXT NOT NULL,"
            "  updated_at TEXT NOT NULL"
            ")"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_todo_states_user_company ON todo_states(user_id, company_id)"
        )
        await conn.commit()
    finally:
        await conn.close()


async def _ensure_db_ready() -> None:
    await _ensure_schema()


def _amount(value: Any) -> Decimal:
    try:
        return money(value or ZERO)
    except (TypeError, ValueError):
        return ZERO


def _has_document(entry: Dict[str, Any]) -> bool:
    status = str(entry.get("billingStatus") or "")
    if status in {"issued", "has_invoice", "has_receipt", "no_invoice"}:
        return True
    return bool(entry.get("invoiceType") or entry.get("invoiceNumber"))


def _has_invoice(entry: Dict[str, Any]) -> bool:
    status = str(entry.get("billingStatus") or "")
    if status in {"issued", "has_invoice"}:
        return True
    return bool(entry.get("invoiceType") or entry.get("invoiceNumber"))


def _todo(entry: Dict[str, Any], rule_id: str, type_: str, level: str, title: str, desc: str, **extra: Any) -> Dict[str, Any]:
    entry_id = str(entry.get("id") or "")
    todo_key = f"entry:{entry_id}:{rule_id}"
    return {
        "id": todo_key, "todoKey": todo_key, "type": type_, "level": level, "priority": level,
        "title": title, "desc": desc, "done": False, "dismissed": False,
        "amount": money_text(_amount(entry.get("amount"))),
        "deadline": extra.get("deadline"),
        "companyId": entry.get("companyId") or entry.get("company_id"),
        "meta": {"entryId": entry_id, "ruleId": rule_id, **extra.get("meta", {})},
    }


def _derive_entry_todos(entries: List[Dict[str, Any]], matched_entry_ids: Set[str] = None) -> List[Dict[str, Any]]:
    if matched_entry_ids is None:
        matched_entry_ids = set()
    todos: List[Dict[str, Any]] = []
    for entry in entries:
        entry_id = str(entry.get("id") or "")
        if not entry_id:
            continue
        title = str(entry.get("title") or entry.get("merchant") or "这笔业务")
        category = str(entry.get("category") or "")
        direction = str(entry.get("direction") or "")
        payer = str(entry.get("payer") or "")
        amount = _amount(entry.get("amount"))
        account_code = str(entry.get("accountCode") or "")
        source = str(entry.get("source") or "")

        if direction == "expense" and source != "invoice_import" and not _has_document(entry):
            todos.append(_todo(entry, "missing_document", "receipt", "high",
                               f"{title} - 补充票据", f"¥{amount:,.0f}，这笔支出需要补齐发票或注明无票原因。"))
        if source == "invoice_import" and entry_id not in matched_entry_ids:
            todos.append(_todo(entry, "invoice_unpaid", "task", "high",
                               f"{title} - 发票待付款/报销", f"¥{amount:,.0f}，此发票在流水中未找到对应支出。请确认是否已付款，或将其与银行流水匹配。"))
        if direction == "income" and not _has_invoice(entry):
            todos.append(_todo(entry, "income_invoice", "tax", "medium",
                               f"{title} - 开票状态确认", "收入流水需要确认是否已开票，便于后续申报。"))
        if category == "固定资产" or account_code.startswith("1601"):
            todos.append(_todo(entry, "fixed_asset_depreciation", "task", "high",
                               f"{title} - 固定资产需确认折旧方案", f"¥{amount:,.0f}，固定资产需要按使用年限计提折旧。",
                               meta={"action": "confirm_asset"}))
        if "招待" in category and amount >= 500:
            todos.append(_todo(entry, "entertainment_limit", "risk", "medium",
                               f"{title} - 招待费税前扣除复核", "招待费扣除比例受限，建议补充业务招待对象和事由。"))
        if "广告" in category and amount >= 10000:
            todos.append(_todo(entry, "ad_limit", "risk", "medium",
                               f"{title} - 广告费扣除比例复核", "广告宣传费金额较高，建议复核税前扣除比例。"))
        if payer == "boss_personal":
            todos.append(_todo(entry, "owner_transfer_nature", "risk", "medium",
                               f"{title} - 股东垫付性质确认", "请确认这笔款项是报销、借款还是资本投入。"))
    return todos


async def _load_states(conn, user_id: str, company_id: str) -> Dict[str, Dict[str, Any]]:
    cursor = await conn.execute(
        "SELECT todo_key, done, dismissed, updated_at FROM todo_states WHERE user_id = ? AND company_id = ?",
        (user_id, company_id),
    )
    rows = await cursor.fetchall()
    return {
        str(row["todo_key"]): {"done": bool(row["done"]), "dismissed": bool(row["dismissed"]), "updatedAt": row["updated_at"]}
        for row in rows
    }


async def list_todos(user_id: str, company_id: str) -> List[Dict[str, Any]]:
    entries = await list_entries(user_id, company_id)
    await list_fixed_assets(user_id, company_id)
    from services.bank_service import get_matched_entry_ids

    matched_entry_ids = await get_matched_entry_ids(company_id)
    
    conn = await get_conn()
    try:
        await _ensure_db_ready()
        # Query unmatched bank statements
        cursor = await conn.execute(
            "SELECT id, transaction_date, description, amount FROM bank_statements "
            "WHERE company_id = ? AND (matched_entry_id IS NULL OR matched_entry_id = '')",
            (company_id,),
        )
        unmatched_statements = [dict(row) for row in await cursor.fetchall()]
        
        states = await _load_states(conn, user_id, company_id)
    finally:
        await conn.close()

    derived = _derive_entry_todos(entries, matched_entry_ids)
    
    # Derive todos from unmatched statements
    for stmt in unmatched_statements:
        stmt_id = stmt["id"]
        todo_key = f"stmt:{stmt_id}:unmatched"
        amount = abs(stmt["amount"])
        direction_label = "收入" if stmt["amount"] >= 0 else "支出"
        title = f"流水待处理 - {stmt['description']}"
        desc = f"¥{amount:,.2f}（{direction_label}），发生于 {stmt['transaction_date'][:10]}，尚未匹配记账或补充发票。"
        
        derived.append({
            "id": todo_key,
            "todoKey": todo_key,
            "type": "bank",
            "level": "high",
            "priority": "high",
            "title": title,
            "desc": desc,
            "done": False,
            "dismissed": False,
            "amount": amount,
            "companyId": company_id,
            "meta": {"statementId": stmt_id, "ruleId": "unmatched_statement"},
        })

    merged: List[Dict[str, Any]] = []
    for todo in derived:
        state = states.get(todo["todoKey"], {})
        item = {**todo, **state}
        if not item.get("dismissed"):
            merged.append(item)
    return merged


async def update_todo_state(
    todo_key: str, *, user_id: str, company_id: str,
    done: Optional[bool] = None, dismissed: Optional[bool] = None,
) -> Dict[str, Any]:
    now = _now_iso()
    conn = await get_conn()
    try:
        await _ensure_db_ready()
        cursor = await conn.execute(
            "SELECT done, dismissed, created_at FROM todo_states WHERE todo_key = ? AND user_id = ? AND company_id = ?",
            (todo_key, user_id, company_id),
        )
        row = await cursor.fetchone()
        next_done = bool(row["done"]) if row else False
        next_dismissed = bool(row["dismissed"]) if row else False
        created_at = row["created_at"] if row else now
        if done is not None:
            next_done = bool(done)
        if dismissed is not None:
            next_dismissed = bool(dismissed)
        await conn.execute(
            "INSERT INTO todo_states (todo_key, user_id, company_id, done, dismissed, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(todo_key) DO UPDATE SET done=excluded.done, dismissed=excluded.dismissed, updated_at=excluded.updated_at",
            (todo_key, user_id, company_id, int(next_done), int(next_dismissed), created_at, now),
        )
        await conn.commit()
        return {"todoKey": todo_key, "done": next_done, "dismissed": next_dismissed, "updatedAt": now}
    finally:
        await conn.close()
