# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""审计日志路由"""
from fastapi import APIRouter, Depends, Query
from deps.auth import AuthContext, require_auth
from services.audit_service import get_audit_logs, count_audit_logs

router = APIRouter(prefix="/api/audit", tags=["audit"], dependencies=[Depends(require_auth)])


@router.get("/logs")
async def list_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: AuthContext = Depends(require_auth),
):
    """Get paginated audit logs."""
    logs = await get_audit_logs(ctx.company_id, limit=limit, offset=offset)
    total = await count_audit_logs(ctx.company_id)
    return {"items": logs, "total": total, "limit": limit, "offset": offset}
