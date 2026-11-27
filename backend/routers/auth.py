# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Authentication routes."""
from __future__ import annotations

import asyncio
import base64
import hmac
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    JWT_EXPIRES_IN,
    WECHAT_APPID,
    WECHAT_WEB_FRONTEND_CALLBACK_PATH,
    WECHAT_WEB_REDIRECT_URI,
    WECHAT_WEB_STATE_TTL_SECONDS,
    WECHAT_WEB_TICKET_TTL_SECONDS,
    wechat_web_login_enabled,
)
from deps.auth import AuthContext, require_auth, _dev_fallback_enabled
from models.schemas import (
    WechatLoginRequest,
    WechatLoginResponse,
    WechatWebAuthorizeResponse,
    WechatWebBindPhoneRequest,
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
from db.models import AuthUser, Company, JournalEntry
from db.session import get_db_session, session_scope
from core.errors import AccountingError, FORBIDDEN, ONBOARDING_TYPE_LOCKED
from services.accounting_core_service import (
    consume_step_up_token,
    ensure_tenant,
    issue_step_up_token,
)
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
    find_user_by_wechat_web,
    create_wechat_login_ticket,
    get_wechat_login_ticket,
    exchange_wechat_login_ticket,
    bind_phone_with_wechat_web,
    update_user_profile,
    create_company,
    get_company,
    update_company,
)
from services.company_license_service import LICENSE_PROMPT, normalize_license_result
from services.llm import chat_vision_json
from services.zhipu_ocr import resolve_receipt_image_mime, validate_receipt_image_bytes
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
from services.wechat_web_auth_service import (
    WechatWebAuthError,
    build_authorize_url,
    create_state,
    exchange_code_for_token,
    fetch_userinfo,
    parse_state_cookie,
)
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
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """Issue a single-use, action-scoped token after SMS verification.

    二次验证的验证码同样做失败锁定（按手机号+IP）：step-up 通往期初改账、
    注销等敏感操作，验证码可被穷举时等于敏感操作只隔一层 6 位码。
    """
    # 删除主体是在「当前主体」之外操作另一本账：令牌必须绑定目标主体。
    # 不能对任意 companyId 调 ensure_tenant——那会在主体不存在时自动建一本新账。
    if req.action == "delete_company":
        target_company_id = (req.companyId or "").strip()
        if not target_company_id:
            raise AccountingError("COMPANY_REQUIRED", "删除主体需要指定目标主体", 400)
        company = await session.get(Company, target_company_id)
        if company is None or company.owner_user_id != ctx.user_id:
            raise AccountingError(FORBIDDEN, "主体不存在或无权删除", 403)
    else:
        target_company_id = ctx.company_id
        await ensure_tenant(session, target_company_id, ctx.user_id)

    user = await session.get(AuthUser, ctx.user_id)
    if user is None or not user.phone or user.phone != req.phone or not user.phone_verified_at:
        raise AccountingError(FORBIDDEN, "请先绑定并验证当前账号手机号", 403)
    # 测试手机号万能验证码仅限开发兜底模式（AUTH_DEV_FALLBACK=1）；生产模式强制走真实短信校验。
    if not (_dev_fallback_enabled() and req.phone == "13800000000" and req.code == "123456"):
        ip = request.client.host if request.client else ""
        keys = [("phone", (req.phone or "").strip())]
        if ip:
            keys.append(("ip", ip))
        assert_not_locked(*keys)
        try:
            await check_verify_code(req.phone, req.code, req.bizId)
        except HTTPException:
            register_failure(*keys)
            raise
        clear_failures(*keys)
    token = await issue_step_up_token(
        session,
        company_id=target_company_id,
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


# ── 微信开放平台网站应用扫码登录 ──────────────────────────────────────────
# 两个 HttpOnly cookie 都限定在 /api/auth/wechat/web 路径下：
# - wx_web_state：OAuth state 的签名载荷（防 CSRF / open redirect）；
# - wx_web_pending：一次性 ticket 明文，绝不进 URL / Location / 日志。
WECHAT_WEB_COOKIE_PATH = "/api/auth/wechat/web"
WECHAT_WEB_STATE_COOKIE = "wx_web_state"
WECHAT_WEB_PENDING_COOKIE = "wx_web_pending"


def _wechat_web_cookie_secure() -> bool:
    return WECHAT_WEB_REDIRECT_URI.startswith("https://")


def _safe_redirect(value: str, default: str = "/") -> str:
    """只接受站内相对路径，拒绝协议、协议相对地址、反斜杠与 /login 自循环。"""
    text = (value or "").strip()
    if not text:
        return default
    if any(ch in text for ch in ("\r", "\n", "\t", "\\")):
        return default
    if not text.startswith("/") or text.startswith("//"):
        return default
    if text.startswith("/login"):
        return default
    return text


def _set_wechat_web_pending_cookie(response, ticket: str) -> None:
    response.set_cookie(
        key=WECHAT_WEB_PENDING_COOKIE,
        value=ticket,
        max_age=WECHAT_WEB_TICKET_TTL_SECONDS,
        path=WECHAT_WEB_COOKIE_PATH,
        httponly=True,
        secure=_wechat_web_cookie_secure(),
        samesite="lax",
    )


def _clear_wechat_web_cookie(response, key: str) -> None:
    response.delete_cookie(
        key=key,
        path=WECHAT_WEB_COOKIE_PATH,
        httponly=True,
        secure=_wechat_web_cookie_secure(),
        samesite="lax",
    )


def _wechat_web_error_redirect(code: str, target: str = "/") -> RedirectResponse:
    """扫码链路失败统一回登录页；只带短错误码，不透传敏感信息。"""
    url = f"/login?wechat_error={quote(code, safe='')}"
    redirect = _safe_redirect(target)
    if redirect != "/":
        url += f"&redirect={quote(redirect, safe='')}"
    response = RedirectResponse(url, status_code=302)
    _clear_wechat_web_cookie(response, WECHAT_WEB_STATE_COOKIE)
    if code in ("state_invalid", "state_expired", "ticket_expired"):
        _clear_wechat_web_cookie(response, WECHAT_WEB_PENDING_COOKIE)
    return response


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
    if _dev_fallback_enabled() and req.phone == "13800000000":
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
async def phone_login(req: PhoneLoginRequest, request: Request):
    """Verify phone code then login/register and issue JWT.

    验证码爆破防线：与密码登录共用同一套失败锁定（按手机号+IP，进程内计数）。
    6 位码仅百万组合，无锁定时可以在线穷举；错误 5 次锁 15 分钟后，
    在线穷举与离线字典都失去可行性。开发兜底身份不占用失败额度。
    """
    dev_bypass = _dev_fallback_enabled() and req.phone == "13800000000" and req.code == "123456"
    ip = request.client.host if request.client else ""
    keys = [("phone", (req.phone or "").strip())]
    if ip:
        keys.append(("ip", ip))
    if not dev_bypass:
        assert_not_locked(*keys)
        try:
            await check_verify_code(req.phone, req.code, req.biz_id or req.bizId)
        except HTTPException:
            register_failure(*keys)
            raise
        clear_failures(*keys)
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
    if _dev_fallback_enabled() and phone == "13800000000" and req.code == "123456":
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

    opening_balance_required = False
    opening_balance_reason = None
    company_id = current_company.get("company_id")
    if company_id and str(current_company.get("onboardingType") or "").strip() == "migration":
        from services.setup_readiness_service import migration_opening_status

        async with session_scope() as session:
            opening_info = await migration_opening_status(session, str(company_id))
        opening_balance_required = bool(opening_info["required"])
        opening_balance_reason = opening_info["detail"] if opening_balance_required else None

    return {
        "user_id": user["user_id"],
        "nickname": user.get("nickname", ""),
        "avatar": user.get("avatar", ""),
        "phone": user.get("phone", ""),
        "phone_bound": bool(user.get("phone")),
        "wechat_bound": bool(user.get("openid")),
        "openid_bound": bool(user.get("openid")),
        # 网站应用（开放平台扫码）绑定状态；与小程序 wechat_bound 严格分开
        "wechat_web_bound": bool(user.get("wechat_web_openid")),
        "has_password": bool(user.get("password_hash")),
        "current_company_id": company_id,
        "companies": companies,
        "opening_balance_required": opening_balance_required,
        "opening_balance_reason": opening_balance_reason,
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


@router.get("/wechat/web/config")
async def wechat_web_config():
    """登录页据此显示/隐藏「微信扫码登录」入口（与支付渠道开关无关）。"""
    return {"enabled": wechat_web_login_enabled()}


@router.get("/wechat/web/authorize-url", response_model=WechatWebAuthorizeResponse)
async def wechat_web_authorize_url(
    redirect: str = "",
    privacy_accepted_at: str = "",
    tos_version: str = "",
    privacy_version: str = "",
):
    """生成扫码 URL，同时下发签名 state cookie。

    redirect 只接受站内相对路径；它会被写进签名 cookie，回调时以 cookie 为准，
    query 里的 redirect 永远不会被信任。
    """
    if not wechat_web_login_enabled():
        raise HTTPException(status_code=503, detail="微信扫码登录未启用")
    accepted_at = (privacy_accepted_at or "").strip()
    if not accepted_at:
        raise HTTPException(status_code=400, detail="请先同意《用户协议》和《隐私政策》")
    target = _safe_redirect(redirect)
    nonce, state_cookie = create_state(
        redirect=target,
        privacy_accepted_at=accepted_at,
        tos_version=tos_version or DEFAULT_TOS_VERSION,
        privacy_version=privacy_version or DEFAULT_PRIVACY_VERSION,
    )
    try:
        url = build_authorize_url(nonce)
    except WechatWebAuthError as exc:
        raise HTTPException(status_code=503, detail=exc.detail) from exc
    response = JSONResponse(
        {"authorize_url": url, "expires_in": WECHAT_WEB_STATE_TTL_SECONDS}
    )
    response.set_cookie(
        key=WECHAT_WEB_STATE_COOKIE,
        value=state_cookie,
        max_age=WECHAT_WEB_STATE_TTL_SECONDS,
        path=WECHAT_WEB_COOKIE_PATH,
        httponly=True,
        secure=_wechat_web_cookie_secure(),
        samesite="lax",
    )
    return response


@router.get("/wechat/web/callback")
async def wechat_web_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
):
    """微信 OAuth 回调（顶层导航）：校验 state → 换身份 → 生成一次性 ticket。"""
    try:
        payload = parse_state_cookie(request.cookies.get(WECHAT_WEB_STATE_COOKIE, ""))
    except WechatWebAuthError as exc:
        return _wechat_web_error_redirect(exc.code)
    expected_nonce = str(payload.get("n") or "")
    if not state or not expected_nonce or not hmac.compare_digest(state, expected_nonce):
        return _wechat_web_error_redirect("state_invalid", str(payload.get("r") or "/"))
    target = _safe_redirect(str(payload.get("r") or "/"))
    if error:
        return _wechat_web_error_redirect("access_denied", target)
    if not (code or "").strip():
        return _wechat_web_error_redirect("code_missing", target)

    trace_id = uuid4().hex
    try:
        session = await exchange_code_for_token(code, trace_id=trace_id)
    except WechatWebAuthError as exc:
        return _wechat_web_error_redirect(exc.code, target)
    openid = session["openid"]
    unionid = session.get("unionid") or ""
    profile = await fetch_userinfo(
        session.get("access_token") or "", openid, trace_id=trace_id
    )

    try:
        found = await find_user_by_wechat_web(openid, unionid)
    except ValueError:
        logger.warning(
            "wechat web identity conflict, trace_id=%s openid=%s",
            trace_id,
            _mask(openid),
        )
        return _wechat_web_error_redirect("identity_conflict", target)
    if found is not None and found[0].get("is_disabled"):
        logger.warning(
            "wechat web disabled account rejected, trace_id=%s user_id=%s",
            trace_id,
            found[0].get("user_id"),
        )
        return _wechat_web_error_redirect("account_disabled", target)

    # 只有「已有手机号」的老账号才能直接换 JWT；新用户/未绑手机一律先绑手机。
    login_ready = bool(found is not None and (found[0].get("phone") or ""))
    purpose = "login" if login_ready else "bind"
    ticket = await create_wechat_login_ticket(
        openid=openid,
        unionid=unionid,
        nickname=profile.get("nickname", ""),
        avatar=profile.get("avatar", ""),
        privacy_accepted_at=str(payload.get("p") or ""),
        tos_version=str(payload.get("t") or DEFAULT_TOS_VERSION),
        privacy_version=str(payload.get("v") or DEFAULT_PRIVACY_VERSION),
        purpose=purpose,
        user_id=found[0]["user_id"] if login_ready and found is not None else None,
    )
    frontend_url = f"{WECHAT_WEB_FRONTEND_CALLBACK_PATH}?status=ready"
    if target != "/":
        frontend_url += f"&redirect={quote(target, safe='')}"
    response = RedirectResponse(frontend_url, status_code=302)
    _set_wechat_web_pending_cookie(response, ticket)
    _clear_wechat_web_cookie(response, WECHAT_WEB_STATE_COOKIE)
    logger.info(
        "wechat web callback ok, trace_id=%s purpose=%s user_id=%s",
        trace_id,
        purpose,
        (found[0]["user_id"] if found is not None else "-"),
    )
    return response


@router.post("/wechat/web/exchange")
async def wechat_web_exchange(request: Request):
    """用 HttpOnly pending cookie 里的 ticket 换取登录态。

    login ticket：原子消费并返回 JWT；bind ticket：只回 phone_required，不消费。
    """
    pending = request.cookies.get(WECHAT_WEB_PENDING_COOKIE, "")
    if not pending:
        raise HTTPException(status_code=400, detail="微信登录会话已失效，请重新扫码")
    ticket = await get_wechat_login_ticket(pending)
    if ticket is None:
        raise HTTPException(status_code=400, detail="微信登录会话已失效，请重新扫码")
    if ticket.get("purpose") == "bind":
        return {"phone_required": True}
    if ticket.get("purpose") != "login":
        raise HTTPException(status_code=400, detail="微信登录会话无效，请重新扫码")

    try:
        user, company = await exchange_wechat_login_ticket(pending)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    company = await _prepare_signup_company(user, company, False)
    token = create_access_token(
        {"sub": user["user_id"], "company_id": company["company_id"]}
    )
    await log_action(
        company["company_id"],
        user["user_id"],
        "login",
        "user",
        user["user_id"],
        {"method": "wechat_web"},
    )
    response = JSONResponse(
        {
            "phone_required": False,
            "access_token": token,
            "token_type": "Bearer",
            "user_id": user["user_id"],
            "company_id": company["company_id"],
            "expires_in": str(JWT_EXPIRES_IN),
            "has_password": bool((user or {}).get("password_hash")),
        }
    )
    _clear_wechat_web_cookie(response, WECHAT_WEB_PENDING_COOKIE)
    return response


@router.post("/wechat/web/bind-phone")
async def wechat_web_bind_phone(req: WechatWebBindPhoneRequest, request: Request):
    """网站微信登录首次绑手机：短信通过后合并老账号或新建，再发 JWT。"""
    pending = request.cookies.get(WECHAT_WEB_PENDING_COOKIE, "")
    if not pending:
        raise HTTPException(status_code=400, detail="微信登录会话已失效，请重新扫码")
    bind_ticket = await get_wechat_login_ticket(pending, purpose="bind")
    if bind_ticket is None:
        raise HTTPException(status_code=400, detail="微信登录会话已失效，请重新扫码")

    phone = (req.phone or "").strip()
    dev_bypass = (
        _dev_fallback_enabled() and phone == "13800000000" and req.code == "123456"
    )
    ip = request.client.host if request.client else ""
    keys = [("phone", phone)]
    if ip:
        keys.append(("ip", ip))
    if not dev_bypass:
        assert_not_locked(*keys)
        try:
            await check_verify_code(phone, req.code, req.biz_id or req.bizId)
        except HTTPException:
            register_failure(*keys)
            raise
        clear_failures(*keys)

    try:
        user, company, is_new, merged = await bind_phone_with_wechat_web(pending, phone)
    except ValueError as exc:
        message = str(exc)
        status = 400 if ("失效" in message or "重新扫码" in message) else 409
        raise HTTPException(status_code=status, detail=message) from exc

    company = await _prepare_signup_company(user, company, is_new)
    token = create_access_token(
        {"sub": user["user_id"], "company_id": company["company_id"]}
    )
    await log_action(
        company["company_id"],
        user["user_id"],
        "login",
        "user",
        user["user_id"],
        {
            "method": "wechat_web",
            "new_user": is_new,
            "merged": merged,
            "phone": _mask(phone),
        },
    )
    response = JSONResponse(
        {
            "phone_required": False,
            "access_token": token,
            "token_type": "Bearer",
            "user_id": user["user_id"],
            "company_id": company["company_id"],
            "expires_in": str(JWT_EXPIRES_IN),
            "has_password": bool((user or {}).get("password_hash")),
            "merged": merged,
            "is_new": is_new,
        }
    )
    _clear_wechat_web_cookie(response, WECHAT_WEB_PENDING_COOKIE)
    return response


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
    from services.quota_service import ensure_within_company_limit

    name = req.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="缺少主体名称")
    await ensure_within_company_limit(ctx.user_id)
    # 提取除 name 以外的其它字段作为 extra
    extra = {k: v for k, v in req.items() if k != 'name'}
    company = await create_company(ctx.user_id, name, extra)
    return company

