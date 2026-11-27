# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""发票「前期已付款入账」路由。

前缀 ``/api/invoices/prior-payment``：候选检测（只读）与确认挂接（写库）。
挂接 = 发票主数据 ``entry_id`` 迁到前期已入账凭证 + 删除收票月挂账草稿，
凭证链路只删 draft，不触碰已过账凭证（A1/A3 语义见 service docstring）。
AccountingError 由 main.py 的全局异常处理器统一转 HTTP（code/status/context）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_db_session
from deps.auth import AuthContext, require_auth
from models.schemas import StrictRequestModel
from services.accounting_core_service import get_idempotent_response, save_idempotent_response
from services.invoice_prior_payment_service import find_prior_payment_candidates, link_prior_payment

router = APIRouter(prefix="/api/invoices/prior-payment", tags=["invoice-prior-payment"], dependencies=[Depends(require_auth)])


class PriorPaymentCandidatesRequest(StrictRequestModel):
    entryIds: list[str]


class PriorPaymentLinkRequest(StrictRequestModel):
    invoiceEntryId: str
    priorEntryId: str


@router.post("/candidates")
async def get_prior_payment_candidates(
    req: PriorPaymentCandidatesRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """批量检测发票挂账凭证可挂接的前期付款凭证（只读，不写库）。"""
    out: dict[str, list] = {}
    for entry_id in req.entryIds[:50]:
        result = await find_prior_payment_candidates(session, company_id=ctx.company_id, invoice_entry_id=str(entry_id))
        out[str(entry_id)] = result["candidates"]
    return {"candidates": out}


@router.post("/link")
async def link_invoice_prior_payment(
    req: PriorPaymentLinkRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """确认挂接：发票迁到前期凭证 + 删除收票月草稿（同一事务，幂等键防重复点击）。"""
    payload = {"invoiceEntryId": req.invoiceEntryId, "priorEntryId": req.priorEntryId}
    operation = f"prior_payment_link:{req.invoiceEntryId}"
    cached = await get_idempotent_response(
        session,
        company_id=ctx.company_id,
        key=idempotency_key,
        operation=operation,
        payload=payload,
    )
    if cached is not None:
        return cached
    result = await link_prior_payment(
        session,
        company_id=ctx.company_id,
        invoice_entry_id=req.invoiceEntryId,
        prior_entry_id=req.priorEntryId,
        user_id=ctx.user_id,
        request_id=idempotency_key or "prior-payment-link",
    )
    await save_idempotent_response(
        session,
        company_id=ctx.company_id,
        key=idempotency_key,
        operation=operation,
        payload=payload,
        status=200,
        response=result,
    )
    return result
