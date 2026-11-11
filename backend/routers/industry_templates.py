# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Authenticated read API for the backend-owned industry catalog."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_db_session
from deps.auth import AuthContext, require_auth
from services.industry_template_service import list_templates, serialize_template


router = APIRouter(
    prefix="/api/industry-templates",
    tags=["industry-templates"],
    dependencies=[Depends(require_auth)],
)


@router.get("")
async def get_industry_templates(
    q: str = Query(default="", max_length=128),
    include_hidden: bool = Query(default=True, alias="includeHidden"),
    _ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    templates = await list_templates(
        session,
        include_hidden=include_hidden,
        include_disabled=False,
        query=q,
    )
    return {
        "items": [serialize_template(template) for template in templates],
        "total": len(templates),
    }
