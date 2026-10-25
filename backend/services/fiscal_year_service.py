# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""年度结账与跨年期初（会计分期的年度闭合）。

设计要点（与月度关账一致的控制强度，但口径不同）：
1. 年度结账**不生成**下一年期初凭证。总账是全历史累计，再生成 opening 凭证会双计；
   跨年期初余额一律从历史分录派生（``derive_year_opening_balances``），
   年度快照只作为审计证据与核对基准。
2. 年度结账要求该年 12 个月全部已关账，否则结的是一笔糊涂账。
3. 结账年度禁止再入账；确需调整必须先反结账年度（owner + step-up）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError
from core.money import ZERO, money
from db.models import (
    AccountingPeriod,
    AuditEvent,
    Company,
    FiscalYearClose,
    JournalEntry,
    JournalLine,
)
from services.accounting_core_service import (
    POSTING_ROLES,
    _json_safe,
    _utcnow,
    book_start_month,
    consume_step_up_token,
    record_audit,
    require_role,
)

SNAPSHOT_RULE_VERSION = "fiscal-year-close-v1"


def year_start(fiscal_year: int) -> date:
    return date(fiscal_year, 1, 1)


def year_end(fiscal_year: int) -> date:
    return date(fiscal_year, 12, 31)


async def get_year_close(session: AsyncSession, company_id: str, fiscal_year: int) -> FiscalYearClose | None:
    return (
        await session.execute(
            select(FiscalYearClose).where(
                FiscalYearClose.company_id == company_id,
                FiscalYearClose.fiscal_year == fiscal_year,
            )
        )
    ).scalar_one_or_none()


async def is_fiscal_year_closed(session: AsyncSession, company_id: str, fiscal_year: int) -> bool:
    row = await get_year_close(session, company_id, fiscal_year)
    return bool(row and row.status == "closed")


async def assert_fiscal_year_open(session: AsyncSession, company_id: str, fiscal_year: int) -> None:
    row = await get_year_close(session, company_id, fiscal_year)
    if row and row.status == "closed":
        raise AccountingError(
            "FISCAL_YEAR_CLOSED",
            f"{fiscal_year} 年度已结账，不能再入账；如需调整请先反结账年度",
            409,
            {"fiscalYear": fiscal_year},
        )


async def close_fiscal_year(
    session: AsyncSession,
    company_id: str,
    fiscal_year: int,
    *,
    user_id: str,
    request_id: str,
) -> dict[str, Any]:
    """结转并锁定一个会计年度。要求本年度各月全部已关账。"""
    await require_role(session, company_id, user_id, {"owner"})
    existing = await get_year_close(session, company_id, fiscal_year)
    if existing and existing.status == "closed":
        raise AccountingError("FISCAL_YEAR_ALREADY_CLOSED", f"{fiscal_year} 年度已结账", 409)

    # 年中建账的账套：启用日期之前的月份没有业务、也不需要关账。下限取启用月，
    # 否则早年误建的脏账期行会把年度结账永久卡住。
    months_from = year_start(fiscal_year)
    book_start = await book_start_month(session, company_id)
    if book_start is not None and book_start > months_from:
        months_from = book_start

    open_months = (
        await session.execute(
            select(func.count()).select_from(AccountingPeriod).where(
                AccountingPeriod.company_id == company_id,
                AccountingPeriod.period_start >= months_from,
                AccountingPeriod.period_start <= date(fiscal_year, 11, 1),
                AccountingPeriod.status != "closed",
            )
        )
    ).scalar_one()
    if open_months:
        raise AccountingError(
            "FISCAL_YEAR_MONTHS_OPEN",
            f"{fiscal_year} 年度还有 {open_months} 个月份未关账，请先完成月度关账",
            409,
            {"fiscalYear": fiscal_year, "openMonthCount": int(open_months)},
        )

    # 12 月关账时已把 3103 全年累计结转至未分配利润；这里取年末科目余额做快照
    balances = await derive_year_closing_balances(session, company_id, fiscal_year)
    snapshot = {
        "fiscalYear": fiscal_year,
        "balances": balances,
        "totalDebit": money(sum((Decimal(v[0]) for v in balances.values()), ZERO)),
        "totalCredit": money(sum((Decimal(v[1]) for v in balances.values()), ZERO)),
    }
    safe_snapshot = _json_safe(snapshot)
    digest = hashlib.sha256(
        json.dumps(safe_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    row = existing or FiscalYearClose(
        company_id=company_id, fiscal_year=fiscal_year, closed_by=user_id,
    )
    row.status = "closed"
    row.snapshot = safe_snapshot
    row.snapshot_hash = digest
    row.rule_versions = {"snapshot": SNAPSHOT_RULE_VERSION}
    row.ledger_watermark = int(
        (await session.execute(select(func.max(AuditEvent.sequence_no)))).scalar_one_or_none() or 0
    )
    row.closed_by = user_id
    row.reopened_by = None
    row.reopened_at = None
    row.reopened_reason = None
    session.add(row)
    await session.flush()

    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="close_fiscal_year",
        entity_type="fiscal_year_close",
        entity_id=row.id,
        request_id=request_id,
        after={"fiscalYear": fiscal_year, "status": "closed", "snapshotHash": digest},
        entity_version=1,
    )
    return {"fiscalYear": fiscal_year, "status": "closed", "snapshotHash": digest, "balances": balances}


async def reopen_fiscal_year(
    session: AsyncSession,
    company_id: str,
    fiscal_year: int,
    *,
    user_id: str,
    request_id: str,
    reason: str,
    step_up_token: str | None,
) -> dict[str, Any]:
    await require_role(session, company_id, user_id, {"owner"})
    if not reason or not reason.strip():
        raise AccountingError("REOPEN_REASON_REQUIRED", "反结账年度必须填写原因")
    await consume_step_up_token(
        session, step_up_token, company_id=company_id, user_id=user_id, action="reopen_fiscal_year"
    )
    row = await get_year_close(session, company_id, fiscal_year)
    if row is None or row.status != "closed":
        raise AccountingError("FISCAL_YEAR_NOT_CLOSED", "只有已结账年度可以反结账", 409)

    row.status = "reopened"
    row.reopened_by = user_id
    row.reopened_at = _utcnow()
    row.reopened_reason = reason.strip()
    await session.flush()
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="reopen_fiscal_year",
        entity_type="fiscal_year_close",
        entity_id=row.id,
        request_id=request_id,
        before={"fiscalYear": fiscal_year, "status": "closed"},
        after={"fiscalYear": fiscal_year, "status": "reopened"},
        reason=reason.strip(),
        entity_version=1,
    )
    return {"fiscalYear": fiscal_year, "status": "reopened"}


