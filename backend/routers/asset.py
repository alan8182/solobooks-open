# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""固定资产路由：/api/asset/*（P1 WP-B 现代化）。

与 /api/intangible 同风格（无 /v1、资源复数、子资源动作）。角色校验在服务层
require_role 完成；本文件只做 require_auth + 参数装配。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_db_session
from deps.auth import AuthContext, require_auth
from models.schemas import (
    AmortizationRunRequest,
    AmortizationRunResponse,
    AmortizationScheduleResponse,
    DepreciationCatchUpRequest,
    DepreciationCatchUpResponse,
    FixedAssetCategoryListResponse,
    FixedAssetCreateRequest,
    FixedAssetDisposalCreateRequest,
    FixedAssetDisposalListResponse,
    FixedAssetDisposalResponse,
    FixedAssetEntryPrefillResponse,
    FixedAssetFromEntryRequest,
    FixedAssetFromEntryResponse,
    FixedAssetListResponse,
    FixedAssetRejectRequest,
    FixedAssetResponse,
    FixedAssetReconciliationResponse,
    FixedAssetUpdateRequest,
)
from services.accounting_core_service import ensure_tenant
from services.depreciation_engine import (
    get_runs,
    get_schedule,
    list_due_depreciation_periods,
    run_depreciation,
    run_depreciation_catch_up,
)
from services.fixed_asset_service import (
    approve_asset,
    create_asset,
    create_asset_from_entry,
    delete_asset,
    get_asset,
    get_fixed_asset_entry_prefill,
    list_assets,
    reject_asset,
    serialize_asset,
    submit_asset,
    update_asset,
)

router = APIRouter(prefix="/api/asset", tags=["asset"], dependencies=[Depends(require_auth)])


def _request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID") or str(uuid4())


def _current_period() -> str:
    return date.today().strftime("%Y-%m")


@router.get("/categories", response_model=FixedAssetCategoryListResponse)
async def fixed_asset_categories(ctx: AuthContext = Depends(require_auth)):
    """固定资产分类目录（唯一权威来源，前端不得硬编码）。"""
    from core.fixed_asset_categories import category_payload

    return {"items": category_payload()}