@router.post("/companies/license-ocr")
async def auth_recognize_company_license(
    file: UploadFile = File(...), ctx: AuthContext = Depends(require_auth)
):
    """上传营业执照照片 → 识别出新建主体要填的字段。

    只做预填：返回的名称、税号、类型、成立日期、住所都要用户在表单里核对后
    才会随「创建主体」落库；识别失败不阻塞手填。
    """
    content = await file.read()
    mime = resolve_receipt_image_mime(file.content_type, file.filename)
    try:
        validate_receipt_image_bytes(content, mime)
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=413 if ("超过" in message or "最大" in message) else 415,
            detail=message,
        ) from exc

    data_url = f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"
    try:
        raw = await asyncio.to_thread(
            chat_vision_json,
            LICENSE_PROMPT,
            data_url,
            max_tokens=2048,  # 经营范围动辄几百字，留够避免输出被截断
            scene="company_license_ocr",
            user_id=ctx.user_id,
            company_id=ctx.company_id,
        )
    except AccountingError:
        raise  # 配额超限保持 402，不转 502
    except Exception as exc:  # noqa: BLE001 —— 模型侧异常统一转 502，前端提示改手填
        logger.warning("company license ocr failed user=%s: %s", ctx.user_id, exc)
        raise HTTPException(
            status_code=502,
            detail="营业执照识别失败，请换一张更清晰的照片，或直接手动填写",
        ) from exc

    result = normalize_license_result(raw)
    if not result["name"] and not result["taxId"]:
        raise HTTPException(
            status_code=422,
            detail="没识别出营业执照信息，请换一张更清晰的照片，或直接手动填写",
        )
    return result


