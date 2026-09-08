"""Category persistence service (async SQLite)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.database import get_conn
from services.account_mapper import CATEGORY_TO_ACCOUNT

# Direction lookup — every category in CATEGORY_TO_ACCOUNT must be mapped.
_DIRECTION_MAP = {
    # ── 收入类 ──
    "主营业务收入": "income", "其他业务收入": "income", "营业外收入": "income", "利息收入": "income",

    # ── 资本与往来 / 资产负债表科目 ──
    "股东投入": "owner_transfer", "资本公积": "owner_transfer",
    "股东垫付": "owner_transfer", "股东取现": "owner_transfer",
    "股东垫付报销": "owner_transfer", "员工垫付报销": "owner_transfer",
    "其他应收款": "owner_transfer", "其他应付款": "owner_transfer", "往来款": "owner_transfer",
    "老板投入": "owner_transfer", "老板垫付": "owner_transfer", "老板取现": "owner_transfer",

    # ── 支出类（不在以上两组的全部默认为支出）──
}

# 可在多个方向下使用的科目（校验时不强制方向）
_ANY_DIRECTION_CATEGORIES = {"股东垫付", "员工垫付"}

_DEFAULT_SORT = {
    "income": 0, "owner_transfer": 100, "expense": 200,
}


def _cat_direction(name: str) -> str:
    """返回分类的默认方向（seed/sort 用，未知默认 expense）。"""
    return _DIRECTION_MAP.get(name, "expense")


def _cat_bound_direction(name: str) -> Optional[str]:
    """返回分类的强制方向；未映射返回 None 表示不限制。"""
    return _DIRECTION_MAP.get(name)


def _cat_allows_any_direction(name: str) -> bool:
    return name in _ANY_DIRECTION_CATEGORIES


def _cat_sort(name: str, base: int) -> int:
    # Establish more refined ordering within expense categories
    order = [
        "原材料采购", "商品采购", "生产成本", "研发支出-费用化支出", "人力成本", "员工福利费",
        "差旅费", "办公费", "业务招待费", "推广费",
        "租金物业", "运费及物流", "水电通讯费", "信息技术服务费",
        "银行及金融手续费", "税费缴纳",
        "固定资产", "设备采购", "预付账款",
        "其他支出",
    ]
    try:
        return base + order.index(name)
    except ValueError:
        return base + 999


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_schema() -> None:
    conn = await get_conn()
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS categories ("
            "  id TEXT PRIMARY KEY,"
            "  company_id TEXT NOT NULL,"
            "  name TEXT NOT NULL,"
            "  direction TEXT NOT NULL,"
            "  parent_id TEXT,"
            "  sort_order INTEGER NOT NULL DEFAULT 0,"
            "  is_system INTEGER NOT NULL DEFAULT 0,"
            "  is_active INTEGER NOT NULL DEFAULT 1,"
            "  created_at TEXT NOT NULL,"
            "  updated_at TEXT NOT NULL"
            ")"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_categories_company ON categories(company_id)"
        )
        await conn.commit()
    finally:
        await conn.close()


async def _seed_system_categories(company_id: str) -> None:
    """Insert system default categories for a company if none exist."""
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT COUNT(*) AS cnt FROM categories WHERE company_id = ?", (company_id,)
        )
        row = await cursor.fetchone()
        if row["cnt"] > 0:
            # 补齐可能缺失的系统分类（针对老公司账本）
            now = _now_iso()
            for name, info in CATEGORY_TO_ACCOUNT.items():
                cursor_check = await conn.execute(
                    "SELECT id FROM categories WHERE company_id = ? AND name = ?", (company_id, name)
                )
                if await cursor_check.fetchone():
                    continue
                direction = info.get("accountType", "损益-费用")
                cat_dir = _cat_direction(name)
                sort_order = _cat_sort(name, _DEFAULT_SORT.get(cat_dir, 200))
                cid = f"sys-{company_id}-{hash(name) % 100000}"
                await conn.execute(
                    "INSERT INTO categories (id, company_id, name, direction, parent_id, sort_order, is_system, is_active, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, None, ?, 1, 1, ?, ?)",
                    (cid, company_id, name, cat_dir, sort_order, now, now)
                )
            await conn.commit()
            return

        now = _now_iso()
        seen = set()
        rows = []
        for name, info in CATEGORY_TO_ACCOUNT.items():
            if name in seen:
                continue
            seen.add(name)
            direction = info.get("accountType", "损益-费用")
            cat_dir = _cat_direction(name)
            sort_order = _cat_sort(name, _DEFAULT_SORT.get(cat_dir, 200))
            cid = f"sys-{company_id}-{hash(name) % 100000}"
            rows.append((
                cid, company_id, name, cat_dir,
                None, sort_order, 1, 1, now, now,
            ))

        if rows:
            await conn.executemany(
                "INSERT INTO categories (id, company_id, name, direction, parent_id, sort_order, is_system, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            await conn.commit()
    finally:
        await conn.close()


async def _ensure_db_ready(company_id: str) -> None:
    await _ensure_schema()
    await _seed_system_categories(company_id)


async def list_categories(company_id: str) -> List[Dict[str, Any]]:
    await _ensure_db_ready(company_id)
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM categories WHERE company_id = ? AND is_active = 1 ORDER BY sort_order ASC, name ASC",
            (company_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def create_category(company_id: str, name: str, direction: str, parent_id: Optional[str] = None, sort_order: int = 0) -> Dict[str, Any]:
    await _ensure_db_ready(company_id)
    conn = await get_conn()
    try:
        now = _now_iso()
        cid = f"cat-{company_id}-{int(datetime.now(timezone.utc).timestamp() * 1000)}"
        await conn.execute(
            "INSERT INTO categories (id, company_id, name, direction, parent_id, sort_order, is_system, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, ?)",
            (cid, company_id, name, direction, parent_id, sort_order, now, now),
        )
        await conn.commit()
        cursor = await conn.execute("SELECT * FROM categories WHERE id = ?", (cid,))
        row = await cursor.fetchone()
        return dict(row) if row else {}
    finally:
        await conn.close()


async def update_category(company_id: str, category_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    await _ensure_db_ready(company_id)
    conn = await get_conn()
    try:
        fields = []
        values = []
        for key in ("name", "direction", "parent_id", "sort_order", "is_active"):
            if key in updates and updates[key] is not None:
                fields.append(f"{key} = ?")
                values.append(updates[key])
        if not fields:
            return None
        fields.append("updated_at = ?")
        values.append(_now_iso())
        values.extend([category_id, company_id])
        await conn.execute(
            f"UPDATE categories SET {', '.join(fields)} WHERE id = ? AND company_id = ?",
            values,
        )
        await conn.commit()
        cursor = await conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def delete_category(company_id: str, category_id: str) -> bool:
    await _ensure_db_ready(company_id)
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "DELETE FROM categories WHERE id = ? AND company_id = ? AND is_system = 0",
            (category_id, company_id),
        )
        await conn.commit()
        return cursor.rowcount > 0
    finally:
        await conn.close()
