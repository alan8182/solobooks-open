#!/usr/bin/env python
# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""清理银行流水被误传到「导入发票」后生成的草稿发票与凭证。

事故（2026-11-18）：用户把《杭州银行交易明细报表_5-8月.xls》拖进结账向导
第 1 步「导入发票」。前端模糊列名映射把「账户余额（元）」当成发票金额，
后端只校验"有号码或有金额"，于是整批写入了无发票号码、无开票日期、无购销方
的草稿凭证与发票台账行；随后台账回填又为同一批凭证补写了一份带当日日期的
发票行，所以每条凭证有 2 条发票记录。

阻断已在代码层修复（前端文件级识别 + 后端 parse 发票要素校验），本脚本只修
本地存量：

范围（按公司 + 创建时间窗 + 内容特征三重命中，绝不扩散）：
  公司 c_1417ff443c2b，创建于 2026-11-18 14:07:00–14:08:00（UTC），
  summary = 「发票-」，metadata.source = invoice_import，metadata.invoiceNumber 为空。

动作（默认 dry-run 只打印；--apply 才落库）：
  1. 逐条校验：状态必须是 draft / pending_review；不得有已确认对账、
     银行流水匹配；不是已过账凭证（过账凭证只能冲销，不能删）。
  2. 走标准 delete_draft：写审计事件、连带删除该凭证的发票台账行
     （invoices.entry_id 的 ON DELETE SET NULL 会留下孤儿票，所以必须走服务层）。
  3. 打印每条「改前 → 改后」，并复核候选数归零。

用法（在 backend 目录）：
  venv/bin/python scripts/repair_bank_statement_as_invoices.py          # dry-run
  venv/bin/python scripts/repair_bank_statement_as_invoices.py --apply  # 执行

回滚：本操作删除的是从未过账的草稿及其派生台账行，无凭证红冲可逆；
如需恢复，按 dry-run 输出的 entry_id / 发票行明细从备份恢复，或让用户重传
正确的发票文件（本批数据本身就是错误来源，不重建）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# 与其它诊断/修复脚本一致：显式读 backend/.env，避免裸 python 连到默认库
try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
except ImportError:  # python-dotenv 缺失时沿用进程环境变量
    pass

# ── 修复范围（显式常量，勿扩散）─────────────────────────────────────────────
COMPANY_ID = "c_1417ff443c2b"
OPERATOR = "u_f70aacab854b"  # 账套 owner
BATCH_START = datetime(2026, 11, 18, 14, 7, 0, tzinfo=timezone.utc)
BATCH_END = datetime(2026, 11, 18, 14, 8, 0, tzinfo=timezone.utc)
EXPECTED_ENTRIES = 15
EXPECTED_TOTAL = Decimal("200397.45")  # 借方合计；贷方同额，避免把两侧相加翻倍


def _matches_batch(entry) -> bool:
    meta = dict(entry.metadata_json or {})
    return (
        str(entry.summary or "").strip() == "发票-"
        and str(meta.get("source") or "") == "invoice_import"
        and not str(meta.get("invoiceNumber") or "").strip()
        and not str(meta.get("seller_name") or "").strip()
        and not str(meta.get("buyer_name") or "").strip()
    )


