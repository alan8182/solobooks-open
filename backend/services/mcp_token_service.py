"""MCP 个人访问令牌（PAT）签发与校验。

安全模型（见《MCP接入方案.md》§3.2）：
- 库里只存 sha256(token_hash)，明文 `sbk_*` 仅签发时返回一次；
- token 绑定单一公司作用域 + scopes（M0 仅 read）；
- 校验失败（不存在/吊销/过期/用户被禁用）统一返回 None，不区分原因。
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from deps.auth import AuthContext
from db.models import MCPAccessToken
from db.session import session_scope

TOKEN_PREFIX = "sbk_"
DEFAULT_SCOPES = "read"
DEFAULT_EXPIRES_DAYS = 365
# scope 常量单一出处（agent/mcp_server 与 routers/mcp_tokens 共用）
SCOPE_READ = "read"
SCOPE_WRITE = "write:draft"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(raw: str) -> str:
    normalized = raw.strip()
    if normalized.lower().startswith("bearer "):
        normalized = normalized.split(" ", 1)[1].strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass
class PatIdentity:
    """一次通过校验的 PAT 身份。"""

    context: AuthContext
    token_id: str
    scopes: frozenset[str]

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


async def issue_token(
    *,
    user_id: str,
    company_id: str,
    name: str,
    scopes: str = DEFAULT_SCOPES,
    expires_days: int | None = DEFAULT_EXPIRES_DAYS,
) -> tuple[str, MCPAccessToken]:
    """签发新 PAT，返回 (明文 token, 落库行)。明文仅此一次。"""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = MCPAccessToken(
        user_id=user_id,
        company_id=company_id,
        name=name or "default",
        token_hash=hash_token(raw),
        scopes=scopes or DEFAULT_SCOPES,
        expires_at=_utcnow() + timedelta(days=expires_days) if expires_days else None,
    )
    async with session_scope() as session:
        session.add(row)
        await session.flush()
    return raw, row


def _is_active_token_row(row: MCPAccessToken | None) -> bool:
    if row is None or row.revoked_at is not None:
        return False
    expires_at = row.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at <= _utcnow():
        return False
    return True


async def validate_token(raw: str) -> PatIdentity | None:
    """校验 Bearer 凭证；任何失败（未知/吊销/过期/禁用）返回 None。"""
    if not raw or not raw.strip():
        return None
    digest = hash_token(raw)

    from sqlalchemy import select

    from db.models import AuthUser

    async with session_scope() as session:
        row = (
            await session.execute(
                select(MCPAccessToken).where(MCPAccessToken.token_hash == digest)
            )
        ).scalars().first()
        if not _is_active_token_row(row):
            return None

        # 与 require_auth 同口径：被禁用账号的 token 立即失效
        flags = (
            await session.execute(
                select(AuthUser.is_disabled).where(AuthUser.user_id == row.user_id)
            )
        ).first()
        if flags is None or flags[0]:
            return None

        row.last_used_at = _utcnow()
        return PatIdentity(
            context=AuthContext(user_id=row.user_id, company_id=row.company_id),
            token_id=row.id,
            scopes=frozenset(s.strip() for s in row.scopes.split(",") if s.strip()),
        )


async def verify_token_alive(token_id: str) -> bool:
    """复核已签发 token_id 是否仍然有效（未吊销/未过期/用户未禁用），并刷新 last_used_at。

    用于 stdio 模式等长驻进程在工具调用时复核（避免启动后吊销失效）。
    """
    from sqlalchemy import select

    from db.models import AuthUser
    from db.session import session_scope

    async with session_scope() as session:
        row = await session.get(MCPAccessToken, token_id)
        if not _is_active_token_row(row):
            return False

        flags = (
            await session.execute(
                select(AuthUser.is_disabled).where(AuthUser.user_id == row.user_id)
            )
        ).first()
        if flags is None or flags[0]:
            return False

        row.last_used_at = _utcnow()
        return True


async def list_tokens(*, user_id: str) -> list[dict]:
    """列出用户自己的令牌（不含 hash），按创建时间倒序。"""
    from sqlalchemy import select

    from db.session import session_scope

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(MCPAccessToken)
                .where(MCPAccessToken.user_id == user_id)
                .order_by(MCPAccessToken.created_at.desc())
            )
        ).scalars().all()
        return [
            {
                "id": row.id,
                "name": row.name,
                "companyId": row.company_id,
                "scopes": row.scopes,
                "lastUsedAt": row.last_used_at.isoformat() if row.last_used_at else None,
                "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
                "revokedAt": row.revoked_at.isoformat() if row.revoked_at else None,
                "createdAt": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]


async def is_company_member(*, user_id: str, company_id: str) -> bool:
    """校验用户是否为该公司的**有效**成员（为其他公司签发令牌时必须）。

    与产品访问门禁同口径：`ensure_tenant` 只放行 `status == 'active'` 的成员，
    被 owner 停用（status='disabled'）的成员不应再能签发令牌。仅查行存在会
    让签发门禁弱于数据门禁（2026-10-12 修正）。
    """
    from sqlalchemy import select

    from db.models import CompanyMember
    from db.session import session_scope

    async with session_scope() as session:
        row = (
            await session.execute(
                select(CompanyMember.company_id).where(
                    CompanyMember.user_id == user_id,
                    CompanyMember.company_id == company_id,
                    CompanyMember.status == "active",
                )
            )
        ).first()
        return row is not None


async def revoke_token(*, token_id: str, user_id: str) -> bool:
    """吊销 token（只能吊销自己的），返回是否发生实际变更。"""
    async with session_scope() as session:
        row = await session.get(MCPAccessToken, token_id)
        if row is None or row.user_id != user_id or row.revoked_at is not None:
            return False
        row.revoked_at = _utcnow()
        return True
