# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from fastapi import APIRouter, Depends, HTTPException
from deps.auth import AuthContext, require_auth
from models.schemas import CategoryCreate, CategoryUpdate, CategoryResponse
from services.category_service import (
    list_categories, create_category, update_category, delete_category,
    list_account_mapping,
)

router = APIRouter(prefix="/api/categories", tags=["categories"], dependencies=[Depends(require_auth)])


@router.get("/account-mapping")
async def get_account_mapping(ctx: AuthContext = Depends(require_auth)):
    """分类 → 会计科目权威映射（前端下拉/凭证预览统一从这里取）。"""
    return {"items": list_account_mapping()}


@router.get("", response_model=list[CategoryResponse])
async def get_categories(ctx: AuthContext = Depends(require_auth)):
    """Get all categories for the current company."""
    return await list_categories(ctx.company_id)


@router.post("", response_model=CategoryResponse)
async def add_category(req: CategoryCreate, ctx: AuthContext = Depends(require_auth)):
    """Create a custom category."""
    cat = await create_category(
        ctx.company_id, req.name, req.direction,
        parent_id=req.parent_id, sort_order=req.sort_order,
    )
    return CategoryResponse(**cat)


@router.put("/{category_id}", response_model=CategoryResponse)
async def edit_category(category_id: str, req: CategoryUpdate, ctx: AuthContext = Depends(require_auth)):
    """Update a category."""
    updates = req.dict(exclude_unset=True, exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    cat = await update_category(ctx.company_id, category_id, updates)
    if not cat:
        raise HTTPException(status_code=404, detail="分类不存在")
    return CategoryResponse(**cat)


@router.delete("/{category_id}")
async def remove_category(category_id: str, ctx: AuthContext = Depends(require_auth)):
    """Delete a custom category (system categories cannot be deleted)."""
    ok = await delete_category(ctx.company_id, category_id)
    if not ok:
        raise HTTPException(status_code=404, detail="分类不存在或为系统分类，无法删除")
    return {"ok": True}
