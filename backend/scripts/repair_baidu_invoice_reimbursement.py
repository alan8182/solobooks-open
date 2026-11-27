#!/usr/bin/env python
# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""更正百度云 1,794.00 发票凭证的付款方：老板垫付 → 报销给老板。

业务事实（用户 2026-11-23 确认）：2026-10-17 的百度云发票不是公司银行直接付给
百度，而是老板（杨云飞）先垫付，2026-10-26 银行再报销给老板。

当前凭证 ``correct-f4b23ac3-d8d-187ec0`` 按「公司银行直接支付」记：
    借 5602015 管理费用-信息技术服务费 1,794.00
    贷 1002   银行存款               1,794.00
正确形态应与腾讯云 481.33 / 智谱 693.84 一致（垫付报销）：
    发票凭证  借 5602015 / 贷 2241 其他应付款（payer=boss_personal）
    报销凭证  借 2241    / 贷 1002 银行存款（由银行流水页确认配对时生成）

已过账凭证不可原地改（A1）：红冲当前凭证，新建 2241 凭证并把发票主数据迁过去；
随后在结账向导/银行页对该流水「确认配对」，由系统生成报销付款凭证。

用法::

    cd backend && venv/bin/python scripts/repair_baidu_invoice_reimbursement.py          # dry-run
    cd backend && venv/bin/python scripts/repair_baidu_invoice_reimbursement.py --apply  # 执行
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
except ImportError:
    pass

from sqlalchemy import select  # noqa: E402

from db.models import Invoice, JournalEntry, JournalLine, LegacyEntryProjection  # noqa: E402
from db.session import session_scope  # noqa: E402
from services.account_mapper import get_voucher_hint  # noqa: E402
from services.accounting_core_service import (  # noqa: E402
    _rebind_invoice_after_repost,
    create_entry,
    reverse_journal,
    serialize_journal,
)

COMPANY_ID = "c_87c2d1fe2dd8"
OPERATOR = "u_f70aacab854b"
CURRENT_ENTRY_ID = "correct-f4b23ac3-d8d-187ec0"
INVOICE_NUMBER = "26117000001510338701"
STATEMENT_ID = "bs-c_87c2d1fe2dd8-1795284934132318-6"
AMOUNT = Decimal("1794.00")
CATEGORY = "信息技术服务费"
REASON = "付款方更正：该发票由老板垫付后报销，原凭证误记为公司银行直接支付"


async def _lines(session) -> list[tuple[str, str, Decimal]]:
    rows = (
        await session.execute(
            JournalLine.__table__.select()
            .where(JournalLine.journal_entry_id == CURRENT_ENTRY_ID)
            .order_by(JournalLine.line_no)
        )
    ).mappings()
    return [(str(r["direction"]), str(r["account_code"]), Decimal(str(r["amount"]))) for r in rows]


async def _statement(session) -> dict:
    from sqlalchemy import text

    row = (
        await session.execute(
            text("SELECT id, amount, matched_entry_id, transaction_date, description FROM bank_statements "
                 "WHERE company_id=:company AND id=:statement"),
            {"company": COMPANY_ID, "statement": STATEMENT_ID},
        )
    ).mappings().first()
    if row is None:
        raise SystemExit(f"✗ 银行流水 {STATEMENT_ID} 不存在")
    return dict(row)


async def _invoice(session) -> Invoice:
    invoice = (
        await session.execute(
            select(Invoice).where(
                Invoice.company_id == COMPANY_ID,
                Invoice.invoice_number == INVOICE_NUMBER,
            )
        )
    ).scalars().first()
    if invoice is None:
        raise SystemExit(f"✗ 发票 {INVOICE_NUMBER} 不存在")
    return invoice


async def _new_payload(meta: dict, entry: JournalEntry) -> dict:
    payload = {**meta}
    for drop_key in (
        "id", "voucherLines", "voucherNo", "voucher_no", "dedupFingerprint",
        "wechat_transaction_key", "invoiceNumber", "allowBackfill", "backfillReason",
    ):
        payload.pop(drop_key, None)
    payload.update(
        {
            "id": f"correct-{CURRENT_ENTRY_ID[:12]}-{uuid4().hex[:6]}",
            "title": meta.get("title") or entry.summary,
            "amount": str(AMOUNT),
            "direction": "expense",
            "category": CATEGORY,
            "occurDate": entry.occurred_on.isoformat(),
            "payer": "boss_personal",
            "source": str(meta.get("source") or "invoice_import"),
            "reason": REASON,
            "voucherLines": get_voucher_hint("expense", CATEGORY, AMOUNT, "boss_personal"),
        }
    )
    return payload


