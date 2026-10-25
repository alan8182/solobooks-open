# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""小私账路由"""
from typing import Any, Dict, Optional
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from deps.auth import AuthContext, require_auth
from pydantic import BaseModel
from services.private_expense_service import (
    list_expenses, get_expense, create_expense,
    update_expense, delete_expense, get_expense_summary,
)

router = APIRouter(prefix="/api/private-expenses", tags=["private-expenses"],
                   dependencies=[Depends(require_auth)])


class CreateRequest(BaseModel):
    title: str
    amount: Decimal
    direction: str = "expense"
    category: str = ""
    occur_date: str = ""
    payer: str = ""
    owner_name: str = "本人"
    private_account: str = ""
    is_available: bool = True
    memo: str = ""
    invoice_status: str = "no_invoice"
    substitute_entry_id: str = ""
    substitute_memo: str = ""


class UpdateRequest(BaseModel):
    title: Optional[str] = None
    amount: Optional[Decimal] = None
    direction: Optional[str] = None
    category: Optional[str] = None
    occur_date: Optional[str] = None
    payer: Optional[str] = None
    owner_name: Optional[str] = None
    private_account: Optional[str] = None
    is_available: Optional[bool] = None
    memo: Optional[str] = None
    invoice_status: Optional[str] = None
    substitute_entry_id: Optional[str] = None
    substitute_memo: Optional[str] = None


@router.get("")
async def list_private_expenses(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    owner_name: str = Query(""),
    ctx: AuthContext = Depends(require_auth),
):
    """分页获取私账列表。"""
    return await list_expenses(ctx.company_id, page=page, page_size=page_size, owner_name=owner_name)


@router.get("/summary")
async def private_expense_summary(
    owner_name: str = Query(""),
    ctx: AuthContext = Depends(require_auth),
):
    """获取私账汇总（总金额、总笔数）。"""
    return await get_expense_summary(ctx.company_id, owner_name=owner_name)


@router.post("")
async def create_private_expense(req: CreateRequest, ctx: AuthContext = Depends(require_auth)):
    """创建私账记录。"""
    return await create_expense(ctx.company_id, ctx.user_id, req.model_dump())


@router.get("/{expense_id}")
async def get_private_expense(expense_id: str, ctx: AuthContext = Depends(require_auth)):
    """获取单条私账记录。"""
    result = await get_expense(ctx.company_id, expense_id)
    if not result:
        raise HTTPException(status_code=404, detail="记录不存在")
    return result


@router.put("/{expense_id}")
async def update_private_expense(
    expense_id: str, req: UpdateRequest, ctx: AuthContext = Depends(require_auth),
):
    """更新私账记录。"""
    clean = {k: v for k, v in req.model_dump().items() if v is not None}
    result = await update_expense(ctx.company_id, expense_id, ctx.user_id, clean)
    if not result:
        raise HTTPException(status_code=404, detail="记录不存在")
    return result


@router.delete("/{expense_id}")
async def delete_private_expense(
    expense_id: str, ctx: AuthContext = Depends(require_auth),
):
    """删除私账记录。"""
    ok = await delete_expense(ctx.company_id, expense_id, ctx.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"ok": True}
