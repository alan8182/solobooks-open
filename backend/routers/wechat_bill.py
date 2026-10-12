"""微信支付账单导入路由"""
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
from typing import Any, Dict, List

from deps.auth import AuthContext, require_auth
from services.wechat_bill_service import parse_wechat_bill, import_wechat_bill, list_wechat_bill_records
from services.audit_service import log_action

router = APIRouter(prefix="/api/wechat-bill", tags=["wechat-bill"], dependencies=[Depends(require_auth)])


class ImportRowsRequest(BaseModel):
    rows: List[Dict[str, Any]]


@router.post("/parse")
async def parse_bill(file: UploadFile = File(...), ctx: AuthContext = Depends(require_auth)):
    """上传微信支付账单 CSV，返回解析预览（不入账）。"""
    content = await file.read()
    return await parse_wechat_bill(content, company_id=ctx.company_id)


@router.post("/import")
async def import_bill(req: ImportRowsRequest, ctx: AuthContext = Depends(require_auth)):
    """将预览通过的行批量导入为记账条目。"""
    result = await import_wechat_bill(ctx.company_id, ctx.user_id, req.rows)
    await log_action(ctx.company_id, ctx.user_id, "import", "wechat_bill", None,
                     {"imported": result["imported"], "failed": result["failed"], "ignored_duplicates": result.get("ignored_duplicates", 0)})
    return result


@router.get("/records")
async def list_records(month: str = Query(...), ctx: AuthContext = Depends(require_auth)):
    """查看当月已导入的微信账单记录（只读，按订单聚合）。"""
    try:
        return await list_wechat_bill_records(ctx.company_id, month)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
