# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from __future__ import annotations

import logging
from uuid import uuid4
from datetime import date
from decimal import Decimal
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError, FORBIDDEN, NOT_FOUND
from db.models import AuthUser, CompanyMember, JournalEntry
from db.session import get_db_session
from deps.auth import AuthContext, require_auth
from models.schemas import (
    AccrualCreateRequest,
    AccrualReverseRequest,
    AccrualSettleRequest,
    BatchPostFailedItem,
    BatchPostRequest,
    BatchPostResponse,
    CompanyMemberCreate,
    CompanyMemberUpdate,
    CorrectCategoryRequest,
    JournalReverseRequest,
    PeriodReopenRequest,
    StrictRequestModel,
)
from services.accounting_core_service import (
    POSTING_ROLES,
    approve_journal,
    available_periods,
    close_period,
    ensure_tenant,
    get_idempotent_response,
    get_journal,
    period_close_checks,
    period_start_for,
    post_journal,
    record_audit,
    reopen_period,
    require_role,
    reverse_journal,
    save_idempotent_response,
    serialize_journal,
    submit_journal_for_review,
)


router = APIRouter(tags=["accounting"], dependencies=[Depends(require_auth)])
logger = logging.getLogger(__name__)


def _request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID") or str(uuid4())


@router.get("/api/record/entries/{entry_id}/posting-options")
async def posting_options(entry_id: str, ctx: AuthContext = Depends(require_auth), session: AsyncSession = Depends(get_db_session)):
    company, member = await ensure_tenant(session, ctx.company_id, ctx.user_id)
    entry = await get_journal(session, entry_id, company_id=ctx.company_id)
    reviewer = member.role in {"owner", "accountant"} and (company.posting_policy != "maker_checker" or entry.created_by != ctx.user_id)
    return {
        "canPost": entry.status == "draft" and reviewer,
        "canApprove": entry.status == "pending_review" and reviewer,
        "canSubmit": entry.status == "draft" and member.role in {"owner", "accountant", "preparer"},
        "postingPolicy": company.posting_policy,
    }


@router.post("/api/record/entries/{entry_id}/submit-review")
async def submit_review_route(entry_id: str, request: Request, ctx: AuthContext = Depends(require_auth), session: AsyncSession = Depends(get_db_session)):
    entry = await submit_journal_for_review(session, entry_id, company_id=ctx.company_id, user_id=ctx.user_id, request_id=_request_id(request))
    return {"entry": await serialize_journal(session, entry)}


