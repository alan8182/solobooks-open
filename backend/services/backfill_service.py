# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""跨期补记的台账查询与导出。

三份清单对应三种使用者：
- 补记台账（按记账期间）：给会计自己核对「这个月有多少笔其实不是这个月的业务」。
- 跨期未处理单据：给结账前的提醒，也是会计催票/补录的工作清单。
- 跨年未处理清单：给出纳/代账做「以前年度应扣未扣支出」专项申报
  （国家税务总局公告 2012 年第 15 号第六条，追补期 5 年）。

只导出，不生成任何凭证——跨年怎么处理需要财税人员判断。
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.money import ZERO, money, money_text
from db.models import JournalEntry, JournalLine, PeriodBackfill, SourceTransaction


def _as_period(value: Any) -> date:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("period is required")
    if len(raw) == 7:
        raw = f"{raw}-01"
    return date.fromisoformat(raw[:10]).replace(day=1)


async def backfills_in_period(
    session: AsyncSession,
    company_id: str,
    posting_period: Any,
    *,
    limit: int | None = 50,
) -> dict[str, Any]:
    """某个记账期间内的补记台账。"""
    month = _as_period(posting_period)
    condition = (
        PeriodBackfill.company_id == company_id,
        PeriodBackfill.posting_period == month,
        PeriodBackfill.status == "active",
    )
    count, total = (
        await session.execute(
            select(
                func.count(PeriodBackfill.id),
                func.coalesce(func.sum(PeriodBackfill.amount), 0),
            ).where(*condition)
        )
    ).one()
    query = (
        select(PeriodBackfill, JournalEntry)
        .join(JournalEntry, JournalEntry.id == PeriodBackfill.journal_entry_id)
        .where(*condition)
        .order_by(PeriodBackfill.business_date, PeriodBackfill.created_at)
    )
    if limit:
        query = query.limit(limit)
    rows = (await session.execute(query)).all()
    return {
        "postingPeriod": month.strftime("%Y-%m"),
        "count": int(count or 0),
        "amount": money_text(money(total or 0)),
        "records": [
            {
                "entryId": ledger.journal_entry_id,
                "voucherRef": entry.voucher_no or "",
                "summary": entry.summary,
                "businessDate": ledger.business_date.isoformat(),
                "originalPeriod": ledger.original_period.strftime("%Y-%m"),
                "postingPeriod": ledger.posting_period.strftime("%Y-%m"),
                "amount": money_text(ledger.amount),
                "reason": ledger.reason,
                "source": ledger.source,
                "entryStatus": entry.status,
            }
            for ledger, entry in rows
        ],
    }


async def cross_period_pending(
    session: AsyncSession,
    company_id: str,
    before: Any,
    *,
    limit: int | None = 50,
    start_from: date | None = None,
) -> dict[str, Any]:
    """记账期间起始日之前、仍未入账的原始单据。

    判据是「没有任何凭证指向它」，而不是来源状态——来源状态只说明导入流程走到哪，
    真正决定是否已入账的是 journal_entries。

    ``start_from`` 是记账区间下限（账套启用月）。启用日期之前的原始单据不在本
    账套的记账范围，结账体检不能把它们报成「跨期未处理」——否则同一批流水会
    同时命中「跨期未处理」与「启用前数据」两条提示，一条要求补记、一条说不在
    范围，用户无从下手。导出底稿时不传，保持全量。
    """
    month = _as_period(before)
    has_entry = exists().where(JournalEntry.source_transaction_id == SourceTransaction.id)
    conditions = [
        SourceTransaction.company_id == company_id,
        SourceTransaction.occurred_on < month,
        SourceTransaction.status.notin_(("reconciled", "void")),
        ~has_entry,
    ]
    if start_from is not None:
        conditions.append(SourceTransaction.occurred_on >= start_from)
    condition = tuple(conditions)
    count, total = (
        await session.execute(
            select(
                func.count(SourceTransaction.id),
                func.coalesce(func.sum(func.abs(SourceTransaction.amount)), 0),
            ).where(*condition)
        )
    ).one()
    query = (
        select(SourceTransaction)
        .where(*condition)
        .order_by(SourceTransaction.occurred_on, SourceTransaction.created_at)
    )
    if limit:
        query = query.limit(limit)
    rows = (await session.execute(query)).scalars().all()
    return {
        "beforePeriod": month.strftime("%Y-%m"),
        "count": int(count or 0),
        "amount": money_text(money(total or 0)),
        "records": [
            {
                "sourceId": source.id,
                "businessDate": source.occurred_on.isoformat(),
                "businessPeriod": source.occurred_on.strftime("%Y-%m"),
                "description": source.description,
                "amount": money_text(source.amount),
                "sourceType": source.source_type,
            }
            for source in rows
        ],
    }


