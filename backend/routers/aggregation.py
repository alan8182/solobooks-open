# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from __future__ import annotations

from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_db_session
from deps.auth import AuthContext, require_auth
from models.schemas import (
    AggregationApplyRulesRequest,
    AggregationDimensionCreate,
    AggregationDimensionResponse,
    AggregationDimensionUpdate,
    AggregationMemberCreate,
    AggregationMemberResponse,
    AggregationMemberUpdate,
    AggregationReplaceRequest,
    AggregationRulesReplaceRequest,
    ReportLineCompareRequest,
    ReportLineRulesReplaceRequest,
)
from services.accounting_core_service import period_start_for
from services.aggregation_service import (
    AGGREGATION_KINDS,
    ALLOCATION_METHODS,
    MEMBER_STATUSES,
    aggregation_report,
    apply_rules,
    clear_line_allocations,
    create_dimension,
    create_member,
    list_dimensions,
    list_members,
    list_pending_lines,
    list_rules,
    replace_line_allocations,
    replace_rules,
    update_dimension,
    update_member,
)
from services.report_line_rule_service import (
    archive_rule as archive_report_line_rule,
)
from services.report_line_rule_service import (
    compare_with_baseline,
    list_rules as list_report_line_rules,
    replace_rules as replace_report_line_rules,
    switch_readiness,
)


router = APIRouter(prefix="/api/aggregation", tags=["aggregation"], dependencies=[Depends(require_auth)])