@router.get("/assets", response_model=FixedAssetListResponse)
async def list_fixed_assets(
    status: str | None = Query(None),
    category: str | None = Query(None),
    keyword: str | None = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    # 首次访问时补一次 legacy 存量迁移（已打标零成本跳过）
    from services.fixed_asset_backfill_service import ensure_backfilled

    await ensure_backfilled(ctx.company_id)
    return await list_assets(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        status=status,
        category=category,
        keyword=keyword,
        page=page,
        page_size=pageSize,
    )


@router.post("/assets", response_model=FixedAssetResponse)
async def create_fixed_asset(
    payload: FixedAssetCreateRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    data = payload.model_dump()
    payment_method = data.pop("paymentMethod", "company_account")
    asset = await create_asset(
        session,
        data,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
        payment_method=payment_method,
    )
    return serialize_asset(asset)


@router.get("/assets/from-entry/{entry_id}", response_model=FixedAssetEntryPrefillResponse)
async def fixed_asset_entry_prefill(
    entry_id: str,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    prefill = await get_fixed_asset_entry_prefill(
        session, entry_id, company_id=ctx.company_id, user_id=ctx.user_id
    )
    return {
        "sourceEntryId": prefill["sourceEntryId"],
        "existingAssetId": prefill["existingAssetId"],
        "name": prefill["name"],
        "acquisitionDate": prefill["acquisitionDate"],
        "availableForUseDate": prefill["availableForUseDate"],
        "originalValue": prefill["originalValue"],
        "suggestedCategory": prefill["suggestedCategory"],
    }


@router.post("/assets/from-entry", response_model=FixedAssetFromEntryResponse)
async def create_fixed_asset_from_entry(
    payload: FixedAssetFromEntryRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    asset = await create_asset_from_entry(
        session,
        payload.model_dump(),
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
    )
    due = await list_due_depreciation_periods(
        session,
        ctx.company_id,
        _current_period(),
        asset_ids=[asset.id],
    )
    return {
        "asset": serialize_asset(asset),
        "duePeriods": due,
        "dueTotalAmount": sum((item["amount"] for item in due), Decimal("0")),
    }


@router.get("/assets/{asset_id}", response_model=FixedAssetResponse)
async def get_fixed_asset(
    asset_id: str,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await get_asset(session, asset_id, company_id=ctx.company_id, user_id=ctx.user_id)


@router.put("/assets/{asset_id}", response_model=FixedAssetResponse)
async def update_fixed_asset(
    asset_id: str,
    payload: FixedAssetUpdateRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    asset = await update_asset(
        session,
        asset_id,
        payload.model_dump(exclude_unset=True),
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
    )
    from services.fixed_asset_service import serialize_asset

    return serialize_asset(asset)


@router.delete("/assets/{asset_id}")
async def delete_fixed_asset(
    asset_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    await delete_asset(
        session, asset_id, company_id=ctx.company_id, user_id=ctx.user_id, request_id=_request_id(request)
    )
    return {"ok": True}


@router.post("/assets/{asset_id}/submit")
async def submit_fixed_asset(
    asset_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await submit_asset(
        session, asset_id, company_id=ctx.company_id, user_id=ctx.user_id, request_id=_request_id(request)
    )


@router.post("/assets/{asset_id}/approve")
async def approve_fixed_asset(
    asset_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await approve_asset(
        session, asset_id, company_id=ctx.company_id, user_id=ctx.user_id, request_id=_request_id(request)
    )


@router.post("/assets/{asset_id}/reject")
async def reject_fixed_asset(
    asset_id: str,
    payload: FixedAssetRejectRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await reject_asset(
        session,
        asset_id,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
        reason=payload.reason,
    )


@router.get("/assets/{asset_id}/schedule", response_model=AmortizationScheduleResponse)
async def fixed_asset_schedule(
    asset_id: str,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    items = await get_schedule(session, asset_id, company_id=ctx.company_id, user_id=ctx.user_id)
    return {"items": items}


@router.post("/depreciation/run", response_model=AmortizationRunResponse)
async def run_fixed_asset_depreciation(
    payload: AmortizationRunRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """一键折旧：支持 dryRun 预览与指定资产分批执行。"""
    return await run_depreciation(
        session,
        payload.period,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
        asset_ids=payload.assetIds,
        dry_run=payload.dryRun,
    )


@router.post("/depreciation/catch-up", response_model=DepreciationCatchUpResponse)
async def run_fixed_asset_depreciation_catch_up(
    payload: DepreciationCatchUpRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """补提截至指定期间的固定资产折旧；逐期生成凭证，不跨期合并。"""
    return await run_depreciation_catch_up(
        session,
        payload.throughPeriod,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
        asset_ids=payload.assetIds,
        dry_run=payload.dryRun,
    )


@router.get("/depreciation/runs")
async def fixed_asset_depreciation_runs(
    period: str = Query(...),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await get_runs(session, period, company_id=ctx.company_id, user_id=ctx.user_id)


@router.get("/reconciliation", response_model=FixedAssetReconciliationResponse)
async def fixed_asset_reconciliation(
    month: str | None = Query(None),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """资产核对总账：卡片合计 vs 1601/1602 科目余额 vs 折旧计划累计。"""
    await ensure_tenant(session, ctx.company_id, ctx.user_id)
    from services.fixed_asset_reconciliation_service import asset_reconciliation

    return await asset_reconciliation(session, ctx.company_id, month=month)


@router.get("/backfill")
async def fixed_asset_backfill_status(
    ctx: AuthContext = Depends(require_auth),
):
    """legacy 存量迁移状态/手动触发（幂等，勾稽不平返回 mismatch 明细）。"""
    from services.fixed_asset_backfill_service import backfill_fixed_assets

    return await backfill_fixed_assets(ctx.company_id)


# ── 处置单 ────────────────────────────────────────────────────────────────


@router.post("/disposals", response_model=FixedAssetDisposalResponse)
async def create_fixed_asset_disposal(
    payload: FixedAssetDisposalCreateRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    from services.fixed_asset_disposal_service import create_disposal, serialize_disposal

    disposal = await create_disposal(
        session,
        payload.model_dump(),
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
    )
    return serialize_disposal(disposal)


@router.get("/disposals", response_model=FixedAssetDisposalListResponse)
async def list_fixed_asset_disposals(
    assetId: str | None = Query(None, alias="assetId"),
    status: str | None = Query(None),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    from services.fixed_asset_disposal_service import list_disposals

    return await list_disposals(
        session, company_id=ctx.company_id, user_id=ctx.user_id, asset_id=assetId, status=status
    )


@router.post("/disposals/{disposal_id}/submit")
async def submit_fixed_asset_disposal(
    disposal_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    from services.fixed_asset_disposal_service import submit_disposal

    return await submit_disposal(
        session, disposal_id, company_id=ctx.company_id, user_id=ctx.user_id, request_id=_request_id(request)
    )


@router.post("/disposals/{disposal_id}/approve")
async def approve_fixed_asset_disposal(
    disposal_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    from services.fixed_asset_disposal_service import approve_disposal

    return await approve_disposal(
        session, disposal_id, company_id=ctx.company_id, user_id=ctx.user_id, request_id=_request_id(request)
    )


@router.post("/disposals/{disposal_id}/cancel")
async def cancel_fixed_asset_disposal(
    disposal_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    from services.fixed_asset_disposal_service import cancel_disposal

    return await cancel_disposal(
        session, disposal_id, company_id=ctx.company_id, user_id=ctx.user_id, request_id=_request_id(request)
    )


# ── 台账导出 ──────────────────────────────────────────────────────────────


@router.get("/export/ledger")
async def export_fixed_asset_ledger(
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """固定资产台账 XLSX 导出。"""
    from io import BytesIO

    from fastapi.responses import StreamingResponse
    from openpyxl import Workbook
    from urllib.parse import quote

    result = await list_assets(
        session, company_id=ctx.company_id, user_id=ctx.user_id, page=1, page_size=2000
    )
    headers_row = [
        "资产编码", "名称", "类别", "取得方式", "取得日期", "起提月份",
        "年限（月）", "折旧方法", "原值", "净残值", "累计折旧", "账面净值",
        "剩余月数", "状态",
    ]
    acquisition_labels = {
        "purchase": "外购", "self_built": "自建",
        "investor_contribution": "投资者投入", "donation": "接受捐赠",
    }
    method_labels = {"straight_line": "年限平均法", "one_time": "一次性折旧"}
    status_labels = {
        "draft": "草稿", "pending_review": "待复核", "active": "使用中", "idle": "闲置",
        "fully_depreciated": "已折旧完毕", "disposing": "处置中", "disposed": "已处置", "scrapped": "已报废",
    }
    wb = Workbook()
    ws = wb.active
    ws.title = "固定资产台账"
    ws.append(headers_row)
    for asset in result["items"]:
        ws.append([
            asset["code"], asset["name"], asset["categoryName"],
            acquisition_labels.get(asset["acquisitionType"], asset["acquisitionType"]),
            asset["acquisitionDate"], asset["availableForUseDate"][:7],
            asset["usefulLifeMonths"],
            method_labels.get(asset["depreciationMethod"], asset["depreciationMethod"]),
            float(asset["originalValue"]), float(asset["residualValue"]),
            float(asset["accumulatedDepreciation"]), float(asset["netValue"]),
            asset["remainingMonths"],
            status_labels.get(asset["status"], asset["status"]),
        ])
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = "fixed-asset-ledger.xlsx"
    quoted = quote(filename)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )
