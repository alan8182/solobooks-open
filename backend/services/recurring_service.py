# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""周期性交易模板服务"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from services.database import get_conn
from services.entry_service import save_entry
from services.audit_service import log_action



def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _compute_next_due(from_date: str, frequency: str) -> str:
    dt = datetime.strptime(from_date, "%Y-%m-%d")
    if frequency == "weekly":
        return (dt + timedelta(days=7)).strftime("%Y-%m-%d")
    elif frequency == "biweekly":
        return (dt + timedelta(days=14)).strftime("%Y-%m-%d")
    elif frequency == "monthly":
        month = dt.month + 1
        year = dt.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(dt.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                           31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
        return f"{year}-{month:02d}-{day:02d}"
    elif frequency == "quarterly":
        month = dt.month + 3
        year = dt.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(dt.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                           31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
        return f"{year}-{month:02d}-{day:02d}"
    elif frequency == "yearly":
        return f"{dt.year + 1}-{dt.month:02d}-{dt.day:02d}"
    else:
        return from_date


async def _ensure_schema() -> None:
    conn = await get_conn()
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS recurring_templates ("
            "  id TEXT PRIMARY KEY,"
            "  company_id TEXT NOT NULL,"
            "  name TEXT NOT NULL,"
            "  template_data TEXT NOT NULL,"
            "  frequency TEXT NOT NULL,"
            "  next_due_date TEXT NOT NULL,"
            "  is_active INTEGER NOT NULL DEFAULT 1,"
            "  created_at TEXT NOT NULL,"
            "  updated_at TEXT NOT NULL"
            ")"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_recurring_company ON recurring_templates(company_id)"
        )
        await conn.commit()
    finally:
        await conn.close()


async def _ensure_db_ready(company_id: str) -> None:
    await _ensure_schema()


async def list_templates(company_id: str) -> List[Dict[str, Any]]:
    await _ensure_db_ready(company_id)
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM recurring_templates WHERE company_id = ? ORDER BY updated_at DESC",
            (company_id,),
        )
        rows = await cursor.fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["template_data"] = json.loads(item["template_data"])
            out.append(item)
        return out
    finally:
        await conn.close()


async def get_template(company_id: str, template_id: str) -> Optional[Dict[str, Any]]:
    await _ensure_db_ready(company_id)
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM recurring_templates WHERE company_id = ? AND id = ?",
            (company_id, template_id),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        item = dict(row)
        item["template_data"] = json.loads(item["template_data"])
        return item
    finally:
        await conn.close()


async def create_template(company_id: str, name: str, template_data: Dict[str, Any],
                          frequency: str, next_due_date: str) -> Optional[Dict[str, Any]]:
    await _ensure_db_ready(company_id)
    conn = await get_conn()
    try:
        now = _now_iso()
        tid = f"rt-{company_id}-{int(datetime.now(timezone.utc).timestamp() * 1000)}"
        await conn.execute(
            "INSERT INTO recurring_templates "
            "(id, company_id, name, template_data, frequency, next_due_date, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (tid, company_id, name, json.dumps(template_data, ensure_ascii=False),
             frequency, next_due_date, now, now),
        )
        await conn.commit()
        cursor = await conn.execute("SELECT * FROM recurring_templates WHERE id = ?", (tid,))
        row = await cursor.fetchone()
        if not row:
            return None
        item = dict(row)
        item["template_data"] = json.loads(item["template_data"])
        return item
    finally:
        await conn.close()


async def update_template(company_id: str, template_id: str,
                          updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    await _ensure_db_ready(company_id)
    conn = await get_conn()
    try:
        fields = []
        values = []
        for key in ("name", "frequency", "next_due_date", "is_active"):
            if key in updates and updates[key] is not None:
                fields.append(f"{key} = ?")
                values.append(updates[key])
        if "template_data" in updates and updates["template_data"] is not None:
            fields.append("template_data = ?")
            values.append(json.dumps(updates["template_data"], ensure_ascii=False))
        if not fields:
            return None
        fields.append("updated_at = ?")
        values.append(_now_iso())
        values.extend([company_id, template_id])
        await conn.execute(
            f"UPDATE recurring_templates SET {', '.join(fields)} WHERE company_id = ? AND id = ?",
            values,
        )
        await conn.commit()
        return await get_template(company_id, template_id)
    finally:
        await conn.close()


async def delete_template(company_id: str, template_id: str) -> bool:
    await _ensure_db_ready(company_id)
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "DELETE FROM recurring_templates WHERE company_id = ? AND id = ?",
            (company_id, template_id),
        )
        await conn.commit()
        return cursor.rowcount > 0
    finally:
        await conn.close()


async def apply_template(company_id: str, template_id: str, *,
                         user_id: str) -> Optional[Dict[str, Any]]:
    """Apply a template: create an entry and advance next_due_date."""
    template = await get_template(company_id, template_id)
    if not template:
        return None
    if not template["is_active"]:
        return None

    data = template["template_data"]
    entry_data = {
        **data,
        "occurDate": _today_date(),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    entry = await save_entry(entry_data, user_id=user_id, company_id=company_id)
    await log_action(company_id, user_id, "apply", "recurring_template", template_id,
                     {"name": template["name"], "entry_id": entry.get("id")})

    # Advance next_due_date
    next_due = _compute_next_due(template["next_due_date"], template["frequency"])
    await update_template(company_id, template_id, {"next_due_date": next_due})

    return entry
