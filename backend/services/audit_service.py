# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from services.json_utils import json_dumps
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.database import get_conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_schema() -> None:
    conn = await get_conn()
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS audit_log ("
            "  id TEXT PRIMARY KEY,"
            "  company_id TEXT NOT NULL,"
            "  user_id TEXT NOT NULL,"
            "  action TEXT NOT NULL,"
            "  entity_type TEXT NOT NULL,"
            "  entity_id TEXT,"
            "  details TEXT,"
            "  created_at TEXT NOT NULL"
            ")"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_company ON audit_log(company_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC)"
        )
        await conn.commit()
    finally:
        await conn.close()


async def log_action(company_id: str, user_id: str, action: str,
                     entity_type: str, entity_id: Optional[str] = None,
                     details: Optional[Dict[str, Any]] = None) -> None:
    """Log an audit action (fire-and-forget)."""
    try:
        await _ensure_schema()
        conn = await get_conn()
        try:
            now = _now_iso()
            log_id = f"aud-{company_id}-{int(datetime.now(timezone.utc).timestamp() * 1000000)}"
            await conn.execute(
                "INSERT INTO audit_log (id, company_id, user_id, action, entity_type, entity_id, details, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (log_id, company_id, user_id, action, entity_type, entity_id,
                 json_dumps(details, ensure_ascii=False) if details else None, now),
            )
            await conn.commit()
        finally:
            await conn.close()
    except Exception:
        pass  # fire-and-forget: never block the main flow


async def get_audit_logs(company_id: str, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """Get paginated audit logs for a company."""
    await _ensure_schema()
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM audit_log WHERE company_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (company_id, limit, offset),
        )
        rows = await cursor.fetchall()
        out = []
        for r in rows:
            item = dict(r)
            if item.get("details"):
                try:
                    item["details"] = json.loads(item["details"])
                except (json.JSONDecodeError, TypeError):
                    pass
            out.append(item)
        return out
    finally:
        await conn.close()


async def count_audit_logs(company_id: str) -> int:
    """Get total audit log count for pagination."""
    await _ensure_schema()
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT COUNT(*) AS cnt FROM audit_log WHERE company_id = ?",
            (company_id,),
        )
        row = await cursor.fetchone()
        return row["cnt"] if row else 0
    finally:
        await conn.close()
