"""M3 申报作业包路由：征期日历 + 作业包 + 闭环。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from deps.auth import AuthContext, require_auth
from services import filing_deadline_service, filing_package_service

router = APIRouter(prefix="/api/tax-filing", tags=["tax-filing"], dependencies=[Depends(require_auth)])


class MarkFiledRequest(BaseModel):
    evidence: Optional[Dict[str, Any]] = None


@router.post("/deadlines/refresh")
async def refresh_deadlines(year: Optional[int] = None, ctx: AuthContext = Depends(require_auth)):
    """按规则物化当年征期（幂等）。"""
    return await filing_deadline_service.refresh_deadlines(ctx.company_id, year)


@router.get("/deadlines")
async def list_deadlines(
    upcoming_days: Optional[int] = None,
    status: Optional[str] = None,
    ctx: AuthContext = Depends(require_auth),
):
    return {"deadlines": await filing_deadline_service.list_deadlines(
        ctx.company_id, upcoming_days=upcoming_days, status=status
    )}


@router.get("/packages/{period}")
async def get_package(period: str, ctx: AuthContext = Depends(require_auth)):
    """生成/读取某期间的申报作业包（实时计算）。"""
    try:
        return await filing_package_service.build_filing_package(
            ctx.user_id, ctx.company_id, period
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/items/{deadline_id}/file")
async def mark_item_filed(
    deadline_id: str,
    req: Optional[MarkFiledRequest] = None,
    ctx: AuthContext = Depends(require_auth),
):
    """标记某申报事项已完成（可附回执归档信息）。"""
    try:
        return await filing_package_service.mark_item_filed(
            ctx.user_id,
            ctx.company_id,
            deadline_id,
            evidence=req.evidence if req else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
