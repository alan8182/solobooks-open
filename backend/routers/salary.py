# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""FastAPI router for Employee Salary & Social Security management."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from decimal import Decimal
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from deps.auth import AuthContext, require_auth
import services.salary_service as service

router = APIRouter(prefix="/api/salary", tags=["salary"], dependencies=[Depends(require_auth)])

# --- Pydantic Schemas ---

class EmployeeCreate(BaseModel):
    name: str = Field(..., min_length=1)
    department: str = Field(..., min_length=1)
    base_salary: Decimal = Field(default=Decimal("0"), ge=0)
    social_security_base: Decimal = Field(default=Decimal("0"), ge=0)
    # 业主/投资者标记：经营所得 B 表"业主工资不得扣除"调增的自动识别来源
    is_owner: bool = False
    # 入职日期（YYYY-MM-DD）：为空表示不限制，工资表只从入职当月起计入该员工
    hire_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    # 月度专项附加扣除合计（子女教育/房贷/赡养老人等），个税累计预扣试算用
    special_additional_deduction: Decimal = Field(default=Decimal("0"), ge=0)

class EmployeeUpdate(BaseModel):
    name: Optional[str] = None
    department: Optional[str] = None
    base_salary: Optional[Decimal] = Field(default=None, ge=0)
    social_security_base: Optional[Decimal] = Field(default=None, ge=0)
    is_active: Optional[int] = None
    is_owner: Optional[bool] = None
    hire_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    special_additional_deduction: Optional[Decimal] = Field(default=None, ge=0)

class RecordUpdateItem(BaseModel):
    id: str
    base_salary: Decimal
    bonus: Decimal
    allowance: Decimal
    deduction: Decimal
    personal_social_security: Decimal
    personal_tax: Decimal
    company_social_security: Decimal

class SheetRecordsUpdate(BaseModel):
    records: List[RecordUpdateItem]

class RdSalaryAllocationItem(BaseModel):
    employeeId: str
    projectId: str
    allocationRate: Decimal = Field(gt=0, le=1)
    allocationBasis: str = Field(..., min_length=1)

class RdSalaryAllocationsUpdate(BaseModel):
    allocations: List[RdSalaryAllocationItem]

# --- Router Endpoints ---

@router.get("/employees")
async def get_employees(ctx: AuthContext = Depends(require_auth)):
    """List all active employees for the company."""
    return await service.list_employees(ctx.company_id)

@router.post("/employees")
async def add_employee(req: EmployeeCreate, ctx: AuthContext = Depends(require_auth)):
    """Create a new employee."""
    return await service.create_employee(
        company_id=ctx.company_id,
        name=req.name,
        department=req.department,
        base_salary=req.base_salary,
        social_security_base=req.social_security_base,
        is_owner=req.is_owner,
        hire_date=req.hire_date,
        special_additional_deduction=req.special_additional_deduction,
    )

