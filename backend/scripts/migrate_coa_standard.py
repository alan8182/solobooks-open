# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""存量科目编码一次性迁移：自造/企业会计准则混合码 → 《小企业会计准则》标准码。

背景：科目表编码对齐小企业会计准则（4位总账+7位明细+9位二级明细）。
本脚本把存量 journal_lines、chart_accounts、legacy entries.payload 里的旧编码
按查表法一次性映射到新编码（原值一次映射，绝不级联替换：旧 5001→5401001
与旧 6001→5001 互不干扰；旧 5301→4301，新 5301 营业外收入另建）。

处理范围：
- journal_lines.account_code/account_name/account_id（PG 需临时禁用 posted 不可变触发器）
- chart_accounts：目标科目补建（含 chart_of_accounts.json 全量）、旧码行删除、
  幸存行（如 2221）的名称/is_leaf 刷新
- entries.payload JSON 投影（voucherLines[].accountCode/accountName + 顶层 accountCode）

保持原样（历史即历史）：source_transactions.raw_payload、audit_events、
PeriodCloseRun 快照、journal_entries.metadata。

用法：
  python scripts/migrate_coa_standard.py [--dry-run] [--sqlite [PATH]]
默认连 DATABASE_URL（生产 PG）；--sqlite 走本地 SQLite（默认 backend/data/app.db）。
幂等：跑完后旧行数为 0，可重复执行。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

CHART_JSON = BACKEND_DIR / "data" / "chart_of_accounts.json"
REPORT_DIR = BACKEND_DIR / "data" / "migration_reports"

# 旧码 → (新码, 新科目名)。查表映射：仅按原始码一次替换。
MAPPING: dict[str, tuple[str, str]] = {
    "6001": ("5001", "主营业务收入"),
    "6051": ("5051", "其他业务收入"),
    "6111": ("5111", "投资收益"),
    "6301": ("5301", "营业外收入"),
    "5001": ("5401001", "主营业务成本-人力"),
    "5101": ("5401002", "主营业务成本-生产"),
    "5301": ("4301", "研发支出-费用化支出"),
    "530101": ("430100101", "研发支出-人员人工"),
    "530102": ("430100102", "研发支出-直接投入"),
    "530103": ("430100103", "研发支出-折旧与摊销"),
    "530104": ("430100104", "研发支出-设计费"),
    "530105": ("430100105", "研发支出-试验费"),
    "530106": ("430100106", "研发支出-委外研发费"),
    "530199": ("430100199", "研发支出-其他研发费用"),
    "5601": ("5602006", "管理费用-差旅费"),
    "5602": ("5602004", "管理费用-办公费"),
    "5603": ("5602002", "管理费用-业务招待费"),
    "5604": ("5602013", "管理费用-租赁费"),
    "5605": ("5602015", "管理费用-信息技术服务费"),
    "5606": ("5601011", "销售费用-运输费"),
    "5607": ("5602005", "管理费用-水电费"),
    "5608": ("5603002", "财务费用-手续费"),
    "5609": ("5602014", "管理费用-职工福利费"),
    "5699": ("5602999", "管理费用-其他"),
    "6401": ("5403", "税金及附加"),
    "6601": ("5601016", "销售费用-业务宣传费"),
    "6602": ("5603002", "财务费用-手续费"),
    "6603": ("5603005", "财务费用-利息收入"),  # 5603004 是现金折扣，利息收入不得占用
    "6711": ("5711", "营业外支出"),
    "6801": ("5801", "所得税费用"),
    "4001": ("3001", "实收资本"),
    "4002": ("3002", "资本公积"),
    "3101": ("3104", "利润分配"),
    "222101": ("222100101", "进项税额"),
    "222102": ("222100105", "销项税额"),
}


