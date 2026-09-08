"""全局搜索路由"""
from fastapi import APIRouter, Depends, Query
from deps.auth import AuthContext, require_auth
from services.search_service import search_all

router = APIRouter(prefix="/api/search", tags=["search"], dependencies=[Depends(require_auth)])


@router.get("")
async def global_search(q: str = Query("", min_length=1), ctx: AuthContext = Depends(require_auth)):
    """Global search across entries and accounts."""
    return await search_all(ctx.company_id, q)
