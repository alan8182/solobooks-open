# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""应收/应付账龄路由"""
from fastapi import APIRouter, Depends, Query
from deps.auth import AuthContext, require_auth
from services.aging_service import query_ar_aging, query_ap_aging

router = APIRouter(prefix="/api/report", tags=["aging"], dependencies=[Depends(require_auth)])


@router.get("/ar-aging")
async def ar_aging(
    as_of: str = "",
    group_by: str = Query("none", description="none | partner"),
    ctx: AuthContext = Depends(require_auth),
):
    """应收账龄分析（应收账款 1122）；group_by=partner 按往来单位分组。"""
    result = await query_ar_aging(ctx.company_id, as_of, group_by=group_by)
    return result


@router.get("/ap-aging")
async def ap_aging(
    as_of: str = "",
    group_by: str = Query("none", description="none | partner"),
    ctx: AuthContext = Depends(require_auth),
):
    """应付账龄分析（应付账款 2202）；group_by=partner 按往来单位分组。"""
    result = await query_ap_aging(ctx.company_id, as_of, group_by=group_by)
    return result
