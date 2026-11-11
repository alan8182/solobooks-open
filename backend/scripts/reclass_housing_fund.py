# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""存量工资负债重分类：单位公积金从 221102 迁到 221105（一次性调整）。

背景：工资模块拆账前，计提凭证把「单位社保 + 单位公积金」合并贷记 221102，
221105 应付职工薪酬-住房公积金 闲置。增量代码修复后新计提直接拆
221102（社保）/221105（公积金）、缴纳分别清账，本脚本只补存量——

对每公司统计「已计提且公积金凭证未缴纳」工资表的 company_housing_fund_total
合计（缓存列由计提路径回写；已缴公积金的月份负债已被清偿，不搬），生成
调整凭证：借 221102 / 贷 221105，落最早未关账期间。

按「未来适用法」处理，不追溯改写历史凭证（posted 不可变是硬约束；221102
历史上承载公积金是当时的设计而非编码错误，不适用 migrate_coa_standard 式
直改历史）。纯负债→负债重分类，不影响损益与权益，不需要 3104006 路线。

幂等：dedupFingerprint = hf_reclass:{company}:v1（PERIOD_CLOSED 兜底重试时
追加 :re 后缀），重复执行命中来源唯一性校验并跳过。
默认 dry-run 只打印计划，--apply 才落库。
用法：
    python scripts/reclass_housing_fund.py [--dry-run] [--apply] [--company c1]
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# scripts 目录自身也进 path：复用 migrate_vat_separation 的期间兜底助手时，
# 直接从 backend 根（pytest / docker exec 任意 cwd）加载都要能找到同目录脚本
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import select  # noqa: E402

from core.money import ZERO, money  # noqa: E402
from db.models import Company, SourceTransaction  # noqa: E402
from db.session import session_scope  # noqa: E402
from services.accounting_core_service import (  # noqa: E402
    DUPLICATE_SOURCE,
    PERIOD_CLOSED,
    AccountingError,
    create_entry,
)

# 复用价税分离迁移的期间兜底助手，口径与既有调整凭证一致
from migrate_vat_separation import _earliest_open_period_start  # noqa: E402

SS_ACCOUNT = ("221102", "应付职工薪酬-单位社保")
HOUSING_ACCOUNT = ("221105", "应付职工薪酬-住房公积金")
ACTIVE_VOUCHER_STATUSES = {"posted", "pending_review"}
SOURCE_TYPE = "housing_fund_reclass"


def _fingerprint(company_id: str, retried: bool) -> str:
    return f"hf_reclass:{company_id}:v1" + (":re" if retried else "")


async def _entry_status_map(session, company_id: str, entry_ids: list[str]) -> dict[str, str]:
    if not entry_ids:
        return {}
    from db.models import JournalEntry

    rows = (await session.execute(
        select(JournalEntry.id, JournalEntry.status).where(
            JournalEntry.company_id == company_id,
            JournalEntry.id.in_(entry_ids),
        )
    )).all()
    return {row.id: row.status for row in rows}


async def _liability_balances(session, company_id: str) -> dict[str, Decimal]:
    """221102/221105 净余额（贷方为正）。

    口径与 test_salary_module._account_net_balance 一致：posted + reversed
    都计入（冲销凭证原地对冲，只算 posted 会把「冲销后归零」算成「翻正」）。
    """
    from sqlalchemy import text

    balances = {"221102": ZERO, "221105": ZERO}
    rows = (await session.execute(
        text(
            "SELECT jl.account_code AS code, "
            "SUM(CASE WHEN jl.direction = 'credit' THEN jl.amount ELSE -jl.amount END) AS net "
            "FROM journal_lines jl "
            "JOIN journal_entries je ON jl.journal_entry_id = je.id "
            "WHERE je.company_id = :cid AND jl.company_id = :cid "
            "AND je.status IN ('posted','reversed') AND jl.account_code IN ('221102','221105') "
            "GROUP BY jl.account_code"
        ),
        {"cid": company_id},
    )).mappings().all()
    for row in rows:
        if row["code"] in balances:
            balances[row["code"]] = money(row["net"] or ZERO)
    return balances


async def _pending_housing_fund_total(session, company_id: str) -> tuple[Decimal, list[dict]]:
    """Σ「旧口径计提且公积金凭证未缴纳」工资表的 company_housing_fund_total。

    排除三类：计提凭证失效（冲销/删除，负债已随冲销消失）、公积金凭证已有效
    缴纳（负债已被清偿）、计提凭证已是拆账后新口径（贷方含 221105 行，公积金
    已正确落位）——第三类不排除会被二次搬移。221102 历史上承载公积金是当时
    设计，凭证口径以行是否存在 221105 贷方为准，不依赖 ruleVersion。
    """
    from sqlalchemy import text

    rows = (await session.execute(
        text(
            "SELECT month, company_housing_fund_total, accrual_voucher_id, hf_paid_voucher_id "
            "FROM salary_sheets WHERE company_id = :cid"
        ),
        {"cid": company_id},
    )).mappings().all()
    if not rows:
        return ZERO, []

    accrual_ids = [str(r["accrual_voucher_id"]) for r in rows if r["accrual_voucher_id"]]
    paid_ids = [str(r["hf_paid_voucher_id"]) for r in rows if r["hf_paid_voucher_id"]]
    statuses = await _entry_status_map(session, company_id, sorted(set(accrual_ids + paid_ids)))

    # 拆账后新口径的计提凭证含 221105 贷方行；这些 sheet 的公积金已在正确科目
    legacy_accrual_ids: set[str] = set()
    for entry_id, status in statuses.items():
        if status not in ACTIVE_VOUCHER_STATUSES or entry_id not in accrual_ids:
            continue
        has_housing_credit = (await session.execute(
            text(
                "SELECT 1 FROM journal_lines "
                "WHERE journal_entry_id = :eid AND company_id = :cid "
                "AND account_code = '221105' AND direction = 'credit' LIMIT 1"
            ),
            {"eid": entry_id, "cid": company_id},
        )).first()
        if has_housing_credit is None:
            legacy_accrual_ids.add(entry_id)

    detail: list[dict] = []
    total = ZERO
    for r in rows:
        hf = money(r["company_housing_fund_total"] or ZERO)
        if hf <= ZERO:
            continue
        accrual_id = str(r["accrual_voucher_id"] or "")
        accrual_active = accrual_id in statuses and statuses[accrual_id] in ACTIVE_VOUCHER_STATUSES
        paid_voucher = str(r["hf_paid_voucher_id"] or "")
        hf_paid_active = bool(paid_voucher) and (
            paid_voucher in statuses and statuses[paid_voucher] in ACTIVE_VOUCHER_STATUSES
        )
        if not accrual_active or hf_paid_active or accrual_id not in legacy_accrual_ids:
            continue
        total = money(total + hf)
        detail.append({"month": str(r["month"]), "amount": float(hf)})
    return total, detail