def _filing_period(month: date, cycle: str) -> str:
    """把一个月份映射到它的增值税申报所属期（月报看月，季报看季度）。"""
    if cycle == "month":
        return f"{month.year}-{month.month:02d}"
    return f"{month.year}-Q{(month.month - 1) // 3 + 1}"


async def vat_period_mismatch(
    session: AsyncSession,
    company_id: str,
    *,
    posting_period: Any = None,
    limit: int | None = 50,
) -> dict[str, Any]:
    """补记带来的「增值税申报所属期」错配。

    小规模纳税人没有进项抵扣，所以真正要盯的不是「抵扣所属期」，而是**销项**
    （纳税义务发生时间）落在哪一期：补记把凭证记到了后一个月，账上增值税跟着走到
    后一期，但纳税义务仍属于原业务期间。季报且跨季时，这会同时改写两个季度的
    销售额与起征点判断。
    """
    from services.vat_service import VAT_PAYABLE_ACCOUNT, get_vat_config_from_session

    config = await get_vat_config_from_session(session, company_id)
    condition = [
        PeriodBackfill.company_id == company_id,
        PeriodBackfill.status == "active",
        # 已冲销的原凭证不再有申报义务（冲销凭证自己会带负向税额）
        JournalEntry.status != "reversed",
    ]
    if posting_period is not None:
        condition.append(PeriodBackfill.posting_period == _as_period(posting_period))

    rows = (
        await session.execute(
            select(PeriodBackfill, JournalEntry)
            .join(JournalEntry, JournalEntry.id == PeriodBackfill.journal_entry_id)
            .where(*condition)
            .order_by(PeriodBackfill.business_date, PeriodBackfill.created_at)
        )
    ).all()
    if not rows:
        return {
            "filingCycle": config.filing_cycle,
            "count": 0,
            "amount": "0.00",
            "records": [],
        }

    entry_ids = [ledger.journal_entry_id for ledger, _entry in rows]
    vat_rows = (
        await session.execute(
            select(JournalLine.journal_entry_id, JournalLine.direction, JournalLine.amount).where(
                JournalLine.journal_entry_id.in_(entry_ids),
                JournalLine.account_code == VAT_PAYABLE_ACCOUNT,
            )
        )
    ).all()
    vat_by_entry: dict[str, Decimal] = {}
    for entry_id, direction, amount in vat_rows:
        signed = money(amount) if direction == "credit" else -money(amount)
        vat_by_entry[entry_id] = vat_by_entry.get(entry_id, ZERO) + signed

    records: list[dict[str, Any]] = []
    for ledger, entry in rows:
        vat_amount = vat_by_entry.get(ledger.journal_entry_id, ZERO)
        if vat_amount == ZERO:
            continue  # 不含增值税的业务（费用票、往来款）不涉及申报所属期
        business_filing = _filing_period(ledger.original_period, config.filing_cycle)
        posting_filing = _filing_period(ledger.posting_period, config.filing_cycle)
        if business_filing == posting_filing:
            continue
        records.append(
            {
                "entryId": ledger.journal_entry_id,
                "voucherRef": entry.voucher_no or "",
                "summary": entry.summary,
                "businessDate": ledger.business_date.isoformat(),
                "businessPeriod": ledger.original_period.strftime("%Y-%m"),
                "postingPeriod": ledger.posting_period.strftime("%Y-%m"),
                "businessFilingPeriod": business_filing,
                "postingFilingPeriod": posting_filing,
                "vatAmount": money_text(vat_amount),
                "reason": ledger.reason,
            }
        )

    if limit:
        records = records[:limit]
    total = sum((money(item["vatAmount"]) for item in records), ZERO)
    return {
        "filingCycle": config.filing_cycle,
        "count": len(records),
        "amount": money_text(total),
        "records": records,
    }