async def _collect(session) -> list[dict]:
    from sqlalchemy import func, select, text

    from db.models import Invoice, JournalEntry, JournalLine, ReconciliationLink

    entries = (
        await session.execute(
            select(JournalEntry).where(
                JournalEntry.company_id == COMPANY_ID,
                JournalEntry.created_at >= BATCH_START,
                JournalEntry.created_at < BATCH_END,
            ).order_by(JournalEntry.created_at)
        )
    ).scalars().all()

    plans: list[dict] = []
    for entry in entries:
        if not _matches_batch(entry):
            continue
        if entry.status not in {"draft", "pending_review"}:
            raise SystemExit(
                f"[abort] {entry.id} 状态为 {entry.status}：已过账凭证只能冲销，不能用本脚本删除"
            )
        confirmed = (
            await session.execute(
                select(ReconciliationLink.id).where(
                    ReconciliationLink.company_id == COMPANY_ID,
                    ReconciliationLink.journal_entry_id == entry.id,
                    ReconciliationLink.status == "confirmed",
                ).limit(1)
            )
        ).first()
        if confirmed:
            raise SystemExit(f"[abort] {entry.id} 已确认对账，请先取消对账")
        # bank_statements / bank_statement_matches 没有 ORM 模型，走只读 SQL 校验引用
        matched = (
            await session.execute(
                text(
                    "SELECT id FROM bank_statements "
                    "WHERE company_id = :company AND matched_entry_id = :entry LIMIT 1"
                ),
                {"company": COMPANY_ID, "entry": entry.id},
            )
        ).first()
        if matched:
            raise SystemExit(f"[abort] {entry.id} 已被银行流水匹配，请先取消匹配")
        legacy_match = (
            await session.execute(
                text(
                    "SELECT entry_id FROM bank_statement_matches "
                    "WHERE company_id = :company AND entry_id = :entry LIMIT 1"
                ),
                {"company": COMPANY_ID, "entry": entry.id},
            )
        ).first()
        if legacy_match:
            raise SystemExit(f"[abort] {entry.id} 存在历史银行匹配记录，请先取消匹配")

        total = (
            await session.execute(
                select(func.coalesce(func.sum(JournalLine.amount), 0)).where(
                    JournalLine.company_id == COMPANY_ID,
                    JournalLine.journal_entry_id == entry.id,
                    JournalLine.direction == "debit",
                )
            )
        ).scalar_one()
        invoice_count = (
            await session.execute(
                select(func.count()).select_from(Invoice).where(
                    Invoice.company_id == COMPANY_ID,
                    Invoice.entry_id == entry.id,
                )
            )
        ).scalar_one()

        plans.append({
            "id": entry.id,
            "status": entry.status,
            "occurred_on": str(entry.occurred_on),
            "amount": Decimal(str(total)),
            "invoice_rows": int(invoice_count),
        })
    return plans


async def _apply(plans: list[dict]) -> None:
    from db.session import session_scope
    from services.accounting_core_service import delete_draft, ensure_tenant

    async with session_scope() as session:
        await ensure_tenant(session, COMPANY_ID, OPERATOR)
        for plan in plans:
            await delete_draft(
                session,
                plan["id"],
                company_id=COMPANY_ID,
                user_id=OPERATOR,
                request_id=f"repair-bank-as-invoice-{uuid4().hex[:8]}",
            )
            print(
                f"[delete] {plan['id']}  {plan['occurred_on']}  "
                f"{plan['amount']}  →  已删除（连带 {plan['invoice_rows']} 条发票台账行）"
            )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="执行修复（默认 dry-run）")
    args = parser.parse_args()

    from db.session import session_scope

    async with session_scope() as session:
        plans = await _collect(session)

    total = sum((p["amount"] for p in plans), Decimal("0"))
    print(f"[scope] company={COMPANY_ID} 窗口 {BATCH_START.isoformat()} – {BATCH_END.isoformat()}")
    print(f"[found] 待清理草稿凭证 {len(plans)} 条，合计 {total}，关联发票台账行 "
          f"{sum(p['invoice_rows'] for p in plans)} 条")
    for plan in plans:
        print(f"  - {plan['id']}  {plan['status']:<8} {plan['occurred_on']}  "
              f"金额 {plan['amount']}  发票行 {plan['invoice_rows']}")

    if not plans:
        print("[done] 没有命中项：数据已清理或不属于本批，无需操作。")
        return

    if len(plans) != EXPECTED_ENTRIES or total != EXPECTED_TOTAL:
        print(
            f"[warn] 命中数与事故基线不一致（预期 {EXPECTED_ENTRIES} 条 / {EXPECTED_TOTAL}，"
            f"实际 {len(plans)} 条 / {total}）。请先人工核对再执行。"
        )
        if args.apply:
            raise SystemExit("[abort] --apply 已中止：范围与基线不一致")

    if not args.apply:
        print("\n[dry-run] 以上为清理计划，未改动任何数据。确认后加 --apply 执行。")
        return

    print("\n[apply] 开始清理……")
    await _apply(plans)

    async with session_scope() as session:
        remaining = await _collect(session)
    print(f"[verify] 清理后仍有 {len(remaining)} 条命中项（应为 0）")
    if remaining:
        raise SystemExit("[verify] 清理未完成，请检查日志")


if __name__ == "__main__":
    asyncio.run(main())
