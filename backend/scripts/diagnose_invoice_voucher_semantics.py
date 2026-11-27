# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""只读诊断：发票凭证的「付款去向」与「分类」语义冲突。

用法::

    cd backend && venv/bin/python scripts/diagnose_invoice_voucher_semantics.py
    cd backend && venv/bin/python scripts/diagnose_invoice_voucher_semantics.py --company c_xxx

只读，不写库。输出四类问题：

1. ``BANK_DIRECT_INVOICE``：发票凭证贷 1002（银行直付）且**没有**银行核销关联。
   正常形态应是费用挂 2241/2202，银行付款由流水配对生成结算凭证；这类凭证
   会让账面银行存款凭空减少。已关联流水的是历史「发票+付款合一」形态，不计入。
2. ``PERSON_ADVANCE_SELLER_AUX``：老板/员工垫付的发票，2241 的往来单位却挂成了
   供应商——公司欠的是垫付人，不是卖家。
3. ``CATEGORY_MISMATCH``：落进「其他支出」的发票，品名其实命中确定性分类规则。
4. ``CATEGORY_FALLBACK``：导入时没有分类依据、仍落到兜底科目的凭证（需人工复核）。

脚本不做任何修复；修复必须另行写幂等脚本并在执行前确认。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
except ImportError:  # python-dotenv 缺失时沿用进程环境变量
    pass

from sqlalchemy import select, text  # noqa: E402

from db.models import JournalEntry, JournalLine, ReconciliationLink  # noqa: E402
from db.session import session_scope  # noqa: E402
from services.invoice_service import match_category_by_item  # noqa: E402


def _payload(raw: object) -> dict:
    try:
        return json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _money(value: object) -> str:
    try:
        return f"{Decimal(str(value)):,.2f}"
    except (ArithmeticError, TypeError, ValueError):
        return str(value)


def _is_invoice_entry(entry: JournalEntry, meta: dict) -> bool:
    return (
        str(meta.get("source") or meta.get("sourceType") or "") == "invoice_import"
        or bool(str(meta.get("invoiceNumber") or "").strip())
    )


def _is_person_advance(meta: dict) -> bool:
    payer = str(meta.get("payer") or "")
    return payer == "boss_personal" or payer.startswith("employee_personal")


async def _companies(company: str | None) -> list[str]:
    if company:
        return [company]
    async with session_scope() as session:
        rows = (
            await session.execute(
                text("SELECT DISTINCT company_id FROM journal_entries ORDER BY company_id")
            )
        ).fetchall()
    return [str(row[0]) for row in rows]


async def _diagnose(company_id: str) -> dict[str, list[dict]]:
    findings: dict[str, list[dict]] = {
        "BANK_DIRECT_INVOICE": [],
        "PERSON_ADVANCE_SELLER_AUX": [],
        "CATEGORY_MISMATCH": [],
        "CATEGORY_FALLBACK": [],
    }
    async with session_scope() as session:
        entries = (
            await session.execute(
                select(JournalEntry).where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.entry_type == "standard",
                )
            )
        ).scalars().all()
        if not entries:
            return findings

        entry_ids = [entry.id for entry in entries]
        lines_by_entry: dict[str, list[JournalLine]] = {}
        for line in (
            await session.execute(
                select(JournalLine).where(JournalLine.journal_entry_id.in_(entry_ids))
            )
        ).scalars().all():
            lines_by_entry.setdefault(line.journal_entry_id, []).append(line)

        linked_ids = set(
            (
                await session.execute(
                    select(ReconciliationLink.journal_entry_id).where(
                        ReconciliationLink.company_id == company_id,
                        ReconciliationLink.status == "confirmed",
                    )
                )
            ).scalars().all()
        )

        for entry in entries:
            meta = _payload(entry.metadata_json)
            if not _is_invoice_entry(entry, meta):
                continue
            lines = lines_by_entry.get(entry.id, [])
            base = {
                "id": entry.id,
                "status": entry.status,
                "date": str(entry.occurred_on),
                "summary": entry.summary,
                "amount": _money(meta.get("amount") or 0),
                "category": str(meta.get("category") or ""),
                "payer": str(meta.get("payer") or ""),
                "invoiceNumber": str(meta.get("invoiceNumber") or ""),
            }

            credit_codes = {str(line.account_code) for line in lines if line.direction == "credit"}
            if (
                "1002" in credit_codes
                and not entry.id.startswith("reim-")
                and entry.id not in linked_ids
            ):
                findings["BANK_DIRECT_INVOICE"].append({**base, "bankLinked": False})

            if _is_person_advance(meta):
                seller = str(
                    meta.get("merchant") or meta.get("seller_name") or ""
                ).strip()
                for line in lines:
                    if line.direction != "credit" or not str(line.account_code).startswith("2241"):
                        continue
                    partner = str((line.auxiliary or {}).get("partner_name") or "").strip()
                    if partner and seller and partner == seller:
                        findings["PERSON_ADVANCE_SELLER_AUX"].append(
                            {**base, "accountCode": str(line.account_code), "auxPartner": partner}
                        )
                        break

            item_name = str(meta.get("item_name") or "")
            direction = str(meta.get("direction") or "expense")
            suggested = match_category_by_item(item_name, direction)
            if suggested and suggested != base["category"] and base["category"] in (
                "其他支出",
                "管理费用-其他",
            ):
                findings["CATEGORY_MISMATCH"].append(
                    {**base, "itemName": item_name, "suggestedCategory": suggested}
                )

            if str(meta.get("categorySource") or "") == "fallback":
                findings["CATEGORY_FALLBACK"].append({**base, "itemName": item_name})

    return findings


def _print_company(company_id: str, findings: dict[str, list[dict]]) -> int:
    total = sum(len(items) for items in findings.values())
    print(f"\n=== {company_id} ===")
    if total == 0:
        print("  ✓ 未发现付款去向/分类语义冲突")
        return 0
    for kind, items in findings.items():
        if not items:
            continue
        print(f"\n  {kind}（{len(items)}）")
        for item in items:
            print(
                f"    - {item['date']} {item['status']:8} {item['id']} "
                f"{item['amount']:>12} {item['category'] or '—'} / {item['payer'] or '—'} "
                f"{item['summary']}"
            )
            for key in ("itemName", "suggestedCategory", "auxPartner", "bankLinked"):
                if key in item:
                    print(f"        {key}: {item[key]}")
    return total


async def main() -> None:
    parser = argparse.ArgumentParser(description="只读诊断发票凭证的付款去向与分类语义")
    parser.add_argument("--company", help="只诊断指定企业 company_id")
    args = parser.parse_args()

    companies = await _companies(args.company)
    if not companies:
        print("没有可诊断的企业账套")
        return

    grand_total = 0
    for company_id in companies:
        try:
            findings = await _diagnose(company_id)
        except Exception as exc:  # 单租户失败不阻断其他账套
            print(f"\n=== {company_id} ===\n  ! 诊断失败：{exc}")
            continue
        grand_total += _print_company(company_id, findings)

    print(f"\n合计待复核 {grand_total} 条。本脚本只读，未修改任何数据。")


if __name__ == "__main__":
    asyncio.run(main())