def vat_period_csv(payload: dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    cycle_label = "月报" if payload.get("filingCycle") == "month" else "季报"
    writer.writerow([f"小规模纳税人增值税申报所属期差异（{cycle_label}）"])
    writer.writerow([
        "以下凭证的增值税计入了「记账申报期」，但纳税义务属于「业务所属申报期」。"
        "请在业务所属期的申报表里体现销售额，避免账表不一致与起征点判断偏差。"
    ])
    writer.writerow(
        ["业务日期", "业务所属期间", "纳税义务申报期", "记账期间", "记账申报期", "凭证号", "摘要", "增值税额", "补记原因"]
    )
    for record in payload.get("records", []):
        writer.writerow(
            [
                record["businessDate"],
                record["businessPeriod"],
                record["businessFilingPeriod"],
                record["postingPeriod"],
                record["postingFilingPeriod"],
                record["voucherRef"],
                record["summary"],
                record["vatAmount"],
                record["reason"],
            ]
        )
    writer.writerow([])
    writer.writerow(["合计", "", "", "", "", "", "", payload.get("amount", ""), ""])
    return buffer.getvalue()


CSV_HEADERS = ["业务日期", "所属期间", "记账期间", "凭证号", "摘要", "金额", "补记原因", "来源"]

def backfills_csv(payload: dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([f"跨期补记台账（记账期间 {payload.get('postingPeriod', '')}）"])
    writer.writerow([f"合计 {payload.get('count', 0)} 笔，金额 {payload.get('amount', '0.00')} 元"])
    writer.writerow([])
    writer.writerow(CSV_HEADERS)
    for record in payload.get("records") or []:
        writer.writerow([
            record.get("businessDate", ""),
            record.get("originalPeriod", ""),
            record.get("postingPeriod", ""),
            record.get("voucherRef", ""),
            record.get("summary", ""),
            record.get("amount", ""),
            record.get("reason", ""),
            record.get("source", ""),
        ])
    return buffer.getvalue()


CROSS_PERIOD_HEADERS = ["业务日期", "所属期间", "摘要", "金额", "单据类型"]


def cross_period_csv(payload: dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([f"跨期未处理单据（早于 {payload.get('beforePeriod', '')}，仍未入账）"])
    writer.writerow([f"合计 {payload.get('count', 0)} 笔，金额 {payload.get('amount', '0.00')} 元"])
    writer.writerow([])
    writer.writerow(CROSS_PERIOD_HEADERS)
    for record in payload.get("records") or []:
        writer.writerow([
            record.get("businessDate", ""),
            record.get("businessPeriod", ""),
            record.get("description", ""),
            record.get("amount", ""),
            record.get("sourceType", ""),
        ])
    return buffer.getvalue()


CROSS_YEAR_HEADERS = ["业务日期", "所属期间", "摘要", "金额", "单据类型", "建议处理"]


def cross_year_csv(payload: dict[str, Any], year: int) -> str:
    """跨年未处理清单：按以前年度应扣未扣支出做专项申报的底稿。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([f"{year} 年度以前、仍未入账的单据清单"])
    writer.writerow(["政策依据：国家税务总局公告 2012 年第 15 号第六条，追补确认期限不超过 5 年"])
    writer.writerow([f"合计 {payload.get('count', 0)} 笔，金额 {payload.get('amount', '0.00')} 元"])
    writer.writerow(["请由财税人员判断是否属于应扣未扣支出，并在专项申报后追补至发生年度扣除。"])
    writer.writerow([])
    writer.writerow(CROSS_YEAR_HEADERS)
    for record in payload.get("records") or []:
        writer.writerow([
            record.get("businessDate", ""),
            record.get("businessPeriod", ""),
            record.get("description", ""),
            record.get("amount", ""),
            record.get("sourceType", ""),
            "专项申报追补至发生年度（5 年内）",
        ])
    return buffer.getvalue()


def csv_response_body(text: str) -> bytes:
    """Excel 打开中文 CSV 需要 BOM，否则乱码。"""
    return ("\ufeff" + text).encode("utf-8")
