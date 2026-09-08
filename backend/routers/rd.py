from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_db_session
from deps.auth import AuthContext, require_auth
from models.schemas import RdProjectCreate, RdProjectResponse, RdProjectUpdate
from services.accounting_core_service import get_idempotent_response, save_idempotent_response
from services.rd_service import (
    RD_EXPENSE_TYPES,
    RD_EVIDENCE_TYPES,
    RD_POLICY_VERSION,
    correct_unallocated_entry,
    create_evidence_document,
    create_project,
    list_projects,
    list_evidence_documents,
    rd_ledger,
    rd_reconciliation_summary,
    unallocated_rd_lines,
    update_project,
)


router = APIRouter(prefix="/api/rd", tags=["rd"], dependencies=[Depends(require_auth)])


class RdCorrectionAllocation(BaseModel):
    projectId: str
    expenseType: Literal["personnel", "direct_input", "depreciation_amortization", "design", "testing", "outsourced", "other"]
    amount: Decimal
    allocationMethod: Literal["direct", "timesheet", "usage", "area", "quantity", "other"] = "direct"
    allocationBasis: str | None = None
    notes: str | None = None


class RdCorrectionRequest(BaseModel):
    correctionDate: date
    reason: str = Field(min_length=2, max_length=500)
    allocations: list[RdCorrectionAllocation] = Field(min_length=1)


class RdEvidenceCreateRequest(BaseModel):
    projectId: str
    entryId: str | None = None
    documentType: Literal["initiation", "invoice", "receipt", "contract", "timesheet", "material_issue", "usage_log", "acceptance", "other"]
    referenceNo: str | None = None
    fileUrl: str | None = None
    status: Literal["available", "pending_review", "missing"] = "available"
    notes: str | None = None


@router.get("/expense-types")
async def expense_types():
    return {"items": RD_EXPENSE_TYPES}


@router.get("/policy-versions")
async def policy_versions():
    return {"items": [{
        "value": RD_POLICY_VERSION,
        "label": "通用研发补助资料准备规则（2026版）",
        "scope": "资料完整性预审，不替代地区专项政策",
    }]}


@router.get("/evidence-types")
async def evidence_types():
    return {"items": RD_EVIDENCE_TYPES}


@router.get("/evidence")
async def evidence_documents(
    project_id: str | None = Query(default=None, alias="projectId"),
    entry_id: str | None = Query(default=None, alias="entryId"),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return {"items": await list_evidence_documents(
        session, ctx.company_id, ctx.user_id, project_id=project_id, entry_id=entry_id,
    )}


@router.post("/evidence")
async def add_evidence_document(
    body: RdEvidenceCreateRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await create_evidence_document(
        session, ctx.company_id, ctx.user_id, body.model_dump(),
        request_id=request.headers.get("X-Request-ID") or str(uuid4()),
    )


@router.get("/projects", response_model=list[RdProjectResponse])
async def projects(
    include_archived: bool = Query(default=False),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await list_projects(session, ctx.company_id, ctx.user_id, include_archived=include_archived)


@router.post("/projects", response_model=RdProjectResponse)
async def add_project(
    body: RdProjectCreate,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await create_project(
        session,
        ctx.company_id,
        ctx.user_id,
        body.model_dump(),
        request_id=request.headers.get("X-Request-ID") or str(uuid4()),
    )


@router.patch("/projects/{project_id}", response_model=RdProjectResponse)
async def edit_project(
    project_id: str,
    body: RdProjectUpdate,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await update_project(
        session,
        ctx.company_id,
        ctx.user_id,
        project_id,
        body.model_dump(exclude_unset=True),
        request_id=request.headers.get("X-Request-ID") or str(uuid4()),
    )


@router.get("/ledger")
async def ledger(
    start_date: date = Query(alias="startDate"),
    end_date: date = Query(alias="endDate"),
    project_id: str | None = Query(default=None, alias="projectId"),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await rd_ledger(
        session,
        ctx.company_id,
        ctx.user_id,
        start_date=start_date,
        end_date=end_date,
        project_id=project_id,
    )


@router.get("/unallocated")
async def unallocated(
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await unallocated_rd_lines(session, ctx.company_id, ctx.user_id)


@router.post("/unallocated/{entry_id}/correct")
async def correct_unallocated(
    entry_id: str,
    body: RdCorrectionRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    payload = body.model_dump()
    operation = f"correct_rd_allocation:{entry_id}"
    cached = await get_idempotent_response(
        session, company_id=ctx.company_id, key=idempotency_key, operation=operation, payload=payload
    )
    if cached is not None:
        return cached
    response = await correct_unallocated_entry(
        session,
        ctx.company_id,
        ctx.user_id,
        entry_id,
        [item.model_dump() for item in body.allocations],
        correction_date=body.correctionDate,
        reason=body.reason,
        request_id=request.headers.get("X-Request-ID") or str(uuid4()),
    )
    await save_idempotent_response(
        session, company_id=ctx.company_id, key=idempotency_key, operation=operation,
        payload=payload, status=200, response=response,
    )
    return response


@router.get("/reconciliation-summary")
async def reconciliation_summary(
    start_date: date = Query(alias="startDate"),
    end_date: date = Query(alias="endDate"),
    policy_version: str = Query(default=RD_POLICY_VERSION, alias="policyVersion"),
    region: str | None = Query(default=None),
    program_batch: str | None = Query(default=None, alias="programBatch"),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await rd_reconciliation_summary(
        session, ctx.company_id, ctx.user_id,
        start_date=start_date, end_date=end_date, policy_version=policy_version,
        region=region, program_batch=program_batch,
    )
