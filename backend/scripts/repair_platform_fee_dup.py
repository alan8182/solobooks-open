#!/usr/bin/env python
# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""修复支付平台手续费发票被"整月批量过账"误过账造成的重复记账。

事故（2026-10-31）：财付通手续费发票导入时按设计归档为底稿（仅归档不记账），
手续费本体已在"微信支付月度汇总"结算凭证入账（借 财务费用-手续费 5603002）。
结账向导整月批量过账未排除底稿，把两张证据发票过账成真实费用凭证——
手续费双计 + 2241 挂账悬空。批量过账漏洞已在 d726b94 修复，本脚本只修存量。

范围（按 entry_id 精确命中，绝不批量扫）：
  - e1ad185e-39b0-44f2-ac78-18b6a03f9c5a  0.68  p2026-08  证据归属：微信支付月度汇总-2026-07
  - d36ee317-ffbf-4fcd-b6f0-1873c73b79e4  0.87  p2026-09  证据归属：微信支付月度汇总-2026-08

动作（默认 dry-run 只打印计划；--apply 才落库）：
  1. 反结账（级联顺序：先 09 后 08；step-up 由脚本直接签发，留审计原因）
  2. 红冲两张误过账发票凭证（reverse_journal，费用与 2241 挂账同步冲平）
  3. 发票投影标记（全走 POSTED_PROJECTION_FIELDS 白名单）：
     is_archive_only=true / billingStatus=reconciled / linkedExpenseEntryId=月度汇总凭证
  4. 重新关账（先 08 后 09，走标准五项检查）

用法：
  python scripts/repair_platform_fee_dup.py            # dry-run
  python scripts/repair_platform_fee_dup.py --apply    # 执行
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── 修复目标（显式清单，勿扩散）───────────────────────────────────────────
COMPANY_ID = "c_9fa4e2250050"
OPERATOR = "u_f70aacab854b"  # 账套 owner，反结账/关账都需要该角色

REPAIRS = [
    {
        "entry_id": "e1ad185e-39b0-44f2-ac78-18b6a03f9c5a",
        "amount": "0.68",
        "period": "2026-08",
        "settlement_title": "微信支付月度汇总-2026-07",
    },
    {
        "entry_id": "d36ee317-ffbf-4fcd-b6f0-1873c73b79e4",
        "amount": "0.87",
        "period": "2026-09",
        "settlement_title": "微信支付月度汇总-2026-08",
    },
]
REOPEN_MONTHS = ["2026-09", "2026-08"]  # 级联：后月先开
RECLOSE_MONTHS = ["2026-08", "2026-09"]  # 先月先关
REOPEN_REASON = "修复平台手续费发票被整月批量过账误过账造成的重复记账（d726b94 后存量清理）"
REVERSE_REASON = "支付平台手续费已在微信月度汇总凭证入账，冲销重复过账的底稿发票凭证"


async def _preflight(session) -> list[dict]:
    """校验修复目标与证据归属，返回带结算凭证 id 的修复计划。"""
    from sqlalchemy import select

    from db.models import AccountingPeriod, JournalEntry, JournalLine
    from services.accounting_core_service import get_journal

    plans: list[dict] = []
    for item in REPAIRS:
        entry = await get_journal(session, item["entry_id"], company_id=COMPANY_ID)
        meta = dict(entry.metadata_json or {})
        assert str(meta.get("source") or "") == "invoice_import", f"{item['entry_id']} 来源不是发票导入"
        assert meta.get("isArchiveOnly") or meta.get("is_archive_only"), f"{item['entry_id']} 不是归档底稿"
        assert entry.status == "posted", f"{item['entry_id']} 状态为 {entry.status}，预期 posted"
        assert str(entry.period_start)[:7] == item["period"], f"{item['entry_id']} 期间不符"
        assert str(meta.get("amount") or "") == item["amount"], f"{item['entry_id']} 金额不符"

        settlement = (
            await session.execute(
                select(JournalEntry).where(
                    JournalEntry.company_id == COMPANY_ID,
                    JournalEntry.summary == item["settlement_title"],
                    JournalEntry.status == "posted",
                )
            )
        ).scalar_one()
        fee_lines = (
            await session.execute(
                select(JournalLine).where(
                    JournalLine.journal_entry_id == settlement.id,
                    JournalLine.account_code == "5603002",
                    JournalLine.direction == "debit",
                )
            )
        ).scalars().all()
        assert fee_lines, f"{item['settlement_title']} 无手续费行"
        assert len(fee_lines) == 1 and str(fee_lines[0].amount) == item["amount"], (
            f"{item['settlement_title']} 手续费 {fee_lines[0].amount if fee_lines else '-'} 与发票 {item['amount']} 不一致"
        )

        period = (
            await session.execute(
                select(AccountingPeriod).where(
                    AccountingPeriod.company_id == COMPANY_ID,
                    AccountingPeriod.period_start == date.fromisoformat(f"{item['period']}-01"),
                )
            )
        ).scalar_one()
        assert period.status == "closed", f"{item['period']} 期次状态 {period.status}，预期 closed"

        plans.append({**item, "settlement_id": settlement.id})
        print(f"[plan] {item['entry_id']} ({item['amount']}, {item['period']}) -> 证据归属 {settlement.id} {item['settlement_title']}")
    return plans


async def _apply(plans: list[dict]) -> None:
    from uuid import uuid4

    from db.session import session_scope
    from services.accounting_core_service import (
        close_period,
        get_journal,
        issue_step_up_token,
        reopen_period,
        reverse_journal,
        update_draft,
    )

    async with session_scope() as session:
        # 1. 反结账（级联：先 09 后 08；step-up token 一次性，每次反结账单独签发）
        for month in REOPEN_MONTHS:
            raw_token = await issue_step_up_token(
                session, company_id=COMPANY_ID, user_id=OPERATOR, action="reopen_period",
            )
            result = await reopen_period(
                session, COMPANY_ID, month, user_id=OPERATOR,
                request_id=f"repair-fee-reopen-{uuid4().hex[:8]}",
                reason=REOPEN_REASON, step_up_token=raw_token,
            )
            print(f"[reopen] {month} -> {result.get('status', 'reopened')}")

        # 2. 红冲 + 3. 投影标记
        for item in plans:
            entry = await get_journal(session, item["entry_id"], company_id=COMPANY_ID)
            if entry.status == "posted":
                await reverse_journal(
                    session, item["entry_id"], company_id=COMPANY_ID, user_id=OPERATOR,
                    request_id=f"repair-fee-rev-{uuid4().hex[:8]}", reason=REVERSE_REASON,
                )
                print(f"[reverse] {item['entry_id']} ({item['amount']}) 红冲完成")
            await update_draft(
                session, item["entry_id"],
                {
                    "is_archive_only": True,
                    "isArchiveOnly": True,
                    "billingStatus": "reconciled",
                    "linkedExpenseEntryId": item["settlement_id"],
                },
                company_id=COMPANY_ID, user_id=OPERATOR,
                request_id=f"repair-fee-mark-{uuid4().hex[:8]}",
            )
            print(f"[mark] {item['entry_id']} -> 证据挂 {item['settlement_id']}")

        # 4. 重新关账（先 08 后 09，标准五项检查）
        for month in RECLOSE_MONTHS:
            result = await close_period(
                session, COMPANY_ID, month, user_id=OPERATOR,
                request_id=f"repair-fee-close-{uuid4().hex[:8]}",
            )
            print(f"[close] {month} -> {result.get('periodStatus', 'closed')}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="执行修复（默认 dry-run）")
    args = parser.parse_args()

    from db.session import session_scope
    from services.accounting_core_service import ensure_tenant

    async with session_scope() as session:
        await ensure_tenant(session, COMPANY_ID, OPERATOR)
        plans = await _preflight(session)

    if not args.apply:
        print("\n[dry-run] 以上为修复计划。确认无误后加 --apply 执行。")
        return
    print("\n[apply] 开始执行修复……")
    await _apply(plans)
    print("\n[done] 修复完成。请到 UI 过验证清单：8/9 月损益手续费单记、2241-财付通余额 0、待办消卡、期次已重关。")


if __name__ == "__main__":
    asyncio.run(main())
