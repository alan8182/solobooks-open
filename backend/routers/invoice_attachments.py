# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""发票电子原件（附件）路由。

前缀 ``/api/invoices/attachments``，语义是"原件归档"：上传即落盘原件
（不可变、只增不减），匹配/作废/下载全程不写凭证，与导入管线解耦。
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from deps.auth import AuthContext, require_auth
from models.schemas import StrictRequestModel
from services.setup_readiness_service import require_migration_opening_ready
from services.attachment_storage_service import (
    AttachmentError,
    list_attachments,
    parked_count,
    read_attachment_file,
    relink_parked_attachments,
    upload_attachments,
    void_attachment,
)

router = APIRouter(prefix="/api/invoices/attachments", tags=["invoice-attachments"], dependencies=[Depends(require_auth)])

_MEDIA_TYPES = {
    ".xml": "application/xml",
    ".zip": "application/zip",
    ".pdf": "application/pdf",
    ".ofd": "application/ofd",
}


def _http_error(exc: AttachmentError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


class VoidRequest(StrictRequestModel):
    reason: str = ""


@router.post("")
async def upload_invoice_attachments(
    files: list[UploadFile] = File(...),
    invoice_id: Optional[str] = Form(None),
    ctx: AuthContext = Depends(require_auth),
):
    """上传发票原件（XML/ZIP/PDF/OFD）。ZIP 自动展开；解析后按发票号自动挂接。"""
    await require_migration_opening_ready(ctx.company_id)
    sources = []
    for upload in files:
        sources.append({"file_name": upload.filename or "", "payload": await upload.read()})
    return await upload_attachments(ctx.company_id, ctx.user_id, sources, invoice_id=invoice_id)


@router.get("")
async def get_invoice_attachments(
    invoice_id: Optional[str] = None,
    parked: bool = False,
    ctx: AuthContext = Depends(require_auth),
):
    """附件列表：``invoice_id`` 按发票查（含作废）；``parked=true`` 查未匹配暂存。"""
    return await list_attachments(ctx.company_id, invoice_id=invoice_id, parked=parked)


@router.get("/parked-count")
async def get_parked_count(ctx: AuthContext = Depends(require_auth)):
    """未匹配原件数量（台账顶部提醒条）。"""
    return {"count": await parked_count(ctx.company_id)}


@router.post("/relink")
async def run_relink(ctx: AuthContext = Depends(require_auth)):
    """未匹配原件按发票号幂等回挂（导入成功后自动调用，也可手动触发）。"""
    return await relink_parked_attachments(ctx.company_id, ctx.user_id)


@router.get("/{attachment_id}/download")
async def download_invoice_attachment(attachment_id: str, ctx: AuthContext = Depends(require_auth)):
    """下载原件：服务端重算 sha256 与登记值比对（不符 409，缺失 410）。"""
    try:
        result = await read_attachment_file(ctx.company_id, attachment_id)
    except AttachmentError as exc:
        raise _http_error(exc) from exc
    filename = result["fileName"]
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        "X-Attachment-Sha256": result["contentSha256"],
    }
    ext = "." + str(result["documentType"]).split("_")[-1] if "_" in str(result["documentType"]) else ".bin"
    return Response(
        content=result["data"],
        media_type=_MEDIA_TYPES.get(ext, "application/octet-stream"),
        headers=headers,
    )


@router.post("/{attachment_id}/void")
async def void_invoice_attachment(
    attachment_id: str,
    payload: VoidRequest,
    ctx: AuthContext = Depends(require_auth),
):
    """作废原件：状态标记 + 审计留痕，文件保留（A2 原件不可变）。"""
    try:
        return await void_attachment(ctx.company_id, ctx.user_id, attachment_id, payload.reason)
    except AttachmentError as exc:
        raise _http_error(exc) from exc
