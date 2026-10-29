# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""用户侧 AI 用量查询 API：本人 token 消耗汇总（只读，跨账套按用户聚合）。

数据来自 services/llm.py 写入的 llm_usage 埋点。配额/套餐体系（P2）上线后，
响应中的 quota 字段将携带当前套餐的额度与余量，前端展示位已按此预留。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from deps.auth import AuthContext, require_auth
from services import usage_service as svc

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("/me")
async def my_usage(
    days: int = Query(30, ge=7, le=90),
    ctx: AuthContext = Depends(require_auth),
):
    """本人 AI 用量汇总：本月/上月、分场景、近 N 天日序列。"""
    return await svc.my_usage_summary(user_id=ctx.user_id, days=days)
