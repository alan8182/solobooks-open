from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError, FORBIDDEN, NOT_FOUND
from db.models import AuthUser, CompanyMember, JournalEntry
from db.session import get_db_session
from deps.auth import AuthContext, require_auth
from models.schemas import (
    BatchPostFailedItem,
    BatchPostRequest,
    BatchPostResponse,
    CompanyMemberCreate,
    CompanyMemberUpdate,
    JournalReverseRequest,
    PeriodReopenRequest,
)
from services.accounting_core_service import (
    POSTING_ROLES,
    approve_journal,
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
                        raise AccountingError("REVIEW_REQUIRED", "制单人与复核人必须不同，不能审批自己的凭证", 409)
                    res = await approve_journal(
                        session,
                        entry.id,
                        company_id=ctx.company_id,
                        user_id=ctx.user_id,
                        request_id=f"{req_base_id}:batch:{entry.id}",
                    )
                elif entry.status == "draft":
                    if company.posting_policy == "maker_checker" and entry.created_by == ctx.user_id:
                        raise AccountingError("REVIEW_REQUIRED", "制单人与复核人必须不同，需提交他人复核", 409)
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
        except Exception as exc:
            title = entry.summary if entry is not None else entry_id
            failed_items.append(
                BatchPostFailedItem(
                    entryId=entry_id,
                    title=title or entry_id,
                    code="UNEXPECTED_ERROR",
                    reason=str(exc),
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
