#!/usr/bin/env python
# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""更正发票导入时未按品名归类、误落 5602999 管理费用-其他的已过账凭证。

背景（2026-11-21 导入批次）：电票 XML/PDF 没有分类列，前端旧逻辑对支出票一律
兜底「其他支出」，导入又走 classify=false（不调 LLM），于是品名写明「技术服务费」
「软件测试服务」「云服务费」的发票全部记进 5602999。2026-11-23 已修导入预览
（按品名给出确定性建议），本脚本只处理修复前已经过账的历史凭证。

动作（默认 dry-run；``--apply`` 才落库）：对显式清单里的每条凭证调用
``correct_entry_category``：红字冲销原凭证 + 按新分类重建过账 + 迁移
``invoices.entry_id`` 与开票状态/付款日期等跟踪字段。审计只增不改。

刻意不动：

  - 2026-08 腾讯云 ¥481.33（期间已关账，应走补记口径，另行确认）
  - 已关联银行流水（confirmed）的凭证：服务层会拒绝，先取消对账再来
  - 其他账套、其他凭证

用法::

    cd backend && venv/bin/python scripts/repair_invoice_import_category.py          # dry-run
    cd backend && venv/bin/python scripts/repair_invoice_import_category.py --apply  # 执行
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
except ImportError:
    pass

from sqlalchemy import select, text  # noqa: E402

from db.models import Invoice, JournalEntry, JournalLine, LegacyEntryProjection, ReconciliationLink  # noqa: E402
from db.session import session_scope  # noqa: E402
from services.accounting_core_service import correct_entry_category, serialize_journal  # noqa: E402

# ── 修复目标（显式清单，勿扩散）───────────────────────────────────────────────
COMPANY_ID = "c_87c2d1fe2dd8"
OPERATOR = "u_f70aacab854b"
REASON = "发票导入未按品名归类（技术服务/软件测试/云服务误入管理费用-其他），按品名规则更正为信息技术服务费"

TARGETS: dict[str, dict[str, str]] = {
    "f4b23ac3-d8d4-4670-9b94-96f197dd97d9": {
        "old": "其他支出",
        "new": "信息技术服务费",
        "note": "北京百度网讯 ¥1794.00（VOU-202610-000010）",
    },
    "5298f14d-5cd2-4cdd-aa60-e1ec2757d46c": {
        "old": "其他支出",
        "new": "信息技术服务费",
        "note": "杭州深度求索 ¥200.00（VOU-202610-000002）",
    },
    "7c1a5497-5218-4461-b814-4c77c79b7e27": {
        "old": "其他支出",
        "new": "信息技术服务费",
        "note": "王翼 ¥300.00（VOU-202610-000003）",
    },
}


def _payload(raw: object) -> dict:
    try:
        return json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


async def _lines(session, entry_id: str) -> list[tuple[str, str, str, str]]:
    rows = (
        await session.execute(
            select(JournalLine).where(JournalLine.journal_entry_id == entry_id).order_by(JournalLine.line_no)
        )
    ).scalars().all()
    return [(line.direction, line.account_code, line.account_name, str(line.amount)) for line in rows]


async def _invoice_for(session, entry_id: str) -> Invoice | None:
    return (
        await session.execute(
            select(Invoice).where(Invoice.company_id == COMPANY_ID, Invoice.entry_id == entry_id)
        )
    ).scalars().first()


async def _confirmed_bank_link(session, entry_id: str) -> bool:
    return (
        await session.execute(
            select(ReconciliationLink.id).where(
                ReconciliationLink.company_id == COMPANY_ID,
                ReconciliationLink.journal_entry_id == entry_id,
                ReconciliationLink.status == "confirmed",
            ).limit(1)
        )
    ).first() is not None


