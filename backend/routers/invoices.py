# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""发票台账路由（P0 WP1）。

与 ``routers/invoice.py``（导入流程）分开：这里是已入账发票的查询与汇总，
前缀 ``/api/invoices``，语义是"主数据"而不是"导入动作"。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from deps.auth import AuthContext, require_auth
from services.audit_service import log_action
from services.invoice_ledger_service import backfill_invoices, invoice_summary, list_invoices

router = APIRouter(prefix="/api/invoices", tags=["invoices"], dependencies=[Depends(require_auth)])


@router.get("")
async def get_invoices(
    month_from: Optional[str] = Query(None, description="起始月份 YYYY-MM"),
    month_to: Optional[str] = Query(None, description="截止月份 YYYY-MM"),
    direction: Optional[str] = Query(None, description="income / expense"),
    keyword: Optional[str] = Query(None, description="发票号/对方/项目模糊匹配"),
    status: Optional[str] = Query(None, description="normal / red / reversed"),
    posting: Optional[str] = Query(None, description="pending（待过账）/ posted（已过账）"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    ctx: AuthContext = Depends(require_auth),
):
    """发票台账：分页 + 期间/方向/关键字/状态过滤。"""
    return await list_invoices(
        ctx.company_id,
        month_from=month_from,
        month_to=month_to,
        direction=direction,
        keyword=keyword,
        status=status,
        posting=posting,
        page=page,
        page_size=page_size,
    )


@router.get("/summary")
async def get_invoice_summary(
    month_from: Optional[str] = Query(None),
    month_to: Optional[str] = Query(None),
    ctx: AuthContext = Depends(require_auth),
):
    """按月进/销项汇总（金额、税额、张数）。"""
    return await invoice_summary(ctx.company_id, month_from=month_from, month_to=month_to)


@router.post("/backfill")
async def run_backfill(ctx: AuthContext = Depends(require_auth)):
    """把存量 payload 里的发票回填进台账（幂等，可重复执行）。"""
    result = await backfill_invoices(ctx.company_id, force=True)
    await log_action(ctx.company_id, ctx.user_id, "backfill", "invoice", None, result)
    return result