@router.post("/api/record/entries/{entry_id}/post")
async def post_entry_route(
    entry_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    operation = f"post_journal:{entry_id}"
    payload = {"entryId": entry_id}
    cached = await get_idempotent_response(
        session, company_id=ctx.company_id, key=idempotency_key, operation=operation, payload=payload
    )
    if cached is not None:
        return cached
    entry = await post_journal(
        session,
        entry_id,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
    )
    response = {"entry": await serialize_journal(session, entry)}
    await save_idempotent_response(
        session,
        company_id=ctx.company_id,
        key=idempotency_key,
        operation=operation,
        payload=payload,
        status=200,
        response=response,
    )
    return response


@router.post("/api/record/entries/{entry_id}/approve")
async def approve_entry_route(
    entry_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    operation = f"approve_journal:{entry_id}"
    payload = {"entryId": entry_id}
    cached = await get_idempotent_response(
        session, company_id=ctx.company_id, key=idempotency_key, operation=operation, payload=payload
    )
    if cached is not None:
        return cached
    entry = await approve_journal(
        session,
        entry_id,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
    )
    response = {"entry": await serialize_journal(session, entry)}
    await save_idempotent_response(
        session,
        company_id=ctx.company_id,
        key=idempotency_key,
        operation=operation,
        payload=payload,
        status=200,
        response=response,
    )
    return response


@router.post("/api/record/entries/batch-post", response_model=BatchPostResponse)
async def batch_post_entries_route(
    body: BatchPostRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    company, member = await ensure_tenant(session, ctx.company_id, ctx.user_id)
    if member.role not in POSTING_ROLES:
        raise HTTPException(status_code=403, detail="当前角色没有过账权限")

    target_ids: list[str] = []
    if body.entryIds:
        seen: set[str] = set()
        for eid in body.entryIds:
            if eid and eid not in seen:
                seen.add(eid)
                target_ids.append(eid)
    elif body.month:
        month_start = period_start_for(body.month)
        rows = (
            await session.execute(
                select(JournalEntry.id)
                .where(
                    JournalEntry.company_id == ctx.company_id,
                    JournalEntry.period_start == month_start,
                    JournalEntry.status.in_(("draft", "pending_review")),
                )
                .order_by(JournalEntry.occurred_on, JournalEntry.id)
            )
        ).scalars().all()
        target_ids = list(rows)
    else:
        raise HTTPException(status_code=422, detail="请提供需要过账的凭证ID列表或月份")

    if not target_ids:
        return BatchPostResponse(
            total=0, postedCount=0, failedCount=0, postedEntryIds=[], failedEntries=[]
        )

    sorted_ids = sorted(target_ids)
    req_base_id = _request_id(request)
    posted_ids: list[str] = []
    failed_items: list[BatchPostFailedItem] = []

    for entry_id in sorted_ids:
        entry = None
        try:
            entry = await get_journal(session, entry_id, company_id=ctx.company_id, lock=True)
            if entry.status == "posted":
                posted_ids.append(entry.id)
                continue

            async with session.begin_nested():
                if entry.status == "pending_review":
                    reviewer = member.role in {"owner", "accountant"} and (
                        company.posting_policy != "maker_checker" or entry.created_by != ctx.user_id
                    )
                    if not reviewer:
                        raise AccountingError(
                            "REVIEW_REQUIRED", "制单人与复核人必须不同，不能审批自己的凭证", 409,
                            {"entryId": entry.id, "status": entry.status, "createdBy": entry.created_by},
                        )
                    res = await approve_journal(
                        session,
                        entry.id,
                        company_id=ctx.company_id,
                        user_id=ctx.user_id,
                        request_id=f"{req_base_id}:batch:{entry.id}",
                    )
                elif entry.status == "draft":
                    if company.posting_policy == "maker_checker" and entry.created_by == ctx.user_id:
                        raise AccountingError(
                            "REVIEW_REQUIRED", "制单人与复核人必须不同，需提交他人复核", 409,
                            {"entryId": entry.id, "status": entry.status, "createdBy": entry.created_by},
                        )
                    res = await post_journal(
                        session,
                        entry.id,
                        company_id=ctx.company_id,
                        user_id=ctx.user_id,
                        request_id=f"{req_base_id}:batch:{entry.id}",
                    )
                else:
                    raise AccountingError("INVALID_STATUS", f"当前凭证状态（{entry.status}）不支持过账", 409)
                posted_ids.append(res.id)
        except AccountingError as exc:
            title = entry.summary if entry is not None else entry_id
            failed_items.append(
                BatchPostFailedItem(
                    entryId=entry_id,
                    title=title or entry_id,
                    code=str(exc.code),
                    reason=str(exc.message),
                )
            )
        except Exception:
            # 未知异常可能携带内部细节（SQL/路径），不下发给客户端，完整堆栈进日志
            logger.exception("batch_post 内部错误 company=%s entry=%s", ctx.company_id, entry_id)
            title = entry.summary if entry is not None else entry_id
            failed_items.append(
                BatchPostFailedItem(
                    entryId=entry_id,
                    title=title or entry_id,
                    code="UNEXPECTED_ERROR",
                    reason="处理该凭证时发生内部错误，请稍后重试",
                )
            )

    if posted_ids:
        await record_audit(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user_id,
            action="batch_post",
            entity_type="journal_entry",
            entity_id=posted_ids[0],
            request_id=req_base_id,
            after={"postedCount": len(posted_ids), "postedEntryIds": posted_ids, "failedCount": len(failed_items)},
        )

    return BatchPostResponse(
        total=len(target_ids),
        postedCount=len(posted_ids),
        failedCount=len(failed_items),
        postedEntryIds=posted_ids,
        failedEntries=failed_items,
    )


@router.post("/api/record/entries/{entry_id}/reverse")
async def reverse_entry_route(
    entry_id: str,
    body: JournalReverseRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    operation = f"reverse_journal:{entry_id}"
    payload = body.model_dump()
    cached = await get_idempotent_response(
        session, company_id=ctx.company_id, key=idempotency_key, operation=operation, payload=payload
    )
    if cached is not None:
        return cached
    entry = await reverse_journal(
        session,
        entry_id,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
        reason=body.reason,
        reversal_date=body.reversalDate,
    )
    response = {"entry": await serialize_journal(session, entry)}
    await save_idempotent_response(
        session,
        company_id=ctx.company_id,
        key=idempotency_key,
        operation=operation,
        payload=payload,
        status=200,
        response=response,
    )
    return response


@router.post("/api/record/entries/{entry_id}/correct-category")
async def correct_entry_category_route(
    entry_id: str,
    body: CorrectCategoryRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """更正已过账凭证的分类/科目：红字冲销 + 按新分类重建并双双过账。"""
    from services.accounting_core_service import correct_entry_category

    result = await correct_entry_category(
        session,
        entry_id,
        body.category,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
        reason=body.reason,
    )
    return result


@router.get("/api/periods/available")
async def available_periods_route(
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    return await available_periods(session, ctx.company_id)


@router.get("/api/periods/backfills")
async def backfills_route(
    month: str,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """某个记账期间的补记台账（跨期补记的备查簿）。"""
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    from services.backfill_service import backfills_in_period

    return await backfills_in_period(session, ctx.company_id, month, limit=None)


@router.get("/api/periods/backfills/export")
async def backfills_export_route(
    scope: str = "backfill",
    month: str | None = None,
    year: int | None = None,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """导出四类清单：补记台账 / 跨期未处理 / 跨年未处理 / 增值税申报期差异。"""
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    from services.backfill_service import (
        backfills_csv,
        backfills_in_period,
        cross_period_csv,
        cross_period_pending,
        cross_year_csv,
        csv_response_body,
        vat_period_csv,
        vat_period_mismatch,
    )

    if scope == "backfill":
        if not month:
            raise HTTPException(status_code=422, detail="导出补记台账需要指定 month")
        payload = await backfills_in_period(session, ctx.company_id, month, limit=None)
        body = backfills_csv(payload)
        filename = f"跨期补记台账-{month}.csv"
    elif scope == "cross-period":
        if not month:
            raise HTTPException(status_code=422, detail="导出跨期未处理单据需要指定 month")
        payload = await cross_period_pending(session, ctx.company_id, month, limit=None)
        body = cross_period_csv(payload)
        filename = f"跨期未处理单据-{month}前.csv"
    elif scope == "vat-period":
        if not month:
            raise HTTPException(status_code=422, detail="导出增值税申报期差异需要指定 month")
        payload = await vat_period_mismatch(session, ctx.company_id, posting_period=month, limit=None)
        body = vat_period_csv(payload)
        filename = f"增值税申报期差异-{month}.csv"
    elif scope == "cross-year":
        if not year:
            raise HTTPException(status_code=422, detail="导出跨年清单需要指定 year")
        payload = await cross_period_pending(session, ctx.company_id, date(year, 1, 1), limit=None)
        body = cross_year_csv(payload, year)
        filename = f"跨年未处理单据-{year}年度以前.csv"
    else:
        raise HTTPException(
            status_code=422, detail="scope 只支持 backfill / cross-period / cross-year / vat-period"
        )

    return Response(
        content=csv_response_body(body),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.post("/api/accruals")
async def create_accrual_route(
    body: AccrualCreateRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """月末暂估入账：借成本费用/存货（不含税） / 贷应付账款。"""
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    from services.accrual_service import create_accrual

    entry = await create_accrual(
        session,
        body.model_dump(),
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
    )
    return {"entry": await serialize_journal(session, entry)}


@router.post("/api/accruals/{entry_id}/reverse")
async def reverse_accrual_route(
    entry_id: str,
    body: AccrualReverseRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """红冲暂估（默认落暂估期间的下一个月 1 日）。"""
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    from services.accrual_service import reverse_accrual

    entry = await reverse_accrual(
        session,
        entry_id,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
        reason=body.reason,
        reversal_date=body.reversalDate,
    )
    return {"entry": await serialize_journal(session, entry)}


@router.post("/api/accruals/{entry_id}/settle")
async def settle_accrual_route(
    entry_id: str,
    body: AccrualSettleRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """票到冲抵：冲回暂估 + 按发票金额正式入账（同一事务）。"""
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    from services.accrual_service import settle_accrual

    return await settle_accrual(
        session,
        entry_id,
        body.model_dump(),
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
    )


@router.get("/api/accruals/pending")
async def pending_accruals_route(
    month: str | None = None,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """未冲回的暂估列表。"""
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    from services.accrual_service import list_pending_accruals

    before = period_start_for(month) if month else None
    return await list_pending_accruals(session, ctx.company_id, before_period=before, limit=None)


@router.get("/api/accruals/risk-export")
async def accrual_risk_export_route(
    year: int,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """暂估未取票清单（汇算清缴纳税调增底稿）。"""
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    from services.accrual_service import accrual_risk_csv, accrual_risk_report

    payload = await accrual_risk_report(session, ctx.company_id, year)
    filename = f"暂估未取票清单-{year}.csv"
    return Response(
        content=("\ufeff" + accrual_risk_csv(payload)).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/api/periods/backfill-target")
async def backfill_target_route(
    businessDate: str,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """预判一笔业务会被记到哪个期间。

    给录入端在提交前弹确认框用：业务日期所属期间已关账时，告诉用户会补记到哪个月、
    是否跨季度、是否跨年（跨年不允许补记）。
    """
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    from core.errors import BACKFILL_NO_OPEN_PERIOD, AccountingError
    from db.models import Company
    from services.accounting_core_service import book_start_date
    from services.period_resolution_service import resolve_posting_period

    # 早于账套启用日期的单据根本不该入账（期初余额走「我的 → 期初设置」），
    # 预判阶段就要说清楚，别让用户填完原因才被 ENTRY_BEFORE_START_DATE 打回。
    company = await session.get(Company, ctx.company_id)
    start = book_start_date(company) if company is not None else None
    if start is not None:
        try:
            business_day = date.fromisoformat(str(businessDate)[:10])
        except ValueError:
            business_day = None
        if business_day is not None and business_day < start:
            return {
                "businessDate": businessDate,
                "needsBackfill": False,
                "targetPeriod": None,
                "crossQuarter": False,
                "crossYear": False,
                "canBackfill": False,
                "blocked": True,
                "message": (
                    f"该单据日期 {business_day.isoformat()} 早于账套启用日期 {start.isoformat()}，"
                    "不能直接入账。启用日期前的业务请在「我的 → 期初设置」录入期初余额，"
                    "或到「我的 → 主体管理」调整启用日期。"
                ),
            }

    try:
        resolution = await resolve_posting_period(
            session,
            ctx.company_id,
            businessDate,
            allow_backfill=True,
            # 预览阶段不抛跨年错误，交给前端展示「不能补记」的理由
            allow_cross_year=True,
        )
    except AccountingError as exc:
        if exc.code == BACKFILL_NO_OPEN_PERIOD:
            return {
                "businessDate": businessDate,
                "needsBackfill": True,
                "targetPeriod": None,
                "crossQuarter": False,
                "crossYear": False,
                "canBackfill": False,
                "message": exc.message,
            }
        raise

    return {
        **resolution.as_dict(),
        "needsBackfill": resolution.is_backfill,
        "targetPeriod": resolution.posting_period.strftime("%Y-%m"),
        "canBackfill": not resolution.cross_year,
    }


@router.get("/api/periods/{month}/close-checks")
async def close_checks_route(
    month: str,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    return await period_close_checks(session, ctx.company_id, month)


@router.post("/api/periods/{month}/close")
async def close_period_route(
    month: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    operation = f"close_period:{month}"
    payload = {"month": month}
    cached = await get_idempotent_response(
        session, company_id=ctx.company_id, key=idempotency_key, operation=operation, payload=payload
    )
    if cached is not None:
        return cached
    response = await close_period(
        session,
        ctx.company_id,
        month,
        user_id=ctx.user_id,
        request_id=_request_id(request),
    )
    await save_idempotent_response(
        session,
        company_id=ctx.company_id,
        key=idempotency_key,
        operation=operation,
        payload=payload,
        status=200,
        response=response,
    )
    return response


@router.post("/api/periods/{month}/reopen")
async def reopen_period_route(
    month: str,
    body: PeriodReopenRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    operation = f"reopen_period:{month}"
    payload = body.model_dump()
    cached = await get_idempotent_response(
        session, company_id=ctx.company_id, key=idempotency_key, operation=operation, payload=payload
    )
    if cached is not None:
        return cached
    response = await reopen_period(
        session,
        ctx.company_id,
        month,
        user_id=ctx.user_id,
        request_id=_request_id(request),
        reason=body.reason,
        step_up_token=body.stepUpToken,
    )
    await save_idempotent_response(
        session,
        company_id=ctx.company_id,
        key=idempotency_key,
        operation=operation,
        payload=payload,
        status=200,
        response=response,
    )
    return response


class FiscalYearReopenRequest(StrictRequestModel):
    reason: str
    stepUpToken: Optional[str] = None


class InventoryTransferRequest(StrictRequestModel):
    month: str
    amount: str | Decimal


@router.post("/api/inventory/transfer")
async def transfer_inventory_route(
    body: InventoryTransferRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """月末存货成本结转：借 主营业务成本 / 贷 库存商品（金额需用户确认）。"""
    from services.inventory_service import transfer_inventory_to_cost

    return await transfer_inventory_to_cost(
        session, ctx.company_id,
        month=body.month, amount=body.amount,
        user_id=ctx.user_id, request_id=_request_id(request),
    )


@router.get("/api/inventory/balance")
async def inventory_balance_route(
    month: str | None = None,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    from services.inventory_service import get_inventory_balance

    return await get_inventory_balance(session, ctx.company_id, month=month)


@router.post("/api/fiscal-years/{fiscal_year}/close")
async def close_fiscal_year_route(
    fiscal_year: int,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """年度结账：锁定该年度、留存余额快照；下年期初由历史分录派生。"""
    from services.fiscal_year_service import close_fiscal_year

    return await close_fiscal_year(
        session, ctx.company_id, fiscal_year,
        user_id=ctx.user_id, request_id=_request_id(request),
    )


@router.post("/api/fiscal-years/{fiscal_year}/reopen")
async def reopen_fiscal_year_route(
    fiscal_year: int,
    body: FiscalYearReopenRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    from services.fiscal_year_service import reopen_fiscal_year

    return await reopen_fiscal_year(
        session, ctx.company_id, fiscal_year,
        user_id=ctx.user_id, request_id=_request_id(request),
        reason=body.reason, step_up_token=body.stepUpToken,
    )


@router.get("/api/fiscal-years")
async def list_fiscal_years_route(
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    from services.fiscal_year_service import list_year_closes

    return {"years": await list_year_closes(session, ctx.company_id)}


@router.get("/api/auth/companies/{company_id}/members")
async def list_members_route(
    company_id: str,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    if company_id != ctx.company_id:
        raise AccountingError(FORBIDDEN, "不能查看其他企业成员", 403)
    await ensure_tenant(session, company_id, ctx.user_id)
    members = (
        await session.execute(select(CompanyMember).where(CompanyMember.company_id == company_id))
    ).scalars().all()
    return {
        "members": [
            {"userId": member.user_id, "role": member.role, "status": member.status}
            for member in members
        ]
    }


@router.post("/api/auth/companies/{company_id}/members")
async def add_member_route(
    company_id: str,
    body: CompanyMemberCreate,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if company_id != ctx.company_id:
        raise AccountingError(FORBIDDEN, "不能管理其他企业成员", 403)
    await require_role(session, company_id, ctx.user_id, {"owner"})
    operation = f"upsert_member:{body.userId}"
    payload = body.model_dump()
    cached = await get_idempotent_response(
        session, company_id=company_id, key=idempotency_key, operation=operation, payload=payload
    )
    if cached is not None:
        return cached
    if await session.get(AuthUser, body.userId) is None:
        raise AccountingError(NOT_FOUND, "目标用户不存在", 404)
    member = await session.get(CompanyMember, {"company_id": company_id, "user_id": body.userId})
    if member is None:
        member = CompanyMember(company_id=company_id, user_id=body.userId, role=body.role, status="active")
        session.add(member)
    else:
        member.role = body.role
        member.status = "active"
    await session.flush()
    await record_audit(
        session,
        company_id=company_id,
        user_id=ctx.user_id,
        action="upsert_company_member",
        entity_type="company_member",
        entity_id=body.userId,
        request_id=_request_id(request),
        after={"role": member.role, "status": member.status},
    )
    response = {"userId": member.user_id, "role": member.role, "status": member.status}
    await save_idempotent_response(
        session, company_id=company_id, key=idempotency_key, operation=operation,
        payload=payload, status=200, response=response,
    )
    return response


@router.patch("/api/auth/companies/{company_id}/members/{user_id}")
async def update_member_route(
    company_id: str,
    user_id: str,
    body: CompanyMemberUpdate,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if company_id != ctx.company_id:
        raise AccountingError(FORBIDDEN, "不能管理其他企业成员", 403)
    await require_role(session, company_id, ctx.user_id, {"owner"})
    operation = f"update_member:{user_id}"
    payload = body.model_dump(exclude_unset=True)
    cached = await get_idempotent_response(
        session, company_id=company_id, key=idempotency_key, operation=operation, payload=payload
    )
    if cached is not None:
        return cached
    member = await session.get(CompanyMember, {"company_id": company_id, "user_id": user_id})
    if member is None:
        raise AccountingError(NOT_FOUND, "企业成员不存在", 404)
    if member.role == "owner":
        raise AccountingError(FORBIDDEN, "不能停用或降级企业所有者", 403)
    before = {"role": member.role, "status": member.status}
    if body.role is not None:
        member.role = body.role
    if body.status is not None:
        member.status = body.status
    await session.flush()
    after = {"role": member.role, "status": member.status}
    await record_audit(
        session,
        company_id=company_id,
        user_id=ctx.user_id,
        action="update_company_member",
        entity_type="company_member",
        entity_id=user_id,
        request_id=_request_id(request),
        before=before,
        after=after,
    )
    response = {"userId": member.user_id, **after}
    await save_idempotent_response(
        session, company_id=company_id, key=idempotency_key, operation=operation,
        payload=payload, status=200, response=response,
    )
    return response