async def derive_year_opening_balances(
    session: AsyncSession, company_id: str, fiscal_year: int
) -> dict[str, tuple[Decimal, Decimal]]:
    """从历史分录派生某年的期初科目余额（发生额合计，借/贷两列原样返回）。

    返回值与 ``_opening_balance_map`` 的输入约定一致：不做余额方向归一，
    由报表侧按科目性质计算正常余额，保留负余额信息。
    """
    boundary = year_start(fiscal_year)
    rows = (
        await session.execute(
            select(
                JournalLine.account_code,
                func.coalesce(func.sum(JournalLine.amount).filter(JournalLine.direction == "debit"), 0),
                func.coalesce(func.sum(JournalLine.amount).filter(JournalLine.direction == "credit"), 0),
            )
            .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.occurred_on < boundary,
                JournalEntry.status.in_(("posted", "reversed")),
                JournalLine.company_id == company_id,
            )
            .group_by(JournalLine.account_code)
            .order_by(JournalLine.account_code)
        )
    ).all()
    return {code: (money(debit), money(credit)) for code, debit, credit in rows if money(debit) or money(credit)}


async def derive_year_closing_balances(
    session: AsyncSession, company_id: str, fiscal_year: int
) -> dict[str, list[str]]:
    """年末科目余额快照（含年末结转凭证之后的余额）。"""
    boundary = year_end(fiscal_year)
    rows = (
        await session.execute(
            select(
                JournalLine.account_code,
                JournalLine.account_name,
                func.coalesce(func.sum(JournalLine.amount).filter(JournalLine.direction == "debit"), 0),
                func.coalesce(func.sum(JournalLine.amount).filter(JournalLine.direction == "credit"), 0),
            )
            .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.occurred_on <= boundary,
                JournalEntry.status.in_(("posted", "reversed")),
                JournalLine.company_id == company_id,
            )
            .group_by(JournalLine.account_code, JournalLine.account_name)
            .order_by(JournalLine.account_code)
        )
    ).all()
    return {
        code: [money(debit), money(credit)]
        for code, _name, debit, credit in rows
        if money(debit) or money(credit)
    }


async def list_year_closes(session: AsyncSession, company_id: str) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(FiscalYearClose)
            .where(FiscalYearClose.company_id == company_id)
            .order_by(FiscalYearClose.fiscal_year.desc())
        )
    ).scalars().all()
    return [
        {
            "fiscalYear": row.fiscal_year,
            "status": row.status,
            "snapshotHash": row.snapshot_hash,
            "closedBy": row.closed_by,
            "closedAt": row.closed_at.isoformat() if row.closed_at else None,
            "reopenedReason": row.reopened_reason,
        }
        for row in rows
    ]


async def _company_exists(session: AsyncSession, company_id: str) -> bool:
    return (
        await session.execute(select(Company.company_id).where(Company.company_id == company_id))
    ).scalar_one_or_none() is not None
