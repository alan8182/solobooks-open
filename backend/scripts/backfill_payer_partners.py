# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""给历史垫付凭证补挂「往来单位」。

垫付人身份原先只存在 ``entry.metadata_json["payer"]`` 和（不落库的）科目名里，
2241/1221 行没有辅助核算，账簿上所有「其他应付款」长得一样。新凭证已由
``_add_lines`` 自动挂载；本脚本把存量补上，之后往来余额表才能按人汇总。

幂等：行上已有 partner_id / partner_name 一律跳过，可以反复执行。

用法（默认只预览，不改库）：

    python scripts/backfill_payer_partners.py --company-id <id>
    python scripts/backfill_payer_partners.py --company-id <id> --apply

执行前请按 docs/finance-core-runbook.md 备份数据库。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.chart_policy import is_person_advance_account  # noqa: E402
from db.models import JournalEntry, JournalLine  # noqa: E402
from db.session import session_scope  # noqa: E402
from services.account_mapper import (  # noqa: E402
    EMPLOYEE_PAYER_PREFIX,
    PAYER_BOSS,
    normalize_payer,
)
from services.auxiliary_service import (  # noqa: E402
    BOSS_PARTNER_FALLBACK_NAME,
    SHAREHOLDER_CATEGORIES,
    _boss_display_name,
    resolve_entry_person,
)

# 冲销行也要补：往来余额表把 posted 与 reversed 一起算，只补一侧会凭空多出余额
SYSTEM_ENTRY_TYPES = ("closing", "opening")


def _person_label(meta: dict, boss_name: str) -> str | None:
    """预览用：与 resolve_entry_person 的判定保持一致，但不建档案。"""
    if str(meta.get("paymentStatus") or "").strip().lower() == "unpaid":
        return None
    normalized = normalize_payer(meta.get("payer"))
    if normalized == PAYER_BOSS or str(meta.get("category") or "").strip() in SHAREHOLDER_CATEGORIES:
        return boss_name or BOSS_PARTNER_FALLBACK_NAME
    if normalized.startswith(f"{EMPLOYEE_PAYER_PREFIX}:"):
        name = normalized.split(":", 1)[1].strip()
        return name or None
    return None


async def backfill_company(session, company_id: str, *, apply: bool) -> tuple[int, int]:
    """在调用方事务里补挂，返回 (凭证数, 行数)。dry-run 不建档案、不写库。"""
    pending = 0
    entries_touched = 0
    entries = (
        await session.execute(
            select(JournalEntry)
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.entry_type.notin_(SYSTEM_ENTRY_TYPES),
            )
            .order_by(JournalEntry.occurred_on, JournalEntry.voucher_no)
        )
    ).scalars().all()
    boss_name = await _boss_display_name(session, company_id)
    for entry in entries:
        meta = entry.metadata_json or {}
        label = _person_label(meta, boss_name)
        if not label:
            continue
        lines = (
            await session.execute(
                select(JournalLine)
                .where(JournalLine.journal_entry_id == entry.id)
                .order_by(JournalLine.line_no)
            )
        ).scalars().all()
        targets = [
            line
            for line in lines
            if is_person_advance_account(line.account_code)
            and not (line.auxiliary or {}).get("partner_id")
            and not (line.auxiliary or {}).get("partner_name")
        ]
        if not targets:
            continue
        partner = None
        if apply:
            partner = await resolve_entry_person(
                session,
                company_id,
                payer=meta.get("payer"),
                category=str(meta.get("category") or ""),
                payment_status=meta.get("paymentStatus"),
                source=str(meta.get("source") or meta.get("sourceType") or ""),
            )
            if partner is None:
                continue
        entries_touched += 1
        for line in targets:
            pending += 1
            partner_name = partner["name"] if partner else label
            print(
                f"{entry.voucher_no or entry.id}  {entry.occurred_on}  "
                f"{line.account_code} {line.account_name}  → 往来单位：{partner_name}"
            )
            if partner:
                line.auxiliary = {
                    **(line.auxiliary or {}),
                    "partner_id": partner["id"],
                    "partner_name": partner["name"],
                }
    return entries_touched, pending


async def run(company_id: str, *, apply: bool) -> int:
    async with session_scope() as session:
        if apply:
            # 已过账凭证的分录有数据库触发器守着（ENTRY_POSTED_IMMUTABLE）。
            # 回填只补 auxiliary、不动金额/科目/期间，属于一次性数据修正：
            # 复用租户清理用的开关，事务级生效，提交即自动失效。
            await session.execute(text("select set_config('solobooks.allow_tenant_purge', 'on', true)"))
        entries_touched, pending = await backfill_company(session, company_id, apply=apply)
        if not apply:
            # 预览不能留事务尾巴：不写库，直接回滚
            await session.rollback()
    mode = "已写入" if apply else "预览（未写入）"
    print(f"\n{mode}：{entries_touched} 张凭证、{pending} 行补挂往来单位。")
    if not apply and pending:
        print("确认无误后加 --apply 执行。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="给历史垫付凭证补挂往来单位（幂等）")
    parser.add_argument("--company-id", required=True, help="企业 ID")
    parser.add_argument("--apply", action="store_true", help="真正写库；缺省只预览")
    args = parser.parse_args()
    return asyncio.run(run(args.company_id, apply=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
