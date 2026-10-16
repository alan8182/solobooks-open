# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""MCP 个人访问令牌（PAT）管理 API：用户管理自己的令牌。

- 列表/吊销只针对本人令牌；
- 签发默认绑定当前公司（JWT company_id），为其他公司签发需校验成员关系；
- scopes 白名单：read / write:draft（写入类工具只产生待确认草稿）；
- 明文 token 仅在签发响应里出现一次。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from deps.auth import AuthContext, require_auth
from services import mcp_token_service as svc

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

ALLOWED_SCOPES = {"read", svc.SCOPE_WRITE}


class CreateTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scopes: str = Field(default="read", max_length=255)
    company_id: str | None = Field(default=None, max_length=128)
    expires_days: int | None = Field(default=365, ge=1, le=3650)


def _parse_scopes(raw: str) -> set[str]:
    scopes = {s.strip() for s in (raw or "").split(",") if s.strip()}
    if not scopes:
        raise HTTPException(status_code=400, detail="scopes 不能为空")
    unknown = scopes - ALLOWED_SCOPES
    if unknown:
        raise HTTPException(status_code=400, detail=f"不支持的 scope: {', '.join(sorted(unknown))}")
    if "read" not in scopes:
        raise HTTPException(status_code=400, detail="write:draft 必须与 read 一同授予")
    return scopes


@router.get("/tokens")
async def list_tokens(ctx: AuthContext = Depends(require_auth)):
    return {"tokens": await svc.list_tokens(user_id=ctx.user_id)}


@router.post("/tokens", status_code=201)
async def create_token(body: CreateTokenRequest, ctx: AuthContext = Depends(require_auth)):
    scopes = _parse_scopes(body.scopes)
    company_id = body.company_id or ctx.company_id
    if company_id != ctx.company_id and not await svc.is_company_member(
        user_id=ctx.user_id, company_id=company_id
    ):
        raise HTTPException(status_code=403, detail="只能为已加入的公司签发令牌")
    raw, row = await svc.issue_token(
        user_id=ctx.user_id,
        company_id=company_id,
        name=body.name.strip(),
        scopes=",".join(sorted(scopes)),
        expires_days=body.expires_days,
    )
    return {
        "token": raw,
        "item": {
            "id": row.id,
            "name": row.name,
            "companyId": row.company_id,
            "scopes": row.scopes,
            "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
            "createdAt": row.created_at.isoformat() if row.created_at else None,
        },
        "notice": "明文令牌仅此一次显示，请立即保存到 MCP 客户端配置中",
    }


@router.delete("/tokens/{token_id}")
async def revoke_token(token_id: str, ctx: AuthContext = Depends(require_auth)):
    ok = await svc.revoke_token(token_id=token_id, user_id=ctx.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="令牌不存在、非本人所有或已吊销")
    return {"revoked": True}
