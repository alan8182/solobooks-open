# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""无形资产路由：/api/intangible/*。

与 /api/asset 同风格（无 /v1、资源复数、子资源动作）。角色校验在服务层
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
    AmortizationCatchUpRequest,
    AmortizationCatchUpResponse,
    AmortizationRunRequest,
    AmortizationScheduleResponse,
    AmortizationSummaryRequest,
    IntangibleAssetCreateRequest,
    IntangibleAssetEntryPrefillResponse,
    IntangibleAssetFromEntryRequest,
    IntangibleAssetFromEntryResponse,
    IntangibleAssetListResponse,
    IntangibleAssetResponse,
    IntangibleAssetUpdateRequest,
    IntangibleCategoryListResponse,
    IntangibleAssetChangeRequest,
    IntangibleDisposalCreateRequest,
    IntangibleDisposalListResponse,
    IntangibleDisposalResponse,
    IntangibleImpairmentTestCreateRequest,
    IntangibleImpairmentTestListResponse,
    IntangibleImpairmentTestResponse,
    IntangibleRejectRequest,
)
from services.accounting_core_service import ensure_tenant
from services.amortization_engine import (
    amortization_summary,
    get_runs,
    get_schedule,
    list_due_amortization_periods,
    run_amortization,
    run_amortization_catch_up,
)
from services.intangible_asset_service import (
    approve_asset,
    create_asset,
    create_intangible_asset_from_entry as create_intangible_asset_from_entry_service,
    delete_asset,
    get_asset,
    get_intangible_asset_entry_prefill,
    list_assets,
    reject_asset,
    serialize_asset,
    submit_asset,
    update_asset,
)

router = APIRouter(prefix="/api/intangible", tags=["intangible"], dependencies=[Depends(require_auth)])


def _request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID") or str(uuid4())


def _current_period() -> str:
    return date.today().strftime("%Y-%m")


@router.get("/categories", response_model=IntangibleCategoryListResponse)
async def intangible_categories(ctx: AuthContext = Depends(require_auth)):
    """无形资产分类目录（唯一权威来源，前端不得硬编码）。"""
    from core.intangible_categories import category_payload

    return {"items": category_payload()}


