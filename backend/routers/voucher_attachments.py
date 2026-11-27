# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""通用凭证附件与银行电子回单路由。

接口清单：
- POST /api/bank/receipts/upload        上传银行回单 / 标准对账单（ZIP / PDF / 图片）
- GET  /api/bank/receipts               查询银行回单列表（支持 parked / month / statement_id）
- POST /api/bank/receipts/relink        流水导入后自动回挂待匹配回单
- POST /api/bank/receipts/{id}/bind     手动绑定回单到指定银行流水
- GET  /api/vouchers/{entry_id}/attachments   查询凭证关联的全部附件（直连+发票+银行回单）
- POST /api/vouchers/{entry_id}/attachments   为指定凭证上传附件（支付截图/报销单/工资表等）
- GET  /api/attachments/{attachment_id}/preview   内联预览原件（用于前端 Blob URL / 图片渲染）
- GET  /api/attachments/{attachment_id}/download  附件下载（Content-Disposition: attachment）
- POST /api/attachments/{attachment_id}/void      软作废附件（禁止删除）
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import ConfigDict, Field

from deps.auth import AuthContext, require_auth
from models.schemas import StrictRequestModel
from services.attachment_storage_service import (
    AttachmentError,
    list_entry_attachments,
    read_attachment_file,
    upload_voucher_attachments,
    void_attachment,
)
from services.bank_receipt_service import (
    bind_bank_receipt,
    list_bank_receipts,
    relink_bank_receipts,
    upload_bank_receipts,
)
from services.setup_readiness_service import require_migration_opening_ready

router = APIRouter(tags=["voucher-attachments"], dependencies=[Depends(require_auth)])

_MEDIA_TYPES: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "pdf": "application/pdf",
    "xml": "application/xml; charset=utf-8",
    "ofd": "application/ofd",
    "zip": "application/zip",
}


class BindReceiptRequest(StrictRequestModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    statement_id: str = Field(..., alias="statementId", min_length=1)


class VoidAttachmentRequest(StrictRequestModel):
    reason: str = Field(..., min_length=1, max_length=200)


def _http_error(exc: AttachmentError) -> HTTPException:
    status = exc.status_code if exc.status_code in (400, 404, 409, 413, 422) else 400
    return HTTPException(status_code=status, detail=str(exc))


def _file_response(data: dict[str, Any], *, disposition: str) -> Response:
    ext = str(data.get("fileExt") or "").lower().lstrip(".")
    file_name = str(data.get("fileName") or "attachment")
    if not ext and "." in file_name:
        ext = file_name.rsplit(".", 1)[-1].lower()
    media_type = _MEDIA_TYPES.get(ext, "application/octet-stream")
    headers = {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(file_name)}",
        "X-Attachment-Sha256": str(data.get("contentSha256") or ""),
    }
    return Response(content=data["data"], media_type=media_type, headers=headers)


async def _read_upload_files(files: list[UploadFile]) -> list[dict[str, Any]]:
    if not files:
        raise HTTPException(status_code=400, detail="请选择至少一个文件")
    payloads: list[dict[str, Any]] = []
    for item in files:
        data = await item.read()
        payloads.append({"file_name": item.filename or "attachment", "payload": data})
    return payloads


@router.post("/api/bank/receipts/upload")
async def upload_bank_receipt_files(
    files: list[UploadFile] = File(...),
    use_vision: bool = Query(True, description="是否启用视觉模型识别无文本层回单"),
    ctx: AuthContext = Depends(require_auth),
):
    await require_migration_opening_ready(ctx.company_id)
    file_items = await _read_upload_files(files)
    try:
        return await upload_bank_receipts(ctx.company_id, ctx.user_id, file_items, use_vision=use_vision)
    except AttachmentError as exc:
        raise _http_error(exc) from exc


@router.get("/api/bank/receipts")
async def get_bank_receipts(
    parked: bool = Query(False, description="仅看未匹配流水的待挂回单"),
    month: Optional[str] = Query(None, description="月份过滤 YYYY-MM"),
    statement_id: Optional[str] = Query(None, description="按银行流水 ID 过滤"),
    ctx: AuthContext = Depends(require_auth),
):
    return await list_bank_receipts(
        ctx.company_id,
        parked=parked,
        month=month,
        statement_id=statement_id,
    )


@router.post("/api/bank/receipts/relink")
async def relink_company_bank_receipts(ctx: AuthContext = Depends(require_auth)):
    await require_migration_opening_ready(ctx.company_id)
    return await relink_bank_receipts(ctx.company_id, ctx.user_id)


@router.post("/api/bank/receipts/{attachment_id}/bind")
async def bind_bank_receipt_to_statement(
    attachment_id: str,
    payload: BindReceiptRequest,
    ctx: AuthContext = Depends(require_auth),
):
    await require_migration_opening_ready(ctx.company_id)
    try:
        return await bind_bank_receipt(
            ctx.company_id,
            ctx.user_id,
            attachment_id,
            payload.statement_id,
        )
    except AttachmentError as exc:
        raise _http_error(exc) from exc


@router.get("/api/vouchers/{entry_id}/attachments")
async def get_voucher_attachments(
    entry_id: str,
    include_void: bool = Query(True, description="是否包含已作废附件"),
    ctx: AuthContext = Depends(require_auth),
):
    try:
        return await list_entry_attachments(ctx.company_id, entry_id, include_void=include_void)
    except AttachmentError as exc:
        raise _http_error(exc) from exc


@router.post("/api/vouchers/{entry_id}/attachments")
async def post_voucher_attachments(
    entry_id: str,
    files: list[UploadFile] = File(...),
    document_type: str = Query("other", description="附件单据类型"),
    document_type_form: Optional[str] = Form(None, alias="documentType"),
    ctx: AuthContext = Depends(require_auth),
):
    await require_migration_opening_ready(ctx.company_id)
    resolved_type = (document_type_form or document_type or "other").strip()
    file_items = await _read_upload_files(files)
    try:
        created = await upload_voucher_attachments(
            ctx.company_id,
            ctx.user_id,
            entry_id,
            resolved_type,
            file_items,
        )
    except AttachmentError as exc:
        raise _http_error(exc) from exc
    return {
        "items": created,
        "count": len(created),
        "storedCount": sum(1 for item in created if item.get("outcome") == "stored"),
        "duplicateCount": sum(1 for item in created if item.get("outcome") == "duplicate"),
        "rejectedCount": sum(1 for item in created if item.get("outcome") == "rejected"),
    }


@router.get("/api/attachments/{attachment_id}/preview")
async def preview_attachment(
    attachment_id: str,
    ctx: AuthContext = Depends(require_auth),
):
    try:
        data = await read_attachment_file(ctx.company_id, attachment_id)
    except AttachmentError as exc:
        raise _http_error(exc) from exc
    return _file_response(data, disposition="inline")


@router.get("/api/attachments/{attachment_id}/download")
async def download_any_attachment(
    attachment_id: str,
    ctx: AuthContext = Depends(require_auth),
):
    try:
        data = await read_attachment_file(ctx.company_id, attachment_id)
    except AttachmentError as exc:
        raise _http_error(exc) from exc
    return _file_response(data, disposition="attachment")


@router.post("/api/attachments/{attachment_id}/void")
async def void_any_attachment(
    attachment_id: str,
    payload: VoidAttachmentRequest,
    ctx: AuthContext = Depends(require_auth),
):
    await require_migration_opening_ready(ctx.company_id)
    try:
        return await void_attachment(ctx.company_id, ctx.user_id, attachment_id, payload.reason)
    except AttachmentError as exc:
        raise _http_error(exc) from exc
