# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""只读诊断：期初建账后「账表核对（LEDGER_TO_REPORT）」不平的取数链路。

用法::

    cd backend && ./venv/Scripts/python.exe scripts/diagnose_opening_report_mismatch.py --company c_xxx --month 2026-10

只读，不写库。逐层打印结账检查 LEDGER_TO_REPORT 用到的每一步取数：

1. 期初凭证定位（_find_entry / _find_ytd_entry）：活的是第几代、什么状态
2. get_opening_balances 返回的期初余额（现金类科目是否在内）
3. 报表发生额（journal_entries_for_reports）里 opening/YTD 凭证的进出情况
4. build_statement_data_from_entries 产出的 balance_current.cash 与检查项

用于在动手修复前确认根因；脚本本身不做任何修复。
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
except ImportError:  # python-dotenv 缺失时沿用进程环境变量
    pass

from services.accounting_core_service import (  # noqa: E402
    journal_entries_for_reports,
    period_close_checks,
    posted_entries_for_reports,
)
from services.opening_balance_service import (  # noqa: E402
    _find_entry,
    _find_ytd_entry,
    get_opening_balances,
)
from services.tax_filing_export_service import (  # noqa: E402
    CASH_ACCOUNT_CODES,
    _account_movements,
    build_statement_data_from_entries,
    parse_period,
)
from db.models import Company  # noqa: E402
from db.session import session_scope  # noqa: E402


async def main(company_id: str, month: str) -> None:
    async with session_scope() as session:
        month_start = parse_period(month, "month").start

        entry = await _find_entry(session, company_id, month_start.year)
        ytd = await _find_ytd_entry(session, company_id, month_start.year)
        print("== 1. 期初凭证定位 ==")
        print(f"  主凭证: {entry.id if entry else None} status={entry.status if entry else None}")
        print(f"  YTD凭证: {ytd.id if ytd else None} status={ytd.status if ytd else None}")

        opening_items = await get_opening_balances(company_id, month_start.year, session=session)
        cash_items = [i for i in opening_items if i["account_code"] in CASH_ACCOUNT_CODES]
        print("\n== 2. get_opening_balances ==")
        print(f"  共 {len(opening_items)} 个科目，其中现金类 {len(cash_items)} 条")
        for item in cash_items:
            print(f"    {item['account_code']} {item['account_name']}: 借 {item['debit_balance']} / 贷 {item['credit_balance']}")
        if entry is None:
            print("  !! 没有找到活期初凭证 —— 期初余额映射为空")

        report_entries = await posted_entries_for_reports(session, company_id)
        types: dict[str, int] = {}
        for e in report_entries:
            types[str(e.get("entryType"))] = types.get(str(e.get("entryType")), 0) + 1
        print("\n== 3. 报表发生额 entryType 分布 ==")
        print(f"  {types}（共 {len(report_entries)} 张）")
        has_entry_type = sum(1 for e in report_entries if "entryType" in e)
        print(f"  带 entryType 键的 {has_entry_type} 张 / {len(report_entries)} 张")

        opening_map_codes = {i["account_code"] for i in opening_items}
        movements_ytd = _account_movements(
            report_entries, parse_period(month, "month").fiscal_year_start,
            parse_period(month, "month").end, include_ytd_opening=True,
        )
        print("\n== 4. 本年累计发生额（报表口径）现金类 ==")
        for code in sorted(CASH_ACCOUNT_CODES):
            info = movements_ytd.get(code)
            print(f"    {code}: {'借 %s / 贷 %s' % (info['debit'], info['credit']) if info else '无发生额'}")
        print(f"  期初映射科目数: {len(opening_map_codes)}")

        company = await session.get(Company, company_id)
        statement = build_statement_data_from_entries(
            report_entries, opening_items, parse_period(month, "month"),
            (company.payload or {}) if company else {},
        )
        print("\n== 5. 报表产出 ==")
        print(f"  balance_current.cash = {statement.balance_current.get('cash')}")
        print(f"  balance_current.asset_total = {statement.balance_current.get('asset_total')}")
        print(f"  balance_current.liability_equity_total = {statement.balance_current.get('liability_equity_total')}")
        print(f"  profit_ytd.net_profit = {statement.profit_ytd.get('net_profit')}")
        print("  -- balance_current 非零行 --")
        skip = {"asset_total", "liability_equity_total", "current_asset_total", "non_current_asset_total",
                "current_liability_total", "non_current_liability_total", "liability_total", "equity_total"}
        for key, val in statement.balance_current.items():
            if key not in skip and val:
                print(f"    {key} = {val}")
        print("  -- profit_ytd 非零行 --")
        for key, val in statement.profit_ytd.items():
            if val:
                print(f"    {key} = {val}")

        checks = await period_close_checks(session, company_id, month)
        ledger_to_report = next(c for c in checks["checks"] if c["code"] == "LEDGER_TO_REPORT")
        print("\n== 6. 结账检查 LEDGER_TO_REPORT ==")
        for key in ("status", "reportDebit", "reportCredit", "ledgerCash", "reportCash"):
            print(f"  {key} = {ledger_to_report.get(key)}")
        for issue in ledger_to_report.get("issues") or []:
            print(f"  issue: {issue.get('message')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", required=True)
    parser.add_argument("--month", default="2026-10")
    args = parser.parse_args()
    asyncio.run(main(args.company, args.month))
