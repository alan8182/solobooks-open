# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""周期性交易模板路由"""
from fastapi import APIRouter, Depends, HTTPException
from deps.auth import AuthContext, require_auth
from pydantic import BaseModel
from typing import Any, Dict, Optional
from services.recurring_service import (
    list_templates, get_template, create_template,
    update_template, delete_template, apply_template,
)

router = APIRouter(prefix="/api/recurring", tags=["recurring"], dependencies=[Depends(require_auth)])


class TemplateCreate(BaseModel):
    name: str
    template_data: Dict[str, Any]
    frequency: str
    next_due_date: str


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    template_data: Optional[Dict[str, Any]] = None
    frequency: Optional[str] = None
    next_due_date: Optional[str] = None
    is_active: Optional[int] = None


@router.get("")
async def get_templates(ctx: AuthContext = Depends(require_auth)):
    """List all recurring templates."""
    return await list_templates(ctx.company_id)


@router.get("/{template_id}")
async def get_template_by_id(template_id: str, ctx: AuthContext = Depends(require_auth)):
    """Get a single template."""
    tpl = await get_template(ctx.company_id, template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    return tpl


@router.post("")
async def add_template(req: TemplateCreate, ctx: AuthContext = Depends(require_auth)):
    """Create a recurring template."""
    tpl = await create_template(
        ctx.company_id, req.name, req.template_data,
        req.frequency, req.next_due_date,
    )
    return tpl


@router.put("/{template_id}")
async def edit_template(template_id: str, req: TemplateUpdate,
                         ctx: AuthContext = Depends(require_auth)):
    """Update a template."""
    updates = req.dict(exclude_unset=True, exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    tpl = await update_template(ctx.company_id, template_id, updates)
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    return tpl


@router.delete("/{template_id}")
async def remove_template(template_id: str, ctx: AuthContext = Depends(require_auth)):
    """Delete a template."""
    ok = await delete_template(ctx.company_id, template_id)
    if not ok:
        raise HTTPException(status_code=404, detail="模板不存在")
    return {"ok": True}


@router.post("/{template_id}/apply")
async def apply_template_entry(template_id: str, ctx: AuthContext = Depends(require_auth)):
    """Apply a template: create an entry from it."""
    entry = await apply_template(ctx.company_id, template_id, user_id=ctx.user_id)
    if not entry:
        raise HTTPException(status_code=400, detail="模板不存在或已停用")
    return {"entry": entry}
