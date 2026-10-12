"""FastAPI router for Employee Salary & Social Security management."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
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

class EmployeeUpdate(BaseModel):
    name: Optional[str] = None
    department: Optional[str] = None
    base_salary: Optional[Decimal] = Field(default=None, ge=0)
    social_security_base: Optional[Decimal] = Field(default=None, ge=0)
    is_active: Optional[int] = None

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
        social_security_base=req.social_security_base
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
async def pay_social(month: str, ctx: AuthContext = Depends(require_auth)):
    """缴纳本月社保公积金（单位+个人部分），生成清偿 2211 挂账的凭证。"""
    try:
        return await service.pay_payroll_liability(ctx.company_id, ctx.user_id, month, "social_security")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sheets/{month}/pay-tax")
async def pay_tax(month: str, ctx: AuthContext = Depends(require_auth)):
    """缴纳本月个税，生成清偿 2221 挂账的凭证。"""
    try:
        return await service.pay_payroll_liability(ctx.company_id, ctx.user_id, month, "tax")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
