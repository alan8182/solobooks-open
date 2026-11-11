# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""微信渠道存量凭证的增值税补提（一次性修复）。

背景：微信月度汇总/逐笔导入曾按含税全额贷记 5001/5051，应交增值税从未挂账
（与手动记账口径不一致）。增量代码修复后新导入已价税分离，本脚本只补存量。

处理规则（复用 migrate_vat_separation.py 的桶级口径，按公司增值税申报周期分桶）：
1. 起征点判断按桶内全部收入条目的不含税净额（已分离条目取 5001 行净额，
   未分离条目按征收率反算；旧迁移/本脚本的补提调整凭证不计入销售额）：
   桶内不含税销售额 < 起征点 → 免税，无需补提；
2. ≥ 起征点 → 只对微信来源（source=wechat_bill）的未分离条目补提增值税
   （逐条按征收率价税分离，与新版导入代码同口径，再按科目分摊汇总）：
   - 本年桶：借 各收入科目 / 贷 2221026
   - 以前年度桶，或桶内期间已关账：借 3104006 未分配利润 / 贷 2221026
3. 跳过：已价税分离（payload.vatSeparated 或凭证含 2221026 净额）的条目；
   已有 vat_sep:* / vat_sep_wechat:* 调整凭证的桶（防与旧迁移脚本双计）；
   已红冲对冲的凭证对（voucherStatus=reversed / isReversal）。

幂等：每桶一张凭证，dedupFingerprint = vat_sep_wechat:{company}:{bucket}:v1
（PERIOD_CLOSED 兜底重试时追加 :re 后缀），重复执行会命中来源唯一性校验并跳过。
默认 dry-run 只打印计划，--apply 才落库。
用法：
    python scripts/repair_wechat_vat_separation.py [--dry-run] [--apply] [--company c1]
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from core.money import ZERO, money  # noqa: E402
from db.models import SourceTransaction  # noqa: E402
from db.session import session_scope  # noqa: E402
from services.accounting_core_service import (  # noqa: E402
    DUPLICATE_SOURCE,
    PERIOD_CLOSED,
    AccountingError,
    create_entry,
    posted_entries_for_reports,
)
from services.vat_service import (  # noqa: E402
    VAT_PAYABLE_ACCOUNT,
    VAT_PAYABLE_ACCOUNT_NAME,
    get_vat_config,
)

# 复用旧迁移脚本的分桶/期间兜底助手，保证两批调整凭证的桶口径完全一致。
from migrate_vat_separation import (  # noqa: E402
    _bucket_label,
    _earliest_open_period_start,
    _parse_date,
    _voucher_vat,
)

REVENUE_ACCOUNT_CODES = {"5001", "5051"}
WECHAT_SOURCE = "wechat_bill"


def _revenue_net_by_account(entry: dict) -> dict[str, Decimal]:
    """按收入科目取净额（贷-借），零净额科目剔除。"""
    voucher = entry.get("voucherLines") or {}
    per_account: dict[str, Decimal] = defaultdict(Decimal)
    for side in ("credit", "debit"):
        sign = Decimal("1") if side == "credit" else Decimal("-1")
        for line in voucher.get(side) or []:
            code = str(line.get("accountCode") or "")
            if code in REVENUE_ACCOUNT_CODES:
                per_account[code] += sign * money(line.get("amount") or ZERO)
    return {code: amount for code, amount in per_account.items() if amount != ZERO}


async def _bucket_already_adjusted(session, company_id: str, label: str) -> bool:
    """该桶是否已有价税分离补提凭证（新旧指纹族都算，防双计）。"""
    fingerprints = {
        f"vat_sep:{company_id}:{label}:v1",
        f"vat_sep:{company_id}:{label}:v1:re",
        f"vat_sep_wechat:{company_id}:{label}:v1",
        f"vat_sep_wechat:{company_id}:{label}:v1:re",
    }
    row = await session.execute(
        select(SourceTransaction.id).where(
            SourceTransaction.company_id == company_id,
            SourceTransaction.external_key.in_(fingerprints),
        )
    )
    return row.first() is not None