def _prefixes(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [item.strip() for item in raw.split(",") if item.strip()]


@router.get("/meta")
async def meta():
    return {
        "kinds": AGGREGATION_KINDS,
        "methods": ALLOCATION_METHODS,
        "memberStatuses": MEMBER_STATUSES,
    }


@router.get("/dimensions", response_model=list[AggregationDimensionResponse])
async def dimensions(
    include_archived: bool = Query(default=False),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await list_dimensions(session, ctx.company_id, ctx.user_id, include_archived=include_archived)


@router.post("/dimensions", response_model=AggregationDimensionResponse)
async def add_dimension(
    body: AggregationDimensionCreate,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await create_dimension(
        session, ctx.company_id, ctx.user_id, body.model_dump(),
        request_id=request.headers.get("X-Request-ID") or str(uuid4()),
    )


@router.patch("/dimensions/{dimension_id}", response_model=AggregationDimensionResponse)
async def edit_dimension(
    dimension_id: str,
    body: AggregationDimensionUpdate,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await update_dimension(
        session, ctx.company_id, ctx.user_id, dimension_id, body.model_dump(exclude_none=True),
        request_id=request.headers.get("X-Request-ID") or str(uuid4()),
    )


@router.get("/dimensions/{dimension_id}/members", response_model=list[AggregationMemberResponse])
async def members(
    dimension_id: str,
    include_archived: bool = Query(default=False),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await list_members(
        session, ctx.company_id, ctx.user_id, dimension_id, include_archived=include_archived,
    )


@router.post("/dimensions/{dimension_id}/members", response_model=AggregationMemberResponse)
async def add_member(
    dimension_id: str,
    body: AggregationMemberCreate,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await create_member(
        session, ctx.company_id, ctx.user_id, dimension_id, body.model_dump(),
        request_id=request.headers.get("X-Request-ID") or str(uuid4()),
    )


@router.patch("/members/{member_id}", response_model=AggregationMemberResponse)
async def edit_member(
    member_id: str,
    body: AggregationMemberUpdate,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await update_member(
        session, ctx.company_id, ctx.user_id, member_id, body.model_dump(exclude_none=True),
        request_id=request.headers.get("X-Request-ID") or str(uuid4()),
    )


@router.get("/dimensions/{dimension_id}/rules")
async def rules(
    dimension_id: str,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return {"items": await list_rules(session, ctx.company_id, ctx.user_id, dimension_id)}


@router.put("/dimensions/{dimension_id}/rules")
async def edit_rules(
    dimension_id: str,
    body: AggregationRulesReplaceRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    items = await replace_rules(
        session, ctx.company_id, ctx.user_id, dimension_id, body.model_dump(),
        request_id=request.headers.get("X-Request-ID") or str(uuid4()),
    )
    return {"items": items}


@router.get("/pending")
async def pending(
    start: str | None = Query(default=None, description="YYYY-MM，缺省不限"),
    end: str | None = Query(default=None, description="YYYY-MM，缺省不限"),
    dimension_id: str | None = Query(default=None, alias="dimensionId"),
    scope_prefixes: str | None = Query(default=None, alias="scopePrefixes", description="逗号分隔的科目前缀"),
    basis: Literal["period", "occurDate"] = Query(default="period"),
    state: Literal["all", "unallocated", "partial", "allocated"] = Query(default="all"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await list_pending_lines(
        session, ctx.company_id, ctx.user_id,
        start=period_start_for(start) if start else None,
        end=period_start_for(end) if end else None,
        dimension_id=dimension_id,
        prefixes=_prefixes(scope_prefixes),
        basis=basis,
        state_filter=None if state == "all" else state,
        limit=limit,
        offset=offset,
    )


@router.put("/lines/{journal_line_id}/allocations")
async def allocate(
    journal_line_id: str,
    body: AggregationReplaceRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await replace_line_allocations(
        session, ctx.company_id, ctx.user_id, journal_line_id, body.model_dump(),
        request_id=request.headers.get("X-Request-ID") or str(uuid4()),
    )


@router.delete("/lines/{journal_line_id}/allocations")
async def deallocate(
    journal_line_id: str,
    request: Request,
    dimension_id: str = Query(alias="dimensionId"),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await clear_line_allocations(
        session, ctx.company_id, user_id=ctx.user_id, journal_line_id=journal_line_id,
        dimension_id=dimension_id,
        request_id=request.headers.get("X-Request-ID") or str(uuid4()),
    )


@router.get("/report")
async def report(
    dimension_id: str = Query(alias="dimensionId"),
    start: str = Query(description="YYYY-MM"),
    end: str = Query(description="YYYY-MM"),
    basis: Literal["period", "occurDate"] = Query(default="period"),
    scope_prefixes: str | None = Query(default=None, alias="scopePrefixes"),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await aggregation_report(
        session, ctx.company_id, ctx.user_id, dimension_id,
        start=period_start_for(start),
        end=period_start_for(end),
        basis=basis,
        prefixes=_prefixes(scope_prefixes),
    )


@router.post("/apply-rules")
async def run_rules(
    body: AggregationApplyRulesRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await apply_rules(
        session, ctx.company_id, ctx.user_id, body.model_dump(),
        request_id=request.headers.get("X-Request-ID") or str(uuid4()),
    )


# ── 报表项映射规则（P2） ──
# 这组端点**不接管**任何对外报表：只做规则维护与双轨对比。
# 达标（连续 N 期零差异）前，报表现仍走 tax_filing_export_service 的硬编码口径。


@router.get("/report-line-rules")
async def report_line_rules(
    report: Literal["profit", "balance"] | None = Query(default=None),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await list_report_line_rules(session, ctx.company_id, report)


@router.put("/report-line-rules")
async def replace_report_line_rules_endpoint(
    body: ReportLineRulesReplaceRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await replace_report_line_rules(
        session, ctx.company_id, ctx.user_id, body.model_dump(),
        request_id=request.headers.get("X-Request-ID") or str(uuid4()),
    )


@router.delete("/report-line-rules/{rule_id}")
async def archive_report_line_rule_endpoint(
    rule_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await archive_report_line_rule(
        session, ctx.company_id, ctx.user_id, rule_id,
        request_id=request.headers.get("X-Request-ID") or str(uuid4()),
    )


@router.post("/report-line-rules/compare")
async def compare_report_line_rules(
    body: ReportLineCompareRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """双轨对比：真实数据跑一遍两种口径，逐行比差异并落审计。"""
    return await compare_with_baseline(
        session, ctx.company_id, ctx.user_id, body.report, body.period,
        period_type=body.periodType,
        request_id=request.headers.get("X-Request-ID") or str(uuid4()),
    )


@router.get("/report-line-rules/switch-readiness")
async def report_line_switch_readiness(
    report: Literal["profit", "balance"] = Query(),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """是否够格把对外报表切到规则口径。只回答问题，不执行切换。"""
    return await switch_readiness(session, ctx.company_id, report)
