# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""微信待结算清算科目升位迁移：1012/ln → 1012001（其他货币资金二级明细）。

背景：1012 的标准科目名是「其他货币资金」；微信支付待结算是其二级明细。
本脚本做三件事（幂等，可重复执行）：
- journal_lines：account_code 1012/ln → 1012001（account_name 统一为 微信支付待结算）
- entries.payload 投影：voucherLines 与顶层 accountCode 同步改写
- chart_accounts：1012 改名「其他货币资金」且 is_leaf=false，补建 1012001 二级行

历史即历史，保持原样：source_transactions.raw_payload、audit_events、PeriodCloseRun 快照。

用法（需 owner 连接，清触发器拦截用 replica 会话角色）：
  DATABASE_URL=postgresql+asyncpg://solobooks_owner:***@host/db python scripts/migrate_wechat_clearing.py [--dry-run]
生产：docker cp 本文件到 solobooks-backend:/app/scripts/ 后，
  docker exec solobooks-backend sh -c 'export DATABASE_URL=$(printenv MIGRATION_DATABASE_URL | sed "s/+psycopg/+asyncpg/"); python /app/scripts/migrate_wechat_clearing.py'
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

OLD_CODES = ("1012", "ln")
NEW_CODE = "1012001"
NEW_NAME = "微信支付待结算"
TOP_NAME = "其他货币资金"


async def migrate(dry_run: bool) -> None:
    from sqlalchemy import text

    from db.session import session_scope

    # session_scope + text()：get_conn 的 compat 层会吞执行错误导致事务中止难排查
    async with session_scope() as session:
        companies = [r[0] for r in (await session.execute(
            text("SELECT DISTINCT company_id FROM chart_accounts")
        )).fetchall()]
        await session.execute(text("SET session_replication_role = replica"))
        try:
            for cid in companies:
                n_lines = (await session.execute(text(
                    "SELECT count(*) FROM journal_lines WHERE company_id = :cid "
                    "AND account_code IN ('1012', 'ln')"), {"cid": cid})).scalar_one()
                n_payload = (await session.execute(text(
                    "SELECT count(*) FROM entries WHERE company_id = :cid "
                    "AND (payload LIKE '%\"1012\"%' OR payload LIKE '%\"ln\"%')"), {"cid": cid})).scalar_one()
                if not dry_run:
                    await session.execute(text(
                        "UPDATE journal_lines SET account_code = :new, account_name = :name "
                        "WHERE company_id = :cid AND account_code IN ('1012', 'ln')"),
                        {"new": NEW_CODE, "name": NEW_NAME, "cid": cid},
                    )
                    rows = (await session.execute(text(
                        "SELECT id, payload FROM entries WHERE company_id = :cid "
                        "AND (payload LIKE '%\"1012\"%' OR payload LIKE '%\"ln\"%')"), {"cid": cid})).mappings().all()
                    for r in rows:
                        p = json.loads(r["payload"])
                        for side in ("debit", "credit"):
                            for line in (p.get("voucherLines", {}) or {}).get(side, []):
                                if str(line.get("accountCode") or "") in OLD_CODES:
                                    line["accountCode"] = NEW_CODE
                                    line["accountName"] = NEW_NAME
                        if str(p.get("accountCode") or "") in OLD_CODES:
                            p["accountCode"] = NEW_CODE
                        await session.execute(text(
                            "UPDATE entries SET payload = cast(:p AS text) WHERE id = :id"),
                            {"p": json.dumps(p, ensure_ascii=False), "id": r["id"]},
                        )
                # 科目表：1012 改名升父级 + 补建二级行（dry-run 保持只读）
                if not dry_run:
                    await session.execute(text(
                    "UPDATE chart_accounts SET name = :top, is_leaf = false, updated_at = NOW() "
                        "WHERE company_id = :cid AND code = '1012'"),
                        {"top": TOP_NAME, "cid": cid},
                    )
                    await session.execute(text(
                        "INSERT INTO chart_accounts (id, company_id, code, name, parent_code, level, category, "
                        "type, normal_side, is_leaf, is_system, is_active, created_at, updated_at) "
                        "SELECT 'acct-' || :cid || '-1012001', :cid, '1012001', '微信支付待结算', '1012', 2, "
                        "c.category, c.type, c.normal_side, true, true, true, NOW(), NOW() "
                        "FROM chart_accounts c WHERE c.company_id = :cid AND c.code = '1012' "
                        "ON CONFLICT DO NOTHING"),
                        {"cid": cid},
                    )
                print(f"[migrate] {cid}: lines={n_lines} payloads={n_payload}" + (" (dry-run)" if dry_run else ""))
        finally:
            await session.execute(text("SET session_replication_role = DEFAULT"))
        print("DONE")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv[1:]
    asyncio.run(migrate(dry_run=dry))
