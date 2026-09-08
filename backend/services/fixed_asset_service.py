from services.json_utils import json_dumps
import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from services.database import get_conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_schema() -> None:
    conn = await get_conn()
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS fixed_assets ("
            "  id TEXT PRIMARY KEY,"
            "  user_id TEXT NOT NULL,"
            "  company_id TEXT NOT NULL,"
            "  payload TEXT NOT NULL,"
            "  created_at TEXT NOT NULL,"
            "  updated_at TEXT NOT NULL"
            ")"
        )
        # Migrations for pre-existing tables missing columns
        cursor = await conn.execute("PRAGMA table_info(fixed_assets)")
        columns = {row["name"] for row in await cursor.fetchall()}
        migration_sql = {
            "user_id": "ALTER TABLE fixed_assets ADD COLUMN user_id TEXT NOT NULL DEFAULT ''",
            "company_id": "ALTER TABLE fixed_assets ADD COLUMN company_id TEXT NOT NULL DEFAULT ''",
            "payload": "ALTER TABLE fixed_assets ADD COLUMN payload TEXT NOT NULL DEFAULT '{}'",
            "created_at": "ALTER TABLE fixed_assets ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
            "updated_at": "ALTER TABLE fixed_assets ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
        }
        for col, sql in migration_sql.items():
            if col not in columns:
                await conn.execute(sql)

        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_assets_company_updated ON fixed_assets(company_id, updated_at DESC)"
        )

        now = _now_iso()
        cursor = await conn.execute(
            "SELECT id, user_id, company_id, payload, created_at, updated_at FROM fixed_assets "
            "WHERE user_id = '' OR company_id = '' OR created_at = '' OR updated_at = ''"
        )
        rows = await cursor.fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except Exception:
                payload = {}
            uid = row["user_id"] or payload.get("userId") or payload.get("user_id") or "unknown"
            cid = row["company_id"] or payload.get("companyId") or payload.get("company_id") or "c1"
            ca = row["created_at"] or payload.get("created_at") or now
            ua = row["updated_at"] or payload.get("updated_at") or ca
            await conn.execute(
                "UPDATE fixed_assets SET user_id = ?, company_id = ?, created_at = ?, updated_at = ? WHERE id = ?",
                (str(uid), str(cid), str(ca), str(ua), row["id"]),
            )
        await conn.commit()
    finally:
        await conn.close()


async def _ensure_db_ready() -> None:
    await _ensure_schema()


async def save_fixed_asset(asset: Dict[str, Any], *, user_id: str, company_id: str) -> Dict[str, Any]:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        asset = dict(asset)
        asset.pop("user_id", None)
        asset.pop("userId", None)
        asset.pop("company_id", None)
        asset.pop("companyId", None)
        asset_id = str(asset.get("id") or "")
        source_entry_id = str(asset.get("sourceEntryId") or "")
        if source_entry_id:
            cursor = await conn.execute(
                "SELECT id, payload FROM fixed_assets WHERE user_id = ? AND company_id = ?",
                (user_id, company_id),
            )
            existing_rows = await cursor.fetchall()
            for existing in existing_rows:
                try:
                    payload = json.loads(existing["payload"] or "{}")
                except Exception:
                    payload = {}
                if str(payload.get("sourceEntryId") or "") == source_entry_id:
                    asset_id = str(existing["id"])
                    break
        if not asset_id:
            import random
            asset_id = f"fa-srv-{int(datetime.now(timezone.utc).timestamp() * 1000)}-{random.randint(1000, 9999)}"

        now = _now_iso()
        cursor = await conn.execute("SELECT created_at FROM fixed_assets WHERE id = ?", (asset_id,))
        row = await cursor.fetchone()
        created_at = str(row["created_at"]) if row else now

        normalized = {
            "accumulatedDepreciation": 0.0,
            "status": "in_use",
            **asset,
            "id": asset_id,
            "companyId": company_id,
            "userId": user_id,
            "created_at": created_at,
            "updated_at": now,
        }

        await conn.execute(
            "INSERT INTO fixed_assets (id, user_id, company_id, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET user_id=excluded.user_id, company_id=excluded.company_id, payload=excluded.payload, updated_at=excluded.updated_at",
            (asset_id, user_id, company_id, json_dumps(normalized, ensure_ascii=False), created_at, now),
        )
        await conn.commit()
        return normalized
    finally:
        await conn.close()


async def list_fixed_assets(user_id: str, company_id: str) -> List[Dict[str, Any]]:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT payload FROM fixed_assets WHERE user_id = ? AND company_id = ? ORDER BY updated_at DESC",
            (user_id, company_id),
        )
        rows = await cursor.fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            try:
                item = json.loads(row["payload"])
                if isinstance(item, dict):
                    if "accumulatedDepreciation" not in item:
                        item["accumulatedDepreciation"] = 0.0
                    if "status" not in item:
                        item["status"] = "in_use"
                    out.append(item)
            except Exception:
                continue
        return out
    finally:
        await conn.close()


async def delete_fixed_asset(asset_id: str, *, user_id: str, company_id: str) -> bool:
    await _ensure_db_ready()
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "DELETE FROM fixed_assets WHERE id = ? AND user_id = ? AND company_id = ?",
            (asset_id, user_id, company_id),
        )
        await conn.commit()
        return cursor.rowcount > 0
    finally:
        await conn.close()
