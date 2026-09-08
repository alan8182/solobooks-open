"""应收/应付账龄路由"""
from fastapi import APIRouter, Depends
from deps.auth import AuthContext, require_auth
from services.aging_service import query_ar_aging, query_ap_aging

router = APIRouter(prefix="/api/report", tags=["aging"], dependencies=[Depends(require_auth)])


@router.get("/ar-aging")
async def ar_aging(as_of: str = "", ctx: AuthContext = Depends(require_auth)):
    """应收账龄分析（应收账款 1122）"""
    result = await query_ar_aging(ctx.company_id, as_of)
    return result


@router.get("/ap-aging")
async def ap_aging(as_of: str = "", ctx: AuthContext = Depends(require_auth)):
    """应付账龄分析（应付账款 2202）"""
    result = await query_ap_aging(ctx.company_id, as_of)
    return result