async def repair(dry_run: bool, only_company: str | None = None) -> None:
    from db.models import Company

    report: dict[str, list] = {
        "adjusted_same_year": [],
        "adjusted_retained": [],
        "skipped_exempt": [],
        "skipped_adjusted_buckets": [],
        "companies_without_wechat_targets": [],
        "skipped_companies": [],
    }

    async with session_scope() as session:
        companies = (await session.execute(select(Company))).scalars().all()
        for company in companies:
            if only_company and company.company_id != only_company:
                continue
            try:
                config = get_vat_config(company.payload or {})
            except ValueError as exc:
                report["skipped_companies"].append(f"{company.company_id}: {exc}")
                continue

            entries = await posted_entries_for_reports(session, company.company_id)
            # 起征点判断用桶内全部收入条目的不含税净额（已分离条目的 5001 行本身就是
            # 不含税额，未分离条目按征收率反算）；补提额只取微信来源的未分离条目。
            bucket_net: dict[str, defaultdict] = defaultdict(lambda: defaultdict(Decimal))
            bucket_meta: dict[str, dict] = {}
            wechat_bucket_tax: dict[str, defaultdict] = defaultdict(lambda: defaultdict(Decimal))
            wechat_in_bucket: dict[str, int] = defaultdict(int)

            for entry in entries:
                if entry.get("entryType") in ("opening", "closing") or entry.get("isClosingTransfer"):
                    continue
                if entry.get("voucherStatus") == "reversed" or entry.get("isReversal"):
                    continue
                if str(entry.get("sourceType") or "") in ("vat_separation_backfill", "wechat_vat_separation_backfill"):
                    # 旧迁移/本脚本的补提调整凭证不是销售额，只防双计
                    continue
                separated = bool(entry.get("vatSeparated")) or _voucher_vat(entry) != ZERO
                occurred = _parse_date(entry.get("occurDate"))
                if occurred is None:
                    continue
                per_account = _revenue_net_by_account(entry)
                if not per_account:
                    continue
                label, _, bucket_end = _bucket_label(occurred, config.filing_cycle)
                bucket_meta[label] = {"end": bucket_end, "year": bucket_end.year}
                for code, amount in per_account.items():
                    if separated:
                        bucket_net[label][code] += amount
                    else:
                        bucket_net[label][code] += money(amount / (Decimal("1") + config.rate))
                if not separated and str(entry.get("source") or "") == WECHAT_SOURCE:
                    wechat_in_bucket[label] += 1
                    for code, gross in per_account.items():
                        net_a = money(gross / (Decimal("1") + config.rate))
                        wechat_bucket_tax[label][code] += money(gross - net_a)

            for label in sorted(bucket_meta):
                if not wechat_in_bucket.get(label):
                    continue
                net_total = sum(bucket_net[label].values(), ZERO)
                if net_total < config.threshold_for(config.filing_cycle):
                    report["skipped_exempt"].append(f"{company.company_id} {label}: 免税，无需补提")
                    continue
                if await _bucket_already_adjusted(session, company.company_id, label):
                    report["skipped_adjusted_buckets"].append(f"{company.company_id} {label}: 已有补提凭证")
                    continue

                per_account_tax = {
                    code: amount
                    for code, amount in wechat_bucket_tax[label].items()
                    if amount != ZERO
                }
                # 红字（退款）科目的负税额不进借方行，避免出现负金额凭证行；
                # 只按正税额科目补提，丢弃的负额在报告中留痕（净额 ≤ 0 即无需补提）。
                positive_tax = {
                    code: amount for code, amount in per_account_tax.items() if amount > ZERO
                }
                vat_total = money(sum(positive_tax.values(), ZERO))
                if vat_total <= ZERO:
                    report["companies_without_wechat_targets"].append(
                        f"{company.company_id} {label}: 微信条目净税额 ≤ 0，无需补提"
                    )
                    continue

                bucket = bucket_meta[label]
                occurred = bucket["end"]
                same_year = bucket["year"] == date.today().year
                lines: list[dict] = []
                if same_year:
                    lines = [
                        {"accountCode": code, "accountName": "", "amount": amount}
                        for code, amount in sorted(positive_tax.items())
                    ]
                if not same_year or not lines:
                    occurred = await _earliest_open_period_start(session, company.company_id, occurred)
                    lines = [{"accountCode": "3104006", "accountName": "未分配利润", "amount": vat_total}]
                    same_year = False

                entry_id = f"wechat-vat-separation-{company.company_id}-{label}-v1"
                payload = {
                    "id": entry_id,
                    "direction": "owner_transfer",
                    "amount": vat_total,
                    "title": f"微信渠道含税收入价税分离补提增值税（{label}）",
                    "category": "价税分离调整",
                    "occurDate": occurred.isoformat(),
                    "sourceType": "wechat_vat_separation_backfill",
                    "dedupFingerprint": f"vat_sep_wechat:{company.company_id}:{label}:v1",
                    "entryType": "standard",
                    "ruleVersion": "wechat-vat-separation-backfill-v1",
                    "voucherLines": {
                        "debit": lines,
                        "credit": [{
                            "accountCode": VAT_PAYABLE_ACCOUNT,
                            "accountName": VAT_PAYABLE_ACCOUNT_NAME,
                            "amount": vat_total,
                        }],
                    },
                }
                key = (
                    f"{company.company_id} {label}: 借 {'+'.join(line['accountCode'] for line in lines)}"
                    f" 贷 {VAT_PAYABLE_ACCOUNT} {vat_total}（{wechat_in_bucket[label]} 条微信条目）"
                )
                if dry_run:
                    (report["adjusted_same_year"] if same_year else report["adjusted_retained"]).append(key)
                    continue
                try:
                    await create_entry(
                        session, payload,
                        company_id=company.company_id, user_id=company.owner_user_id,
                        request_id=f"repair-wechat-vat-sep-{label}", confirm=True,
                    )
                    (report["adjusted_same_year"] if same_year else report["adjusted_retained"]).append(key)
                except AccountingError as exc:
                    if exc.code == DUPLICATE_SOURCE:
                        report["skipped_adjusted_buckets"].append(f"{company.company_id} {label}: 已有补提凭证")
                        continue
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
                            request_id=f"repair-wechat-vat-sep-{label}-re", confirm=True,
                        )
                        report["adjusted_retained"].append(f"{key}（期间已关账，改记未分配利润）")
                    else:
                        raise

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(("DRY-RUN（未执行，--apply 落库）" if dry_run else "已执行")
          + f"：本年收入科目调整 {len(report['adjusted_same_year'])}、"
            f"未分配利润调整 {len(report['adjusted_retained'])}、"
            f"免税跳过 {len(report['skipped_exempt'])}、"
            f"已有补提跳过 {len(report['skipped_adjusted_buckets'])}、"
            f"跳过公司 {len(report['skipped_companies'])}")


if __name__ == "__main__":
    args = sys.argv[1:]
    company = None
    if "--company" in args:
        company = args[args.index("--company") + 1]
    asyncio.run(repair(dry_run="--apply" not in args, only_company=company))
