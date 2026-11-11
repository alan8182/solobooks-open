# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""邮件单据通道 API：专属收件地址展示/重置、收件箱附件列表/下载/删除。

附件导入不在此处——前端从收件箱拉取文件内容后走现有解析确认管线
（发票/银行/微信账单向导），成功后回调 /consumed 标记。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from config import mail_inbound_enabled
from deps.auth import AuthContext, require_auth
from services.audit_service import log_action
from services import mail_inbox_service as svc

router = APIRouter(prefix="/api/mailbox", tags=["mailbox"], dependencies=[Depends(require_auth)])

_MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "csv": "text/csv",
}


@router.get("")
async def get_mailbox(ctx: AuthContext = Depends(require_auth)):
    """专属收件地址（幂等：首次访问生成；优先用用户手机号作为地址码）。"""
    row = await svc.ensure_address(ctx.company_id, phone=await svc.get_user_mobile(ctx.user_id))
    return {"enabled": mail_inbound_enabled(), **svc.address_payload(row)}


@router.post("/address/reset")
async def reset_mailbox_address(ctx: AuthContext = Depends(require_auth)):
    """重置专属地址：旧地址随之失效。"""
    row = await svc.reset_address(ctx.company_id, phone=await svc.get_user_mobile(ctx.user_id))
    await log_action(ctx.company_id, ctx.user_id, "reset", "mailbox_address", row.id,
                     {"addressCode": row.address_code})
    return {"enabled": mail_inbound_enabled(), **svc.address_payload(row)}


@router.get("/documents")
async def list_documents(
    status: Optional[str] = Query(None, description="pending / consumed / dismissed / unsupported"),
    ctx: AuthContext = Depends(require_auth),
):
    return {"items": await svc.list_documents(ctx.company_id, status=status)}


@router.get("/documents/{document_id}/file")
async def download_document(document_id: str, ctx: AuthContext = Depends(require_auth)):
    """附件内容：前端拉取后构造 File 走现有导入向导；浏览器直接打开则为下载。"""
    from urllib.parse import quote

    found = await svc.get_document_file(ctx.company_id, document_id)
    if found is None:
        raise HTTPException(status_code=404, detail="附件不存在或已删除")
    media_type = _MEDIA_TYPES.get(found["ext"], "application/octet-stream")
    # 中文名走 RFC 5987（filename*=UTF-8''…），ASCII 回退名仅作旧客户端兼容
    ascii_name = found["filename"].encode("ascii", "ignore").decode() or "attachment"
    utf8_name = quote(found["filename"])
    return Response(
        content=found["data"],
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'
            )
        },
    )


@router.post("/documents/{document_id}/consumed")
async def mark_document_consumed(document_id: str, ctx: AuthContext = Depends(require_auth)):
    row = await svc.mark_consumed(ctx.company_id, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="附件不存在或已删除")
    await log_action(ctx.company_id, ctx.user_id, "import", "inbox_document", document_id,
                     {"filename": row["filename"]})
    return row


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str, ctx: AuthContext = Depends(require_auth)):
    ok = await svc.delete_document(ctx.company_id, document_id)
    if not ok:
        raise HTTPException(status_code=404, detail="附件不存在或已删除")
    await log_action(ctx.company_id, ctx.user_id, "delete", "inbox_document", document_id, {})
    return {"deleted": True}
