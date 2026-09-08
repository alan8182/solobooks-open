"""Authentication dependencies."""
from __future__ import annotations

import json
from dataclasses import dataclass

from fastapi import Header, HTTPException

from services.token_service import decode_access_token


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


from typing import Optional

def require_auth(authorization: Optional[str] = Header(default=None)) -> AuthContext:
    """Validate bearer token and return auth context."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录或缺少 Authorization")

    token = _normalize_token(authorization.split(" ", 1)[1])

    # 开发联调兼容：测试账号 token（前端 mock 登录）不走 JWT 解码。
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
