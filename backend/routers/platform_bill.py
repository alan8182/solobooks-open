# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""三方平台账单导入路由（微信 / 支付宝，后续平台复用同一入口）。"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from deps.auth import AuthContext, require_auth
from services.audit_service import log_action
from services.setup_readiness_service import require_migration_opening_ready
from services.platform_bill_registry import (
    PLATFORM_LABELS,
    detect_platform,
    import_platform_bill,
    list_platform_bill_records,
    parse_platform_bill,
)
from services.vat_service import UnsupportedTaxpayerType

router = APIRouter(
    prefix="/api/platform-bill",
    tags=["platform-bill"],
    dependencies=[Depends(require_auth)],
)


class ImportRequest(BaseModel):
    platform: str
    rows: Optional[List[Dict[str, Any]]] = None
    importMode: Optional[str] = "summary"  # summary（按账单期间汇总）或 detail（逐笔明细）
    summary: Optional[Dict[str, Any]] = None
    occurDate: Optional[str] = None
    allowBackfill: bool = False
    backfillReason: Optional[str] = None


@router.post("/parse")
async def parse_bill(file: UploadFile = File(...), ctx: AuthContext = Depends(require_auth)):
    """上传平台账单 CSV，自动识别微信/支付宝并返回解析预览（不入账）。"""
    await require_migration_opening_ready(ctx.company_id)
    content = await file.read()
    platform = detect_platform(content)
    if not platform:
        raise HTTPException(
            status_code=422,
            detail="无法识别账单平台，请上传微信支付或支付宝商家后台导出的 CSV 文件",
        )
    try:
        return await parse_platform_bill(platform, content, company_id=ctx.company_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/import")
async def import_bill(req: ImportRequest, ctx: AuthContext = Depends(require_auth)):
    """将预览通过的行导入为记账条目（汇总或逐笔）。"""
    await require_migration_opening_ready(ctx.company_id)
    if req.platform not in PLATFORM_LABELS:
        raise HTTPException(status_code=422, detail=f"暂不支持的平台：{req.platform}")
    try:
        result = await import_platform_bill(
            req.platform,
            ctx.company_id,
            ctx.user_id,
            import_mode=req.importMode or "summary",
            rows=req.rows or [],
            summary=req.summary,
            occur_date=req.occurDate or "",
            allow_backfill=bool(req.allowBackfill),
            backfill_reason=req.backfillReason,
        )
    except UnsupportedTaxpayerType as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await log_action(
        ctx.company_id, ctx.user_id, "import", f"{req.platform}_bill", None,
        {
            "imported": result["imported"],
            "failed": result["failed"],
            "ignored_duplicates": result.get("ignored_duplicates", 0),
            "mode": req.importMode,
        },
    )
    return result


@router.get("/records")
async def list_records(
    month: str = Query(...),
    platform: str = Query(""),
    ctx: AuthContext = Depends(require_auth),
):
    """查看当月已导入的平台账单记录；不传 platform 时合并微信与支付宝。"""
    try:
        return await list_platform_bill_records(ctx.company_id, month, platform)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