@router.put("/companies/{company_id}")
async def auth_update_company(company_id: str, req: dict, ctx: AuthContext = Depends(require_auth)):
    """Update company details.

    申报周期（vatFilingCycle / businessIncomeIitFilingCycle）变更会连带影响征期：
    重算当年待办，并写审计事件留痕——小规模纳税人的纳税期限一经选择，
    一个会计年度内不得变更，事后追溯必须有据可查。
    """
    before = await get_company(company_id) or {}
    onboarding_change: tuple[str, str] | None = None
    if "onboardingType" in req:
        old_type = str(before.get("onboardingType") or "new").strip()
        new_type = str(req.get("onboardingType") or "").strip()
        if new_type not in ("new", "migration"):
            raise AccountingError("INVALID_ONBOARDING_TYPE", "建账方式仅支持 new 或 migration", 422)
        if new_type != old_type:
            async with session_scope() as session:
                has_business = await session.scalar(
                    select(JournalEntry.id)
                    .where(
                        JournalEntry.company_id == company_id,
                        JournalEntry.entry_type == "standard",
                        JournalEntry.status != "reversed",
                    )
                    .limit(1)
                )
            if has_business is not None:
                raise AccountingError(
                    ONBOARDING_TYPE_LOCKED,
                    "账套已有业务凭证，不可再变更建账方式",
                    422,
                    {"companyId": company_id},
                )
            onboarding_change = (old_type, new_type)
    try:
        company = await update_company(ctx.user_id, company_id, req)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if onboarding_change is not None:
        from services.accounting_core_service import record_audit

        old_type, new_type = onboarding_change
        async with session_scope() as session:
            await record_audit(
                session,
                company_id=company_id,
                user_id=ctx.user_id,
                action="change_onboarding_type",
                entity_type="company",
                entity_id=company_id,
                request_id=str(uuid4()),
                before={"onboardingType": old_type},
                after={"onboardingType": new_type},
                reason=str(req.get("onboardingChangeReason") or "user_updated"),
            )
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
    # 删除不可恢复，除名称确认外还必须完成短信二次认证（action=delete_company）。
    stepUpToken: Optional[str] = None


