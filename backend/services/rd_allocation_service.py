# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select

from core.errors import AccountingError
from db.models import RdProject, RdSalaryAllocation
from db.session import session_scope
from services.accounting_core_service import ensure_tenant, record_audit, require_role


def _month_range(month: str) -> tuple[date, date]:
    try:
        year, month_number = map(int, month.split("-", 1))
        return date(year, month_number, 1), date(year, month_number, calendar.monthrange(year, month_number)[1])
    except (TypeError, ValueError) as exc:
        raise AccountingError("INVALID_MONTH", "月份格式必须为 YYYY-MM", 422) from exc


def serialize_salary_allocation(row: RdSalaryAllocation) -> dict[str, Any]:
    return {
        "id": row.id,
        "month": row.month,
        "employeeId": row.employee_id,
        "projectId": row.project_id,
        "allocationRate": str(row.allocation_rate),
        "allocationBasis": row.allocation_basis,
    }


async def get_salary_allocations(company_id: str, user_id: str, month: str) -> list[dict[str, Any]]:
    _month_range(month)
    async with session_scope() as session:
        await ensure_tenant(session, company_id, user_id)
        rows = (
            await session.execute(
                select(RdSalaryAllocation)
                .where(RdSalaryAllocation.company_id == company_id, RdSalaryAllocation.month == month)
                .order_by(RdSalaryAllocation.employee_id, RdSalaryAllocation.project_id)
            )
        ).scalars().all()
        return [serialize_salary_allocation(row) for row in rows]


async def replace_salary_allocations(
    company_id: str,
    user_id: str,
    month: str,
    allocations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    start, end = _month_range(month)
    async with session_scope() as session:
        await require_role(session, company_id, user_id, {"owner", "accountant", "preparer"})
        totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        normalized = []
        project_ids = set()
        for item in allocations:
            employee_id = str(item.get("employeeId") or "").strip()
            project_id = str(item.get("projectId") or "").strip()
            basis = str(item.get("allocationBasis") or "").strip()
            try:
                allocation_rate = Decimal(str(item.get("allocationRate")))
            except Exception as exc:
                raise AccountingError("INVALID_RD_ALLOCATION", "研发工时比例格式无效", 422) from exc
            if not employee_id or not project_id or not basis:
                raise AccountingError("RD_ALLOCATION_INCOMPLETE", "研发工资分配必须填写员工、项目和工时依据", 422)
            if allocation_rate <= 0 or allocation_rate > 1:
                raise AccountingError("INVALID_RD_ALLOCATION", "研发工时比例必须大于0且不超过100%", 422)
            totals[employee_id] += allocation_rate
            project_ids.add(project_id)
            normalized.append((employee_id, project_id, allocation_rate, basis))
        overflow = [employee_id for employee_id, total in totals.items() if total > Decimal("1.000000")]
        if overflow:
            raise AccountingError("RD_ALLOCATION_OVERFLOW", "同一员工当月研发工时比例合计不能超过100%", 422, {"employeeIds": overflow})
        projects = (
            await session.execute(
                select(RdProject).where(RdProject.company_id == company_id, RdProject.id.in_(project_ids))
            )
        ).scalars().all() if project_ids else []
        project_map = {project.id: project for project in projects}
        invalid_projects = []
        for project_id in project_ids:
            project = project_map.get(project_id)
            if (
                project is None
                or project.status not in {"active", "completed"}
                or project.start_date > end
                or (project.end_date and project.end_date < start)
            ):
                invalid_projects.append(project_id)
        if invalid_projects:
            raise AccountingError("INVALID_RD_PROJECT", "研发项目在该工资月份无效", 422, {"projectIds": invalid_projects})
        before = await get_salary_allocations_in_session(session, company_id, month)
        await session.execute(
            delete(RdSalaryAllocation).where(
                RdSalaryAllocation.company_id == company_id,
                RdSalaryAllocation.month == month,
            )
        )
        now = datetime.now(timezone.utc)
        rows = []
        for employee_id, project_id, allocation_rate, basis in normalized:
            row = RdSalaryAllocation(
                id=str(uuid4()),
                company_id=company_id,
                month=month,
                employee_id=employee_id,
                project_id=project_id,
                allocation_rate=allocation_rate,
                allocation_basis=basis,
                created_by=user_id,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            rows.append(row)
        await session.flush()
        after = [serialize_salary_allocation(row) for row in rows]
        await record_audit(
            session,
            company_id=company_id,
            user_id=user_id,
            action="replace_rd_salary_allocations",
            entity_type="salary_sheet",
            entity_id=f"salary-{month}",
            request_id=f"rd-salary-{uuid4()}",
            before={"allocations": before},
            after={"allocations": after},
        )
        return after


async def get_salary_allocations_in_session(session, company_id: str, month: str) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(RdSalaryAllocation).where(
                RdSalaryAllocation.company_id == company_id,
                RdSalaryAllocation.month == month,
            )
        )
    ).scalars().all()
    return [serialize_salary_allocation(row) for row in rows]


async def validate_asset_rd_config(company_id: str, user_id: str, payload: dict[str, Any]) -> None:
    project_id = str(payload.get("rdProjectId") or "").strip()
    raw_rate = payload.get("rdUseRate")
    basis = str(payload.get("rdAllocationBasis") or "").strip()
    if not project_id and (raw_rate is None or Decimal(str(raw_rate or 0)) == 0) and not basis:
        return
    if not project_id or raw_rate is None or not basis:
        raise AccountingError("RD_ASSET_ALLOCATION_INCOMPLETE", "研发资产必须填写项目、使用比例和分摊依据", 422)
    try:
        use_rate = Decimal(str(raw_rate))
    except Exception as exc:
        raise AccountingError("INVALID_RD_ALLOCATION", "研发使用比例格式无效", 422) from exc
    if use_rate <= 0 or use_rate > 1:
        raise AccountingError("INVALID_RD_ALLOCATION", "研发使用比例必须大于0且不超过100%", 422)
    async with session_scope() as session:
        await ensure_tenant(session, company_id, user_id)
        project = await session.get(RdProject, project_id)
        if project is None or project.company_id != company_id or project.status not in {"active", "completed"}:
            raise AccountingError("INVALID_RD_PROJECT", "请选择当前企业有效的研发项目", 422)
