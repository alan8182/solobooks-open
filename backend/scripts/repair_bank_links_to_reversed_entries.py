#!/usr/bin/env python
# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""解除「银行流水匹配到已冲销凭证」的悬空关联。

事故（2026-11-23）：结账向导「撤销原入账」先红冲凭证、后没有解链，导致
``bank_statements.matched_entry_id`` 与 ``reconciliation_links`` 仍指向已冲销
凭证；流水既不算待处理、凭证又已红冲，账面与流水两端都对不上。

本脚本对显式清单里的流水调用 ``confirm_statement_match(entry_ids=[])``（与界面
「取消关联」同一权威路径），回退 billingStatus/paymentDate 并把流水恢复为待处理。
只处理「匹配凭证已冲销/不存在」的流水；凭证仍有效的关联一律拒绝，避免误伤。

用法::

    cd backend && venv/bin/python scripts/repair_bank_links_to_reversed_entries.py          # dry-run
    cd backend && venv/bin/python scripts/repair_bank_links_to_reversed_entries.py --apply  # 执行
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
except ImportError:
    pass

from sqlalchemy import text  # noqa: E402

from db.models import JournalEntry  # noqa: E402
from db.session import session_scope  # noqa: E402
from services.bank_settlement_service import confirm_statement_match  # noqa: E402

COMPANY_ID = "c_87c2d1fe2dd8"
OPERATOR = "u_f70aacab854b"

# 2026-11-23 13:10~13:11 UTC 用户在结账向导点「撤销原入账」留下的悬空关联
STATEMENT_IDS = [
    "bs-c_87c2d1fe2dd8-1795284934125654-0",
    "bs-c_87c2d1fe2dd8-1795284934127433-1",
    "bs-c_87c2d1fe2dd8-1795284934129541-3",
    "bs-c_87c2d1fe2dd8-1795284934133193-7",
]


async def _inspect(session, statement_id: str) -> tuple[str, str]:
    row = (
        await session.execute(
            text(
                "SELECT matched_entry_id, description FROM bank_statements "
                "WHERE company_id=:company AND id=:statement"
            ),
            {"company": COMPANY_ID, "statement": statement_id},
        )
    ).mappings().first()
    if row is None:
        return "", ""
    return str(row["matched_entry_id"] or ""), str(row["description"] or "")


async def run(apply: bool) -> None:
    for statement_id in STATEMENT_IDS:
        async with session_scope() as session:
            entry_id, description = await _inspect(session, statement_id)
            if not entry_id:
                print(f"↷ {statement_id}: 已无匹配，跳过")
                continue
            entry = await session.get(JournalEntry, entry_id)
            status = entry.status if entry else "missing"
            print(f"\n{statement_id}  {description}")
            print(f"  匹配凭证: {entry_id} → {status}")
            if status != "reversed":
                raise SystemExit(f"✗ {statement_id}: 匹配凭证状态为 {status}，不是已冲销，拒绝解链")
            if not apply:
                print("  dry-run：将解除关联并把流水恢复为待处理")
                continue
            result = await confirm_statement_match(
                session,
                company_id=COMPANY_ID,
                user_id=OPERATOR,
                statement_id=statement_id,
                entry_ids=[],
                request_id=f"repair-dangling-bank-{statement_id[-6:]}",
            )
            after_entry_id, _ = await _inspect(session, statement_id)
            if after_entry_id:
                raise SystemExit(f"✗ {statement_id}: 解链后仍有匹配 {after_entry_id}，回滚")
            print(f"  ✓ 已解链，流水回到待处理（{result.get('status')}）")


async def main() -> None:
    parser = argparse.ArgumentParser(description="解除银行流水到已冲销凭证的悬空关联")
    parser.add_argument("--apply", action="store_true", help="实际写库；缺省为 dry-run")
    args = parser.parse_args()
    await run(args.apply)


if __name__ == "__main__":
    asyncio.run(main())
