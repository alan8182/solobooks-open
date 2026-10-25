# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Authoritative voucher and trial-balance routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

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
from services.accounting_core_service import ensure_tenant, get_journal, serialize_journal
from services.ledger_book_service import build_ledger_book


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
    """试算平衡：与科目余额表同一实现、同一口径。

    历史上先是独立 SQL 聚合，后改为从 balance-summary 派生，始终与账簿两套
    取数路径并存、口径容易漂移；现在统一从整册（build_ledger_book）的 balance
    段派生（剔除容器合计行），端点与返回结构保持不变。
    """
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    book = await build_ledger_book(ctx.company_id, month=req.month, include_zero_rows=True)
    balance = book["balance"]
    items = [
        TrialBalanceItem(
            accountCode=item["accountCode"],
            accountName=item["accountName"],
            debitTotal=d2(item["debitTotal"]),
            creditTotal=d2(item["creditTotal"]),
            balance=d2(item["balance"]),
        )
        for item in balance["items"]
        if not item["isParent"]
    ]
    return TrialBalanceResponse(
        month=req.month,
        items=items,
        isBalanced=balance["isBalanced"],
    )
