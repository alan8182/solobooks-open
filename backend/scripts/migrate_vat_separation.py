# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""历史含税收入的价税分离回补（一次性迁移）。

背景：历史版本的销售收入按价税合计全额计入 5001/5051，应交增值税从未挂账。
已过账凭证不可修改，也不允许反结账重开（会破坏关账快照与审计链），
因此按"未来适用法"生成调整凭证，不追溯重述历史报表。

处理规则（按公司增值税申报周期分桶，默认季报、1%、季 30 万起征点）：
1. 桶内不含税销售额 < 起征点 → 免税，收款额本身就是不含税收入，无需调整；
2. 桶内不含税销售额 >= 起征点 → 补提增值税：
   - 本年桶：借 各收入科目（按科目分摊税额）/ 贷 2221026
   - 以前年度桶，或桶内期间已关账：借 3104006 未分配利润 / 贷 2221026
3. 已做价税分离（payload.vatSeparated 或凭证含 2221026 行）的凭证一律跳过。

幂等：每桶一张凭证，dedupFingerprint = vat_sep:{company}:{bucket}:v1。
用法：
    python scripts/migrate_vat_separation.py [--dry-run] [--company c1]
"""
from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from core.money import ZERO, money  # noqa: E402
from db.session import session_scope  # noqa: E402
from services.accounting_core_service import (  # noqa: E402
    AccountingError,
    PERIOD_CLOSED,
    create_entry,
    posted_entries_for_reports,
)
from services.vat_service import (  # noqa: E402
    VAT_PAYABLE_ACCOUNT,
    VAT_PAYABLE_ACCOUNT_NAME,
    get_vat_config,
)

RETAINED_EARNINGS_CODES = ("3104006", "3104")
REVENUE_ACCOUNT_CODES = {"5001", "5051"}


def _bucket_label(occurred: date, cycle: str) -> tuple[str, date, date]:
    """返回（桶标识，桶起始日，桶结束日）。"""
    if cycle == "month":
        end_day = _month_end(occurred)
        return f"{occurred.year}-{occurred.month:02d}", date(occurred.year, occurred.month, 1), end_day
    quarter = (occurred.month - 1) // 3 + 1
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    return (
        f"{occurred.year}Q{quarter}",
        date(occurred.year, start_month, 1),
        date(occurred.year, end_month, _month_end(date(occurred.year, end_month, 1)).day),
    )


def _month_end(day: date) -> date:
    import calendar

    return date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])


def _voucher_vat(entry: dict) -> Decimal:
    voucher = entry.get("voucherLines") or {}
    credit = sum((money(l.get("amount") or ZERO) for l in voucher.get("credit") or []
                  if str(l.get("accountCode") or "") == VAT_PAYABLE_ACCOUNT), ZERO)
    debit = sum((money(l.get("amount") or ZERO) for l in voucher.get("debit") or []
                 if str(l.get("accountCode") or "") == VAT_PAYABLE_ACCOUNT), ZERO)
    return money(credit - debit)


def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


async def _earliest_open_period_start(session, company_id: str, fallback: date) -> date:
    from db.models import AccountingPeriod

    rows = (await session.execute(
        select(AccountingPeriod).where(AccountingPeriod.company_id == company_id)
    )).scalars().all()
    open_starts = sorted(p.period_start for p in rows if p.status in ("open", "reopened"))
    return open_starts[0] if open_starts else fallback


async def migrate(dry_run: bool = False, only_company: str | None = None) -> None:
    from db.models import Company

    async with session_scope() as session:
        companies = (await session.execute(select(Company))).scalars().all()
        report: dict[str, list] = {"adjusted_same_year": [], "adjusted_retained": [], "skipped_exempt": [], "skipped_companies": []}

        for company in companies:
            if only_company and company.company_id != only_company:
                continue
            try:
                config = get_vat_config(company.payload or {})
            except ValueError as exc:
                report["skipped_companies"].append(f"{company.company_id}: {exc}")
                continue

            entries = await posted_entries_for_reports(session, company.company_id)
            buckets: dict[str, dict] = defaultdict(lambda: {"gross": defaultdict(Decimal), "end": None, "year": None})
            for entry in entries:
                if entry.get("entryType") in ("opening", "closing") or entry.get("isClosingTransfer"):
                    continue
                if entry.get("vatSeparated") or _voucher_vat(entry) != ZERO:
                    continue
                occurred = _parse_date(entry.get("occurDate"))
                if occurred is None:
                    continue
                voucher = entry.get("voucherLines") or {}
                per_account = defaultdict(Decimal)
                for side in ("credit", "debit"):
                    sign = Decimal("1") if side == "credit" else Decimal("-1")
                    for line in voucher.get(side) or []:
                        code = str(line.get("accountCode") or "")
                        if code in REVENUE_ACCOUNT_CODES:
                            per_account[code] += sign * money(line.get("amount") or ZERO)
                for code, amount in list(per_account.items()):
                    if amount == ZERO:
                        per_account.pop(code)
                if not per_account:
                    continue
                label, _, bucket_end = _bucket_label(occurred, config.filing_cycle)
                bucket = buckets[label]
                bucket["end"] = bucket_end
                bucket["year"] = bucket_end.year
                for code, amount in per_account.items():
                    bucket["gross"][code] += amount

            for label, bucket in sorted(buckets.items()):
                gross_total = sum(bucket["gross"].values(), ZERO)
                net_total = money(gross_total / (Decimal("1") + config.rate))
                if net_total < config.threshold_for(config.filing_cycle):
                    report["skipped_exempt"].append(f"{company.company_id} {label}: 免税，无需调整")
                    continue

                vat_total = money(gross_total - net_total)
                occurred = bucket["end"]
                same_year = bucket["year"] == date.today().year
                lines: list[dict] = []
                if same_year:
                    per_account_tax = {}
                    for code, gross in bucket["gross"].items():
                        net_a = money(gross / (Decimal("1") + config.rate))
                        per_account_tax[code] = money(gross - net_a)
                    for code, amount in sorted(per_account_tax.items()):
                        if amount > ZERO:
                            lines.append({"accountCode": code, "accountName": "", "amount": amount})
                if not same_year or not lines:
                    occurred = await _earliest_open_period_start(session, company.company_id, occurred)
                    lines = [{"accountCode": "3104006", "accountName": "未分配利润", "amount": vat_total}]
                else:
                    vat_total = sum((l["amount"] for l in lines), ZERO)

                entry_id = f"vat-separation-{company.company_id}-{label}-v1"
                payload = {
                    "id": entry_id,
                    "direction": "owner_transfer",
                    "amount": vat_total,
                    "title": f"历史含税收入价税分离补提增值税（{label}）",
                    "category": "价税分离调整",
                    "occurDate": occurred.isoformat(),
                    "sourceType": "vat_separation_backfill",
                    "dedupFingerprint": f"vat_sep:{company.company_id}:{label}:v1",
                    "entryType": "standard",
                    "ruleVersion": "vat-separation-backfill-v1",
                    "voucherLines": {
                        "debit": lines,
                        "credit": [{"accountCode": VAT_PAYABLE_ACCOUNT, "accountName": VAT_PAYABLE_ACCOUNT_NAME, "amount": vat_total}],
                    },
                }
                key = f"{company.company_id} {label}: 借 {'+'.join(l['accountCode'] for l in lines)} 贷 {VAT_PAYABLE_ACCOUNT} {vat_total}"
                if dry_run:
                    (report["adjusted_same_year"] if same_year else report["adjusted_retained"]).append(key)
                    continue
                try:
                    await create_entry(
                        session, payload,
                        company_id=company.company_id, user_id=company.owner_user_id,
                        request_id=f"migrate-vat-sep-{label}", confirm=True,
                    )
                    (report["adjusted_same_year"] if same_year else report["adjusted_retained"]).append(key)
                except AccountingError as exc:
                    if exc.code == PERIOD_CLOSED:
                        payload["occurDate"] = await _earliest_open_period_start(
                            session, company.company_id, date.today()
                        )
                        payload["id"] = f"{entry_id}-re"
                        payload["dedupFingerprint"] = f"{payload['dedupFingerprint']}:re"
                        payload["voucherLines"]["debit"] = [
                            {"accountCode": "3104006", "accountName": "未分配利润", "amount": vat_total}
                        ]
                        await create_entry(
                            session, payload,
                            company_id=company.company_id, user_id=company.owner_user_id,
                            request_id=f"migrate-vat-sep-{label}-re", confirm=True,
                        )
                        report["adjusted_retained"].append(f"{key}（期间已关账，改记未分配利润）")
                    else:
                        raise

        import json as _json

        print(_json.dumps(report, ensure_ascii=False, indent=2))
        print(("DRY-RUN（未执行）" if dry_run else "已执行")
              + f"：本年收入科目调整 {len(report['adjusted_same_year'])}、"
                f"未分配利润调整 {len(report['adjusted_retained'])}、"
                f"免税跳过 {len(report['skipped_exempt'])}、"
                f"跳过公司 {len(report['skipped_companies'])}")


if __name__ == "__main__":
    args = sys.argv[1:]
    company = None
    if "--company" in args:
        company = args[args.index("--company") + 1]
    asyncio.run(migrate(dry_run="--dry-run" in args, only_company=company))
