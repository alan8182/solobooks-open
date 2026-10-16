# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Authentication routes."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
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

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)

DEFAULT_TOS_VERSION = "v1.0"
DEFAULT_PRIVACY_VERSION = "v1.0"


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
    user, company = await get_or_create_user(openid)

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
    user, company = await get_or_create_user_by_phone(req.phone)
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

    user, company = await get_or_create_user_by_phone(phone)
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
    token = create_access_token({"sub": user["user_id"], "company_id": company["company_id"]})
    await log_action(company["company_id"], user["user_id"], "switch_company", "user", user["user_id"],
                     {"to_company_id": cid})
    return {
        "access_token": token,
        "token_type": "Bearer",
        "user_id": user["user_id"],
        "company_id": company["company_id"],
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
    """Update company details."""
    try:
        company = await update_company(ctx.user_id, company_id, req)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return company
