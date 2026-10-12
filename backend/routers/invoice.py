"""发票导入路由"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from deps.auth import AuthContext, require_auth
from pydantic import BaseModel
from services.invoice_service import parse_invoice_rows, classify_invoice, import_invoices
from services.invoice_llm_prompts import INVOICE_COLUMN_MAPPING_PROMPT
from services.llm import safe_chat_text_json
from services.audit_service import log_action

router = APIRouter(prefix="/api/invoice", tags=["invoice"], dependencies=[Depends(require_auth)])


class ParseRowsRequest(BaseModel):
    rows: List[Dict[str, Any]]


class ImportRequest(BaseModel):
    rows: List[Dict[str, Any]]
    classify: bool = True


class ParseColumnsRequest(BaseModel):
    columns: List[str]


@router.post("/parse")
async def parse_invoice(req: ParseRowsRequest, ctx: AuthContext = Depends(require_auth)):
    """Parse and validate invoice rows."""
    result = await parse_invoice_rows(req.rows, company_id=ctx.company_id)
    return result


@router.post("/import")
async def import_invoice(
    req: ImportRequest,
    ctx: AuthContext = Depends(require_auth),
):
    """Import invoices as accounting entries."""
    result = await import_invoices(ctx.company_id, ctx.user_id, req.rows, classify=req.classify)
    await log_action(ctx.company_id, ctx.user_id, "import", "invoice", None,
                     {"count": result.get("imported"), "total": result.get("total")})
    return result


@router.post("/parse-columns")
async def parse_invoice_columns(req: ParseColumnsRequest, ctx: AuthContext = Depends(require_auth)):
    """LLM column mapping for invoice Excel."""
    prompt = INVOICE_COLUMN_MAPPING_PROMPT.format(columns=", ".join(req.columns))
    fallback = [{"column": c, "mapping": None} for c in req.columns]
    result = safe_chat_text_json(
        "你是一个发票数据导入助手。请返回JSON。",
        prompt,
        fallback=fallback,
        max_retries=1,
        scene="invoice",
        user_id=ctx.user_id,
        company_id=ctx.company_id,
    )
    return {"mappings": result}


class ClassifyEntryRequest(BaseModel):
    business_context: Optional[str] = None


@router.post("/classify-entry/{entry_id}")
async def classify_entry_invoice(
    entry_id: str,
    req: Optional[ClassifyEntryRequest] = None,
    ctx: AuthContext = Depends(require_auth)
):
    """Use AI to suggest classification for an existing invoice entry."""
    from services.entry_service import get_entry
    from services.invoice_service import classify_invoice, get_real_company_name
    
    entry = await get_entry(entry_id, user_id=ctx.user_id, company_id=ctx.company_id)
    if not entry:
        raise HTTPException(status_code=404, detail="记账条目不存在")
        
    # ponytail: pseudo row matching the structure classify_invoice expects
    row = {
        "buyer_name": entry.get("buyer_name") or entry.get("buyerName") or "",
        "seller_name": entry.get("seller_name") or entry.get("sellerName") or entry.get("merchant") or "",
        "item_name": entry.get("item_name") or entry.get("itemName") or entry.get("title") or "",
        "amount": entry.get("amount", 0),
        "total_amount": entry.get("total_amount", entry.get("amount", 0)),
        "tax_amount": entry.get("tax_amount") or entry.get("vatAmount") or 0,
        "tax_rate": entry.get("tax_rate") or entry.get("vatRate") or "",
        "direction": entry.get("direction", "expense"),
        "business_context": req.business_context if req else None,
    }
    
    company_name = await get_real_company_name(ctx.company_id, [row])
    suggestion = await classify_invoice(row, company_name)
    return suggestion
