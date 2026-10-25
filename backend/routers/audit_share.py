# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Solo Audit Share 协审链接路由。

两组端点：
- 老板侧 ``/api/audit-shares``：require_auth，签发/预览/列表/撤销/批注处理；
- CPA 侧 ``/api/share/{token}``：免注册公开访问，凭 URL token 只读快照 + 写批注。

错误约定：404 = 链接不存在；410 = 已撤销/已过期（body 带 reason）；429 = 限流。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from deps.auth import AuthContext, require_auth
from services import audit_share_public, audit_share_service

logger = logging.getLogger(__name__)

owner_router = APIRouter(prefix="/api/audit-shares", tags=["audit-share"])
public_router = APIRouter(prefix="/api/share", tags=["audit-share"])


# ---------------------------------------------------------------------------
# 老板侧
# ---------------------------------------------------------------------------

class CreateShareRequest(BaseModel):
    month: str = Field(..., description="体检月份 YYYY-MM")
    anonymized: bool = True
    expiresInDays: int = 30  # 7/30/90/0（0=永久）


class PreviewShareRequest(BaseModel):
    month: str
    anonymized: bool = True


class AnnotationStatusRequest(BaseModel):
    status: str  # open/acknowledged/resolved/dismissed
    resolutionNote: Optional[str] = None


def _http_status_for_public_error(exc: Exception) -> HTTPException:
    if isinstance(exc, audit_share_public.ShareAccessError):
        reason = exc.reason
        if reason == "not_found":
            return HTTPException(status_code=404, detail="链接不存在")
        return HTTPException(status_code=410, detail="链接已撤销" if reason == "revoked" else "链接已过期")
    if isinstance(exc, TimeoutError):
        return HTTPException(status_code=429, detail="访问过于频繁，请稍后再试")
    logger.exception("audit share public error")
    return HTTPException(status_code=500, detail="服务异常，请稍后再试")


@owner_router.post("")
async def create_share(req: CreateShareRequest, ctx: AuthContext = Depends(require_auth)):
    """生成体检快照并签发协审链接。明文 token 只在本次响应返回一次。"""
    try:
        overview, token = await audit_share_service.create_share(
            ctx.company_id,
            ctx.user_id,
            req.month,
            anonymized=req.anonymized,
            expires_in_days=req.expiresInDays,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("create audit share failed company=%s", ctx.company_id)
        raise HTTPException(status_code=500, detail=f"生成体检快照失败：{exc}") from exc
    overview["url"] = f"/share/{token}"
    overview["token"] = token
    return overview


@owner_router.post("/preview")
async def preview_share(req: PreviewShareRequest, ctx: AuthContext = Depends(require_auth)):
    """生成前脱敏预览：统计 + 异常清单样例（所见即所得）。"""
    try:
        return await audit_share_service.preview_snapshot(
            ctx.company_id, ctx.user_id, req.month, anonymized=req.anonymized
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("preview audit share failed company=%s", ctx.company_id)
        raise HTTPException(status_code=500, detail=f"生成预览失败：{exc}") from exc


@owner_router.get("")
async def list_shares(ctx: AuthContext = Depends(require_auth)):
    return await audit_share_service.list_shares(ctx.company_id, ctx.user_id)


@owner_router.get("/{share_id}")
async def get_share(share_id: str, ctx: AuthContext = Depends(require_auth)):
    detail = await audit_share_service.get_share_detail(ctx.company_id, share_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="协审链接不存在")
    return detail


@owner_router.post("/{share_id}/revoke")
async def revoke_share(share_id: str, ctx: AuthContext = Depends(require_auth)):
    overview = await audit_share_service.revoke_share(ctx.company_id, ctx.user_id, share_id)
    if overview is None:
        raise HTTPException(status_code=404, detail="协审链接不存在")
    return overview


@owner_router.post("/{share_id}/annotations/{annotation_id}/status")
async def update_annotation_status(
    share_id: str,
    annotation_id: str,
    req: AnnotationStatusRequest,
    ctx: AuthContext = Depends(require_auth),
):
    """老板处理批注：已知悉/已解决/忽略，状态回写。"""
    try:
        result = await audit_share_service.update_annotation_status(
            ctx.company_id, ctx.user_id, share_id, annotation_id,
            req.status, req.resolutionNote,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="批注不存在")
    return result


# ---------------------------------------------------------------------------
# CPA 侧（免注册，凭 URL token）
# ---------------------------------------------------------------------------

class PublicAnnotationRequest(BaseModel):
    authorName: str = "协审会计师"
    anchorType: str = "entry"  # overall/entry/check_item/report_section
    anchorId: Optional[str] = None
    body: str


@public_router.get("/{token}")
async def public_get_share(token: str):
    """打开协审链接：返回主体信息 + 冻结快照。失效链接 404/410。"""
    try:
        return await audit_share_public.get_share_view(token)
    except Exception as exc:  # noqa: BLE001
        raise _http_status_for_public_error(exc) from exc


@public_router.get("/{token}/annotations")
async def public_list_annotations(token: str):
    try:
        return {"annotations": await audit_share_public.list_annotations(token)}
    except Exception as exc:  # noqa: BLE001
        raise _http_status_for_public_error(exc) from exc


@public_router.post("/{token}/annotations")
async def public_add_annotation(token: str, req: PublicAnnotationRequest):
    try:
        return await audit_share_public.add_annotation(
            token,
            author_name=req.authorName,
            anchor_type=req.anchorType,
            anchor_id=req.anchorId,
            body=req.body,
        )
    except audit_share_public.ShareAccessError as exc:
        raise _http_status_for_public_error(exc) from exc
    except TimeoutError as exc:
        raise _http_status_for_public_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("public annotation failed")
        raise HTTPException(status_code=500, detail="提交批注失败，请稍后再试") from exc


router = owner_router
