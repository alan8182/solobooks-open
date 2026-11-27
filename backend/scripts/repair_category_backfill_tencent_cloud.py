#!/usr/bin/env python
# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""2026-08 腾讯云云服务费发票的分类更正（Q3 已报送，走补记口径）。

原凭证 ``VOU-202608-000005``（2026-08-18）把「*生产生活服务*云服务费」记进
5602999 管理费用-其他。2026-Q3 的增值税、所得税、财务报表与印花税已于
2026-11-21 报送，按 runbook「已报送不改原月、走补记」：不动 8 月凭证，在最早的
未关账期间（2026-10）补一笔等额重分类：

    借 管理费用-信息技术服务费 5602015  481.33
    贷 管理费用-其他            5602999  481.33

净额不变、只改明细科目；摘要由 create_entry 自动加「补记2026-08｜」前缀，
`period_backfills` 留痕。原 8 月凭证与已报送报表保持原样。

用法::

    cd backend && venv/bin/python scripts/repair_category_backfill_tencent_cloud.py          # dry-run
    cd backend && venv/bin/python scripts/repair_category_backfill_tencent_cloud.py --apply  # 执行
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

from db.models import JournalEntry, JournalLine, PeriodBackfill  # noqa: E402
from db.session import session_scope  # noqa: E402
from services.accounting_core_service import create_entry, serialize_journal  # noqa: E402
from services.period_resolution_service import resolve_posting_period  # noqa: E402

COMPANY_ID = "c_87c2d1fe2dd8"
OPERATOR = "u_f70aacab854b"
SOURCE_ENTRY_ID = "e54d129f-cb71-43ae-aef5-5956718a6b00"
ADJUSTMENT_ID = "catfix-tencent-cloud-202608"
AMOUNT = Decimal("481.33")
BACKFILL_REASON = (
    "2026-08 腾讯云云服务费发票误记管理费用-其他；2026-Q3 已报送，不改原月，"
    "按品名在最早未关账期间补记重分类为信息技术服务费"
)


def _payload() -> dict:
    return {
        "id": ADJUSTMENT_ID,
        "title": "更正管理费用明细：腾讯云云服务费（原记其他支出）",
        "direction": "expense",
        "category": "信息技术服务费",
        "amount": "481.33",
        # 业务日期仍是原发票日期；allowBackfill 让系统把它记进最早的未关账期间
        "occurDate": "2026-08-18",
        "allowBackfill": True,
        "backfillReason": BACKFILL_REASON,
        "source": "manual",
        "voucherAuthority": "manual",
        "correctsEntryId": SOURCE_ENTRY_ID,
        "reason": "按品名补记重分类：管理费用-其他 → 管理费用-信息技术服务费",
        "voucherLines": {
            "debit": [
                {"accountCode": "5602015", "accountName": "管理费用-信息技术服务费", "amount": "481.33"}
            ],
            "credit": [
                {"accountCode": "5602999", "accountName": "管理费用-其他", "amount": "481.33"}
            ],
        },
    }


async def _show_original(session) -> None:
    entry = await session.get(JournalEntry, SOURCE_ENTRY_ID)
    if entry is None or entry.company_id != COMPANY_ID:
        raise SystemExit(f"✗ 原凭证 {SOURCE_ENTRY_ID} 不存在")
    print(f"原凭证: {entry.id} / {entry.status} / {entry.occurred_on}")
    for line in (
        await session.execute(
            JournalLine.__table__.select()
            .where(JournalLine.journal_entry_id == SOURCE_ENTRY_ID)
            .order_by(JournalLine.line_no)
        )
    ).mappings():
        print(f"  {line['direction']:6} {line['account_code']} {line['account_name']} {line['amount']}")


async def dry_run() -> None:
    async with session_scope() as session:
        await _show_original(session)
        existing = await session.get(JournalEntry, ADJUSTMENT_ID)
        print(f"\n补记凭证: {ADJUSTMENT_ID} -> {'已存在（' + existing.status + '），将跳过' if existing else '待创建'}")
        resolution = await resolve_posting_period(session, COMPANY_ID, "2026-08-18", allow_backfill=True)
        print(
            f"期间判定: 业务期间 {resolution.business_period:%Y-%m} → 记账期间 "
            f"{resolution.posting_period:%Y-%m}（补记={resolution.is_backfill}，跨季={resolution.cross_quarter}）"
        )
        print("计划分录:")
        for direction, line in (("借", "debit"), ("贷", "credit")):
            for item in _payload()["voucherLines"][line]:
                print(f"  {direction} {item['accountCode']} {item['accountName']} {item['amount']}")


async def apply() -> None:
    async with session_scope() as session:
        existing = await session.get(JournalEntry, ADJUSTMENT_ID)
        if existing is not None:
            print(f"↷ {ADJUSTMENT_ID} 已存在（{existing.status}），跳过")
            return
        source = await session.get(JournalEntry, SOURCE_ENTRY_ID)
        if source is None or source.status != "posted":
            raise SystemExit("✗ 原凭证不是 posted，停止")
        entry = await create_entry(
            session,
            _payload(),
            company_id=COMPANY_ID,
            user_id=OPERATOR,
            request_id=f"repair-category-backfill-{ADJUSTMENT_ID}",
            confirm=True,
        )
        payload = await serialize_journal(session, entry)
        if entry.status != "posted" or entry.period_start.strftime("%Y-%m") != "2026-10":
            raise SystemExit(f"✗ 补记未落到 2026-10（status={entry.status}），回滚")
        if not entry.summary.startswith("补记2026-08｜"):
            raise SystemExit(f"✗ 摘要未加补记前缀：{entry.summary}，回滚")
        lines = sorted(
            (line.direction, line.account_code, line.amount)
            for line in (
                await session.execute(
                    JournalLine.__table__.select().where(JournalLine.journal_entry_id == entry.id)
                )
            ).mappings()
        )
        if lines != [
            ("credit", "5602999", AMOUNT),
            ("debit", "5602015", AMOUNT),
        ]:
            raise SystemExit(f"✗ 补记分录不符合预期：{lines}，回滚")
        ledger = (
            await session.execute(
                PeriodBackfill.__table__.select().where(PeriodBackfill.journal_entry_id == entry.id)
            )
        ).mappings().first()
        if ledger is None or ledger["status"] != "active":
            raise SystemExit("✗ period_backfills 未留痕，回滚")
        print(f"✓ 已补记 {entry.id} / {payload.get('voucherRef')}")
        print(f"  摘要: {entry.summary}")
        print(f"  期间: {entry.period_start:%Y-%m}（业务期间 {ledger['original_period']:%Y-%m}）")
        print(f"  分录: {lines}")
        print(f"  原凭证 {SOURCE_ENTRY_ID} 保持 {source.status}，8 月报表口径不变")


async def main() -> None:
    parser = argparse.ArgumentParser(description="腾讯云云服务费分类更正（补记口径）")
    parser.add_argument("--apply", action="store_true", help="实际写库；缺省为 dry-run")
    args = parser.parse_args()
    await (apply() if args.apply else dry_run())


if __name__ == "__main__":
    asyncio.run(main())
