# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from fastapi import APIRouter, Depends

from deps.auth import AuthContext, require_auth
from models.schemas import TodoStateUpdate
from services.todo_service import list_todos, update_todo_state

router = APIRouter(prefix="/api/todos", tags=["todos"], dependencies=[Depends(require_auth)])


@router.get("")
@router.get("/")
async def get_todos(ctx: AuthContext = Depends(require_auth)):
    """Return current server-derived todos with persisted handling state."""
    return {"todos": await list_todos(ctx.user_id, ctx.company_id)}


@router.patch("/{todo_key:path}")
async def patch_todo(todo_key: str, req: TodoStateUpdate, ctx: AuthContext = Depends(require_auth)):
    """Persist done/dismissed state for a stable todo key."""
    state = await update_todo_state(
        todo_key,
        user_id=ctx.user_id,
        company_id=ctx.company_id,
        done=req.done,
        dismissed=req.dismissed,
    )
    return {"todo": state}
