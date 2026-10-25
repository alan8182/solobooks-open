# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Chart of accounts tree service (async SQLite)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.database import get_conn



def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_seed() -> dict:
    path = os.path.join(os.path.dirname(__file__), "..", "data", "chart_of_accounts.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not data.get("_attribution"):
        raise ValueError("ERR_JZXK_COA_SCHEMA_INVALID: 缺失核心科目规范签名")
    return data


def _build_seed_rows(company_id: str, now: str) -> List[tuple]:
    """Build seed rows from chart_of_accounts.json into a 3-level tree."""
    data = _load_seed()
    groups = data.get("accounts", {})
    rows = []
    seen_codes = set()

    for group_name, accounts in groups.items():
        # Level 0: group node
        gid = f"grp-{company_id}-{hash(group_name) % 100000}"
        if group_name not in seen_codes:
            seen_codes.add(group_name)
            rows.append((
                gid, company_id, group_name, group_name, None,
                0, None, None, False, True, True, now, now,
            ))

        for code, info in accounts.items():
            name = info["name"]
            acct_type = info.get("type", "")

            # Determine if this is a sub-account (6-digit or longer)
            is_sub = len(code) > 4
            parent_code = info.get("parentCode") or (group_name if not is_sub else code[:4])
            level = 1 + (len(code) > 4) + (len(code) > 7)

            if code not in seen_codes:
                seen_codes.add(code)
                cid = f"acct-{company_id}-{code}"
                rows.append((
                    cid, company_id, code, name, parent_code,
                    level, group_name, acct_type, True,
                    True, True, now, now,
                ))

    return rows


async def _ensure_schema() -> None:
    conn = await get_conn()
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS chart_accounts ("
            "  id TEXT PRIMARY KEY,"
            "  company_id TEXT NOT NULL,"
            "  code TEXT NOT NULL,"
            "  name TEXT NOT NULL,"
            "  parent_code TEXT,"
            "  level INTEGER NOT NULL DEFAULT 1,"
            "  category TEXT,"
            "  type TEXT,"
            "  is_leaf INTEGER NOT NULL DEFAULT 1,"
            "  is_system INTEGER NOT NULL DEFAULT 1,"
            "  is_active INTEGER NOT NULL DEFAULT 1,"
            "  created_at TEXT NOT NULL,"
            "  updated_at TEXT NOT NULL"
            ")"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chart_accounts_company ON chart_accounts(company_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chart_accounts_parent ON chart_accounts(parent_code)"
        )
        await conn.commit()
    finally:
        await conn.close()


async def _seed_system_accounts(company_id: str) -> None:
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT COUNT(*) AS cnt FROM chart_accounts WHERE company_id = ?", (company_id,)
        )
        row = await cursor.fetchone()
        if row["cnt"] > 0:
            # 补齐权益核心科目（针对已有历史数据的企业；编码已对齐小企业会计准则）
            now = _now_iso()
            for code, name in [("3001", "实收资本"), ("3002", "资本公积")]:
                cid = f"acct-{company_id}-{code}"
                await conn.execute(
                    "INSERT OR IGNORE INTO chart_accounts "
                    "(id, company_id, code, name, parent_code, level, category, type, is_leaf, is_system, is_active, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (cid, company_id, code, name, "权益类", 1, "权益类", "所有者权益", True, True, True, now, now)
                )
            await conn.commit()
            return

        now = _now_iso()
        seed_rows = _build_seed_rows(company_id, now)

        if seed_rows:
            await conn.executemany(
                "INSERT INTO chart_accounts "
                "(id, company_id, code, name, parent_code, level, category, type, is_leaf, is_system, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                seed_rows,
            )
            await conn.commit()
    finally:
        await conn.close()


async def _ensure_db_ready(company_id: str) -> None:
    await _ensure_schema()
    await _seed_system_accounts(company_id)


async def list_accounts(company_id: str) -> List[Dict[str, Any]]:
    await _ensure_db_ready(company_id)
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM chart_accounts WHERE company_id = ? AND is_active = ? ORDER BY level ASC, code ASC",
            (company_id, True),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def get_account_tree(company_id: str) -> List[Dict[str, Any]]:
    """Return tree structure: level-0 groups → level-1 accounts → level-2 sub-accounts."""
    accounts = await list_accounts(company_id)

    # Build lookup: code → node
    node_map: Dict[str, dict] = {}
    for acct in accounts:
        node_map[acct["code"]] = {**acct, "children": []}

    roots = []
    for code, node in node_map.items():
        parent = node.get("parent_code")
        if parent and parent in node_map:
            node_map[parent]["children"].append(node)
        else:
            roots.append(node)

    # Sort children by code
    def sort_tree(nodes):
        nodes.sort(key=lambda n: n["code"])
        for n in nodes:
            if n["children"]:
                sort_tree(n["children"])

    sort_tree(roots)
    return roots


async def create_account(company_id: str, code: str, name: str, parent_code: Optional[str] = None,
                         level: int = 1, category: Optional[str] = None, type_: Optional[str] = None) -> Optional[Dict[str, Any]]:
    await _ensure_db_ready(company_id)
    conn = await get_conn()
    try:
        # Check duplicate
        cursor = await conn.execute(
            "SELECT id FROM chart_accounts WHERE company_id = ? AND code = ?", (company_id, code)
        )
        if await cursor.fetchone():
            return None

        now = _now_iso()
        cid = f"cust-{company_id}-{code}"
        await conn.execute(
            "INSERT INTO chart_accounts (id, company_id, code, name, parent_code, level, category, type, is_leaf, is_system, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, company_id, code, name, parent_code, level, category, type_, True, False, True, now, now),
        )
        await conn.commit()
        cursor = await conn.execute("SELECT * FROM chart_accounts WHERE id = ?", (cid,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def update_account(company_id: str, code: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    await _ensure_db_ready(company_id)
    conn = await get_conn()
    try:
        fields = []
        values = []
        for key in ("name", "parent_code", "level", "category", "type", "is_active", "quantity_enabled", "unit"):
            if key in updates and updates[key] is not None:
                fields.append(f"{key} = ?")
                values.append(updates[key])
        if not fields:
            return None
        fields.append("updated_at = ?")
        values.append(_now_iso())
        values.extend([company_id, code])
        await conn.execute(
            f"UPDATE chart_accounts SET {', '.join(fields)} WHERE company_id = ? AND code = ?",
            values,
        )
        await conn.commit()
        cursor = await conn.execute(
            "SELECT * FROM chart_accounts WHERE company_id = ? AND code = ?", (company_id, code)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def delete_account(company_id: str, code: str) -> bool:
    await _ensure_db_ready(company_id)
    conn = await get_conn()
    try:
        # Prevent deleting system accounts or groups with children
        cursor = await conn.execute(
            "SELECT is_system FROM chart_accounts WHERE company_id = ? AND code = ?",
            (company_id, code),
        )
        row = await cursor.fetchone()
        if not row:
            return False
        if row["is_system"]:
            return False

        # Check for children
        cursor = await conn.execute(
            "SELECT COUNT(*) AS cnt FROM chart_accounts WHERE company_id = ? AND parent_code = ?",
            (company_id, code),
        )
        child_row = await cursor.fetchone()
        if child_row and child_row["cnt"] > 0:
            return False

        cursor = await conn.execute(
            "DELETE FROM chart_accounts WHERE company_id = ? AND code = ? AND is_system = ?",
            (company_id, code, False),
        )
        await conn.commit()
        return cursor.rowcount > 0
    finally:
        await conn.close()


async def ensure_sub_account(company_id: str, parent_code: str, name: str) -> Optional[Dict[str, Any]]:
    """确保某一级科目下存在指定二级科目；不存在则自动生成末位 code 创建。

    用于 LLM 依据流水/发票内容建议新二级科目后一键落地：父科目必须是一级
    （4 位）且存在，同名二级已存在则直接复用，否则沿用父科目下现有子科目的
    位数自动取最大数值 +1，避免与手动建科时手填 code 冲突。
    """
    parent_code = str(parent_code or "").strip()
    name = str(name or "").strip()
    if len(parent_code) != 4 or not parent_code.isdigit() or not name:
        return None

    await _ensure_db_ready(company_id)
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM chart_accounts WHERE company_id = ? AND code = ? AND is_active = 1",
            (company_id, parent_code),
        )
        parent = await cursor.fetchone()
        if not parent:
            return None

        cursor = await conn.execute(
            "SELECT * FROM chart_accounts WHERE company_id = ? AND parent_code = ? AND name = ?",
            (company_id, parent_code, name),
        )
        existing = await cursor.fetchone()
        if existing:
            return dict(existing)

        cursor = await conn.execute(
            "SELECT code FROM chart_accounts WHERE company_id = ? AND parent_code = ?",
            (company_id, parent_code),
        )
        rows = await cursor.fetchall()
        children = [str(r["code"]) for r in rows]
        # 明细位数沿用父科目下现有子科目（一级 4 位 + N 位明细），默认 2 位。
        # 在明细序号空间内找最小空位，跳过「其他」哨兵（如 5602999 的 999），
        # 避免 max+1 跨入相邻一级科目的 code 区间（5602 下 999 会算出 5603000）。
        detail_width = max(
            (len(c) - len(parent_code) for c in children if c.startswith(parent_code)),
            default=2,
        )
        used = {
            int(c[len(parent_code):])
            for c in children
            if c.startswith(parent_code) and c[len(parent_code):].isdigit()
        }
        seq = 1
        while seq in used:
            seq += 1
        code = f"{parent_code}{seq:0{detail_width}d}"

        now = _now_iso()
        cid = f"cust-{company_id}-{code}"
        await conn.execute(
            "INSERT INTO chart_accounts (id, company_id, code, name, parent_code, level, category, type, is_leaf, is_system, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, company_id, code, name, parent_code, 2, parent["category"], parent["type"], True, False, True, now, now),
        )
        await conn.commit()
        cursor = await conn.execute("SELECT * FROM chart_accounts WHERE id = ?", (cid,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()
