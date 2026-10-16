"""为测试账号重做一本干净的小微企业演示账本。

用法（backend venv 下）:
  python scripts/seed_demo_book.py seed                 # 新建演示账本并灌入样本数据
  python scripts/seed_demo_book.py verify --company-id c_xxx
  python scripts/seed_demo_book.py wipe --company-id c_xxx   # 备份后清空一本账（含公司主体）

生产运行: docker cp 本文件到 solobooks-backend:/app/scripts/ 后 docker exec 执行。
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
MONTHS = [4, 5, 6, 7, 8, 9]

# 每月营业收入：(日, 金额, 客户, 开票状态)——reconciled 表示银行已收、发票未确认 → 待开票演示
REVENUE = {
    4: [(8, 12000, "杭州云启贸易有限公司", "issued"), (15, 6800, "杭州白鹭文化传媒有限公司", "issued"), (24, 4500, "上海临溪信息技术有限公司", "issued")],
    5: [(9, 15000, "杭州云启贸易有限公司", "issued"), (16, 5200, "上海临溪信息技术有限公司", "reconciled"), (26, 3800, "杭州白鹭文化传媒有限公司", "issued")],
    6: [(7, 9600, "杭州白鹭文化传媒有限公司", "issued"), (17, 6400, "杭州云启贸易有限公司", "issued"), (25, 3200, "上海临溪信息技术有限公司", "issued")],
    7: [(8, 13500, "杭州云启贸易有限公司", "issued"), (15, 4800, "上海临溪信息技术有限公司", "issued"), (24, 2600, "杭州白鹭文化传媒有限公司", "reconciled")],
    8: [(6, 11200, "杭州云启贸易有限公司", "issued"), (14, 5600, "杭州白鹭文化传媒有限公司", "issued"), (22, 3000, "个人客户-小程序定制", "no_invoice")],
    9: [(4, 14800, "杭州云启贸易有限公司", "issued"), (12, 5400, "上海临溪信息技术有限公司", "reconciled"), (21, 3300, "杭州白鹭文化传媒有限公司", "issued")],
}
# 月度固定支出：(日, 金额, 类目, 开票状态, 摘要模板)
CLOUD = {4: 360, 5: 360, 6: 412.5, 7: 431.25, 8: 431.25}
TRAFFIC = {5: 128, 6: 86, 7: 214, 8: 96}


def build_book():
    entries: list[dict] = []
    statements: list[dict] = []
    pairs: list[tuple[str, str]] = []
    seq = 0

    def add(day: int, month: int, title: str, amount: float, direction: str, category: str,
            *, billing: str = "", source: str = "manual", payer: str = "company_account",
            invoice_no: str = "", match: bool = True, desc: str = "",
            voucher_lines: dict | None = None) -> str:
        nonlocal seq
        seq += 1
        occur = f"2026-{month:02d}-{day:02d}"
        eid = f"demo-{month:02d}{day:02d}-{seq:03d}"
        entry = {
            "id": eid, "title": title, "amount": f"{amount:.2f}", "direction": direction,
            "category": category, "payer": payer, "occurDate": occur, "source": source,
        }
        if billing:
            entry["billingStatus"] = billing
        if match:
            entry["paymentDate"] = occur
        if invoice_no:
            entry["invoiceNumber"] = invoice_no
        if voucher_lines:
            # 显式凭证行：绕过科目映射的默认对手科目（默认贷 1002 会误动银行存款）
            entry["voucherLines"] = voucher_lines
        entries.append(entry)
        if match:
            incoming = direction in ("income", "owner_transfer")
            desc = desc or f"招行对公 | {title}"
            statements.append({
                "id": f"bs-demo-{seq:03d}", "transaction_date": occur,
                "description": desc,
                "amount": round(amount if incoming else -amount, 2),
            })
            pairs.append((occur, desc, eid))
        return eid

    # ── 开业：股东投入注册资本 ──
    add(1, 4, "股东投入注册资本", 200000, "owner_transfer", "股东投入", desc="招行对公 | 股东投资款入账")

    for m in MONTHS:
        # 收入
        for day, amount, client, billing in REVENUE[m]:
            add(day, m, f"技术服务费-{client}", amount, "income", "主营业务收入", billing=billing,
                desc=f"招行对公 | 转入-项目结算({client})")
        # 房租（房东个人收款，无票）
        add(5, m, "办公室租金", 2000, "expense", "房租物业", billing="no_invoice",
            desc="招行对公 | 转出-办公室租金")
        # 银行手续费
        add(20, m, "银行账户手续费", 15 + m, "expense", "银行及金融手续费", billing="no_invoice",
            desc="招行对公 | 扣除-账户管理费")
        # 办公费
        add(12, m, "办公用品采购", 80 + m * 12, "expense", "办公费", billing="has_invoice",
            desc="招行对公 | 转出-办公用品")
        # 云服务费（4-8 月已付款；9 月仅收到发票 → 发票待付款演示）
        if m in CLOUD:
            add(18, m, "腾讯云服务器费用", CLOUD[m], "expense", "信息技术服务费", billing="has_invoice",
                desc="招行对公 | 转出-云服务费")
        # 交通费
        if m in TRAFFIC:
            add(22 if m != 8 else 20, m, "市内交通费报销", TRAFFIC[m], "expense", "交通",
                payer="employee_personal:林晓", billing="has_invoice", match=False,
                desc="员工垫付-市内交通")
        # 工资（当月发放，人力成本直入，工资表即凭证）
        add(28, m, f"{m} 月员工工资发放", 18000, "expense", "人力成本", billing="no_invoice",
            desc="招行对公 | 代发工资")

    # 固定资产：4/10 购办公设备
    add(10, 4, "购办公电脑及显示器", 16800, "expense", "固定资产", billing="has_invoice",
        desc="招行对公 | 转出-办公设备采购")

    # 9 月腾讯云发票已收到、尚未付款 → 发票待付款演示
    add(18, 9, "发票-腾讯云计算（北京）有限责任公司", 431.25, "expense", "信息技术服务费",
        billing="has_invoice", source="invoice_import",
        invoice_no="26517000000318574962", match=False,
        voucher_lines={
            "debit": [{"accountCode": "5602015", "accountName": "管理费用-信息技术服务费", "amount": 431.25}],
            "credit": [{"accountCode": "2241", "accountName": "其他应付款", "amount": 431.25}],
        })

    # 9 月末两笔已到账未记账流水 → 流水待处理演示
    statements.extend([
        {"id": "bs-demo-u001", "transaction_date": "2026-09-28", "description": "招行对公 | 转入-项目尾款(杭州白鹭文化传媒有限公司)", "amount": 6200},
        {"id": "bs-demo-u002", "transaction_date": "2026-09-29", "description": "招行对公 | 转入-技术服务预付款(个人客户)", "amount": 2400},
    ])

    # 余额按时间顺序粗算（仅展示用）
    statements.sort(key=lambda s: (s["transaction_date"], s["id"]))
    balance = 0.0
    for s in statements:
        balance = round(balance + s["amount"], 2)
        s["balance"] = balance
    return entries, statements, pairs


async def seed() -> None:
    from services.account_service import create_company
    from services.entry_service import save_entry
    from services.bank_service import import_statements_json
    from services.database import get_conn
    from services.todo_service import list_todos

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

    company = await create_company(USER_ID, COMPANY_NAME, {
        "taxpayerType": "small_scale",
        "industry": "软件和信息技术服务业",
    })
    cid = company["company_id"]
    print(f"[seed] 账本已创建: {COMPANY_NAME} company_id={cid}")

    entries, statements, pairs = build_book()
    for e in entries:
        await save_entry(e, user_id=USER_ID, company_id=cid, confirm=True)
    print(f"[seed] 记账 {len(entries)} 笔（已过账）")

    await import_statements_json(cid, statements)
    print(f"[seed] 银行流水 {len(statements)} 条")

    conn = await get_conn()
    try:
        for occur, desc, eid in pairs:
            # import_statements_json 自行生成 sid，按「日期+摘要」反查真实 id
            cur = await conn.execute(
                "SELECT id FROM bank_statements WHERE company_id = ? AND transaction_date = ? AND description = ? LIMIT 1",
                (cid, occur, desc),
            )
            row = await cur.fetchone()
            if not row:
                print(f"[seed][warn] 未找到流水: {desc}")
                continue
            sid = row[0]
            await conn.execute(
                "INSERT INTO bank_statement_matches (statement_id, company_id, entry_id, previous_billing_status, previous_payment_date, created_at) "
                "VALUES (?, ?, ?, NULL, NULL, NOW())",
                (sid, cid, eid),
            )
            await conn.execute(
                "UPDATE bank_statements SET matched_entry_id = ? WHERE id = ? AND company_id = ?",
                (eid, sid, cid),
            )
        await conn.commit()
    finally:
        await conn.close()
    print(f"[seed] 流水配对 {len(pairs)} 条")

    await verify(cid)


async def verify(company_id: str) -> None:
    from services.database import get_conn
    from services.todo_service import list_todos

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
    todos = await list_todos(USER_ID, company_id)
    by_cat: dict[str, int] = {}
    for t in todos:
        by_cat[t.get("category") or "other"] = by_cat.get(t.get("category") or "other", 0) + 1

    print(f"[verify] company={company_id}")
    print(f"[verify] 借方合计 {debit:,.2f} / 贷方合计 {credit:,.2f} / 平衡: {abs(debit - credit) < 0.005}")
    print(f"[verify] entries={n_entries} vouchers={n_vouchers} statements={n_stmt} unmatched={n_unmatched}")
    print(f"[verify] 待办 {len(todos)} 条，按分类: {json.dumps(by_cat, ensure_ascii=False)}")


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
