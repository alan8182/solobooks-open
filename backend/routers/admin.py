# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""管理后台路由（平台级，全部挂 require_admin）。

M0：/api/admin/overview 系统概览——DB 探活、用户/公司计数、今日 agent 调用、
LLM 熔断状态、版本号。
M1：/api/admin/usage/* token 用量统计——多维聚合（用户/公司/天/模型）与
最近调用明细，数据来自 services/llm.py 埋点写入的 llm_usage 表。
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text

from config import LLM_PRICE_TABLE
from core.plans import PLAN_CATALOG, get_plan
from db.models import AgentAction, AuthUser, Company, CompanyMember, IndustryTemplate, JournalEntry, LlmUsage
from db.session import session_scope
from deps.auth import AuthContext, require_admin
from services import quota_service
from services.industry_template_service import (
    IndustryTemplateValues,
    create_template,
    list_templates,
    serialize_template,
    update_template,
)
from services.llm import llm_circuit_state

router = APIRouter(prefix="/api/admin", tags=["admin"])

logger = logging.getLogger(__name__)

APP_VERSION = "3.0.0"


def _utc_day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def _cost_estimate(model: str, prompt_tokens: int, completion_tokens: int) -> Optional[float]:
    """按 config.LLM_PRICE_TABLE（元/千 tokens）估算成本；模型缺价返回 None。"""
    price = LLM_PRICE_TABLE.get(model)
    if not price:
        return None
    cost = (
        prompt_tokens / 1000 * price.get("prompt", 0)
        + completion_tokens / 1000 * price.get("completion", 0)
    )
    return round(cost, 4)


@router.get("/overview")
async def admin_overview(admin: AuthContext = Depends(require_admin)):
    """平台概览聚合：系统状态 + 核心计数。

    「今日/本月」按 UTC 日界计算（created_at 以 UTC 存储），
    对内部运营看板而言 8 小时口径偏差可接受。
    """
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    async with session_scope() as session:
        db_ok = (await session.execute(text("SELECT 1"))).scalar() == 1
        operator_row = (
            await session.execute(
                select(AuthUser.nickname, AuthUser.phone).where(AuthUser.user_id == admin.user_id)
            )
        ).first()
        user_total = (
            await session.execute(select(func.count()).select_from(AuthUser))
        ).scalar() or 0
        company_total = (
            await session.execute(select(func.count()).select_from(Company))
        ).scalar() or 0
        actions_today = (
            await session.execute(
                select(func.count())
                .select_from(AgentAction)
                .where(AgentAction.created_at >= day_start)
            )
        ).scalar() or 0
        conversations_today = (
            await session.execute(
                select(func.count(func.distinct(AgentAction.conversation_id)))
                .select_from(AgentAction)
                .where(
                    AgentAction.created_at >= day_start,
                    AgentAction.conversation_id.is_not(None),
                )
            )
        ).scalar() or 0
        tokens_today = (
            await session.execute(
                select(func.coalesce(func.sum(LlmUsage.total_tokens), 0)).where(
                    LlmUsage.created_at >= day_start
                )
            )
        ).scalar() or 0
        tokens_month = (
            await session.execute(
                select(func.coalesce(func.sum(LlmUsage.total_tokens), 0)).where(
                    LlmUsage.created_at >= month_start
                )
            )
        ).scalar() or 0
        # 异常调用榜（M3）：近 7 天错误类型 Top + 最近 5 条非 ok 记录
        top_errors = (
            await session.execute(
                select(LlmUsage.error_type, func.count().label("cnt"))
                .where(
                    LlmUsage.status == "error",
                    LlmUsage.created_at >= now - timedelta(days=7),
                )
                .group_by(LlmUsage.error_type)
                .order_by(func.count().desc())
                .limit(3)
            )
        ).all()
        recent_errors = (
            (
                await session.execute(
                    select(LlmUsage)
                    .where(LlmUsage.status != "ok")
                    .order_by(LlmUsage.created_at.desc(), LlmUsage.id.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )

    return {
        "operator": {
            "userId": admin.user_id,
            "nickname": (operator_row[0] if operator_row else "") or "",
            "phone": (operator_row[1] if operator_row else "") or "",
        },
        "database": {
            "ok": db_ok,
            "engine": "sqlite" if os.getenv("ACCOUNTING_TEST_MODE") == "1" else "postgresql",
        },
        "counts": {"users": int(user_total), "companies": int(company_total)},
        "agent": {
            "actionsToday": int(actions_today),
            "conversationsToday": int(conversations_today),
        },
        "llm": {
            "circuitOpen": llm_circuit_state()["tripped"],
            "errorCount7d": int(sum(c for _, c in top_errors)),
            "topErrors": [
                {"type": t or "unknown", "count": int(c)} for t, c in top_errors
            ],
            "recentErrors": [
                {
                    "createdAt": e.created_at.isoformat() if e.created_at else None,
                    "scene": e.scene,
                    "model": e.model,
                    "errorType": e.error_type or "",
                    "userId": e.user_id,
                }
                for e in recent_errors
            ],
        },
        # M1 埋点已上线：自 llm_usage 建表（埋点上线日）起积累
        "usage": {
            "trackingEnabled": True,
            "tokensToday": int(tokens_today),
            "tokensThisMonth": int(tokens_month),
        },
        "version": APP_VERSION,
    }


_USAGE_GROUPS = {
    "user": LlmUsage.user_id,
    "company": LlmUsage.company_id,
    "model": LlmUsage.model,
    "day": func.date(LlmUsage.created_at),
}


@router.get("/usage/tokens")
async def usage_tokens(
    group_by: str = Query("model", pattern="^(user|company|day|model)$"),
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    _admin: AuthContext = Depends(require_admin),
):
    """token 用量多维统计：按用户/公司/天/模型 GROUP BY，附带成本估算列。"""
    today = datetime.now(timezone.utc).date()
    try:
        from_day = date.fromisoformat(from_date) if from_date else today - timedelta(days=29)
        to_day = date.fromisoformat(to_date) if to_date else today
    except ValueError:
        raise HTTPException(status_code=422, detail="from/to 需为 YYYY-MM-DD 日期") from None
    if from_day > to_day:
        raise HTTPException(status_code=422, detail="from 不能晚于 to")

    start = _utc_day_start(from_day)
    end = _utc_day_start(to_day + timedelta(days=1))  # 含 to 当天（左闭右开）
    dimension = _USAGE_GROUPS[group_by]

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(
                    dimension.label("dim"),
                    LlmUsage.model.label("model"),
                    func.count().label("calls"),
                    func.coalesce(func.sum(LlmUsage.prompt_tokens), 0).label("prompt"),
                    func.coalesce(func.sum(LlmUsage.completion_tokens), 0).label("completion"),
                    func.coalesce(func.sum(LlmUsage.total_tokens), 0).label("total"),
                )
                .where(LlmUsage.created_at >= start, LlmUsage.created_at < end)
                .group_by(dimension, LlmUsage.model)
            )
        ).all()

    # 先按（维度, 模型）聚合再在应用层折算成本并合并成维度行——跨模型混算时成本仍准确
    folded: dict = {}
    for dim, model, calls, prompt, completion, total in rows:
        key = str(dim) if dim is not None else ""
        slot = folded.setdefault(
            key,
            {"key": key, "calls": 0, "promptTokens": 0, "completionTokens": 0, "totalTokens": 0, "cost": 0.0},
        )
        cost = _cost_estimate(model, int(prompt), int(completion))
        slot["calls"] += int(calls)
        slot["promptTokens"] += int(prompt)
        slot["completionTokens"] += int(completion)
        slot["totalTokens"] += int(total)
        if cost is None:
            slot["cost"] = None
        elif slot["cost"] is not None:
            slot["cost"] += cost

    out_rows = []
    for slot in folded.values():
        cost = slot["cost"]
        out_rows.append(
            {
                "key": slot["key"],
                "calls": slot["calls"],
                "promptTokens": slot["promptTokens"],
                "completionTokens": slot["completionTokens"],
                "totalTokens": slot["totalTokens"],
                "costEstimate": round(cost, 4) if cost is not None else None,
            }
        )
    out_rows.sort(key=lambda r: r["totalTokens"], reverse=True)

    priced = [r for r in out_rows if r["costEstimate"] is not None]
    return {
        "groupBy": group_by,
        "from": from_day.isoformat(),
        "to": to_day.isoformat(),
        "pricingConfigured": bool(priced) and len(priced) == len(out_rows),
        "rows": out_rows,
    }


@router.get("/usage/recent")
async def usage_recent(
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None, pattern="^(ok|error|circuit_open)$"),
    _admin: AuthContext = Depends(require_admin),
):
    """最近 LLM/OCR/ASR 调用明细（排查慢调用与报错）。"""
    async with session_scope() as session:
        stmt = (
            select(LlmUsage)
            .order_by(LlmUsage.created_at.desc(), LlmUsage.id.desc())
            .limit(limit)
        )
        if status:
            stmt = stmt.where(LlmUsage.status == status)
        rows = (await session.execute(stmt)).scalars().all()

    return {
        "items": [
            {
                "id": r.id,
                "createdAt": r.created_at.isoformat() if r.created_at else None,
                "userId": r.user_id,
                "companyId": r.company_id,
                "scene": r.scene,
                "model": r.model,
                "promptTokens": r.prompt_tokens,
                "completionTokens": r.completion_tokens,
                "totalTokens": r.total_tokens,
                "durationMs": r.duration_ms,
                "status": r.status,
                "errorType": r.error_type,
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# M2：用户与公司管理 + 操作留痕
# ---------------------------------------------------------------------------

class AdminUserPatchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    is_disabled: Optional[bool] = Field(None, alias="isDisabled")
    is_platform_admin: Optional[bool] = Field(None, alias="isPlatformAdmin")
    plan_code: Optional[str] = Field(None, alias="planCode")
    plan_anchor_at: Optional[str] = Field(None, alias="planAnchorAt")
    plan_expires_at: Optional[str] = Field(None, alias="planExpiresAt")
    quota_bonus_tokens: Optional[int] = Field(None, alias="quotaBonusTokens")


async def _write_audit_log(
    operator_id: str,
    action: str,
    target_id: str,
    detail: dict,
    *,
    target_type: str = "user",
) -> None:
    """管理操作留痕；写入失败不影响主操作（留痕是尽力而为，不阻塞业务）。"""
    from db.models import AdminAuditLog

    try:
        async with session_scope() as session:
            session.add(
                AdminAuditLog(
                    operator_id=operator_id,
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    detail=detail,
                )
            )
    except Exception:  # noqa: BLE001
        logger.warning("admin_audit_logs 写入失败（忽略）: action=%s target=%s", action, target_id)


@router.get("/industry-templates")
async def admin_industry_templates(
    q: str = Query(default="", max_length=128),
    _admin: AuthContext = Depends(require_admin),
):
    """行业模板目录：管理员可看到隐藏、停用和自定义模板。"""
    async with session_scope() as session:
        rows = await list_templates(
            session,
            include_hidden=True,
            include_disabled=True,
            query=q,
        )
        return {
            "items": [serialize_template(template) for template in rows],
            "total": len(rows),
        }


@router.post("/industry-templates")
async def admin_create_industry_template(
    values: IndustryTemplateValues,
    admin: AuthContext = Depends(require_admin),
):
    try:
        async with session_scope() as session:
            template = await create_template(session, values, user_id=admin.user_id)
            result = serialize_template(template)
        await _write_audit_log(
            admin.user_id,
            "create_industry_template",
            result["id"],
            {"code": result["code"], "name": result["name"]},
            target_type="industry_template",
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/industry-templates/{template_id}")
async def admin_update_industry_template(
    template_id: str,
    values: IndustryTemplateValues,
    admin: AuthContext = Depends(require_admin),
):
    try:
        async with session_scope() as session:
            template: IndustryTemplate | None = await update_template(
                session,
                template_id,
                values,
                user_id=admin.user_id,
            )
            if template is None:
                raise HTTPException(status_code=404, detail="行业模板不存在")
            result = serialize_template(template)
            audit_detail = {
                "code": result["code"],
                "name": result["name"],
                "version": result["version"],
                "enabled": result["enabled"],
            }
        await _write_audit_log(
            admin.user_id,
            "update_industry_template",
            result["id"],
            audit_detail,
            target_type="industry_template",
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _user_summary(user: AuthUser, company_count: int = 0, roles: Optional[list] = None) -> dict:
    plan = get_plan(getattr(user, "plan_code", None))
    return {
        "userId": user.user_id,
        "nickname": user.nickname or "",
        "phone": user.phone or "",
        "isPlatformAdmin": bool(user.is_platform_admin),
        "isDisabled": bool(user.is_disabled),
        "planCode": plan.code,
        "planName": plan.name,
        "planAnchorAt": user.plan_anchor_at or "",
        "quotaBonusTokens": int(getattr(user, "quota_bonus_tokens", 0) or 0),
        "companyCount": company_count,
        "roles": roles or [],
        "createdAt": user.created_at,
        "lastLoginAt": user.last_login_at or "",
    }


@router.get("/users")
async def admin_users(
    query: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    _admin: AuthContext = Depends(require_admin),
):
    """用户列表：搜索（ID/手机号/昵称模糊）+ 分页，含公司数与角色。"""
    from db.models import AdminAuditLog  # noqa: F401  (确保模型已加载)

    async with session_scope() as session:
        stmt = select(AuthUser)
        if query.strip():
            like = f"%{query.strip()}%"
            stmt = stmt.where(
                AuthUser.user_id.ilike(like)
                | AuthUser.phone.ilike(like)
                | AuthUser.nickname.ilike(like)
            )
        total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
        rows = (
            (
                await session.execute(
                    stmt.order_by(AuthUser.created_at.desc(), AuthUser.user_id)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )

        memberships = {}
        if rows:
            member_rows = (
                await session.execute(
                    select(CompanyMember.user_id, CompanyMember.role).where(
                        CompanyMember.user_id.in_([u.user_id for u in rows])
                    )
                )
            ).all()
            for user_id, role in member_rows:
                roles, count = memberships.setdefault(user_id, ([], 0))
                memberships[user_id] = (roles + ([role] if role not in roles else []), count + 1)

    items = []
    for u in rows:
        roles, count = memberships.get(u.user_id, ([], 0))
        items.append(_user_summary(u, company_count=count, roles=roles))
    return {"items": items, "total": int(total), "page": page, "pageSize": page_size}


@router.patch("/users/{user_id}")
async def admin_patch_user(
    user_id: str,
    req: AdminUserPatchRequest,
    admin: AuthContext = Depends(require_admin),
):
    """禁用/启用用户、授予/撤销平台管理员、切换商业套餐/锚点日期/赠送额度。

    禁止管理员修改自己的禁用/管理员标记，避免自锁。
    """
    if user_id == admin.user_id and (req.is_disabled is not None or req.is_platform_admin is not None):
        raise HTTPException(status_code=422, detail="不能修改自己的管理状态，请由其他管理员操作")

    anchor_provided = "plan_anchor_at" in req.model_fields_set
    anchor_to_set: Optional[str] = None
    if anchor_provided:
        raw_anchor = (req.plan_anchor_at or "").strip()
        if raw_anchor:
            parsed_anchor = quota_service.parse_iso_date(raw_anchor)
            if parsed_anchor is None:
                raise HTTPException(status_code=422, detail="planAnchorAt 日期格式无效，应为 YYYY-MM-DD")
            anchor_to_set = parsed_anchor.isoformat()
        else:
            anchor_to_set = None

    expires_provided = "plan_expires_at" in req.model_fields_set
    expires_to_set: Optional[str] = None
    if expires_provided:
        raw_expires = (req.plan_expires_at or "").strip()
        if raw_expires:
            parsed_expires = quota_service.parse_iso_date(raw_expires)
            if parsed_expires is None:
                raise HTTPException(status_code=422, detail="planExpiresAt 日期格式无效，应为 YYYY-MM-DD")
            expires_to_set = parsed_expires.isoformat()
        else:
            expires_to_set = None

    if req.plan_code is not None and req.plan_code not in PLAN_CATALOG:
        raise HTTPException(status_code=422, detail=f"无效套餐编码: {req.plan_code}")
    if req.quota_bonus_tokens is not None and req.quota_bonus_tokens < 0:
        raise HTTPException(status_code=422, detail="赠送额度不能为负数")

    actions = []
    if req.is_disabled is not None:
        actions.append("disable_user" if req.is_disabled else "enable_user")
    if req.is_platform_admin is not None:
        actions.append("grant_admin" if req.is_platform_admin else "revoke_admin")
    if req.plan_code is not None or anchor_provided or expires_provided:
        actions.append("change_plan")
    if req.quota_bonus_tokens is not None:
        actions.append("update_quota_bonus")
    if not actions:
        raise HTTPException(status_code=422, detail="未提供任何要修改的字段")

    plan_touched = (
        req.plan_code is not None or anchor_provided or expires_provided or req.quota_bonus_tokens is not None
    )

    async with session_scope() as session:
        user = (
            await session.execute(select(AuthUser).where(AuthUser.user_id == user_id))
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        before = {
            "isDisabled": bool(user.is_disabled),
            "isPlatformAdmin": bool(user.is_platform_admin),
            "planCode": getattr(user, "plan_code", None) or "trial",
            "planAnchorAt": user.plan_anchor_at or "",
            "planExpiresAt": user.plan_expires_at or "",
            "quotaBonusTokens": int(getattr(user, "quota_bonus_tokens", 0) or 0),
        }
        if req.is_disabled is not None:
            user.is_disabled = req.is_disabled
        if req.is_platform_admin is not None:
            user.is_platform_admin = req.is_platform_admin
        if req.plan_code is not None:
            user.plan_code = req.plan_code
        if anchor_provided:
            user.plan_anchor_at = anchor_to_set
        if expires_provided:
            user.plan_expires_at = expires_to_set
        if req.quota_bonus_tokens is not None:
            user.quota_bonus_tokens = req.quota_bonus_tokens
        after = {
            "isDisabled": bool(user.is_disabled),
            "isPlatformAdmin": bool(user.is_platform_admin),
            "planCode": getattr(user, "plan_code", None) or "trial",
            "planAnchorAt": user.plan_anchor_at or "",
            "planExpiresAt": user.plan_expires_at or "",
            "quotaBonusTokens": int(getattr(user, "quota_bonus_tokens", 0) or 0),
        }

    if plan_touched:
        # 套餐/锚点/加量变更需立即失效 30s 配额缓存，保证用户端与卡口即时生效
        quota_service.clear_state_cache()

    for action in actions:
        await _write_audit_log(
            admin.user_id,
            action,
            user_id,
            {"before": before, "after": after},
        )
    return {"user": _user_summary(user), "changed": actions}


@router.get("/companies")
async def admin_companies(
    query: str = "",
    _admin: AuthContext = Depends(require_admin),
):
    """公司列表：成员数、账套凭证量、最近活跃（最近一次凭证创建时间）。"""
    name_expr = Company.payload["name"].as_string()

    member_sq = (
        select(CompanyMember.company_id.label("cid"), func.count().label("member_count"))
        .group_by(CompanyMember.company_id)
        .subquery()
    )
    entry_sq = (
        select(
            JournalEntry.company_id.label("cid"),
            func.count().label("entry_count"),
            func.max(JournalEntry.created_at).label("last_activity"),
        )
        .group_by(JournalEntry.company_id)
        .subquery()
    )

    async with session_scope() as session:
        stmt = (
            select(
                Company,
                AuthUser.nickname.label("owner_nickname"),
                AuthUser.phone.label("owner_phone"),
                func.coalesce(member_sq.c.member_count, 0).label("member_count"),
                func.coalesce(entry_sq.c.entry_count, 0).label("entry_count"),
                entry_sq.c.last_activity.label("last_activity"),
            )
            .outerjoin(AuthUser, AuthUser.user_id == Company.owner_user_id)
            .outerjoin(member_sq, member_sq.c.cid == Company.company_id)
            .outerjoin(entry_sq, entry_sq.c.cid == Company.company_id)
        )
        if query.strip():
            like = f"%{query.strip()}%"
            stmt = stmt.where(Company.company_id.ilike(like) | name_expr.ilike(like))
        rows = (await session.execute(stmt.order_by(Company.created_at.desc()))).all()

    items = []
    for company, owner_nickname, owner_phone, member_count, entry_count, last_activity in rows:
        items.append(
            {
                "companyId": company.company_id,
                "name": (company.payload or {}).get("name") or "",
                "ownerUserId": company.owner_user_id,
                "ownerName": owner_nickname or "",
                "ownerPhone": owner_phone or "",
                "baseCurrency": company.base_currency,
                "accountingStandard": company.accounting_standard,
                "memberCount": int(member_count),
                "entryCount": int(entry_count),
                "lastActivityAt": last_activity.isoformat() if last_activity else None,
                "createdAt": company.created_at,
            }
        )
    return {"items": items, "total": len(items)}


@router.get("/audit-logs")
async def admin_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100, alias="pageSize"),
    _admin: AuthContext = Depends(require_admin),
):
    """管理操作留痕查询（倒序分页）。"""
    from db.models import AdminAuditLog

    async with session_scope() as session:
        total = (
            await session.execute(select(func.count()).select_from(AdminAuditLog))
        ).scalar() or 0
        rows = (
            (
                await session.execute(
                    select(AdminAuditLog)
                    .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
    return {
        "items": [
            {
                "id": r.id,
                "operatorId": r.operator_id,
                "action": r.action,
                "targetType": r.target_type,
                "targetId": r.target_id,
                "detail": r.detail,
                "createdAt": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "total": int(total),
        "page": page,
        "pageSize": page_size,
    }


# ---------------------------------------------------------------------------
# M3：Agent 审计 + 数据运维
# ---------------------------------------------------------------------------

class AgentActionMarkRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(pattern="^(approved|rejected|expired)$")
    note: Optional[str] = None


@router.get("/agent-actions")
async def admin_agent_actions(
    status: str = Query("all", pattern="^(all|pending|approved|rejected|expired)$"),
    company_id: str = Query("", alias="companyId"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    _admin: AuthContext = Depends(require_admin),
):
    """Agent 动作流水（列表不含 payload/evidence 大字段，详情走 /{id}）。"""
    async with session_scope() as session:
        stmt = select(AgentAction)
        if status != "all":
            stmt = stmt.where(AgentAction.status == status)
        if company_id.strip():
            stmt = stmt.where(AgentAction.company_id == company_id.strip())
        total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
        rows = (
            (
                await session.execute(
                    stmt.order_by(AgentAction.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
    return {
        "items": [
            {
                "id": r.id,
                "companyId": r.company_id,
                "userId": r.user_id,
                "conversationId": r.conversation_id,
                "actionType": r.action_type,
                "title": r.title,
                "status": r.status,
                "createdAt": r.created_at.isoformat() if r.created_at else None,
                "resolvedAt": r.resolved_at.isoformat() if r.resolved_at else None,
                "resolvedBy": r.resolved_by,
            }
            for r in rows
        ],
        "total": int(total),
        "page": page,
        "pageSize": page_size,
    }


@router.get("/agent-actions/{action_id}")
async def admin_agent_action_detail(
    action_id: str,
    _admin: AuthContext = Depends(require_admin),
):
    """Agent 动作详情：含 payload/evidence/result 全量。"""
    async with session_scope() as session:
        row = (
            await session.execute(select(AgentAction).where(AgentAction.id == action_id))
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="动作不存在")
    return {
        "id": row.id,
        "companyId": row.company_id,
        "userId": row.user_id,
        "conversationId": row.conversation_id,
        "actionType": row.action_type,
        "title": row.title,
        "status": row.status,
        "payload": row.payload,
        "evidence": row.evidence,
        "result": row.result,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "resolvedAt": row.resolved_at.isoformat() if row.resolved_at else None,
        "resolvedBy": row.resolved_by,
    }


@router.patch("/agent-actions/{action_id}")
async def admin_mark_agent_action(
    action_id: str,
    req: AgentActionMarkRequest,
    admin: AuthContext = Depends(require_admin),
):
    """手动标记处理（运维工具）：不执行任何业务逻辑，仅改变状态。

    仅待确认（pending）的动作可被标记；处理人记录为当前管理员。
    """
    async with session_scope() as session:
        row = (
            await session.execute(select(AgentAction).where(AgentAction.id == action_id))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="动作不存在")
        if row.status != "pending":
            raise HTTPException(status_code=422, detail=f"仅待确认动作可标记（当前 {row.status}）")
        row.status = req.status
        row.resolved_at = datetime.now(timezone.utc)
        row.resolved_by = admin.user_id
        marked_at = row.resolved_at

    await _write_audit_log(
        admin.user_id,
        f"agent_action_{req.status}",
        action_id,
        {"targetType": "agent_action", "note": req.note or "", "markedAt": marked_at.isoformat()},
    )
    return {"id": action_id, "status": req.status, "resolvedBy": admin.user_id}


class CleanupRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    table: str = Field(pattern="^(llm_usage|agent_actions|admin_audit_logs)$")
    before: str  # YYYY-MM-DD，删除该 UTC 日期零点之前的记录
    dryRun: bool = False


@router.get("/cleanup/stats")
async def admin_cleanup_stats(_admin: AuthContext = Depends(require_admin)):
    """三张可清理膨胀表的规模概览（总行数 + 最早记录时间）。"""
    from db.models import AdminAuditLog

    async with session_scope() as session:
        items = []
        for name, model in (
            ("llm_usage", LlmUsage),
            ("agent_actions", AgentAction),
            ("admin_audit_logs", AdminAuditLog),
        ):
            total, oldest = (
                await session.execute(
                    select(func.count(), func.min(model.created_at)).select_from(model)
                )
            ).one()
            items.append(
                {
                    "table": name,
                    "total": int(total or 0),
                    "oldest": oldest.isoformat() if oldest else None,
                }
            )
    return {"items": items}


@router.post("/cleanup")
async def admin_cleanup(
    req: CleanupRequest,
    admin: AuthContext = Depends(require_admin),
):
    """数据运维：按时间清理膨胀表（llm_usage / agent_actions / admin_audit_logs）。

    dryRun=true 仅返回将删除的行数；执行删除会写审计留痕。
    """
    from db.models import AdminAuditLog

    try:
        before_day = date.fromisoformat(req.before)
    except ValueError:
        raise HTTPException(status_code=422, detail="before 需为 YYYY-MM-DD 日期") from None
    boundary = _utc_day_start(before_day)

    model = AdminAuditLog if req.table == "admin_audit_logs" else None
    if model is None:
        from db.models import LlmUsage as _llm, AgentAction as _agent

        model = _llm if req.table == "llm_usage" else _agent

    async with session_scope() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(model).where(model.created_at < boundary)
            )
        ).scalar() or 0
        deleted = 0
        if not req.dryRun and count:
            result = await session.execute(
                model.__table__.delete().where(model.created_at < boundary)
            )
            deleted = result.rowcount or 0

    if not req.dryRun:
        await _write_audit_log(
            admin.user_id,
            "cleanup",
            req.table,
            {"before": req.before, "deleted": int(deleted), "preview": int(count)},
        )
    return {
        "table": req.table,
        "before": req.before,
        "dryRun": req.dryRun,
        "matched": int(count),
        "deleted": int(deleted),
    }

              
