# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Opening balances backed by a system-generated journal."""
from decimal import Decimal
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from deps.auth import AuthContext, require_auth
from db.session import get_db_session
from services.opening_balance_service import (
    check_balanced,
    get_opening_balances,
    get_opening_details,
    reset_opening_balances,
    save_opening_balances,
)
from services.accounting_core_service import get_idempotent_response, save_idempotent_response

router = APIRouter(prefix="/api/opening", tags=["opening"], dependencies=[Depends(require_auth)])


class OpeningBalanceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    account_code: str
    account_name: Optional[str] = None
    debit_balance: Decimal = Decimal("0")
    credit_balance: Decimal = Decimal("0")
    partner_name: Optional[str] = None


class TaxOpeningMemory(BaseModel):
    model_config = ConfigDict(extra="ignore")
    prior_year_loss: Decimal = Decimal("0")
    prepaid_cit_tax: Decimal = Decimal("0")
    migration_note: str = ""


class SaveOpeningRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    fiscal_year: int
    start_period: Optional[str] = None  # 如 "2026-06"，为空默认 "{fiscal_year}-01"
    items: List[OpeningBalanceItem]
    ytd_items: Optional[List[OpeningBalanceItem]] = None  # 年中建账损益本年累计发生额
    tax_memory: Optional[TaxOpeningMemory] = None
    fixed_assets: Optional[List[dict]] = None


class ResetOpeningRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    fiscal_year: int
    reason: Optional[str] = "期初数据冲销重置"


@router.get("/balances")
async def get_balances(
    fiscal_year: int,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """获取期初完整信息，包含资产负债期初、损益本年累计与持久化税务备忘。"""
    return await get_opening_details(ctx.company_id, fiscal_year, session=session)


@router.put("/balances")
async def save_balances(
    req: SaveOpeningRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    step_up_token: str | None = Header(default=None, alias="Step-Up-Token"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    payload = req.model_dump()
    operation = f"opening_balance:{req.fiscal_year}:{req.start_period or '01'}"
    cached = await get_idempotent_response(
        session,
        company_id=ctx.company_id,
        key=idempotency_key,
        operation=operation,
        payload=payload,
    )
    if cached is not None:
        return cached

    entry = await save_opening_balances(
        ctx.company_id,
        req.fiscal_year,
        [item.model_dump() for item in req.items],
        user_id=ctx.user_id,
        step_up_token=step_up_token,
        request_id=request.headers.get("X-Request-ID") or f"opening:{ctx.company_id}:{req.fiscal_year}",
        start_period=req.start_period,
        ytd_items=[item.model_dump() for item in req.ytd_items] if req.ytd_items else None,
        tax_memory=req.tax_memory.model_dump() if req.tax_memory else None,
        fixed_assets=req.fixed_assets,
        session=session,
    )
    response = {"ok": True, "entry": entry}
    await save_idempotent_response(
        session,
        company_id=ctx.company_id,
        key=idempotency_key,
        operation=operation,
        payload=payload,
        status=200,
        response=response,
    )
    return response


@router.post("/reset")
async def reset_balances(
    req: ResetOpeningRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    step_up_token: str | None = Header(default=None, alias="Step-Up-Token"),
):
    """冲销已过账的期初凭证，以便重新调整或重新导入。"""
    request_id = request.headers.get("X-Request-ID") or f"reset-opening:{ctx.company_id}:{req.fiscal_year}"
    return await reset_opening_balances(
        ctx.company_id,
        req.fiscal_year,
        user_id=ctx.user_id,
        step_up_token=step_up_token,
        request_id=request_id,
        reason=req.reason or "期初数据冲销重置",
        session=session,
    )


@router.get("/balanced")
async def balance_status(
    fiscal_year: int,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await check_balanced(ctx.company_id, fiscal_year, session=session)
