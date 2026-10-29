# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Authentication dependencies."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException

from services.token_service import decode_access_token

logger = logging.getLogger(__name__)

_column_check_warned = False


@dataclass
class AuthContext:
    """Current authenticated identity extracted from JWT."""

    user_id: str
    company_id: str


def _normalize_token(raw: str) -> str:
    """
    归一化 Authorization 中的 token：
    - 去除首尾引号
    - 兼容重复 Bearer 前缀
    - 兼容误传 JSON（如 {"access_token":"..."}）
    """
    token = (raw or "").strip().strip("\"'")
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip().strip("\"'")
    if token.startswith("{") and token.endswith("}"):
        try:
            data = json.loads(token)
            candidate = (
                data.get("access_token")
                or data.get("token")
                or data.get("authToken")
                or ""
            )
            if isinstance(candidate, str) and candidate.strip():
                token = candidate.strip().strip("\"'")
                if token.lower().startswith("bearer "):
                    token = token.split(" ", 1)[1].strip().strip("\"'")
        except Exception:
            pass
    return token


def _dev_fallback_enabled() -> bool:
    """开发兜底开关（读取自 .env / 环境变量，改后需重启进程）。

    兜底行为：test-token 前缀与非 JWT 垃圾 token 一律按测试身份放行，
    仅供本地联调；默认关闭，联调时在 backend/.env 显式打开。
    deploy/docker-compose.yml 生产部署强制 AUTH_DEV_FALLBACK=0。
    """
    return os.getenv("AUTH_DEV_FALLBACK", "0").strip().lower() in ("1", "true", "yes", "on")


def _parse_identity(authorization: Optional[str]) -> AuthContext:
    """从 Authorization 头解析登录身份（JWT 或开发兜底），失败抛 401。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录或缺少 Authorization")

    token = _normalize_token(authorization.split(" ", 1)[1])

    # 开发联调兼容：测试账号 token（前端 mock 登录）不走 JWT 解码。
    if _dev_fallback_enabled():
        if token.startswith("test-token") or "test-token" in authorization:
            return AuthContext(user_id="test_user_001", company_id="c1")
        # 开发兜底：非 JWT（三段式）token 统一按测试身份处理，避免联调被历史脏 token 阻塞。
        if token.count(".") != 2:
            return AuthContext(user_id="test_user_001", company_id="c1")
    try:
        payload = decode_access_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user_id = payload.get("sub")
    company_id = payload.get("company_id")
    if not user_id or not company_id:
        raise HTTPException(status_code=401, detail="token 缺少用户上下文")
    return AuthContext(user_id=user_id, company_id=company_id)


async def _load_auth_state(
    user_id: str, company_id: str
) -> tuple[Optional[tuple[bool, bool]], Optional[bool]]:
    """查询用户的 (is_platform_admin, is_disabled) 与对公司归属（单会话两次查询）。

    - 用户不存在时 flags 返回 None（兜底身份可能无库记录），成员校验一并跳过：
      此处 fail-open，禁用/成员拦截只对真实存在的用户行生效。
    - has_access 与 ensure_tenant / PAT 校验（has_company_access）严格同口径：
      成员行存在须 status == 'active'，行缺失时 owner 放行；None 表示查询失败
      降级为不拦截。
    """
    global _column_check_warned
    from sqlalchemy import select

    from db.models import AuthUser
    from db.session import session_scope
    from services.mcp_token_service import has_company_access

    try:
        async with session_scope() as session:
            row = (
                await session.execute(
                    select(AuthUser.is_platform_admin, AuthUser.is_disabled).where(
                        AuthUser.user_id == user_id
                    )
                )
            ).first()
            if row is None:
                return None, None
            has_access = await has_company_access(
                session=session, user_id=user_id, company_id=company_id
            )
    except Exception as exc:  # noqa: BLE001
        if not _column_check_warned:
            _column_check_warned = True
            logger.warning(
                "auth 校验状态查询失败，禁用/成员校验降级为放行（迁移未执行或表缺失？）: %s",
                exc,
            )
        return None, None
    return (row[0], row[1]), has_access


async def require_auth(authorization: Optional[str] = Header(default=None)) -> AuthContext:
    """Validate bearer token and return auth context.

    支持两类令牌：
    1. 外部/插件长效 PAT (sbk_...)：查 mcp_tokens 表并校验
    2. Web 前端短效 JWT (或开发兜底 test-token)
    每次请求顺路查库校验 is_disabled 与公司成员归属：
    禁用/移出公司即时生效（与 JWT 7 天有效期的撤销时效缺口收口）。
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录或缺少 Authorization")

    raw_token = _normalize_token(authorization.split(" ", 1)[1])
    if raw_token.startswith("sbk_"):
        from services.mcp_token_service import validate_token
        pat = await validate_token(raw_token)
        if not pat:
            raise HTTPException(status_code=401, detail="PAT 令牌无效或已过期")
        return pat.context

    ctx = _parse_identity(authorization)
    flags, has_access = await _load_auth_state(ctx.user_id, ctx.company_id)
    if flags and flags[1]:
        raise HTTPException(status_code=401, detail="账号已被禁用，请联系管理员")
    if flags is not None and has_access is False:
        raise HTTPException(status_code=403, detail="无权访问该企业账套")
    return ctx


async def require_admin(authorization: Optional[str] = Header(default=None)) -> AuthContext:
    """平台管理员依赖：在 require_auth 之上校验 is_platform_admin。

    管理员身份不进 JWT claim，每次请求查库校验——撤销管理员权限即时生效。
    平台管理员按定义跨公司操作，不适用公司成员归属校验（忽略 has_access）。
    """
    ctx = _parse_identity(authorization)
    flags, _has_access = await _load_auth_state(ctx.user_id, ctx.company_id)
    # 禁用优先于管理员校验：被禁账号无论访问什么都返回 401
    if flags and flags[1]:
        raise HTTPException(status_code=401, detail="账号已被禁用，请联系管理员")
    if not flags or not flags[0]:
        raise HTTPException(status_code=403, detail="需要平台管理员权限")
    return ctx
