# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""凭证册装订路由（Phase 2 单据整理）。

语义：按月把已过账凭证装订成册——预览（无关账，水印）、签发定版（关账后，
复用账簿快照机制 book_kind='voucher'）、列表/下载/作废。附单据数走真实
附件聚合（A3），与凭证打印页共用同一端点。
"""
from __future__ import annotations

import hashlib
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from deps.auth import AuthContext, require_auth
from services.audit_service import log_action
from services.attachment_storage_service import attachment_counts_by_entries
from services.ledger_book_snapshot_service import (
    SnapshotError,
    find_matching_issued_snapshot,
    get_snapshot,
    issue_voucher_book_snapshot,
    list_snapshots,
    render_snapshot_pdf_from_row_async,
    void_snapshot,
)
from services.voucher_book_pdf_service import render_voucher_book_pdf_async
from services.voucher_book_service import VoucherBookTooLarge, build_voucher_book

router = APIRouter(prefix="/api/voucher-book", tags=["voucher-book"], dependencies=[Depends(require_auth)])


def _raise_snapshot_error(exc: SnapshotError) -> HTTPException:
    status = exc.status_code if exc.status_code in (404, 409, 413, 422) else 400
    return HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message})


@router.get("/preview")
async def get_voucher_book_preview(
    month: str = Query(..., description="期间 YYYY-MM"),
    ctx: AuthContext = Depends(require_auth),
):
    """仿真册预览 payload（只读，不落库、不签发）。

    与签发同源（build_voucher_book），前端阅读器据此渲染封面/凭证/发票复现/
    原件目录/签章；同内容已签发时返回 signed=true 与册号，阅读器据此决定是否
    显示「预览件」水印。
    """
    try:
        book = await build_voucher_book(ctx.company_id, month=month)
    except VoucherBookTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    match = await find_matching_issued_snapshot(
        ctx.company_id,
        book_kind="voucher",
        period=month[:7],
        content_hash_value=str(book["meta"].get("contentHash") or ""),
    )
    return {
        "payload": book,
        "signed": match is not None,
        "bookNo": match.book_no if match is not None else None,
        "status": match.status if match is not None else None,
    }


def _pdf_response(
    pdf_bytes: bytes,
    file_name: str,
    *,
    headers: dict[str, str],
) -> Response:
    all_headers = {
        **headers,
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}",
        "X-Voucher-Book-Sha256": hashlib.sha256(pdf_bytes).hexdigest(),
    }
    return Response(content=pdf_bytes, media_type="application/pdf", headers=all_headers)


@router.get("/pdf")
async def get_voucher_book_pdf(
    month: str = Query(..., description="期间 YYYY-MM"),
    ctx: AuthContext = Depends(require_auth),
):
    """凭证册预览/已签发件下载：同内容已签发时直接复用归档件（不重复渲染）。"""
    try:
        book = await build_voucher_book(ctx.company_id, month=month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    content_hash_value = str(book["meta"]["contentHash"])
    match = await find_matching_issued_snapshot(
        ctx.company_id, book_kind="voucher", period=month[:7], content_hash_value=content_hash_value
    )
    if match is not None:
        result = await render_snapshot_pdf_from_row_async(match)
        await log_action(
            ctx.company_id, ctx.user_id, "export", "voucher_book", match.id,
            {"format": "pdf", "bookNo": match.book_no, "pageCount": result["pageCount"], "source": "issued"},
        )
        return _pdf_response(
            result["pdfBytes"],
            result["fileName"],
            headers={
                "X-Voucher-Book-No": match.book_no,
                "X-Voucher-Book-Status": match.status,
                "X-Voucher-Book-Archived": "true",
                "X-Voucher-Book-Page-Count": str(result["pageCount"]),
            },
        )
    rendered = await render_voucher_book_pdf_async(book)
    await log_action(
        ctx.company_id, ctx.user_id, "export", "voucher_book", None,
        {"format": "pdf", "month": month[:7], "pageCount": rendered.page_count, "source": "preview"},
    )
    month_text = month[:7].replace("-", "年") + "月"
    company_name = str(book["meta"]["companyName"] or "")
    file_name = f"{company_name}_凭证册_{month_text}_预览件.pdf" if company_name else f"凭证册_{month_text}_预览件.pdf"
    return _pdf_response(
        rendered.pdf_bytes,
        file_name,
        headers={
            "X-Voucher-Book-Status": "preview",
            "X-Voucher-Book-Archived": "false",
            "X-Voucher-Book-Page-Count": str(rendered.page_count),
        },
    )


@router.post("/snapshots")
async def issue_voucher_book(
    payload: dict,
    ctx: AuthContext = Depends(require_auth),
):
    """签发正式凭证册：期间必须已关账；每次签发新册新号（重签发不受阻）。"""
    month = str(payload.get("month") or "")
    try:
        result = await issue_voucher_book_snapshot(
            ctx.company_id, month=month, user_id=ctx.user_id
        )
    except SnapshotError as exc:
        raise _raise_snapshot_error(exc) from exc
    await log_action(
        ctx.company_id, ctx.user_id, "issue", "voucher_book", result["id"],
        {"bookNo": result["bookNo"], "pageCount": result["pageCount"], "pdfSha256": result["pdfSha256"]},
    )
    return result


@router.get("/snapshots")
async def list_voucher_book_snapshots(
    period: Optional[str] = None,
    ctx: AuthContext = Depends(require_auth),
):
    """已签发凭证册列表（只含 book_kind='voucher'）。"""
    return {"items": await list_snapshots(ctx.company_id, period=period, book_kind="voucher")}


@router.get("/snapshots/{snapshot_id}")
async def get_voucher_book_snapshot(snapshot_id: str, ctx: AuthContext = Depends(require_auth)):
    """取回凭证册完整 payload（屏幕预览用，与签发时逐字一致）。"""
    try:
        return await get_snapshot(ctx.company_id, snapshot_id)
    except SnapshotError as exc:
        raise _raise_snapshot_error(exc) from exc


@router.get("/snapshots/{snapshot_id}/download")
async def download_voucher_book_snapshot(snapshot_id: str, ctx: AuthContext = Depends(require_auth)):
    """下载已签发凭证册 PDF（优先归档件，sha256 校验 409，丢失只读现渲染）。"""
    try:
        result = await render_snapshot_pdf_by_id(ctx.company_id, snapshot_id)
    except SnapshotError as exc:
        raise _raise_snapshot_error(exc) from exc
    return _pdf_response(
        result["pdfBytes"],
        result["fileName"],
        headers={
            "X-Voucher-Book-No": result["bookNo"],
            "X-Voucher-Book-Status": result["status"],
            "X-Voucher-Book-Archived": "true" if result["archivedPdf"] else "false",
            "X-Voucher-Book-Page-Count": str(result["pageCount"]),
        },
    )


async def render_snapshot_pdf_by_id(company_id: str, snapshot_id: str) -> dict:
    from services.ledger_book_snapshot_service import render_snapshot_pdf

    return await render_snapshot_pdf(company_id, snapshot_id)


@router.post("/snapshots/{snapshot_id}/void")
async def void_voucher_book(
    snapshot_id: str,
    payload: dict,
    ctx: AuthContext = Depends(require_auth),
):
    """作废凭证册：状态标记 + 审计留痕，册与归档件保留（A2）。"""
    try:
        result = await void_snapshot(ctx.company_id, snapshot_id, reason=str(payload.get("reason") or ""), user_id=ctx.user_id)
    except SnapshotError as exc:
        raise _raise_snapshot_error(exc) from exc
    await log_action(
        ctx.company_id, ctx.user_id, "void", "voucher_book", snapshot_id,
        {"bookNo": result["bookNo"], "reason": result["voidReason"]},
    )
    return result


@router.get("/period-status")
async def get_voucher_book_period_status(
    month: str = Query(..., description="期间 YYYY-MM"),
    ctx: AuthContext = Depends(require_auth),
):
    """期间关账状态（签发条可用性判断；与账簿/凭证册同一 AccountingPeriod 真相）。"""
    from datetime import date

    from sqlalchemy import select as sa_select

    from db.models import AccountingPeriod
    from db.session import session_scope

    try:
        year, mon = (int(part) for part in month[:7].split("-", 1))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"期间格式无效：{month}") from exc
    async with session_scope() as session:
        status = await session.scalar(
            sa_select(AccountingPeriod.status).where(
                AccountingPeriod.company_id == ctx.company_id,
                AccountingPeriod.period_start == date(year, mon, 1),
            )
        )
    return {"month": month[:7], "periodStatus": str(status or "open")}


@router.get("/attachment-counts")
async def get_attachment_counts(
    entry_ids: str = Query(..., description="逗号分隔的凭证 id"),
    ctx: AuthContext = Depends(require_auth),
):
    """凭证 → 真实附单据数（发票→active 附件聚合；打印页与凭证册共用）。"""
    ids = [item.strip() for item in entry_ids.split(",") if item.strip()]
    return {"counts": await attachment_counts_by_entries(ctx.company_id, ids)}