async def dry_run() -> None:
    async with session_scope() as session:
        entry = await session.get(JournalEntry, CURRENT_ENTRY_ID)
        if entry is None or entry.company_id != COMPANY_ID:
            raise SystemExit(f"✗ 当前凭证 {CURRENT_ENTRY_ID} 不存在")
        invoice = await _invoice(session)
        statement = await _statement(session)
        print(f"当前凭证: {entry.id} / {entry.status} / {entry.entry_type} / {entry.occurred_on}")
        for direction, code, amount in await _lines(session):
            print(f"  {direction:6} {code} {amount}")
        print(f"发票绑定: {invoice.invoice_number} -> {invoice.entry_id}")
        print(f"银行流水: {statement['transaction_date']} {statement['amount']} 匹配={statement['matched_entry_id'] or '未匹配'}")
        print("\n计划:")
        print("  1) 红冲当前凭证（reversal，账上净额归零）")
        print("  2) 新建已过账凭证：借 5602015  / 贷 2241 其他应付款（payer=boss_personal）")
        print(f"  3) 发票 {invoice.invoice_number} 的 entry_id 迁到新凭证")
        print("  4) 之后在银行页对该流水确认配对，生成 借2241/贷1002 的报销付款凭证")
        if invoice.entry_id != CURRENT_ENTRY_ID:
            raise SystemExit(f"✗ 发票当前指向 {invoice.entry_id}，不是待更正的凭证，停止")
        if statement["matched_entry_id"]:
            raise SystemExit(f"✗ 流水已匹配 {statement['matched_entry_id']}，请先取消关联，停止")
        if Decimal(str(statement["amount"])) != -AMOUNT:
            raise SystemExit(f"✗ 流水金额 {statement['amount']} 与发票金额不一致，停止")


async def apply() -> None:
    async with session_scope() as session:
        entry = await session.get(JournalEntry, CURRENT_ENTRY_ID, with_for_update=True)
        if entry is None or entry.company_id != COMPANY_ID:
            raise SystemExit(f"✗ 当前凭证 {CURRENT_ENTRY_ID} 不存在")
        invoice = await _invoice(session)
        if invoice.entry_id != CURRENT_ENTRY_ID:
            print(f"↷ 发票已指向 {invoice.entry_id}，跳过")
            return
        if entry.status != "posted" or entry.entry_type != "standard":
            raise SystemExit(f"✗ 当前凭证状态 {entry.status}/{entry.entry_type}，需 posted/standard")
        meta = dict(entry.metadata_json or {})
        if str(meta.get("payer") or "") != "company_account":
            print(f"↷ 当前凭证付款方已是 {meta.get('payer')}，跳过")
            return
        if Decimal(str(meta.get("amount") or "0")) != AMOUNT:
            raise SystemExit("✗ 凭证金额与预期不符，停止")

        reversal = await reverse_journal(
            session,
            CURRENT_ENTRY_ID,
            company_id=COMPANY_ID,
            user_id=OPERATOR,
            request_id=f"repair-baidu-reimburse:{CURRENT_ENTRY_ID}:rev",
            reason=REASON,
        )
        payload = await _new_payload(meta, entry)
        new_entry = await create_entry(
            session,
            payload,
            company_id=COMPANY_ID,
            user_id=OPERATOR,
            request_id=f"repair-baidu-reimburse:{CURRENT_ENTRY_ID}:new",
            confirm=True,
        )
        await _rebind_invoice_after_repost(
            session,
            company_id=COMPANY_ID,
            old_entry_id=CURRENT_ENTRY_ID,
            new_entry=new_entry,
            user_id=OPERATOR,
            request_id=f"repair-baidu-reimburse:{CURRENT_ENTRY_ID}",
        )
        # 发票号在重记 payload 中被主动剔除（外部来源键冲突），过账后补回投影
        projection = await session.get(LegacyEntryProjection, new_entry.id)
        if projection is not None:
            current = json.loads(projection.payload) if isinstance(projection.payload, str) else dict(projection.payload or {})
            current["invoiceNumber"] = INVOICE_NUMBER
            projection.payload = json.dumps(current, ensure_ascii=False)

        if new_entry.status != "posted":
            raise SystemExit(f"✗ 新凭证状态 {new_entry.status}，回滚")
        lines = sorted(
            (str(l.direction), str(l.account_code), Decimal(str(l.amount)))
            for l in (
                await session.execute(
                    JournalLine.__table__.select().where(JournalLine.journal_entry_id == new_entry.id)
                )
            ).mappings()
        )
        expected = sorted([("credit", "2241", AMOUNT), ("debit", "5602015", AMOUNT)])
        if lines != expected:
            raise SystemExit(f"✗ 新凭证分录不符合预期：{lines}，回滚")
        await session.refresh(invoice)
        if invoice.entry_id != new_entry.id:
            raise SystemExit(f"✗ 发票未迁到新凭证：{invoice.entry_id}，回滚")
        serialized = await serialize_journal(session, new_entry)
        if serialized.get("voucherLines", {}).get("credit", [{}])[0].get("accountCode") != "2241":
            raise SystemExit("✗ 投影分录不是 2241，回滚")
        print(f"✓ 红冲 {CURRENT_ENTRY_ID} -> {reversal.id}")
        print(f"✓ 新凭证 {new_entry.id} / {serialized.get('voucherRef')}")
        for direction, code, amount in lines:
            print(f"   {direction:6} {code} {amount}")
        print(f"✓ 发票 {INVOICE_NUMBER} 已迁到 {invoice.entry_id}")
        print("下一步：在银行页把该流水与发票凭证确认配对，生成报销付款凭证（借2241/贷1002）。")


async def main() -> None:
    parser = argparse.ArgumentParser(description="更正百度云发票为老板垫付报销形态")
    parser.add_argument("--apply", action="store_true", help="实际写库；缺省为 dry-run")
    args = parser.parse_args()
    await (apply() if args.apply else dry_run())


if __name__ == "__main__":
    asyncio.run(main())
