"""全局搜索服务"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from services.database import get_conn


async def search_all(company_id: str, query: str) -> Dict[str, List[Dict[str, Any]]]:
    """Search entries and chart_accounts by keyword."""
    q = query.strip().lower()
    if not q:
        return {"entries": [], "accounts": []}

    entries = await _search_entries(company_id, q)
    accounts = await _search_accounts(company_id, q)

    return {"entries": entries, "accounts": accounts}


async def _search_entries(company_id: str, q: str) -> List[Dict[str, Any]]:
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT payload FROM entries WHERE company_id = ? ORDER BY updated_at DESC LIMIT 200",
            (company_id,),
        )
        rows = await cursor.fetchall()
        out = []
        for row in rows:
            try:
                item = json.loads(row["payload"])
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            # Search in relevant fields
            title = (item.get("title") or "").lower()
            category = (item.get("category") or "").lower()
            merchant = (item.get("merchant") or "").lower()
            if q in title or q in category or q in merchant:
                out.append(item)
            if len(out) >= 20:
                break
        return out
    finally:
        await conn.close()


async def _search_accounts(company_id: str, q: str) -> List[Dict[str, Any]]:
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM chart_accounts WHERE company_id = ? AND (name LIKE ? OR code LIKE ?) AND is_active = 1 ORDER BY level ASC, code ASC LIMIT 20",
            (company_id, f"%{q}%", f"%{q}%"),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await conn.close()
