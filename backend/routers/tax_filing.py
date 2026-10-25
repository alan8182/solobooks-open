# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""M3 申报作业包路由：征期日历 + 作业包 + 闭环。"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from deps.auth import AuthContext, require_auth
from services import filing_deadline_service, filing_package_service

router = APIRouter(prefix="/api/tax-filing", tags=["tax-filing"], dependencies=[Depends(require_auth)])


class MarkFiledRequest(BaseModel):
    evidence: Optional[Dict[str, Any]] = None


class WaiveRequest(BaseModel):
    reason: Optional[str] = None


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


@router.get("/obligations")
async def obligations(
    month: str,
    ctx: AuthContext = Depends(require_auth),
):
    """某个结账月份对应的本期申报义务（结账向导展示用）。"""
    try:
        return await filing_deadline_service.obligations_for_month(ctx.company_id, month)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/packages/{period}")
async def get_package(period: str, ctx: AuthContext = Depends(require_auth)):
    """生成/读取某期间的申报作业包（实时计算）。"""
    try:
        return await filing_package_service.build_filing_package(
            ctx.user_id, ctx.company_id, period
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class IitAnnualRequest(BaseModel):
    """经营所得 B 表汇算清缴：用户可录入字段（均为可选，缺省用系统默认）。"""

    fiscalYear: int
    # 业主/投资者本人工资调增额（不得税前扣除）；缺省 0，账面工资总额作参考
    ownerSalary: Optional[Decimal] = None
    # 减除费用，缺省 6 万/年；已在综合所得扣除过 6 万的必须传 0
    statutoryDeduction: Optional[Decimal] = None
    specialDeductions: Optional[Decimal] = None
    specialAdditionalDeductions: Optional[Decimal] = None
    otherDeductions: Optional[Decimal] = None
    # 年内 A 表已预缴合计（按申报回执填写）
    prepaidTotal: Optional[Decimal] = None


@router.post("/iit-annual-assist")
async def get_iit_annual_assist(req: IitAnnualRequest, ctx: AuthContext = Depends(require_auth)):
    """经营所得 B 表汇算清缴作业包（次年 1-3 月汇算窗口使用）。"""
    from services import business_income_iit_service

    try:
        card = await business_income_iit_service.build_annual_settlement_assist(
            ctx.user_id, ctx.company_id, req.fiscalYear,
            owner_salary=req.ownerSalary,
            statutory_deduction=req.statutoryDeduction,
            special_deductions=req.specialDeductions or Decimal("0"),
            special_additional_deductions=req.specialAdditionalDeductions or Decimal("0"),
            other_deductions=req.otherDeductions or Decimal("0"),
            prepaid_total=req.prepaidTotal or Decimal("0"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 附汇算征期状态（business_income_iit_annual 物化在年度期间上）
    deadline = None
    if card.get("applicable"):
        try:
            rows = await filing_deadline_service.list_deadlines(ctx.company_id)
            row = next(
                (r for r in rows
                 if r["taxKind"] == "business_income_iit_annual"
                 and r["period"] == str(req.fiscalYear)),
                None,
            )
            if row:
                deadline = {
                    "deadlineId": row["id"],
                    "status": row["status"],
                    "dueDate": row["dueDate"],
                    "daysLeft": row["daysLeft"],
                }
        except Exception:  # noqa: BLE001 —— 征期读取失败不拖垮测算
            pass
    return {**card, "deadline": deadline}


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


@router.post("/items/{deadline_id}/waive")
async def waive_item(
    deadline_id: str,
    req: Optional[WaiveRequest] = None,
    ctx: AuthContext = Depends(require_auth),
):
    """把某申报事项标记为本期不适用（豁免），不参与逾期统计与催办。"""
    try:
        return await filing_deadline_service.waive_deadline(
            ctx.company_id,
            ctx.user_id,
            deadline_id,
            reason=req.reason if req else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