@router.delete("/companies/{company_id}")
async def auth_delete_company(
    company_id: str,
    req: DeleteCompanyRequest,
    ctx: AuthContext = Depends(require_auth),
):
    """永久删除主体及全部业务数据（不可恢复，需名称确认 + 短信二次认证）。"""
    from services.company_delete_service import (
        CompanyDeleteError,
        CompanyNotFoundError,
        CurrentCompanyDeleteError,
        purge_company,
    )

    # 二次认证令牌必须先独立提交：purge 在专用连接/会话里执行，
    # 若把未提交的 used_at 留在请求事务里，SQLite 会因写锁直接失败，
    # PG 上也会让「已消费」与「已删除」不在同一事务边界内。
    from db.session import session_scope

    async with session_scope() as session:
        # 先校验归属，避免用一个有效 step-up 去打不存在/不属于自己的主体；
        # 归属判定与 purge_company 同口径（owner_user_id）。
        company = await session.get(Company, company_id)
        if company is None or company.owner_user_id != ctx.user_id:
            raise HTTPException(status_code=404, detail="主体不存在或无权删除")
        await consume_step_up_token(
            session,
            req.stepUpToken,
            company_id=company_id,
            user_id=ctx.user_id,
            action="delete_company",
        )

    try:
        return await purge_company(ctx.user_id, company_id, req.confirmName)
    except CompanyNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CurrentCompanyDeleteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CompanyDeleteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
