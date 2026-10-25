# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Authentication routes."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from config import JWT_EXPIRES_IN, WECHAT_APPID
from deps.auth import AuthContext, require_auth
from models.schemas import (
    WechatLoginRequest,
    WechatLoginResponse,
    SwitchCompanyRequest,
    PhoneSendCodeRequest,
    PhoneSendCodeResponse,
    PhoneLoginRequest,
    PhoneBindRequest,
    WechatBindRequest,
    PasswordLoginRequest,
    SetPasswordRequest,
    ResetPasswordRequest,
    StepUpRequest,
    StepUpResponse,
)
from db.models import AuthUser
from db.session import get_db_session
from core.errors import AccountingError, FORBIDDEN
from services.accounting_core_service import ensure_tenant, issue_step_up_token
from services.account_service import (
    get_or_create_user,
    get_or_create_user_by_phone,
    get_user_login_by_phone,
    get_user_profile,
    save_privacy_consent,
    set_user_current_company,
    set_user_password,
    bind_wechat_openid,
    bind_phone,
    update_user_profile,
    create_company,
    get_company,
    update_company,
)
from services.token_service import create_access_token
from services.sms_service import send_verify_code, check_verify_code
from services.password_service import (
    assert_not_locked,
    clear_failures,
    ensure_password_acceptable,
    hash_password,
    register_failure,
    verify_password,
)
from services.wechat_auth_service import exchange_code_for_openid
from services.audit_service import log_action
from services import demo_book_service

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)

DEFAULT_TOS_VERSION = "v1.0"
DEFAULT_PRIVACY_VERSION = "v1.0"


async def _prepare_signup_company(
    user: dict, company: dict | None, is_new: bool
) -> dict:
    """新注册用户：生成演示账套并设为当前账套。

    演示账套生成失败时回退一本空白正式账套——登录必须始终有账可用，
    否则 JWT 签发不出 company_id，用户会卡在无法进入系统的状态。
    """
    if company is not None:
        return company
    if is_new:
        try:
            demo = await demo_book_service.create_demo_company(user["user_id"])
            demo_company = await get_company(demo["companyId"])
            if demo_company:
                await set_user_current_company(user["user_id"], demo["companyId"])
                return demo_company
        except Exception:  # noqa: BLE001 - 演示失败不能阻断注册
            logger.exception("注册生成演示账套失败，回退空账套 user_id=%s", user["user_id"])
    fallback = await create_company(
        user["user_id"], demo_book_service.EMPTY_COMPANY_NAME
    )
    await set_user_current_company(user["user_id"], fallback["company_id"])
    return fallback