async def dry_run() -> None:
    async with session_scope() as session:
        periods = (
            await session.execute(
                text(
                    "SELECT period_start, status FROM accounting_periods "
                    "WHERE company_id = :company ORDER BY period_start DESC LIMIT 3"
                ),
                {"company": COMPANY_ID},
            )
        ).fetchall()
        print("期间状态:", [(str(row[0]), row[1]) for row in periods])
        for entry_id, spec in TARGETS.items():
            entry = await session.get(JournalEntry, entry_id)
            if entry is None or entry.company_id != COMPANY_ID:
                print(f"\n✗ {entry_id}: 不存在或不属于 {COMPANY_ID}")
                continue
            meta = _payload(entry.metadata_json)
            projected = _payload((await session.get(LegacyEntryProjection, entry_id)).payload)
            print(f"\n{entry_id}  {spec['note']}")
            print(f"  状态: {entry.status} / {entry.entry_type} / 凭证号 {projected.get('voucherRef') or '-'}")
            print(f"  分类: {meta.get('category')} → {spec['new']}（期望原值 {spec['old']}）")
            print(f"  分录: {await _lines(session, entry_id)}")
            invoice = await _invoice_for(session, entry_id)
            print(f"  发票主数据: {invoice.id if invoice else '无'}")
            if await _confirmed_bank_link(session, entry_id):
                print("  ⚠ 存在 confirmed 银行核销关联，服务层会拒绝；需先取消对账")
            if entry.status != "posted":
                print("  ↷ 已不是 posted（可能已处理），跳过")


async def apply() -> None:
    for entry_id, spec in TARGETS.items():
        async with session_scope() as session:
            entry = await session.get(JournalEntry, entry_id)
            if entry is None or entry.company_id != COMPANY_ID:
                print(f"✗ {entry_id}: 不存在或不属于 {COMPANY_ID}，跳过")
                continue
            meta = _payload(entry.metadata_json)
            if entry.status != "posted":
                print(f"↷ {entry_id}: 状态 {entry.status}，已处理过，跳过")
                continue
            if str(meta.get("category") or "") == spec["new"]:
                print(f"↷ {entry_id}: 分类已是 {spec['new']}，跳过")
                continue
            if str(meta.get("category") or "") != spec["old"]:
                raise SystemExit(
                    f"✗ {entry_id}: 当前分类是「{meta.get('category')}」，与预期「{spec['old']}」不符，停止"
                )
            before = await serialize_journal(session, entry)
            invoice = await _invoice_for(session, entry_id)
            result = await correct_entry_category(
                session,
                entry_id,
                spec["new"],
                company_id=COMPANY_ID,
                user_id=OPERATOR,
                request_id=f"repair-invoice-category-{entry_id[:8]}",
                reason=REASON,
            )
            corrected = await session.get(JournalEntry, result["correctedId"])
            if corrected is None or corrected.status != "posted":
                raise SystemExit(f"✗ {entry_id}: 重记凭证未成功过账，回滚")
            # 核对链：发票主数据迁到新凭证、跟踪字段不丢、借贷平衡
            after = await serialize_journal(session, corrected)
            if invoice is not None:
                rebound = await session.get(Invoice, invoice.id)
                if rebound is None or rebound.entry_id != corrected.id:
                    raise SystemExit(f"✗ {entry_id}: invoices.entry_id 未迁移到 {corrected.id}，回滚")
            projection = _payload((await session.get(LegacyEntryProjection, corrected.id)).payload)
            for key in ("billingStatus", "paymentDate"):
                if before.get(key) is not None and projection.get(key) != before.get(key):
                    raise SystemExit(f"✗ {entry_id}: 字段 {key} 丢失（{before.get(key)} → {projection.get(key)}），回滚")
            print(f"\n✓ {entry_id}  {spec['note']}")
            print(f"  原凭证 {before.get('voucherRef')} → reversed；冲销 {result['reversalId']}")
            print(f"  新凭证 {after.get('voucherRef')}（{corrected.id}）分类 {spec['new']}")
            print(f"  新分录: {await _lines(session, corrected.id)}")
            if invoice is not None:
                print(f"  发票 {invoice.id} entry_id 已迁到新凭证")


async def main() -> None:
    parser = argparse.ArgumentParser(description="更正发票导入误落管理费用-其他的已过账凭证")
    parser.add_argument("--apply", action="store_true", help="实际写库；缺省为 dry-run")
    args = parser.parse_args()
    if args.apply:
        await apply()
    else:
        await dry_run()


if __name__ == "__main__":
    asyncio.run(main())
