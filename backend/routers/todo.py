# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from deps.auth import AuthContext, require_auth
from models.schemas import TodoStateUpdate
from services.todo_service import (
    list_todos,
    list_todos_bundle,
    update_todo_state,
    advise_todos_with_llm,
    link_invoice_to_expense,
)

router = APIRouter(prefix="/api/todos", tags=["todos"], dependencies=[Depends(require_auth)])


class LinkInvoiceExpenseRequest(BaseModel):
    invoiceEntryId: str
    expenseEntryId: str


@router.get("")
@router.get("/")
async def get_todos(ctx: AuthContext = Depends(require_auth)):
    """Return current server-derived todos with persisted handling state and auto-resolved items."""
    return await list_todos_bundle(ctx.user_id, ctx.company_id)


@router.post("/ai-advise")
async def get_todo_ai_advice(ctx: AuthContext = Depends(require_auth)):
    """Enrich pending todos with deep LLM advice and structured recommendations."""
    todos = await advise_todos_with_llm(ctx.user_id, ctx.company_id)
    return {"todos": todos}


@router.post("/link-invoice")
async def post_link_invoice_expense(
    req: LinkInvoiceExpenseRequest,
    ctx: AuthContext = Depends(require_auth),
):
    """Link an imported invoice entry to an existing expense entry and eliminate duplicate booking."""
    result = await link_invoice_to_expense(
        invoice_entry_id=req.invoiceEntryId,
        expense_entry_id=req.expenseEntryId,
        user_id=ctx.user_id,
        company_id=ctx.company_id,
    )
    return {"success": True, "result": result}


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