def _load_chart_definitions() -> dict[str, dict]:
    """从 chart_of_accounts.json 读全量目标科目定义（与 seed_accounts 同源）。"""
    data = json.loads(CHART_JSON.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for group_name, accounts in data.get("accounts", {}).items():
        for code, info in accounts.items():
            code = str(code)
            direction = info.get("direction")
            if direction not in ("debit", "credit"):
                direction = "credit" if code.startswith(("2", "3")) else "debit"
            out[code] = {
                "name": str(info.get("name") or code),
                "category": str(group_name),
                "type": str(info.get("type") or ""),
                "parent_code": info.get("parentCode") or None,
                "level": 1 + (len(code) > 4) + (len(code) > 7),
                "normal_side": direction,
                "is_leaf": bool(info.get("isLeaf", True)),
            }
    return out


def _payload_touches_old(payload_text: str) -> bool:
    return any(f'"{old}"' in payload_text for old in MAPPING)


def _rewrite_payload(payload: dict) -> bool:
    changed = False
    voucher = payload.get("voucherLines") or {}
    for side in ("debit", "credit"):
        for line in voucher.get(side) or []:
            old = str(line.get("accountCode") or "")
            if old in MAPPING:
                new_code, new_name = MAPPING[old]
                line["accountCode"] = new_code
                line["accountName"] = new_name
                changed = True
    old_top = str(payload.get("accountCode") or "")
    if old_top in MAPPING:
        new_code, new_name = MAPPING[old_top]
        payload["accountCode"] = new_code
        payload["accountName"] = new_name
        changed = True
    return changed


async def _companies_with_old_codes(session) -> list[str]:
    from sqlalchemy import text

    placeholders = ", ".join(f":o{i}" for i in range(len(MAPPING)))
    params = {f"o{i}": old for i, old in enumerate(MAPPING)}
    rows = await session.execute(text(
        f"SELECT DISTINCT jl.company_id FROM journal_lines jl "
        f"JOIN journal_entries je ON je.id = jl.journal_entry_id "
        f"WHERE jl.account_code IN ({placeholders}) AND je.rule_version = 'account-mapping-v1'"
    ), params)
    return list(rows.scalars().all())


async def _count_old_lines(session, company_id: str) -> dict[str, int]:
    from sqlalchemy import text

    counts = {}
    for old in MAPPING:
        n = (await session.execute(text(
            "SELECT COUNT(*) FROM journal_lines jl "
            "JOIN journal_entries je ON je.id = jl.journal_entry_id "
            "WHERE jl.company_id=:c AND jl.account_code=:o AND je.rule_version = 'account-mapping-v1'"
        ), {"c": company_id, "o": old})).scalar()
        if n:
            counts[old] = int(n)
    return counts


async def _collect_plan(session) -> dict[str, dict]:
    """按公司收集：待迁移行数、冲突码（旧码同时是新目标码且旧行还在）。"""
    from sqlalchemy import text

    plan: dict[str, dict] = {}
    companies = await _companies_with_old_codes(session)
    for company_id in companies:
        existing = {c for c in (await session.execute(text(
            "SELECT code FROM chart_accounts WHERE company_id=:c"
        ), {"c": company_id})).scalars().all()}
        source_codes = set(MAPPING) & existing
        # 目标码被仍在的旧行占用（如旧 5001/5301/3101）→ 需先迁走旧行再建新行
        target_conflicts = {MAPPING[old][0] for old in source_codes if MAPPING[old][0] in source_codes}
        counts = await _count_old_lines(session, company_id)
        payload_count = 0
        rows = (await session.execute(text(
            "SELECT e.payload FROM entries e "
            "JOIN journal_entries je ON je.id = e.id "
            "WHERE e.company_id=:c AND je.rule_version = 'account-mapping-v1'"
        ), {"c": company_id})).scalars().all()
        for raw in rows:
            if _payload_touches_old(raw or ""):
                payload_count += 1
        plan[company_id] = {
            "line_counts": counts,
            "payload_entries": payload_count,
            "source_rows": sorted(source_codes),
            "target_conflicts": sorted(target_conflicts),
        }
    return plan


def _insert_chart_sql():
    return (
        "INSERT INTO chart_accounts (id, company_id, code, name, parent_code, level, category, type, "
        "normal_side, is_leaf, is_system, is_active, created_at, updated_at) "
        "VALUES (:id, :c, :code, :name, :parent_code, :level, :category, :type, "
        ":normal_side, :is_leaf, TRUE, TRUE, :now, :now)"
    )


async def migrate(dry_run: bool = False, sqlite_path: str | None = None) -> None:
    if sqlite_path:
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{sqlite_path}"

    from sqlalchemy import text

    from db.session import session_scope

    chart = _load_chart_definitions()
    is_pg = not sqlite_path and "sqlite" not in (os.environ.get("DATABASE_URL") or "").lower()

    async with session_scope() as session:
        dialect = session.bind.dialect.name
        is_pg = dialect == "postgresql"
        plan = await _collect_plan(session)

    total_lines = sum(sum(p["line_counts"].values()) for p in plan.values())
    total_payloads = sum(p["payload_entries"] for p in plan.values())
    print(f"{'DRY-RUN' if dry_run else 'RUN'}: companies={len(plan)} lines_to_move={total_lines} payload_entries={total_payloads}")
    for company_id, info in plan.items():
        print(f"  company={company_id} lines={info['line_counts']} payloads={info['payload_entries']} "
              f"conflicts={info['target_conflicts']}")

    if dry_run:
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    report: dict = {"started_at": now_iso, "companies": {}}

    for company_id, info in plan.items():
        conflicts = set(info["target_conflicts"])
        company_report = {"lines_moved": info["line_counts"], "payload_entries": info["payload_entries"],
                          "chart_created": [], "chart_deleted": []}

        async with session_scope() as session:
            # T1：补建无冲突目标行 + 按 JSON 刷新幸存行（2221 等同名保留码）
            for code, defn in chart.items():
                if code in conflicts:
                    continue
                exists = (await session.execute(text(
                    "SELECT 1 FROM chart_accounts WHERE company_id=:c AND code=:code"
                ), {"c": company_id, "code": code})).first()
                if not exists:
                    await session.execute(text(_insert_chart_sql()), {
                        "id": f"acct-{company_id}-{code}", "c": company_id, "code": code,
                        "name": defn["name"], "parent_code": defn["parent_code"], "level": defn["level"],
                        "category": defn["category"], "type": defn["type"],
                        "normal_side": defn["normal_side"], "is_leaf": defn["is_leaf"], "now": now_iso,
                    })
                    company_report["chart_created"].append(code)
                elif code not in MAPPING:
                    await session.execute(text(
                        "UPDATE chart_accounts SET name=:name, category=:category, type=:type, "
                        "normal_side=:side, is_leaf=:leaf, level=:level, parent_code=:parent, updated_at=:now "
                        "WHERE company_id=:c AND code=:code"
                    ), {"name": defn["name"], "category": defn["category"], "type": defn["type"],
                       "side": defn["normal_side"], "leaf": defn["is_leaf"], "level": defn["level"],
                       "parent": defn["parent_code"], "now": now_iso, "c": company_id, "code": code})

        if is_pg:
            async with session_scope() as session:
                await session.execute(text("ALTER TABLE journal_lines DISABLE TRIGGER trg_journal_line_immutable"))
                await session.execute(text("ALTER TABLE journal_entries DISABLE TRIGGER trg_journal_entry_immutable"))
        try:
            async with session_scope() as session:
                # T2：重指非冲突映射的分录行（仅 v1 旧账套，保证幂等）
                for old, (new, new_name) in MAPPING.items():
                    if new in conflicts:
                        continue
                    await session.execute(text(
                        "UPDATE journal_lines SET account_code=:new, account_name=:newname, account_id=:acct "
                        "WHERE company_id=:c AND account_code=:old AND journal_entry_id IN "
                        "(SELECT je.id FROM journal_entries je WHERE je.company_id=:c AND je.rule_version='account-mapping-v1')"
                    ), {"new": new, "newname": new_name, "acct": f"acct-{company_id}-{new}",
                        "c": company_id, "old": old})
            async with session_scope() as session:
                # T3：删除让位旧行（分录已重指且无任何行引用才删）
                for code in sorted(conflicts):
                    await session.execute(text(
                        "DELETE FROM chart_accounts WHERE company_id=:c AND code=:code AND NOT EXISTS "
                        "(SELECT 1 FROM journal_lines jl WHERE jl.company_id=:c AND jl.account_code=:code)"
                    ), {"c": company_id, "code": code})
                    company_report["chart_deleted"].append(code)
            async with session_scope() as session:
                # T4：补建被占用的目标行（5001/5301/3101）
                for code in sorted(conflicts):
                    defn = chart[code]
                    await session.execute(text(_insert_chart_sql()), {
                        "id": f"acct-{company_id}-{code}", "c": company_id, "code": code,
                        "name": defn["name"], "parent_code": defn["parent_code"], "level": defn["level"],
                        "category": defn["category"], "type": defn["type"],
                        "normal_side": defn["normal_side"], "is_leaf": defn["is_leaf"], "now": now_iso,
                    })
                    company_report["chart_created"].append(code)
            async with session_scope() as session:
                # T5：重指冲突映射的分录行（旧 6001→5001、旧 6301→5301）
                for old, (new, new_name) in MAPPING.items():
                    if new not in conflicts:
                        continue
                    await session.execute(text(
                        "UPDATE journal_lines SET account_code=:new, account_name=:newname, account_id=:acct "
                        "WHERE company_id=:c AND account_code=:old AND journal_entry_id IN "
                        "(SELECT je.id FROM journal_entries je WHERE je.company_id=:c AND je.rule_version='account-mapping-v1')"
                    ), {"new": new, "newname": new_name, "acct": f"acct-{company_id}-{new}",
                        "c": company_id, "old": old})
        finally:
            if is_pg:
                async with session_scope() as session:
                    await session.execute(text("ALTER TABLE journal_lines ENABLE TRIGGER trg_journal_line_immutable"))
                    await session.execute(text("ALTER TABLE journal_entries ENABLE TRIGGER trg_journal_entry_immutable"))

        async with session_scope() as session:
            # T8 依赖：屏蔽 journal/审计不可变触发器对本会话的影响
            #（ALTER TABLE DISABLE 经 session_scope 提交后仍可能被并发会话重置，
            #  会话级参数对本会话百分百生效；需表 owner/超户权限）
            await session.execute(text("SET session_replication_role = replica"))
            # T6：清理其余旧码科目行（分录已全部重指且无引用才删）
            for old in MAPPING:
                result = await session.execute(text(
                    "DELETE FROM chart_accounts WHERE company_id=:c AND code=:code AND NOT EXISTS "
                    "(SELECT 1 FROM journal_lines jl WHERE jl.company_id=:c AND jl.account_code=:code)"
                ), {"c": company_id, "code": old})
                if result.rowcount:
                    company_report["chart_deleted"].append(old)
            # T7：重写 legacy entries.payload 投影（仅 v1 旧账套）
            rows = (await session.execute(text(
                "SELECT e.id, e.payload FROM entries e "
                "JOIN journal_entries je ON je.id = e.id "
                "WHERE e.company_id=:c AND je.rule_version = 'account-mapping-v1'"
            ), {"c": company_id})).mappings().all()
            for row in rows:
                raw = row["payload"]
                if not raw or not _payload_touches_old(raw):
                    continue
                payload = json.loads(raw)
                if _rewrite_payload(payload):
                    await session.execute(text(
                        "UPDATE entries SET payload=:p WHERE id=:id"
                    ), {"p": json.dumps(payload, ensure_ascii=False), "id": row["id"]})
            # T8：整账套升版，防重复迁移（5001/5301 等两套语义共用的码）
            await session.execute(text(
                "UPDATE journal_entries SET rule_version='account-mapping-v2' "
                "WHERE company_id=:c AND rule_version != 'account-mapping-v2'"
            ), {"c": company_id})

        report["companies"][company_id] = company_report

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"coa_standard_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"DONE: report -> {report_path}")


if __name__ == "__main__":
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    sqlite_path = None
    if "--sqlite" in args:
        idx = args.index("--sqlite")
        rest = args[idx + 1:] if idx + 1 < len(args) else []
        sqlite_path = rest[0] if rest and not rest[0].startswith("--") else str(BACKEND_DIR / "data" / "app.db")
    asyncio.run(migrate(dry_run=dry_run, sqlite_path=sqlite_path))
