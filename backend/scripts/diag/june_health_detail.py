#!/usr/bin/env python
# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""只读诊断：打印 6 月对账健康 amount_mismatches 的银行影响与投影状态。"""
import asyncio
import json

from services.bank_service import get_reconciliation_health
from services.database import get_conn


async def main() -> None:
    rep = await get_reconciliation_health("c_f9c4c18df32d", "2026-06")
    print("highRiskCount:", rep["summary"]["highRiskCount"], "warningCount:", rep["summary"]["warningCount"])
    for it in rep["amount_mismatches"]:
        print(it["statementId"], "stmt=", it["statementAmount"], "effect=", it["entryBankEffect"], "diff=", it["difference"])

    entry_ids = (
        "0728ab57-d071-4f25-a734-60f81b2675f7", "a7ce2a80-02df-406f-bc4d-4e219c61f0be",
        "c229d5a3-e09c-4040-af54-b29d57a0a7af", "8ac20b58-618b-4e67-89af-79def7bfca6b",
        "01b98cd7-e232-493d-89d4-46b3c731481c", "61a74819-918f-45f3-b49d-f7f93d99b594",
    )
    conn = await get_conn()
    try:
        placeholders = ",".join("?" for _ in entry_ids)
        cur = await conn.execute(
            "SELECT id,"
            f" json_extract(payload, '$.voucherLines') IS NULL AS no_voucher_lines,"
            " json_extract(payload, '$.voucherStatus') AS voucher_status,"
            " json_extract(payload, '$.billingStatus') AS billing_status,"
            f" substr(json_extract(payload, '$.voucherLines'), 1, 160) AS lines_head"
            f" FROM entries WHERE company_id = ? AND id IN ({placeholders})",
            ("c_f9c4c18df32d", *entry_ids),
        )
        for row in await cur.fetchall():
            print(dict(row))
    finally:
        await conn.close()


asyncio.run(main())
