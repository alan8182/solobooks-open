# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""把 data/chart_of_accounts.json 同步到所有存量公司的科目表。

- 补建缺失科目行（id=acct-<cid>-<code>，parent/level/category/type 按 JSON 与编码长度推导）
- 刷新同名冲突的改名（按 JSON name 覆盖已存在行的 name）
幂等可重跑。需 owner 连接（replica 会话角色屏蔽触发器）：
  DATABASE_URL=postgresql+asyncpg://solobooks_owner:***@host/db python scripts/sync_chart_from_json.py [--dry-run]
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

CHART_JSON = Path(__file__).resolve().parent.parent / "data" / "chart_of_accounts.json"


async def sync(dry_run: bool) -> None:
    from sqlalchemy import text

    from db.session import session_scope

    doc = json.loads(CHART_JSON.read_text(encoding="utf-8"))
    groups = doc["accounts"]
    now = datetime.now(timezone.utc).isoformat()

    async with session_scope() as session:
        await session.execute(text("SET session_replication_role = replica"))
        try:
            companies = [r[0] for r in (await session.execute(
                text("SELECT DISTINCT company_id FROM chart_accounts")
            )).fetchall()]
            for cid in companies:
                added = renamed = 0
                for group, accounts in groups.items():
                    for code, info in accounts.items():
                        name = info["name"]
                        is_sub = len(code) > 4
                        parent = info.get("parentCode") or (code[:4] if is_sub else None)
                        level = 1 + int(is_sub) + int(len(code) > 7)
                        row_id = f"acct-{cid}-{code}"
                        existing = (await session.execute(text(
                            "SELECT name FROM chart_accounts WHERE company_id = :c AND code = :code"
                        ), {"c": cid, "code": code})).scalar()
                        if existing is None:
                            await session.execute(text(
                                "INSERT INTO chart_accounts (id, company_id, code, name, parent_code, level, "
                                "category, type, normal_side, is_leaf, is_system, is_active, created_at, updated_at) "
                                "VALUES (:id, :c, :code, :name, :parent, :level, :cat, :type, :side, "
                                ":leaf, true, true, :now, :now) ON CONFLICT DO NOTHING"
                            ), {
                                "id": row_id, "c": cid, "code": code, "name": name,
                                "parent": parent, "level": level, "cat": group,
                                "type": info.get("type"), "side": info.get("direction"),
                                "leaf": bool(info.get("isLeaf", True)) or not is_sub, "now": now,
                            })
                            added += 1
                        elif existing != name:
                            await session.execute(text(
                                "UPDATE chart_accounts SET name = :name, updated_at = :now "
                                "WHERE company_id = :c AND code = :code"
                            ), {"name": name, "now": now, "c": cid, "code": code})
                            renamed += 1
                print(f"[sync] {cid}: +{added} renamed={renamed}" + (" (dry-run)" if dry_run else ""))
        finally:
            await session.execute(text("SET session_replication_role = DEFAULT"))
        print("DONE")


if __name__ == "__main__":
    asyncio.run(sync(dry_run="--dry-run" in sys.argv[1:]))
