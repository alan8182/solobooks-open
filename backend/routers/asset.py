# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from fastapi import APIRouter, Depends, HTTPException, Query
from deps.auth import AuthContext, require_auth
from models.schemas import FixedAssetCreate, FixedAssetUpdate, FixedAssetResponse
from services.fixed_asset_service import save_fixed_asset, list_fixed_assets, delete_fixed_asset
from services.depreciation_engine import run_depreciation, get_depreciation_schedule
from typing import List, Optional

router = APIRouter(prefix="/api/asset", tags=["asset"], dependencies=[Depends(require_auth)])

@router.post("/assets", response_model=FixedAssetResponse)
async def create_asset(asset: FixedAssetCreate, ctx: AuthContext = Depends(require_auth)):
    """创建或同步固定资产"""
    from services.rd_allocation_service import validate_asset_rd_config
    await validate_asset_rd_config(ctx.company_id, ctx.user_id, asset.model_dump())
    saved = await save_fixed_asset(asset.model_dump(), user_id=ctx.user_id, company_id=ctx.company_id)
    return FixedAssetResponse(**saved)

@router.get("/assets", response_model=List[FixedAssetResponse])
async def get_assets(ctx: AuthContext = Depends(require_auth)):
    """获取当前主体的所有资产"""
    assets = await list_fixed_assets(ctx.user_id, ctx.company_id)
    return [FixedAssetResponse(**a) for a in assets]

@router.put("/assets/{asset_id}", response_model=FixedAssetResponse)
async def update_asset(asset_id: str, updates: FixedAssetUpdate, ctx: AuthContext = Depends(require_auth)):
    """更新资产状态（折旧、报废等）"""
    assets = await list_fixed_assets(ctx.user_id, ctx.company_id)
    target = next((a for a in assets if a["id"] == asset_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="资产不存在")
    
    updated_data = {**target, **updates.model_dump(exclude_unset=True)}
    from services.rd_allocation_service import validate_asset_rd_config
    await validate_asset_rd_config(ctx.company_id, ctx.user_id, updated_data)
    saved = await save_fixed_asset(updated_data, user_id=ctx.user_id, company_id=ctx.company_id)
    return FixedAssetResponse(**saved)

@router.delete("/assets/{asset_id}")
async def remove_asset(asset_id: str, ctx: AuthContext = Depends(require_auth)):
    """删除资产"""
    ok = await delete_fixed_asset(asset_id, user_id=ctx.user_id, company_id=ctx.company_id)
    if not ok:
        raise HTTPException(status_code=404, detail="资产不存在")
    return {"ok": True}


@router.post("/depreciation/run")
async def run_depreciation_endpoint(period: str = Query(...), ctx: AuthContext = Depends(require_auth)):
    """批量计提折旧（指定月份 YYYY-MM）"""
    result = await run_depreciation(ctx.company_id, period, user_id=ctx.user_id)
    return result


@router.get("/depreciation/schedule")
async def depreciation_schedule(asset_id: Optional[str] = None, ctx: AuthContext = Depends(require_auth)):
    """查询折旧明细"""
    rows = await get_depreciation_schedule(ctx.company_id, asset_id)
    return {"items": rows}
