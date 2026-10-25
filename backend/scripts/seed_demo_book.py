"""为测试账号重做一本干净的小微企业演示账本。

用法（backend venv 下）:
  python scripts/seed_demo_book.py seed                 # 新建演示账本并灌入样本数据
  python scripts/seed_demo_book.py verify --company-id c_xxx
  python scripts/seed_demo_book.py wipe --company-id c_xxx   # 备份后清空一本账（含公司主体）

生产运行: docker cp 本文件到 solobooks-backend:/app/scripts/ 后 docker exec 执行。

样本数据统一由 ``services.demo_book_service.build_demo_book`` 生成（按「今天」动态
推算月份），不再在脚本里硬编年份——硬编的 2026-04~09 到了次年必然撞
``assert_book_started``（记账日期早于启用日期）。流水配对也走服务里的
``_match_statements``，和真实认领路径一样把凭证标成「已对账」。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

USER_ID = "u_17200624f777"
COMPANY_NAME = "杭州南山科技有限公司"
MONTHS = 6


async def seed() -> None:
    from db.session import session_scope
    from services.account_service import create_company
    from services.bank_service import import_statements_json
    from services.database import get_conn
    from services.demo_book_service import (
        _match_statements,
        _recompute_balances,
        build_demo_book,
    )
    from services.entry_service import save_entry

    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT company_id FROM auth_companies WHERE payload::jsonb->>'name' = ? AND owner_user_id = ?",
            (COMPANY_NAME, USER_ID),
        )
        row = await cursor.fetchone()
        if row:
            raise SystemExit(f"同名账本已存在: {row[0]}（如需重跑请先 wipe）")
    finally:
        await conn.close()

    entries, statements, pairs, start_date = build_demo_book(months=MONTHS)

    company = await create_company(USER_ID, COMPANY_NAME, {
        "taxpayerType": "small_scale",
        "industry": "软件和信息技术服务业",
        "startDate": start_date.isoformat(),
    })
    cid = company["company_id"]
    print(f"[seed] 账本已创建: {COMPANY_NAME} company_id={cid}")

    # JournalEntry 主键全局唯一，演示 id 只按日期递增 → 加账套前缀隔离
    id_map = {str(entry["id"]): f"{cid}-{entry['id']}" for entry in entries}
    for entry in entries:
        entry["id"] = id_map[str(entry["id"])]
    pairs = [(occurred, summary, id_map[entry_id]) for occurred, summary, entry_id in pairs]

    for entry in entries:
        await save_entry(
            entry,
            user_id=USER_ID,
            company_id=cid,
            confirm=True,
            trusted_voucher=bool(entry.get("voucherLines")),
        )
    print(f"[seed] 记账 {len(entries)} 笔（已过账）")

    result = await import_statements_json(cid, statements)
    written = int(result.get("imported") or 0) + int(result.get("skipped") or 0)
    if written != len(statements):
        raise SystemExit(
            f"[seed] 流水导入失败: {written}/{len(statements)}"
            + (f"（{result.get('firstError')}）" if result.get("firstError") else "")
        )
    print(f"[seed] 银行流水 {written} 条")

    async with session_scope() as session:
        matched = await _match_statements(session, cid, pairs)
    await _recompute_balances(cid)
    print(f"[seed] 流水配对 {matched} 条")

    await verify(cid)


async def verify(company_id: str) -> None:
    from services.database import get_conn
    from services.demo_book_service import _bank_effect, _covered_entry_ids
    from services.entry_service import list_entries
    from services.todo_service import list_todos
    from db.session import session_scope

    conn = await get_conn()
    try:
        debit = await scalar(conn, "SELECT COALESCE(SUM(amount), 0) FROM journal_lines WHERE company_id = ? AND direction = 'debit'", company_id)
        credit = await scalar(conn, "SELECT COALESCE(SUM(amount), 0) FROM journal_lines WHERE company_id = ? AND direction = 'credit'", company_id)
        n_entries = await scalar(conn, "SELECT count(*) FROM entries WHERE company_id = ?", company_id)
        n_vouchers = await scalar(conn, "SELECT count(*) FROM journal_entries WHERE company_id = ?", company_id)
        n_stmt = await scalar(conn, "SELECT count(*) FROM bank_statements WHERE company_id = ?", company_id)
        n_unmatched = await scalar(
            conn, "SELECT count(*) FROM bank_statements WHERE company_id = ? AND (matched_entry_id IS NULL OR matched_entry_id = '')",
            company_id,
        )
    finally:
        await conn.close()

    async with session_scope() as session:
        covered = await _covered_entry_ids(session, company_id)
    entries = await list_entries(USER_ID, company_id)
    uncovered = [
        str(entry.get("id"))
        for entry in entries
        if str(entry.get("id")) not in covered and _bank_effect(entry) != 0
    ]

    todos = await list_todos(USER_ID, company_id)
    by_cat: dict[str, int] = {}
    for t in todos:
        by_cat[t.get("category") or "other"] = by_cat.get(t.get("category") or "other", 0) + 1

    print(f"[verify] company={company_id}")
    print(f"[verify] 借方合计 {debit:,.2f} / 贷方合计 {credit:,.2f} / 平衡: {abs(debit - credit) < 0.005}")
    print(f"[verify] entries={n_entries} vouchers={n_vouchers} statements={n_stmt} unmatched={n_unmatched}")
    print(f"[verify] 公户收支缺流水 {len(uncovered)} 笔")
    print(f"[verify] 待办 {len(todos)} 条，按分类: {json.dumps(by_cat, ensure_ascii=False)}")

    if n_stmt == 0:
        raise SystemExit("[verify] 失败：一条流水都没有")
    if uncovered:
        raise SystemExit(f"[verify] 失败：{len(uncovered)} 笔公户收支没有对应流水")


async def scalar(conn, sql: str, param: str) -> int:
    cur = await conn.execute(sql, (param,))
    row = await cur.fetchone()
    return int(row[0] or 0)


# ── 清库（删除一本账的全部数据）───────────────────────────────────

WIPE_SAFE_LIST = {
    "c_41e4385e891e": "芥子星辰（杭州）主测试账本",
    "c_250f4c4ed371": "测试数据存档账本",
}


async def wipe(company_id: str, backup_dir: str, force: bool = False) -> None:
    from services.database import get_conn

    if not force and company_id not in WIPE_SAFE_LIST:
        raise SystemExit(f"拒绝清空 {company_id}：不在已知测试账本清单内（--force 覆盖）")

    conn = await get_conn()
    try:
        cur = await conn.execute(
            "SELECT DISTINCT table_name FROM information_schema.columns "
            "WHERE column_name = 'company_id' AND table_schema = 'public'"
        )
        table_names = [r[0] for r in await cur.fetchall()]
        backup: dict[str, list] = {}
        for t in table_names:
            cur = await conn.execute(f'SELECT * FROM "{t}" WHERE company_id = ?', (company_id,))
            rows = await cur.fetchall()
            if rows:
                backup[t] = [dict(r) for r in rows]
        Path(backup_dir).mkdir(parents=True, exist_ok=True)
        out = Path(backup_dir) / f"wipe_{company_id}_{date.today().isoformat()}.json"
        out.write_text(json.dumps(backup, ensure_ascii=False, default=str, indent=1), encoding="utf-8")
        print(f"[wipe] 已备份 {sum(len(v) for v in backup.values())} 行 → {out}")

        # 清库用 replica 会话角色：屏蔽 journal 不可变约束、审计不可变拦截、
        # 级联审计触发器（备份已保全全部行；需表 owner/超户权限）
        await conn.execute("SET session_replication_role = replica")
        try:
            for t in table_names:
                cur = await conn.execute(f'DELETE FROM "{t}" WHERE company_id = ?', (company_id,))
                if cur.rowcount:
                    print(f"[wipe] {t}: -{cur.rowcount}")
        finally:
            await conn.execute("SET session_replication_role = DEFAULT")
        # 公司主体三件套（company_id 为外键列的已含在扫描内）
        for t in ("auth_companies", "company_members", "company_profiles"):
            cur = await conn.execute(f'DELETE FROM "{t}" WHERE company_id = :c', {"c": company_id})
            if cur.rowcount:
                print(f"[wipe] {t}: -{cur.rowcount}")
        await conn.commit()
    finally:
        await conn.close()
    print(f"[wipe] {company_id} 清空完成")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["seed", "verify", "wipe"])
    parser.add_argument("--company-id", default="")
    parser.add_argument("--backup-dir", default="./data/backups")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.command == "seed":
        await seed()
    elif args.command == "verify":
        if not args.company_id:
            raise SystemExit("verify 需要 --company-id")
        await verify(args.company_id)
    else:
        if not args.company_id:
            raise SystemExit("wipe 需要 --company-id")
        await wipe(args.company_id, args.backup_dir, force=args.force)


if __name__ == "__main__":
    asyncio.run(main())