@router.get("/assets", response_model=IntangibleAssetListResponse)
async def list_intangible_assets(
    status: str | None = Query(None),
    category: str | None = Query(None),
    keyword: str | None = Query(None),
    expiringInDays: int | None = Query(None, alias="expiringInDays"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await list_assets(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        status=status,
        category=category,
        keyword=keyword,
        expiring_in_days=expiringInDays,
        page=page,
        page_size=pageSize,
    )


@router.post("/assets", response_model=IntangibleAssetResponse)
async def create_intangible_asset(
    payload: IntangibleAssetCreateRequest,
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


@router.get("/assets/from-entry/{entry_id}", response_model=IntangibleAssetEntryPrefillResponse)
async def intangible_asset_entry_prefill(
    entry_id: str,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    prefill = await get_intangible_asset_entry_prefill(
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
        "voucherNo": prefill.get("voucherNo"),
        "summary": prefill.get("summary"),
        "sourceAccount": prefill.get("sourceAccount"),
    }


@router.post("/assets/from-entry", response_model=IntangibleAssetFromEntryResponse)
async def create_intangible_asset_from_entry(
    payload: IntangibleAssetFromEntryRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    asset = await create_intangible_asset_from_entry_service(
        session,
        payload.model_dump(),
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
    )
    due = await list_due_amortization_periods(
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


@router.get("/assets/{asset_id}", response_model=IntangibleAssetResponse)
async def get_intangible_asset(
    asset_id: str,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await get_asset(session, asset_id, company_id=ctx.company_id, user_id=ctx.user_id)


@router.put("/assets/{asset_id}", response_model=IntangibleAssetResponse)
async def update_intangible_asset(
    asset_id: str,
    payload: IntangibleAssetUpdateRequest,
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
    from services.intangible_asset_service import serialize_asset

    return serialize_asset(asset)


@router.delete("/assets/{asset_id}")
async def delete_intangible_asset(
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
async def submit_intangible_asset(
    asset_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await submit_asset(
        session, asset_id, company_id=ctx.company_id, user_id=ctx.user_id, request_id=_request_id(request)
    )


@router.post("/assets/{asset_id}/approve")
async def approve_intangible_asset(
    asset_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await approve_asset(
        session, asset_id, company_id=ctx.company_id, user_id=ctx.user_id, request_id=_request_id(request)
    )


@router.post("/assets/{asset_id}/reject")
async def reject_intangible_asset(
    asset_id: str,
    payload: IntangibleRejectRequest,
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
async def intangible_schedule(
    asset_id: str,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    items = await get_schedule(session, asset_id, company_id=ctx.company_id, user_id=ctx.user_id)
    return {"items": items}


@router.post("/amortization/run")
async def run_intangible_amortization(
    payload: AmortizationRunRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """一键摊销：支持 dryRun 预览与指定资产分批执行。"""
    return await run_amortization(
        session,
        payload.period,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
        asset_ids=payload.assetIds,
        dry_run=payload.dryRun,
    )


@router.post("/amortization/catch-up", response_model=AmortizationCatchUpResponse)
async def run_intangible_amortization_catch_up(
    payload: AmortizationCatchUpRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """补提截至指定期间的无形资产摊销；逐期生成凭证，不跨期合并。"""
    return await run_amortization_catch_up(
        session,
        payload.throughPeriod,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
        asset_ids=payload.assetIds,
        dry_run=payload.dryRun,
    )


@router.get("/amortization/runs")
async def intangible_amortization_runs(
    period: str = Query(...),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await get_runs(session, period, company_id=ctx.company_id, user_id=ctx.user_id)


@router.post("/reports/amortization-summary")
async def intangible_amortization_summary(
    payload: AmortizationSummaryRequest,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    return await amortization_summary(
        session, payload.periodFrom, payload.periodTo, company_id=ctx.company_id, user_id=ctx.user_id
    )


# ── 处置单（P1；处置服务随批次 6 落地，懒导入避免循环依赖）────────────────


@router.post("/disposals", response_model=IntangibleDisposalResponse)
async def create_intangible_disposal(
    payload: IntangibleDisposalCreateRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    from services.intangible_disposal_service import create_disposal, serialize_disposal

    disposal = await create_disposal(
        session,
        payload.model_dump(),
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
    )
    return serialize_disposal(disposal)


@router.get("/disposals", response_model=IntangibleDisposalListResponse)
async def list_intangible_disposals(
    assetId: str | None = Query(None, alias="assetId"),
    status: str | None = Query(None),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    from services.intangible_disposal_service import list_disposals

    return await list_disposals(
        session, company_id=ctx.company_id, user_id=ctx.user_id, asset_id=assetId, status=status
    )


@router.post("/disposals/{disposal_id}/submit")
async def submit_intangible_disposal(
    disposal_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    from services.intangible_disposal_service import submit_disposal

    return await submit_disposal(
        session, disposal_id, company_id=ctx.company_id, user_id=ctx.user_id, request_id=_request_id(request)
    )


@router.post("/disposals/{disposal_id}/approve")
async def approve_intangible_disposal(
    disposal_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    from services.intangible_disposal_service import approve_disposal

    return await approve_disposal(
        session, disposal_id, company_id=ctx.company_id, user_id=ctx.user_id, request_id=_request_id(request)
    )


@router.post("/disposals/{disposal_id}/cancel")
async def cancel_intangible_disposal(
    disposal_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    from services.intangible_disposal_service import cancel_disposal

    return await cancel_disposal(
        session, disposal_id, company_id=ctx.company_id, user_id=ctx.user_id, request_id=_request_id(request)
    )


# ── 减值测试（P2）─────────────────────────────────────────────────────────


@router.post("/impairment-tests", response_model=IntangibleImpairmentTestResponse)
async def create_intangible_impairment_test(
    payload: IntangibleImpairmentTestCreateRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    from services.intangible_impairment_service import create_test

    return await create_test(
        session,
        payload.model_dump(),
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
    )


@router.get("/impairment-tests", response_model=IntangibleImpairmentTestListResponse)
async def list_intangible_impairment_tests(
    assetId: str | None = Query(None, alias="assetId"),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    from services.intangible_impairment_service import list_tests

    return await list_tests(session, company_id=ctx.company_id, user_id=ctx.user_id, asset_id=assetId)


@router.post("/impairment-tests/{test_id}/review", response_model=IntangibleImpairmentTestResponse)
async def review_intangible_impairment_test(
    test_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    from services.intangible_impairment_service import review_test

    return await review_test(
        session, test_id, company_id=ctx.company_id, user_id=ctx.user_id, request_id=_request_id(request)
    )


# ── 变更单（会计估计变更，未来适用法）────────────────────────────────────


@router.post("/assets/{asset_id}/changes")
async def change_intangible_asset(
    asset_id: str,
    payload: IntangibleAssetChangeRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    from services.intangible_asset_service import apply_life_change

    return await apply_life_change(
        session,
        asset_id,
        payload.model_dump(),
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        request_id=_request_id(request),
    )


# ── 到期提醒 / A105080 预览 / 台账导出（P2+P3）───────────────────────────


@router.get("/reports/expiring", response_model=IntangibleAssetListResponse)
async def expiring_intangible_assets(
    days: int = Query(90, ge=1, le=365),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    result = await list_assets(
        session, company_id=ctx.company_id, user_id=ctx.user_id, expiring_in_days=days, page=1, page_size=200
    )
    return result


@router.get("/reports/a105080-preview")
async def intangible_a105080_preview(
    year: int = Query(...),
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    from services.amortization_engine import a105080_preview

    return await a105080_preview(session, year, company_id=ctx.company_id, user_id=ctx.user_id)


@router.get("/export/ledger")
async def export_intangible_ledger(
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """无形资产台账 XLSX 导出。"""
    from io import BytesIO

    from fastapi.responses import StreamingResponse
    from openpyxl import Workbook
    from urllib.parse import quote

    result = await list_assets(
        session, company_id=ctx.company_id, user_id=ctx.user_id, page=1, page_size=2000
    )
    headers_row = [
        "资产编码", "名称", "类别", "取得方式", "取得日期", "起摊月份",
        "年限（月）", "原值", "净残值", "累计摊销", "账面净值",
        "剩余月数", "权利到期日", "状态",
    ]
    acquisition_labels = {
        "purchase": "外购", "self_developed": "研发资本化",
        "investor_contribution": "投资者投入", "donation": "接受捐赠",
    }
    status_labels = {
        "draft": "草稿", "pending_review": "待复核", "active": "摊销中", "idle": "已停用",
        "fully_amortized": "已摊销完毕", "disposing": "处置中", "disposed": "已处置", "scrapped": "已核销",
    }
    wb = Workbook()
    ws = wb.active
    ws.title = "无形资产台账"
    ws.append(headers_row)
    for asset in result["items"]:
        ws.append([
            asset["code"], asset["name"], asset["categoryName"],
            acquisition_labels.get(asset["acquisitionType"], asset["acquisitionType"]),
            asset["acquisitionDate"], asset["availableForUseDate"][:7],
            asset["usefulLifeMonths"],
            float(asset["originalValue"]), float(asset["residualValue"]),
            float(asset["accumulatedAmortization"]), float(asset["netValue"]),
            asset["remainingMonths"],
            asset["expiryDate"] or "",
            status_labels.get(asset["status"], asset["status"]),
        ])
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = "intangible-ledger.xlsx"
    quoted = quote(filename)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )
