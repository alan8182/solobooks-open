# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""上手引导路由：账套就绪度 + 演示账套。

就绪度是 GET 纯读接口，**不要在这里调 ``ensure_tenant``**——它会 seed 科目，
既让只读接口产生写副作用，又会让「科目表」永远自证完成。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_db_session
from deps.auth import AuthContext, require_auth
from models.schemas import DemoBookDeleteResponse, DemoBookResponse, SetupReadinessResponse
from services import demo_book_service
from services.setup_readiness_service import compute_readiness

router = APIRouter(
    prefix="/api/setup", tags=["setup"], dependencies=[Depends(require_auth)]
)


@router.get("/readiness", response_model=SetupReadinessResponse)
async def get_readiness(
    view: Literal["boss", "accounting"] = "boss",
    waived: str = "",
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
):
    """账套就绪度：账套能不能开始用。

    ``waived`` 是逗号分隔的步骤 key（如 ``legacy_import``），由前端记住用户
    主动跳过的可选项；判定逻辑仍只在后端，前端不参与算状态。
    """
    keys = [item.strip() for item in (waived or "").split(",") if item.strip()]
    return await compute_readiness(
        session,
        ctx.company_id,
        user_id=ctx.user_id,
        view=view,
        waived=keys,
    )


@router.post("/demo", response_model=DemoBookResponse)
async def create_demo_book(ctx: AuthContext = Depends(require_auth)):
    """生成（或复用）当前用户的演示账套。

    幂等：同一用户已有演示账套时直接返回它，不重复建。
    3 个月约 22~25 张凭证，耗时 4~10 秒，前端需要单独放宽超时。
    """
    return await demo_book_service.create_demo_company(ctx.user_id)


@router.delete("/demo/{company_id}", response_model=DemoBookDeleteResponse)
async def delete_demo_book(company_id: str, ctx: AuthContext = Depends(require_auth)):
    """删除演示账套。只允许删自己的、且带 ``isDemo`` 标记的账套。"""
    return await demo_book_service.delete_demo_company(ctx.user_id, company_id)
