# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""修复 c_84ff4c3d950e 两张发票草稿的分类（仅改分类，不动付款去向）。

诊断来源：``scripts/diagnose_invoice_voucher_semantics.py``
- ``8d983347-…`` 腾讯「微信平台服务」¥300：其他支出 → 信息技术服务费
- ``ddf487dc-…`` 财付通「微信支付通道服务费」¥0.68：其他支出 → 银行及金融手续费

两张都是 **草稿**（open 期间），走 ``update_draft`` 更正即可，无需红冲重记。
凭证的结算侧（贷 2241 及往来单位）由 ``_preserve_settlement_lines`` 原样保留，
脚本在写后再次核对。幂等：已经是目标分类的直接跳过。

用法::

    cd backend && venv/bin/python scripts/repair_invoice_category_drafts.py          # dry-run
    cd backend && venv/bin/python scripts/repair_invoice_category_drafts.py --apply  # 执行

回滚：两张仍是草稿，用 ``update_draft`` 把 category 改回「其他支出」即可；
本脚本重复执行不会叠加任何凭证。apply 为全有或全无：任一目标校验失败即整批回滚。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
except ImportError:
    pass

from sqlalchemy import select  # noqa: E402

from db.models import JournalEntry, JournalLine  # noqa: E402
from db.session import session_scope  # noqa: E402
from services.accounting_core_service import update_draft  # noqa: E402
from services.account_mapper import CATEGORY_TO_ACCOUNT  # noqa: E402

COMPANY_ID = "c_84ff4c3d950e"
OPERATOR = "u_f70aacab854b"
TARGETS = (
    {
        "entry_id": "8d983347-31ab-4074-9fcb-c11641549640",
        "expected_category": "其他支出",
        "target_category": "信息技术服务费",
        "expected_amount": Decimal("300.00"),
    },
    {
        "entry_id": "ddf487dc-fefd-4462-a95b-d520d5fb27cc",
        "expected_category": "其他支出",
        "target_category": "银行及金融手续费",
        "expected_amount": Decimal("0.68"),
    },
)


async def _lines(session, entry_id: str) -> list[JournalLine]:
    return list(
        (
            await session.execute(
                select(JournalLine)
                .where(JournalLine.journal_entry_id == entry_id)
                .order_by(JournalLine.line_no)
            )
        ).scalars().all()
    )


def _side_codes(lines: list[JournalLine], side: str) -> list[str]:
    return [str(line.account_code) for line in lines if line.direction == side]


def _credit_snapshot(lines: list[JournalLine]) -> list[tuple[str, str, dict]]:
    return [
        (str(line.account_code), str(line.amount), dict(line.auxiliary or {}))
        for line in lines
        if line.direction == "credit"
    ]


async def _inspect(session, target: dict, *, dry_run: bool) -> str:
    """校验单个目标，返回 skip / ready / 错误说明。不写库。"""
    entry = await session.get(JournalEntry, target["entry_id"])
    if entry is None or entry.company_id != COMPANY_ID:
        return f"✗ 凭证不存在或不属于 {COMPANY_ID}"
    meta = dict(entry.metadata_json or {})
    current_category = str(meta.get("category") or "")
    amount = Decimal(str(meta.get("amount") or "0"))
    lines = await _lines(session, entry.id)
    print(f"\n{entry.id} / {entry.status} / {entry.occurred_on} / {entry.summary}")
    print(f"  分类: {current_category}    金额: {amount}    期间: {entry.period_start}")
    for line in lines:
        aux = (line.auxiliary or {}).get("partner_name") or ""
        print(f"  {line.direction:6} {line.account_code} {line.account_name:20} {line.amount} {aux}")

    if entry.status != "draft":
        return f"✗ 状态是 {entry.status}，不是草稿，停止"
    if current_category == target["target_category"]:
        print(f"  ↷ 已是目标分类「{target['target_category']}」，跳过")
        return "skip"
    if current_category != target["expected_category"]:
        return f"✗ 当前分类「{current_category}」与预期「{target['expected_category']}」不符，停止"
    if amount != target["expected_amount"]:
        return f"✗ 金额 {amount} 与预期 {target['expected_amount']} 不符，停止"
    if _side_codes(lines, "credit") != ["2241"]:
        return f"✗ 贷方不是单一 2241：{_side_codes(lines, 'credit')}，停止"
    expected_code = str(
        CATEGORY_TO_ACCOUNT.get(target["target_category"], {}).get("accountCode") or ""
    )
    if not expected_code:
        return f"✗ 目标分类「{target['target_category']}」没有科目映射，停止"
    if "1002" in _side_codes(lines, "credit"):
        return "✗ 贷方含 1002，停止"

    if dry_run:
        print(
            f"  计划: 借 {_side_codes(lines, 'debit')} → {expected_code}"
            f"（{target['target_category']}），贷 2241 与往来单位保持不变"
        )
    return "ready"


async def _apply_one(session, target: dict) -> None:
    """执行并核对；任何不符都抛异常，由 session_scope 整批回滚。"""
    entry = await session.get(JournalEntry, target["entry_id"])
    lines_before = await _lines(session, entry.id)
    credit_before = _credit_snapshot(lines_before)
    expected_code = str(
        CATEGORY_TO_ACCOUNT[target["target_category"]]["accountCode"]
    )
    await update_draft(
        session,
        entry.id,
        {"category": target["target_category"], "categorySource": "manual"},
        company_id=COMPANY_ID,
        user_id=OPERATOR,
        request_id=f"repair-invoice-category:{entry.id}",
    )
    await session.flush()
    lines_after = await _lines(session, entry.id)
    if _side_codes(lines_after, "debit") != [expected_code]:
        raise RuntimeError(
            f"{entry.id}: 写后借方 {_side_codes(lines_after, 'debit')} 不是 {expected_code}，回滚"
        )
    if _credit_snapshot(lines_after) != credit_before:
        raise RuntimeError(f"{entry.id}: 写后结算侧发生变化，回滚")
    print(f"  ✓ {entry.id} 已改为「{target['target_category']}」；贷 2241 与往来单位保持不变")


async def main() -> None:
    parser = argparse.ArgumentParser(description="修复两张发票草稿的分类")
    parser.add_argument("--apply", action="store_true", help="实际写库；缺省为 dry-run")
    args = parser.parse_args()

    # 第一轮只读校验：任一目标不满足前置条件就不进入写库阶段
    async with session_scope() as session:
        statuses = [await _inspect(session, target, dry_run=not args.apply) for target in TARGETS]

    if any(status.startswith("✗") for status in statuses):
        print("\n存在不满足条件的目标，未写库。")
        for target, status in zip(TARGETS, statuses):
            print(f"  {target['entry_id']}: {status}")
        raise SystemExit(1)

    if not args.apply:
        print("\n本次为 dry-run，未写库。确认后加 --apply 执行。")
        return

    async with session_scope() as session:
        for target, status in zip(TARGETS, statuses):
            if status == "skip":
                print(f"\n↷ {target['entry_id']} 已是目标分类，跳过")
                continue
            await _apply_one(session, target)

    print("\n修复完成。")


if __name__ == "__main__":
    asyncio.run(main())