@router.put("/employees/{employee_id}")
async def update_employee(employee_id: str, req: EmployeeUpdate, ctx: AuthContext = Depends(require_auth)):
    """Update employee details."""
    updated = await service.update_employee(
        company_id=ctx.company_id,
        employee_id=employee_id,
        updates=req.dict(exclude_unset=True)
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Employee not found")
    return updated

@router.delete("/employees/{employee_id}")
async def delete_employee(employee_id: str, ctx: AuthContext = Depends(require_auth)):
    """Archive/delete employee."""
    archived = await service.archive_employee(ctx.company_id, employee_id)
    if not archived:
        raise HTTPException(status_code=404, detail="Employee not found")
    return {"success": True}

@router.get("/sheets/{month}")
async def get_sheet(month: str, ctx: AuthContext = Depends(require_auth)):
    """Get or auto-create the salary sheet for a month."""
    if not len(month) == 7 or month[4] != "-":
        raise HTTPException(status_code=400, detail="Invalid month format, use YYYY-MM")
    return await service.get_or_create_salary_sheet(ctx.company_id, month)


class PayrollRatesUpdate(BaseModel):
    """账套级社保公积金比例（0~1 的小数，如 8% 填 0.08）。"""

    socialPersonal: Optional[Decimal] = Field(default=None, ge=0, le=1)
    socialCompany: Optional[Decimal] = Field(default=None, ge=0, le=1)
    housingPersonal: Optional[Decimal] = Field(default=None, ge=0, le=1)
    housingCompany: Optional[Decimal] = Field(default=None, ge=0, le=1)


class DepartmentItem(BaseModel):
    """部门字典项。isRd 决定该部门工资走研发支出还是管理费用。"""

    name: str = Field(..., min_length=1, max_length=32)
    isRd: bool = False


class DepartmentsUpdate(BaseModel):
    departments: List[DepartmentItem]


@router.get("/departments")
async def get_departments(ctx: AuthContext = Depends(require_auth)):
    """账套部门字典 + 各部门在职人数。名册里在用的部门一定出现在选项里。"""
    return await service.get_departments(ctx.company_id)


@router.put("/departments")
async def put_departments(
    req: DepartmentsUpdate,
    ctx: AuthContext = Depends(require_auth),
):
    """保存部门字典；仍有在职员工的部门不允许移除。"""
    try:
        return await service.save_departments(
            ctx.company_id, [item.model_dump() for item in req.departments]
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/rates")
async def get_rates(ctx: AuthContext = Depends(require_auth)):
    """读取本账套的社保公积金比例（未自定义时返回杭州口径默认值）。"""
    return await service.get_payroll_rates(ctx.company_id)


class TaxPreviewRequest(BaseModel):
    """个税试算入参：本期应发与代扣由前端带入（草稿可能尚未保存）。"""

    month: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    employeeId: str = Field(..., min_length=1)
    gross: Decimal = Field(default=Decimal("0"), ge=0)
    withheld: Decimal = Field(default=Decimal("0"), ge=0)
    specialAdditional: Optional[Decimal] = Field(default=None, ge=0)
    otherDeduction: Decimal = Field(default=Decimal("0"), ge=0)


@router.post("/tax/preview")
async def preview_tax(req: TaxPreviewRequest, ctx: AuthContext = Depends(require_auth)):
    """按累计预扣法试算本期应扣个税（结果仅供参考，以扣缴端申报数为准）。"""
    from services.salary_iit_service import preview_individual_income_tax

    try:
        return await preview_individual_income_tax(
            ctx.company_id,
            req.employeeId,
            req.month,
            gross=req.gross,
            withheld=req.withheld,
            special_additional=req.specialAdditional,
            other_deduction=req.otherDeduction,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))



@router.put("/rates")
async def put_rates(
    req: PayrollRatesUpdate,
    month: Optional[str] = None,
    recalculate: bool = False,
    ctx: AuthContext = Depends(require_auth),
):
    """保存账套级比例；recalculate=true 时顺带按新比例重算指定月份的草稿工资表。"""
    try:
        result = await service.save_payroll_rates(
            ctx.company_id, req.model_dump(exclude_none=True)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if recalculate and month:
        try:
            await service.recalculate_draft_withholdings(ctx.company_id, month)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return result


@router.put("/sheets/{month}/records")
async def update_records(month: str, req: SheetRecordsUpdate, ctx: AuthContext = Depends(require_auth)):
    """Update all record amounts in the salary sheet."""
    try:
        records_data = [item.dict() for item in req.records]
        return await service.update_salary_records(ctx.company_id, month, records_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/sheets/{month}/rd-allocations")
async def get_rd_allocations(month: str, ctx: AuthContext = Depends(require_auth)):
    from services.rd_allocation_service import get_salary_allocations
    return {"allocations": await get_salary_allocations(ctx.company_id, ctx.user_id, month)}

@router.put("/sheets/{month}/rd-allocations")
async def put_rd_allocations(
    month: str,
    req: RdSalaryAllocationsUpdate,
    ctx: AuthContext = Depends(require_auth),
):
    from services.rd_allocation_service import replace_salary_allocations
    allocations = await replace_salary_allocations(
        ctx.company_id,
        ctx.user_id,
        month,
        [item.model_dump() for item in req.allocations],
    )
    return {"allocations": allocations}

@router.post("/sheets/{month}/accrue")
async def accrue_sheet(month: str, ctx: AuthContext = Depends(require_auth)):
    """Accrue salary sheet and generate double-entry voucher."""
    try:
        return await service.accrue_salary_sheet(ctx.company_id, ctx.user_id, month)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

class SheetPayRequest(BaseModel):
    """实际发薪日（YYYY-MM-DD），缺省取操作当天；真实发薪多在工资所属期次月。"""
    payDate: Optional[str] = None


class SalaryPaymentApplyRequest(BaseModel):
    month: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    statementId: str = Field(..., min_length=1)


@router.get("/payments/suggestions")
async def payment_suggestions(ctx: AuthContext = Depends(require_auth)):
    """按工资所属月匹配银行付款流水；只返回候选，不自动入账。"""
    return await service.list_salary_payment_suggestions(ctx.company_id)


@router.post("/payments/apply")
async def apply_payment(
    req: SalaryPaymentApplyRequest,
    ctx: AuthContext = Depends(require_auth),
):
    """确认流水为工资发放：生成发放凭证并按流水日期核销。"""
    from uuid import uuid4

    try:
        return await service.apply_salary_payment_from_statement(
            ctx.company_id,
            ctx.user_id,
            req.month,
            req.statementId,
            request_id=f"salary-payment-{uuid4().hex}",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sheets/{month}/pay")
async def pay_sheet(month: str, req: Optional[SheetPayRequest] = None, ctx: AuthContext = Depends(require_auth)):
    """Pay salary sheet and generate cash outflow voucher."""
    try:
        return await service.pay_salary_sheet(
            ctx.company_id, ctx.user_id, month,
            pay_date=req.payDate if req else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sheets/{month}/pay-social")
async def pay_social(month: str, req: Optional[SheetPayRequest] = None, ctx: AuthContext = Depends(require_auth)):
    """缴纳本月社会保险（单位+个人部分），不含公积金。

    借 221102（单位社保）+ 221104（代扣个人社保），贷 1002。
    """
    try:
        return await service.pay_payroll_liability(
            ctx.company_id, ctx.user_id, month, "social_security",
            pay_date=req.payDate if req else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sheets/{month}/pay-housing-fund")
async def pay_housing_fund(month: str, req: Optional[SheetPayRequest] = None, ctx: AuthContext = Depends(require_auth)):
    """缴纳本月住房公积金（单位+个人部分），与社保可分开核销。"""
    try:
        return await service.pay_payroll_liability(
            ctx.company_id, ctx.user_id, month, "housing_fund",
            pay_date=req.payDate if req else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sheets/{month}/pay-tax")
async def pay_tax(month: str, req: Optional[SheetPayRequest] = None, ctx: AuthContext = Depends(require_auth)):
    """缴纳本月个税。

    借 2221012（应交个人所得税），贷 1002。个税为 0 时直接返回
    taxPaymentStatus=not_required，无需生成 0 元凭证。
    """
    try:
        return await service.pay_payroll_liability(
            ctx.company_id, ctx.user_id, month, "tax",
            pay_date=req.payDate if req else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class PayrollLiabilityApplyRequest(BaseModel):
    month: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    kind: str = Field(..., pattern=r"^(social_security|housing_fund|tax)$")
    statementId: str = Field(..., min_length=1)


@router.get("/liabilities/suggestions")
async def liability_suggestions(ctx: AuthContext = Depends(require_auth)):
    """按工资所属月匹配社保/公积金/个税银行付款流水；只返回候选。"""
    return await service.list_payroll_liability_payment_suggestions(ctx.company_id)


@router.post("/liabilities/apply")
async def apply_liability(
    req: PayrollLiabilityApplyRequest,
    ctx: AuthContext = Depends(require_auth),
):
    """确认流水为社保/公积金/个税缴纳：生成缴纳凭证并按流水日期核销。"""
    from uuid import uuid4

    try:
        return await service.apply_payroll_liability_from_statement(
            ctx.company_id,
            ctx.user_id,
            req.month,
            req.kind,
            req.statementId,
            request_id=f"salary-liability-{uuid4().hex}",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _csv_response(body: str, filename: str) -> Response:
    from services.backfill_service import csv_response_body

    return Response(
        content=csv_response_body(body),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/sheets/{month}/export")
async def export_sheet(month: str, ctx: AuthContext = Depends(require_auth)):
    """导出整月工资表 CSV（含合计行与费率口径）。"""
    try:
        body, filename = await service.export_salary_sheet_csv(ctx.company_id, month)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _csv_response(body, filename)


@router.get("/sheets/{month}/payslip/{record_id}")
async def export_payslip(month: str, record_id: str, ctx: AuthContext = Depends(require_auth)):
    """导出单人工资条 CSV，用于发给员工。"""
    try:
        body, filename = await service.export_payslip_csv(ctx.company_id, month, record_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _csv_response(body, filename)