async def _already_adjusted(session, company_id: str) -> bool:
    fingerprints = {_fingerprint(company_id, False), _fingerprint(company_id, True)}
    row = await session.execute(
        select(SourceTransaction.id).where(
            SourceTransaction.company_id == company_id,
            SourceTransaction.external_key.in_(fingerprints),
        )
    )
    return row.first() is not None


async def reclass(dry_run: bool, only_company: str | None = None) -> None:
    report: dict[str, list] = {
        "reclassified": [],
        "skipped_zero": [],
        "skipped_adjusted": [],
    }

    async with session_scope() as session:
        companies = (await session.execute(select(Company))).scalars().all()
        for company in companies:
            if only_company and company.company_id != only_company:
                continue
            company_id = company.company_id
            if await _already_adjusted(session, company_id):
                report["skipped_adjusted"].append(company_id)
                continue

            pending, detail = await _pending_housing_fund_total(session, company_id)
            before = await _liability_balances(session, company_id)
            if pending <= ZERO:
                report["skipped_zero"].append(
                    {"company_id": company_id, "balances": {k: float(v) for k, v in before.items()}}
                )
                continue

            fallback = date.today()
            occurred = await _earliest_open_period_start(session, company_id, fallback)
            payload = {
                "id": f"hf-reclass-{company_id}-v1",
                "direction": "owner_transfer",
                "amount": pending,
                "title": "住房公积金应付重分类（社保/公积金分账）",
                "category": "工资公积金重分类",
                "occurDate": occurred.isoformat(),
                "sourceType": SOURCE_TYPE,
                "dedupFingerprint": _fingerprint(company_id, False),
                "entryType": "standard",
                "ruleVersion": "housing-fund-reclass-v1",
                "voucherLines": {
                    "debit": [{"accountCode": SS_ACCOUNT[0], "accountName": SS_ACCOUNT[1], "amount": pending}],
                    "credit": [{"accountCode": HOUSING_ACCOUNT[0], "accountName": HOUSING_ACCOUNT[1], "amount": pending}],
                },
            }
            after = {
                "221102": money(before["221102"] - pending),
                "221105": money(before["221105"] + pending),
            }
            plan = {
                "company_id": company_id,
                "amount": float(pending),
                "occurDate": occurred.isoformat(),
                "pendingByMonth": detail,
                "balancesBefore": {k: float(v) for k, v in before.items()},
                "balancesAfter": {k: float(v) for k, v in after.items()},
            }
            if dry_run:
                report["reclassified"].append({**plan, "applied": False})
                continue
            try:
                await create_entry(
                    session, payload,
                    company_id=company_id, user_id=company.owner_user_id,
                    request_id=f"reclass-housing-fund-{company_id}", confirm=True,
                )
                report["reclassified"].append({**plan, "applied": True})
            except AccountingError as exc:
                if getattr(exc, "code", "") == DUPLICATE_SOURCE:
                    report["skipped_adjusted"].append(company_id)
                    continue
                if getattr(exc, "code", "") == PERIOD_CLOSED:
                    # 兜底：期间状态在读取后被并发关闭 → 重算最早开放期间、
                    # 指纹追加 :re 再试一次（对齐 migrate_vat_separation 兜底）
                    retried_date = await _earliest_open_period_start(session, company_id, fallback)
                    payload["occurDate"] = retried_date.isoformat()
                    payload["dedupFingerprint"] = _fingerprint(company_id, True)
                    payload["id"] = f"hf-reclass-{company_id}-v1-re"
                    await create_entry(
                        session, payload,
                        company_id=company_id, user_id=company.owner_user_id,
                        request_id=f"reclass-housing-fund-{company_id}-re", confirm=True,
                    )
                    report["reclassified"].append({**plan, "applied": True, "retriedPeriodClosed": True})
                    continue
                raise

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        ("DRY-RUN（未执行，--apply 落库）" if dry_run else "已执行")
        + f"：重分类 {len(report['reclassified'])}、"
        + f"无待清公积金跳过 {len(report['skipped_zero'])}、"
        + f"已有调整凭证跳过 {len(report['skipped_adjusted'])}"
    )
    return report


if __name__ == "__main__":
    args = sys.argv[1:]
    company = None
    if "--company" in args:
        company = args[args.index("--company") + 1]
    asyncio.run(reclass(dry_run="--apply" not in args, only_company=company))
