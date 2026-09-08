"""Opening balances backed by a system-generated journal."""
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from deps.auth import AuthContext, require_auth
from db.session import get_db_session
from services.opening_balance_service import check_balanced, get_opening_balances, save_opening_balances
from services.accounting_core_service import get_idempotent_response, save_idempotent_response

router = APIRouter(prefix="/api/opening", tags=["opening"], dependencies=[Depends(require_auth)])


class OpeningBalanceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_code: str
    account_name: Optional[str] = None
    debit_balance: Decimal = Decimal("0")
    credit_balance: Decimal = Decimal("0")


class SaveOpeningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fiscal_year: int
    items: List[OpeningBalanceItem]


@router.get("/balances")
async def get_balances(
    fiscal_year: int,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return {"items": await get_opening_balances(ctx.company_id, fiscal_year, session=session)}


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
    operation = f"opening_balance:{req.fiscal_year}"
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


@router.get("/balanced")
async def balance_status(
    fiscal_year: int,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await check_balanced(ctx.company_id, fiscal_year, session=session)