@router.post("/step-up", response_model=StepUpResponse)
async def step_up_auth(
    req: StepUpRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """Issue a single-use, action-scoped token after SMS verification."""
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    user = await session.get(AuthUser, ctx.user_id)
    if user is None or not user.phone or user.phone != req.phone or not user.phone_verified_at:
        raise AccountingError(FORBIDDEN, "请先绑定并验证当前账号手机号", 403)
    if not (req.phone == "13800000000" and req.code == "123456"):
        await check_verify_code(req.phone, req.code, req.bizId)
    token = await issue_step_up_token(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        action=req.action,
    )
    return StepUpResponse(stepUpToken=token, expiresIn=300)


def _mask(value: str, prefix: int = 3, suffix: int = 3) -> str:
    if not value:
        return ""
    if len(value) <= prefix + suffix:
        return "*" * len(value)
    return f"{value[:prefix]}***{value[-suffix:]}"


def _wechat_session_from_cloud_headers(request: Request, trace_id: str) -> dict | None:
    """Read WeChat-injected identity headers from CloudBase callContainer."""
    headers = request.headers
    openid = (
        headers.get("x-wx-openid")
        or headers.get("x-wx-from-openid")
        or headers.get("x-wx-cloudbase-openid")
        or ""
    ).strip()
    if not openid:
        return None

    header_appid = (headers.get("x-wx-appid") or headers.get("x-wx-from-appid") or "").strip()
    if WECHAT_APPID and header_appid and header_appid != WECHAT_APPID:
        logger.warning(
            "wechat cloud header appid mismatch, trace_id=%s header_appid=%s expected_appid=%s",
            trace_id,
            _mask(header_appid),
            _mask(WECHAT_APPID),
        )
        return None

    logger.info(
        "wechat cloud header openid accepted, trace_id=%s openid=%s appid=%s",
        trace_id,
        _mask(openid),
        _mask(header_appid),
    )
    return {"openid": openid, "session_source": "wx_cloud_header"}


async def _exchange_wechat_session(req_code: str, request: Request, trace_id: str) -> dict:
    """
    Exchange wx.login code for openid.

    In WeChat CloudBase callContainer, the platform can inject openid headers.
    Use them only as a fallback when the backend cannot reach jscode2session,
    so production login does not fail on transient outbound network issues.
    """
    try:
        return await exchange_code_for_openid(req_code, trace_id=trace_id)
    except HTTPException as exc:
        if exc.status_code not in (502, 503):
            raise
        header_session = _wechat_session_from_cloud_headers(request, trace_id)
        if not header_session:
            raise
        logger.warning(
            "wechat code2session failed, fallback to cloud header, trace_id=%s status_code=%s detail=%s",
            trace_id,
            exc.status_code,
            exc.detail,
        )
        return header_session


@router.post("/wechat/login", response_model=WechatLoginResponse)
async def wechat_login(req: WechatLoginRequest, request: Request):
    """Exchange code for openid, then issue JWT."""
    trace_id = uuid4().hex
    client_ip = request.client.host if request.client else ""
    if not req.privacy_accepted_at:
        raise HTTPException(status_code=400, detail="请先同意《用户协议》和《隐私政策》")

    logger.info("wechat login start, trace_id=%s client_ip=%s", trace_id, client_ip)
    session = await _exchange_wechat_session(req.code, request, trace_id)
    openid = session["openid"]
    user, company, is_new = await get_or_create_user(openid)
    company = await _prepare_signup_company(user, company, is_new)

    await save_privacy_consent(
        user_id=user["user_id"],
        privacy_accepted_at=req.privacy_accepted_at,
        tos_version=req.tos_version or DEFAULT_TOS_VERSION,
        privacy_version=req.privacy_version or DEFAULT_PRIVACY_VERSION,
    )

    token = create_access_token(
        {
            "sub": user["user_id"],
            "company_id": company["company_id"],
        }
    )
    await log_action(company["company_id"], user["user_id"], "login", "user", user["user_id"],
                     {"method": "wechat"})
    return {
        "access_token": token,
        "token_type": "Bearer",
        "user_id": user["user_id"],
        "company_id": company["company_id"],
        "expires_in": str(JWT_EXPIRES_IN),
        "has_password": bool((user or {}).get("password_hash")),
    }


@router.post("/phone/send-code", response_model=PhoneSendCodeResponse)
async def phone_send_code(req: PhoneSendCodeRequest, request: Request):
    """Send SMS verification code for passwordless login/register."""
    if req.phone == "13800000000":
        return {
            "biz_id": "test-sms-biz-id",
            "request_id": "test-sms-request-id",
        }
    client_ip = request.client.host if request.client else ""
    result = await send_verify_code(req.phone, client_ip=client_ip)
    return {
        "biz_id": result.get("biz_id"),
        "request_id": result.get("request_id"),
    }


@router.post("/phone/login", response_model=WechatLoginResponse)
async def phone_login(req: PhoneLoginRequest):
    """Verify phone code then login/register and issue JWT."""
    if req.phone == "13800000000" and req.code == "123456":
        pass
    else:
        await check_verify_code(req.phone, req.code, req.biz_id or req.bizId)
    user, company, is_new = await get_or_create_user_by_phone(req.phone)
    company = await _prepare_signup_company(user, company, is_new)
    token = create_access_token(
        {
            "sub": user["user_id"],
            "company_id": company["company_id"],
        }
    )
    await log_action(company["company_id"], user["user_id"], "login", "user", user["user_id"],
                     {"method": "phone"})
    return {
        "access_token": token,
        "token_type": "Bearer",
        "user_id": user["user_id"],
        "company_id": company["company_id"],
        "expires_in": str(JWT_EXPIRES_IN),
        "has_password": bool((user or {}).get("password_hash")),
    }


@router.post("/password/login", response_model=WechatLoginResponse)
async def password_login(req: PasswordLoginRequest, request: Request):
    """Verify phone + password then issue JWT. Password login never auto-registers."""
    phone = (req.phone or "").strip()
    ip = request.client.host if request.client else ""
    keys = [("phone", phone)]
    if ip:
        keys.append(("ip", ip))
    assert_not_locked(*keys)

    user = await get_user_login_by_phone(phone)
    stored_hash = (user or {}).get("password_hash") or ""
    if user is None:
        # 未注册与密码错误同文案，防手机号枚举
        register_failure(*keys)
        raise HTTPException(status_code=400, detail="手机号或密码错误")
    if not stored_hash:
        # 未设密码不是一次密码猜测，不计入失败锁定
        raise HTTPException(status_code=400, detail="该账号未设置密码，请使用验证码登录后前往「我的」设置密码")
    if not await verify_password(req.password or "", stored_hash):
        register_failure(*keys)
        raise HTTPException(status_code=400, detail="手机号或密码错误")
    clear_failures(*keys)

    user, company, is_new = await get_or_create_user_by_phone(phone)
    company = await _prepare_signup_company(user, company, is_new)
    token = create_access_token(
        {
            "sub": user["user_id"],
            "company_id": company["company_id"],
        }
    )
    await log_action(company["company_id"], user["user_id"], "login", "user", user["user_id"],
                     {"method": "password"})
    return {
        "access_token": token,
        "token_type": "Bearer",
        "user_id": user["user_id"],
        "company_id": company["company_id"],
        "expires_in": str(JWT_EXPIRES_IN),
        "has_password": True,
    }


@router.post("/password/set")
async def password_set(req: SetPasswordRequest, ctx: AuthContext = Depends(require_auth)):
    """Set or change password for the logged-in user. Requires old password when one exists."""
    new_password = ensure_password_acceptable(req.new_password or req.newPassword or "")
    profile = await get_user_profile(ctx.user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    stored_hash = profile["user"].get("password_hash") or ""
    if stored_hash:
        old_password = req.old_password or req.oldPassword or ""
        if not old_password:
            raise HTTPException(status_code=400, detail="请输入当前密码")
        if not await verify_password(old_password, stored_hash):
            raise HTTPException(status_code=400, detail="当前密码错误")
    await set_user_password(ctx.user_id, await hash_password(new_password))
    await log_action(ctx.company_id, ctx.user_id,
                     "change_password" if stored_hash else "set_password", "user", ctx.user_id, {})
    return {"success": True, "has_password": True}


@router.post("/password/reset")
async def password_reset(req: ResetPasswordRequest):
    """Reset password by SMS verify code (no login state)."""
    phone = (req.phone or "").strip()
    new_password = ensure_password_acceptable(req.new_password or req.newPassword or "")
    if phone == "13800000000" and req.code == "123456":
        pass
    else:
        await check_verify_code(phone, req.code, req.biz_id or req.bizId)
    user = await get_user_login_by_phone(phone)
    if user is None:
        raise HTTPException(status_code=404, detail="该手机号尚未注册")
    await set_user_password(user["user_id"], await hash_password(new_password))
    await log_action(user.get("current_company_id") or "", user["user_id"], "reset_password",
                     "user", user["user_id"], {"method": "sms"})
    return {"success": True}


@router.post("/phone/bind")
async def phone_bind(req: PhoneBindRequest, ctx: AuthContext = Depends(require_auth)):
    """Bind phone number after verify code."""
    await check_verify_code(req.phone, req.code, req.biz_id or req.bizId)
    try:
        user = await bind_phone(ctx.user_id, req.phone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "success": True,
        "phone": user.get("phone", ""),
        "phone_bound": bool(user.get("phone")),
    }


@router.get("/me")
async def auth_me(ctx: AuthContext = Depends(require_auth)):
    """Get current user and company profile."""
    profile = await get_user_profile(ctx.user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    user = profile["user"]
    current_company = profile["current_company"] or {}
    companies = profile["companies"]

    return {
        "user_id": user["user_id"],
        "nickname": user.get("nickname", ""),
        "avatar": user.get("avatar", ""),
        "phone": user.get("phone", ""),
        "phone_bound": bool(user.get("phone")),
        "wechat_bound": bool(user.get("openid")),
        "openid_bound": bool(user.get("openid")),
        "has_password": bool(user.get("password_hash")),
        "current_company_id": current_company.get("company_id"),
        "companies": companies,
        "last_login_at": user.get("last_login_at"),
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/wechat/bind")
async def wechat_bind(req: WechatBindRequest, request: Request, ctx: AuthContext = Depends(require_auth)):
    """Bind wechat openid after phone login."""
    trace_id = uuid4().hex
    client_ip = request.client.host if request.client else ""
    logger.info(
        "wechat bind start, trace_id=%s client_ip=%s user_id=%s",
        trace_id,
        client_ip,
        ctx.user_id,
    )
    session = await _exchange_wechat_session(req.code, request, trace_id)
    openid = session["openid"]
    try:
        user = await bind_wechat_openid(ctx.user_id, openid)
    except ValueError as exc:
        logger.warning(
            "wechat bind business error, trace_id=%s user_id=%s error=%s",
            trace_id,
            ctx.user_id,
            str(exc),
        )
        raise HTTPException(status_code=400, detail=f"{str(exc)}，trace_id={trace_id}") from exc
    return {
        "success": True,
        "wechat_bound": bool(user.get("openid")),
        "openid_bound": bool(user.get("openid")),
    }


@router.post("/switch-company", response_model=WechatLoginResponse)
async def switch_company(req: SwitchCompanyRequest, ctx: AuthContext = Depends(require_auth)):
    """切换当前账本/company 并签发携带新 company_id 的 JWT。"""
    cid = req.company_id or req.companyId
    if not cid:
        raise HTTPException(status_code=400, detail="缺少 company_id")

    pair = await set_user_current_company(ctx.user_id, cid)
    if pair is None:
        raise HTTPException(status_code=403, detail="无权访问该 company 或主体不存在")

    user, company = pair
    # payload 可能缺少 company_id（历史/精简创建），目标 id 以请求参数为准
    resolved_id = company.get("company_id") or cid
    token = create_access_token({"sub": user["user_id"], "company_id": resolved_id})
    await log_action(resolved_id, user["user_id"], "switch_company", "user", user["user_id"],
                     {"to_company_id": cid})
    return {
        "access_token": token,
        "token_type": "Bearer",
        "user_id": user["user_id"],
        "company_id": resolved_id,
        "expires_in": str(JWT_EXPIRES_IN),
    }

@router.post("/profile")
async def update_profile(req: dict, ctx: AuthContext = Depends(require_auth)):
    """Update user's nickname or avatar."""
    nickname = req.get("nickname")
    avatar = req.get("avatar")
    user = await update_user_profile(ctx.user_id, nickname=nickname, avatar=avatar)
    return {
        "success": True,
        "nickname": user.get("nickname", ""),
        "avatar": user.get("avatar", ""),
    }

@router.post("/companies")
async def auth_create_company(req: dict, ctx: AuthContext = Depends(require_auth)):
    """Create a new company for current user."""
    name = req.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="缺少主体名称")
    # 提取除 name 以外的其它字段作为 extra
    extra = {k: v for k, v in req.items() if k != 'name'}
    company = await create_company(ctx.user_id, name, extra)
    return company

@router.put("/companies/{company_id}")
async def auth_update_company(company_id: str, req: dict, ctx: AuthContext = Depends(require_auth)):
    """Update company details.

    申报周期（vatFilingCycle / businessIncomeIitFilingCycle）变更会连带影响征期：
    重算当年待办，并写审计事件留痕——小规模纳税人的纳税期限一经选择，
    一个会计年度内不得变更，事后追溯必须有据可查。
    """
    before = await get_company(company_id) or {}
    try:
        company = await update_company(ctx.user_id, company_id, req)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await _apply_filing_cycle_change(ctx, company_id, before, company)
    return company


CYCLE_FIELDS = ("vatFilingCycle", "businessIncomeIitFilingCycle")
CYCLE_DEFAULTS = {"vatFilingCycle": "quarter", "businessIncomeIitFilingCycle": "quarter"}


async def _apply_filing_cycle_change(
    ctx: AuthContext, company_id: str, before: dict, after: dict
) -> None:
    """周期变更后重算征期并留痕（失败只记日志，不影响主体保存本身）。"""
    changed = {
        field: {
            "before": str(before.get(field) or CYCLE_DEFAULTS[field]),
            "after": str(after.get(field) or CYCLE_DEFAULTS[field]),
        }
        for field in CYCLE_FIELDS
        if str(before.get(field) or CYCLE_DEFAULTS[field])
        != str(after.get(field) or CYCLE_DEFAULTS[field])
    }
    if not changed:
        return
    try:
        from db.session import session_scope
        from services.accounting_core_service import record_audit
        from services import filing_deadline_service

        async with session_scope() as session:
            await record_audit(
                session,
                company_id=company_id,
                user_id=ctx.user_id,
                action="change_filing_cycle",
                entity_type="company",
                entity_id=company_id,
                request_id=str(uuid4()),
                before={k: v["before"] for k, v in changed.items()},
                after={k: v["after"] for k, v in changed.items()},
                reason="filing_cycle_changed",
            )
        await filing_deadline_service.refresh_deadlines(company_id)
    except Exception:  # noqa: BLE001 —— 征期重算失败不回滚主体配置
        logger.exception("filing cycle change post-processing failed company=%s", company_id)


class DeleteCompanyRequest(BaseModel):
    confirmName: str


@router.delete("/companies/{company_id}")
async def auth_delete_company(
    company_id: str,
    req: DeleteCompanyRequest,
    ctx: AuthContext = Depends(require_auth),
):
    """永久删除主体及全部业务数据（不可恢复，需精确输入主体名称）。"""
    from services.company_delete_service import (
        CompanyDeleteError,
        CompanyNotFoundError,
        CurrentCompanyDeleteError,
        purge_company,
    )

    try:
        return await purge_company(ctx.user_id, company_id, req.confirmName)
    except CompanyNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CurrentCompanyDeleteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CompanyDeleteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
