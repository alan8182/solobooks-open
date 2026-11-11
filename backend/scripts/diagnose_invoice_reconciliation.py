# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""只读诊断：发票台账与银行核销中的「指向已冲销凭证」冲突。

用法::

    cd backend && ./.venv313/bin/python scripts/diagnose_invoice_reconciliation.py
    cd backend && ./.venv313/bin/python scripts/diagnose_invoice_reconciliation.py --company c_xxx

只读，不写库。输出三类冲突：

1. 发票主数据 entry_id 指向已冲销/不存在的凭证
2. 银行流水 matched_entry_id 指向已冲销/不存在的凭证
3. 账目 billingStatus=reconciled 但没有任何生效的银行匹配

用于在跑数据修复前确认影响面；脚本本身不做任何修复。
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
except ImportError:  # python-dotenv 缺失时沿用进程环境变量
    pass

from sqlalchemy import text  # noqa: E402

from db.session import get_engine  # noqa: E402


def _payload(raw: object) -> dict:
    try:
        return json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


async def _companies(conn, company: str | None) -> list[str]:
    if company:
        return [company]
    rows = (await conn.execute(text("SELECT company_id FROM auth_companies ORDER BY company_id"))).fetchall()
    return [str(row[0]) for row in rows]


async def _invoice_conflicts(conn, company_id: str) -> list[dict]:
    rows = (
        await conn.execute(
            text(
                """
                SELECT i.id, i.entry_id, i.invoice_number, i.invoice_date, i.seller_name,
                       i.amount_incl_tax, j.status, j.entry_type, j.summary
                FROM invoices i
                LEFT JOIN journal_entries j ON j.id = i.entry_id
                WHERE i.company_id = :company
                  AND (
                    i.entry_id IS NULL
                    OR j.id IS NULL
                    OR j.status = 'reversed'
                  )
                ORDER BY i.invoice_date, i.id
                """
            ),
            {"company": company_id},
        )
    ).fetchall()
    return [dict(row._mapping) for row in rows]


async def _statement_conflicts(conn, company_id: str) -> list[dict]:
    rows = (
        await conn.execute(
            text(
                """
                SELECT s.id, s.transaction_date, s.description, s.amount, s.matched_entry_id,
                       j.status, j.entry_type, j.summary
                FROM bank_statements s
                LEFT JOIN journal_entries j ON j.id = s.matched_entry_id
                WHERE s.company_id = :company
                  AND s.matched_entry_id IS NOT NULL
                  AND s.matched_entry_id != ''
                  AND (j.id IS NULL OR j.status = 'reversed')
                ORDER BY s.transaction_date, s.id
                """
            ),
            {"company": company_id},
        )
    ).fetchall()
    return [dict(row._mapping) for row in rows]


async def _dangling_reconciled(conn, company_id: str) -> list[dict]:
    rows = (
        await conn.execute(
            text(
                """
                SELECT e.id, e.payload AS payload
                FROM entries e
                WHERE e.company_id = :company
                  AND e.payload LIKE '%"billingStatus": "reconciled"%'
                ORDER BY e.updated_at
                """
            ),
            {"company": company_id},
        )
    ).fetchall()
    matched_rows = (
        await conn.execute(
            text(
                """
                SELECT s.matched_entry_id AS entry_id
                FROM bank_statements s
                JOIN journal_entries j ON j.id = s.matched_entry_id
                WHERE s.company_id = :company AND j.status != 'reversed'
                  AND s.matched_entry_id IS NOT NULL AND s.matched_entry_id != ''
                UNION
                SELECT m.entry_id
                FROM bank_statement_matches m
                JOIN journal_entries j ON j.id = m.entry_id
                WHERE m.company_id = :company AND j.status != 'reversed'
                """
            ),
            {"company": company_id},
        )
    ).fetchall()
    matched = {str(row[0]) for row in matched_rows if row[0]}
    conflicts = []
    for row in rows:
        entry_id = str(row[0])
        if entry_id in matched:
            continue
        payload = _payload(row[1])
        conflicts.append(
            {
                "id": entry_id,
                "occurDate": payload.get("occurDate"),
                "title": payload.get("title"),
                "amount": payload.get("amount"),
                "payer": payload.get("payer"),
                "invoiceNumber": payload.get("invoiceNumber"),
            }
        )
    return conflicts


async def diagnose(company: str | None) -> int:
    engine = get_engine()
    total = 0
    try:
        async with engine.connect() as conn:
            for company_id in await _companies(conn, company):
                invoice_conflicts = await _invoice_conflicts(conn, company_id)
                statement_conflicts = await _statement_conflicts(conn, company_id)
                dangling = await _dangling_reconciled(conn, company_id)
                if not (invoice_conflicts or statement_conflicts or dangling):
                    continue
                print(f"\n=== 账套 {company_id} ===")
                if invoice_conflicts:
                    print("[1] 发票主数据指向已冲销/缺失凭证：" + str(len(invoice_conflicts)) + " 条")
                    for row in invoice_conflicts:
                        print(
                            "    - 发票 {id} 号 {number} {date} {seller} {amount} "
                            "-> 凭证 {entry_id} 状态 {status}".format(
                                id=row["id"],
                                number=row["invoice_number"] or "(无号)",
                                date=row["invoice_date"],
                                seller=row["seller_name"],
                                amount=row["amount_incl_tax"],
                                entry_id=row["entry_id"],
                                status=row["status"] or "缺失",
                            )
                        )
                if statement_conflicts:
                    print("[2] 银行流水指向已冲销/缺失凭证：" + str(len(statement_conflicts)) + " 条")
                    for row in statement_conflicts:
                        print(
                            "    - 流水 {id} {date} {desc} {amount} -> 凭证 {entry_id} 状态 {status}".format(
                                id=row["id"],
                                date=row["transaction_date"],
                                desc=str(row["description"])[:40],
                                amount=row["amount"],
                                entry_id=row["matched_entry_id"],
                                status=row["status"] or "缺失",
                            )
                        )
                if dangling:
                    print("[3] 标记已核销但无生效银行匹配：" + str(len(dangling)) + " 条")
                    for row in dangling:
                        print(
                            "    - 账目 {id} {date} {title} {amount} 垫付方 {payer}".format(
                                id=row["id"],
                                date=row["occurDate"],
                                title=row["title"],
                                amount=row["amount"],
                                payer=row["payer"],
                            )
                        )
                total += len(invoice_conflicts) + len(statement_conflicts) + len(dangling)
    finally:
        await engine.dispose()
    if total == 0:
        print("未发现发票/银行核销指向已冲销凭证的冲突。")
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="只读诊断发票与银行核销的冲销冲突")
    parser.add_argument("--company", default=None, help="只诊断指定账套（默认全部）")
    args = parser.parse_args()
    asyncio.run(diagnose(args.company))


if __name__ == "__main__":
    main()
