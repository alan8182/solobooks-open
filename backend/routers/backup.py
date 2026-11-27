# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""备份与归档路由（P0 WP5）。

归档生成为同步请求：期间上限 24 个月、分录行上限 5 万行（见服务层），
超出时提示分批，避免把 web worker 拖到超时。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import quote

import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from deps.auth import AuthContext, require_auth
from services.audit_service import log_action
from services.backup_archive_service import (
    build_archive,
    build_ledger_pdf_archive,
    get_record,
    list_records,
)
from services.ledger_book_snapshot_service import (
    SnapshotError,
    find_snapshot_pdf_sha_by_path,
)

router = APIRouter(prefix="/api/backup", tags=["backup"], dependencies=[Depends(require_auth)])

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _download_response(
    path: str,
    filename: str,
    extra_headers: dict[str, str] | None = None,
    *,
    expected_sha256: str | None = None,
) -> FileResponse:
    if not Path(path).exists():
        raise HTTPException(status_code=410, detail="归档文件已被清理，请重新生成")
    # 下发前重算哈希：签发件与登记的 pdf_sha256 必须一致，坏文件宁可不给。
    digest = await anyio.to_thread.run_sync(_sha256_file, path)
    if expected_sha256 and digest != expected_sha256:
        raise HTTPException(
            status_code=409,
            detail="归档文件校验失败（sha256 不符），请从备份恢复后重试",
        )
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        "X-Backup-Sha256": digest,
    }
    if extra_headers:
        headers.update(extra_headers)
    media_type = "application/pdf" if filename.lower().endswith(".pdf") else _XLSX
    return FileResponse(path, media_type=media_type, filename=filename, headers=headers)


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
    return await _download_response(
        result["filePath"],
        result["fileName"],
        {
            "X-Backup-Id": result["id"],
            "X-Backup-Fingerprint": result["fingerprint"],
            "X-Backup-Vouchers": str(result["voucherCount"]),
        },
    )


@router.post("/ledger-pdf")
async def create_ledger_pdf_archive(
    year: int = Query(..., ge=2000, le=2999, description="会计年度"),
    month: str | None = Query(None, description="月份 YYYY-MM；月度册必填"),
    scope: str = Query("year", description="year 或 month"),
    snapshot_id: str | None = Query(None, description="已签发账簿快照 ID（可选）"),
    ctx: AuthContext = Depends(require_auth),
):
    """生成连续电子账簿 PDF（带封面/目录/书签）并留痕归档。"""
    try:
        result = await build_ledger_pdf_archive(
            ctx.company_id,
            ctx.user_id,
            year=year,
            month=month,
            scope=scope,
            snapshot_id=snapshot_id,
        )
    except SnapshotError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await log_action(
        ctx.company_id, ctx.user_id, "export", "backup_archive", result["id"],
        {
            "format": "pdf",
            "scope": f"{result['periodFrom']}~{result['periodTo']}",
            "voucherCount": result["voucherCount"],
            "pageCount": result["pageCount"],
            "fingerprint": result["fingerprint"],
            "bookNo": result["bookNo"],
            "status": result["status"],
        },
    )
    headers = {
        "X-Backup-Id": result["id"],
        "X-Backup-Fingerprint": result["fingerprint"],
        "X-Backup-Vouchers": str(result["voucherCount"]),
        "X-Book-Page-Count": str(result["pageCount"]),
        "X-Book-Pdf-Sha256": result["pdfSha256"],
        "X-Book-Status": result["status"],
    }
    if result.get("bookNo"):
        headers["X-Book-No"] = str(result["bookNo"])
    return await _download_response(
        result["filePath"],
        result["fileName"],
        headers,
        expected_sha256=result.get("pdfSha256"),
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
    expected = await find_snapshot_pdf_sha_by_path(ctx.company_id, record["filePath"])
    return await _download_response(
        record["filePath"],
        record["fileName"],
        expected_sha256=expected,
    )
