"""Authoritative voucher and trial-balance routes."""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import JournalEntry, JournalLine
from db.session import get_db_session
from deps.auth import AuthContext, require_auth
from models.schemas import (
    TrialBalanceItem,
    TrialBalanceRequest,
    TrialBalanceResponse,
    VoucherDraft,
    VoucherEntry,
    VoucherGenerateRequest,
    VoucherGenerateResponse,
    d2,
)
from services.accounting_core_service import ensure_tenant, get_journal, period_start_for, serialize_journal


router = APIRouter(prefix="/api/ledger", tags=["voucher"], dependencies=[Depends(require_auth)])


@router.post("/voucher", response_model=VoucherGenerateResponse)
async def generate_voucher(
    req: VoucherGenerateRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """Return persisted vouchers; never regenerate accounting lines from client data."""
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    vouchers: list[VoucherDraft] = []
    seen: set[str] = set()
    for entry_id in req.entryIds:
        if not entry_id or entry_id in seen:
            continue
        seen.add(entry_id)
        entry = await get_journal(session, entry_id, company_id=ctx.company_id)
        payload = await serialize_journal(session, entry, api_money=False)
        hint = payload["voucherLines"]
        vouchers.append(
            VoucherDraft(
                voucherDate=entry.occurred_on.isoformat(),
                voucherNumber=entry.voucher_no,
                summary=entry.summary,
                debitEntries=[VoucherEntry(**line) for line in hint["debit"]],
                creditEntries=[VoucherEntry(**line) for line in hint["credit"]],
                attachmentCount=1 if payload.get("billingStatus") in ("has_invoice", "has_receipt") else 0,
            )
        )
    return VoucherGenerateResponse(vouchers=vouchers)


@router.post("/trial-balance", response_model=TrialBalanceResponse)
async def trial_balance(
    req: TrialBalanceRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """Calculate the period trial balance only from posted journal lines."""
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
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
                JournalEntry.company_id == ctx.company_id,
                JournalEntry.period_start == period_start_for(req.month),
                JournalEntry.status.in_(("posted", "reversed")),
            )
            .group_by(JournalLine.account_code, JournalLine.account_name)
            .order_by(JournalLine.account_code)
        )
    ).all()
    items: list[TrialBalanceItem] = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    for code, name, debit, credit in rows:
        debit_value = d2(debit)
        credit_value = d2(credit)
        total_debit += debit_value
        total_credit += credit_value
        items.append(
            TrialBalanceItem(
                accountCode=code,
                accountName=name,
                debitTotal=debit_value,
                creditTotal=credit_value,
                balance=d2(debit_value - credit_value),
            )
        )
    return TrialBalanceResponse(
        month=req.month,
        items=items,
        isBalanced=total_debit == total_credit,
    )
