# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""备份与归档路由（P0 WP5）。

归档生成为同步请求：期间上限 24 个月、分录行上限 5 万行（见服务层），
超出时提示分批，避免把 web worker 拖到超时。
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from deps.auth import AuthContext, require_auth
from services.audit_service import log_action
from services.backup_archive_service import build_archive, get_record, list_records

router = APIRouter(prefix="/api/backup", tags=["backup"], dependencies=[Depends(require_auth)])

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _download_response(path: str, filename: str, extra_headers: dict[str, str] | None = None) -> FileResponse:
    if not Path(path).exists():
        raise HTTPException(status_code=410, detail="归档文件已被清理，请重新生成")
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    if extra_headers:
        headers.update(extra_headers)
    return FileResponse(path, media_type=_XLSX, filename=filename, headers=headers)


@router.post("/archive")
async def create_archive(
    year: int = Query(..., ge=2000, le=2999, description="会计年度"),
    months: str | None = Query(None, description="期间，如 1-10；留空为全年"),
    ctx: AuthContext = Depends(require_auth),
):
    """生成归档工作簿并直接下载（封面带数据指纹）。"""
    try:
        result = await build_archive(ctx.company_id, ctx.user_id, year=year, months=months)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await log_action(
        ctx.company_id, ctx.user_id, "export", "backup_archive", result["id"],
        {
            "scope": f"{result['periodFrom']}~{result['periodTo']}",
            "voucherCount": result["voucherCount"],
            "fingerprint": result["fingerprint"],
        },
    )
    return _download_response(
        result["filePath"],
        result["fileName"],
        {
            "X-Backup-Id": result["id"],
            "X-Backup-Fingerprint": result["fingerprint"],
            "X-Backup-Vouchers": str(result["voucherCount"]),
        },
    )


@router.get("/records")
async def get_records(
    limit: int = Query(50, ge=1, le=200),
    ctx: AuthContext = Depends(require_auth),
):
    """历史归档记录（时间/范围/大小/指纹），可重新下载。"""
    return {"items": await list_records(ctx.company_id, limit=limit)}


@router.get("/records/{record_id}/download")
async def download_record(record_id: str, ctx: AuthContext = Depends(require_auth)):
    record = await get_record(ctx.company_id, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="归档记录不存在")
    return _download_response(record["filePath"], record["fileName"])
