"""存量手续费科目迁移：5608 管理费用-银行及金融手续费 → 6602 财务费用-手续费。

《小企业会计准则》口径：支付手续费归财务费用。与既有的 6603
财务费用-利息收入对齐，利息与手续费同族展示。

处理对象：仅 category='银行及金融手续费' 条目名下的 5608 分录行
（其他分类不碰），同步 chart_accounts、journal_lines、legacy 投影。

用法：python scripts/migrate_fee_account.py [--dry-run]
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from db.session import session_scope  # noqa: E402

OLD_CODE, OLD_NAME = "5608", "管理费用-银行及金融手续费"
NEW_CODE, NEW_NAME = "6602", "财务费用-手续费"


async def _pass_chart_and_collect() -> list:
    """事务一：确保 6602 科目存在，并返回待迁移的 (company_id, [(line_id, entry_id), ...])。"""
    async with session_scope() as session:
        companies = (await session.execute(text(
            "SELECT DISTINCT company_id FROM journal_lines WHERE account_code = :old"
        ), {"old": OLD_CODE})).scalars().all()
        plan = []
        for company_id in companies:
            exists = (await session.execute(text(
                "SELECT 1 FROM chart_accounts WHERE company_id=:c AND code=:code"
            ), {"c": company_id, "code": NEW_CODE})).first()
            if not exists:
                import datetime as _dt

                now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
                await session.execute(text(
                    "INSERT INTO chart_accounts (id, company_id, code, name, parent_code, level, category, type, normal_side, is_leaf, is_system, is_active, created_at, updated_at) "
                    "VALUES (:id, :c, :code, :name, NULL, 1, '损益-费用类', '费用', 'debit', TRUE, TRUE, TRUE, :now, :now)"
                ), {"id": f"acct-{company_id}-{NEW_CODE}", "c": company_id, "code": NEW_CODE, "name": NEW_NAME, "now": now_iso})
            rows = (await session.execute(text(
                "SELECT jl.id FROM journal_lines jl "
                "JOIN entries e ON e.id = jl.journal_entry_id "
                "WHERE jl.company_id=:c AND jl.account_code=:old "
                "AND e.payload::jsonb->>'category' = '银行及金融手续费'"
            ), {"c": company_id, "old": OLD_CODE})).scalars().all()
            plan.append((company_id, list(rows)))
        return plan


async def migrate(dry_run: bool = False) -> None:
    plan = await _pass_chart_and_collect()
    moved_lines = sum(len(ids) for _, ids in plan)
    if not dry_run:
        # 科目重分类不改动金额与方向（借贷平衡不受影响），系统级调账需临时绕过
        # posted 不可变触发器。UPDATE 会触发 deferred 校验，DISABLE/UPDATE/ENABLE
        # 必须拆成独立事务（同事务内 ENABLE 会报 pending trigger events）。
        async with session_scope() as session:
            await session.execute(text("ALTER TABLE journal_lines DISABLE TRIGGER trg_journal_line_immutable"))
        try:
            async with session_scope() as session:
                await session.execute(text(
                    "UPDATE journal_lines jl SET account_code=:new, account_name=:newname, "
                    "account_id = 'acct-' || jl.company_id || '-" + NEW_CODE + "' "
                    "FROM entries e WHERE e.id = jl.journal_entry_id AND jl.account_code=:old "
                    "AND e.payload::jsonb->>'category' = '银行及金融手续费'"
                ), {"new": NEW_CODE, "newname": NEW_NAME, "old": OLD_CODE})
        finally:
            async with session_scope() as session:
                await session.execute(text("ALTER TABLE journal_lines ENABLE TRIGGER trg_journal_line_immutable"))
    async with session_scope() as session:
        companies = [c for c, _ in plan] or (await session.execute(text(
            "SELECT DISTINCT company_id FROM entries WHERE payload::jsonb->>'category' = '银行及金融手续费'"
        ))).scalars().all()
        for company_id in companies:
            if dry_run:
                continue

            # 3. legacy 投影 + 分录科目字段
            projections = (await session.execute(text(
                "SELECT id, payload FROM entries WHERE company_id=:c "
                "AND payload::jsonb->>'category' = '银行及金融手续费'"
            ), {"c": company_id})).mappings().all()
            if not dry_run:
                for row in projections:
                    payload = json.loads(row["payload"])
                    voucher = payload.get("voucherLines") or {}
                    changed = False
                    for side in ("debit", "credit"):
                        for line in voucher.get(side) or []:
                            if str(line.get("accountCode")) == OLD_CODE:
                                line["accountCode"] = NEW_CODE
                                line["accountName"] = NEW_NAME
                                changed = True
                    if payload.get("accountCode") == OLD_CODE:
                        payload["accountCode"] = NEW_CODE
                        payload["accountName"] = NEW_NAME
                        changed = True
                    if changed:
                        await session.execute(text(
                            "UPDATE entries SET payload = :p WHERE id = :id"
                        ), {"p": json.dumps(payload, ensure_ascii=False), "id": row["id"]})

        print(f"{'DRY-RUN' if dry_run else 'DONE'}: companies_with_fee_lines={len(plan)} fee_lines_moved={moved_lines}")


if __name__ == "__main__":
    asyncio.run(migrate(dry_run="--dry-run" in sys.argv))
