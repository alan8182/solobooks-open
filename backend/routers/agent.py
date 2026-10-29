# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Agent 路由：SSE 对话 + 待确认动作审批。"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from deps.auth import AuthContext, require_auth
from agent import actions as agent_actions
from agent.loop import LoopContext, run_agent_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])


class AgentChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class AgentChatRequest(BaseModel):
    messages: list[AgentChatMessage]
    conversationId: Optional[str] = None
    enableThinking: Optional[bool] = False
    workspaceView: Optional[str] = None


class AgentActionRejectRequest(BaseModel):
    reason: Optional[str] = None


class AgentActionApproveRequest(BaseModel):
    """批量清单可传：排除的行索引 + 用户修正的字段（按行索引）。

    allowBackfill / backfillReason：单据所属期间已关账时，前端确认补记期间后带上；
    不带则后端维持 PERIOD_CLOSED，不会静默把凭证换到别的月份。
    stepUpToken：期初余额导入等高敏操作二次验证令牌。
    """
    excludeIndexes: Optional[List[int]] = None
    edits: Optional[Dict[str, Dict[str, Any]]] = None
    allowBackfill: bool = False
    backfillReason: Optional[str] = None
    stepUpToken: Optional[str] = None


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


@router.post("/chat")
async def agent_chat(req: AgentChatRequest, ctx: AuthContext = Depends(require_auth)):
    """Agent 对话（SSE 流式）。工具调用过程与待确认动作以事件形式推送。"""
    if not req.messages:
        raise HTTPException(status_code=422, detail="消息不能为空")
    if req.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="最后一条消息必须是用户消息")

    loop_ctx = LoopContext(
        user_id=ctx.user_id,
        company_id=ctx.company_id,
        conversation_id=req.conversationId or uuid.uuid4().hex[:12],
        workspace_view=req.workspaceView,
    )

    async def event_stream():
        try:
            async for event in run_agent_stream(
                loop_ctx,
                [m.model_dump() for m in req.messages],
                enable_thinking=bool(req.enableThinking),
            ):
                yield _sse(event)
        except Exception:
            logger.exception("agent chat stream failed")
            yield _sse({"type": "error", "message": "Agent 处理失败，请稍后重试。"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/actions")
async def list_actions(status: str = "pending", ctx: AuthContext = Depends(require_auth)):
    return {"actions": await agent_actions.list_actions(ctx.company_id, status=status)}


@router.get("/actions/{action_id}")
async def get_action(action_id: str, ctx: AuthContext = Depends(require_auth)):
    return await agent_actions.get_action(action_id, company_id=ctx.company_id)


@router.post("/actions/{action_id}/approve")
async def approve_action(
    action_id: str,
    req: Optional[AgentActionApproveRequest] = None,
    ctx: AuthContext = Depends(require_auth),
):
    return await agent_actions.approve_action(
        action_id,
        user_id=ctx.user_id,
        company_id=ctx.company_id,
        exclude_indexes=req.excludeIndexes if req else None,
        edits=req.edits if req else None,
        allow_backfill=bool(req.allowBackfill) if req else False,
        backfill_reason=req.backfillReason if req else None,
        step_up_token=req.stepUpToken if req else None,
    )


@router.post("/actions/{action_id}/reject")
async def reject_action(
    action_id: str,
    req: Optional[AgentActionRejectRequest] = None,
    ctx: AuthContext = Depends(require_auth),
):
    reason = req.reason if req else None
    return await agent_actions.reject_action(
        action_id, user_id=ctx.user_id, company_id=ctx.company_id, reason=reason
    )
