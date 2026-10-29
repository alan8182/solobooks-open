# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""银行对账路由"""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import json
import csv
import io
import re
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from db.session import get_db_session
from deps.auth import AuthContext, require_auth
from pydantic import BaseModel, Field
from services.bank_service import (
    confirm_suggestion_flow,
    import_statements, import_statements_json, get_statements, auto_match,
    get_difference_report, get_statement_ids, ai_suggest_matches,
    classify_single_statement, _direction_matches_statement,
    build_group_reconciliation_check, ensure_bank_schema, get_reconciliation_health,
    _bank_effect_from_voucher, repair_matched_statement_effect,
    import_statements_core, delete_statements,
    get_wechat_settlement_candidates, identify_statement_fast_rule, safe_money,
    is_salary_payment_description, is_payroll_liability_description,
    find_duplicate_bank_entries, save_company_category_rule,
)
import services.salary_service as salary_service
from services.category_service import is_ambiguity_category
from services.bank_llm_prompts import BANK_COLUMN_MAPPING_PROMPT
from services.llm import safe_chat_text_json
from services.audit_service import log_action
from core.money import ZERO, money
from services.accounting_core_service import (
    get_idempotent_response,
    save_idempotent_response,
    sync_bank_reconciliation,
    assert_bank_reconciliable,
)

router = APIRouter(prefix="/api/bank", tags=["bank"], dependencies=[Depends(require_auth)])
logger = logging.getLogger(__name__)



class ImportRequest(BaseModel):
    csv_content: str
    statement_id: str = ""
    force_duplicates: bool = False


@router.post("/import")
async def import_bank(
    req: ImportRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """导入银行流水 CSV"""
    payload = req.model_dump()
    cached = await get_idempotent_response(
        session, company_id=ctx.company_id, key=idempotency_key,
        operation="import_bank_csv", payload=payload,
    )
    if cached is not None:
        return cached
    await ensure_bank_schema()
    result = await import_statements_core(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        rows=list(csv.DictReader(io.StringIO(req.csv_content))),
        statement_id=req.statement_id,
        request_id=request.headers.get("X-Request-ID") or str(uuid4()),
        force_duplicates=req.force_duplicates,
    )
    await save_idempotent_response(
        session, company_id=ctx.company_id, key=idempotency_key,
        operation="import_bank_csv", payload=payload, status=200, response=result,
    )
    return result


@router.get("/statements")
async def list_statements(statement_id: str = "", ctx: AuthContext = Depends(require_auth)):
    """查看银行流水"""
    sid = statement_id or None
    items = await get_statements(ctx.company_id, sid)
    return {"items": items}


@router.post("/match")
async def match_statements(ctx: AuthContext = Depends(require_auth)):
    """自动匹配银行流水与账务记录"""
    result = await auto_match(ctx.company_id)
    return result


@router.get("/difference")
async def difference_report(month: str = Query(...), ctx: AuthContext = Depends(require_auth)):
    """差异报告：银行有账上没有 / 账上有银行没有"""
    result = await get_difference_report(ctx.company_id, month)
    return result


class RepairEffectRequest(BaseModel):
    statement_id: str
    auto_post: bool = False


@router.post("/repair-effect")
async def repair_effect(req: RepairEffectRequest, ctx: AuthContext = Depends(require_auth),
                        session: AsyncSession = Depends(get_db_session)):
    """修复已匹配流水与 1002 银行存款影响不一致的确定性问题。"""
    try:
        if isinstance(session, AsyncSession):
            from services.bank_settlement_service import confirm_statement_match
            await ensure_bank_schema()
            result = await confirm_statement_match(session, company_id=ctx.company_id,
                user_id=ctx.user_id, statement_id=req.statement_id, entry_ids=None,
                request_id=str(uuid4()), auto_post=bool(req.auto_post))
            return {**result, "status": "repaired" if result["settlementCreated"] else "already_repaired"}
        return await repair_matched_statement_effect(
            ctx.company_id,
            ctx.user_id,
            req.statement_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/statement-ids")
async def list_statement_ids(ctx: AuthContext = Depends(require_auth)):
    """获取所有流水批次 ID"""
    ids = await get_statement_ids(ctx.company_id)
    return {"ids": ids}


class ImportJsonRequest(BaseModel):
    rows: List[Dict[str, Any]]
    statement_id: str = ""
    # 同日+同摘要+同金额可能是真实两笔交易，默认拦截并返回明细，确认后才强制导入
    force_duplicates: bool = False


@router.post("/import-json")
async def import_bank_json(
    req: ImportJsonRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """导入银行流水 JSON（前端 Excel 解析后传入）"""
    payload = req.model_dump()
    cached = await get_idempotent_response(
        session, company_id=ctx.company_id, key=idempotency_key,
        operation="import_bank_json", payload=payload,
    )
    if cached is not None:
        return cached
    await ensure_bank_schema()
    result = await import_statements_core(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        rows=req.rows,
        statement_id=req.statement_id,
        request_id=request.headers.get("X-Request-ID") or str(uuid4()),
        force_duplicates=req.force_duplicates,
    )
    await save_idempotent_response(
        session, company_id=ctx.company_id, key=idempotency_key,
        operation="import_bank_json", payload=payload, status=200, response=result,
    )
    return result


class ParseColumnsRequest(BaseModel):
    columns: List[str]


@router.post("/parse-columns")
async def parse_bank_columns(req: ParseColumnsRequest, ctx: AuthContext = Depends(require_auth)):
    """LLM 列映射：将 Excel 列名映射到系统字段"""
    prompt = BANK_COLUMN_MAPPING_PROMPT.format(columns=", ".join(req.columns))
    fallback = [{"column": c, "mapping": None} for c in req.columns]
    result = await asyncio.to_thread(
        safe_chat_text_json,
        "你是一个银行流水数据导入助手。请返回JSON。",
        prompt,
        fallback=fallback,
        max_retries=1,
        scene="bank_statement",
        user_id=ctx.user_id,
        company_id=ctx.company_id,
    )
    return {"mappings": result}


class AiSuggestRequest(BaseModel):
    month: Optional[str] = None  # 账期 YYYY-MM；不传则按最近的未匹配流水兜底


@router.post("/ai-suggest")
async def ai_suggest(payload: AiSuggestRequest, ctx: AuthContext = Depends(require_auth)):
    """AI 智能识别未匹配银行流水"""
    month = (payload.month or "").strip() or None
    if month:
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise HTTPException(status_code=422, detail="month 格式应为 YYYY-MM")
        try:
            date.fromisoformat(f"{month}-01")
        except ValueError:
            raise HTTPException(status_code=422, detail="month 不是有效账期")
    # 必须带账期：不传时服务层只取全库最近 20 条未匹配流水，历史账期会全部落空
    suggestions = await ai_suggest_matches(ctx.company_id, month=month, user_id=ctx.user_id)
    return {"suggestions": suggestions, "total": len(suggestions)}




@router.get("/reconciliation-health")
async def reconciliation_health(month: str = Query(...), ctx: AuthContext = Depends(require_auth)):
    """只读对账健康检查，用于月结前阻断高风险对账问题。"""
    return await get_reconciliation_health(ctx.company_id, month)

class ConfirmSuggestionRequest(BaseModel):
    statement_id: str
    type: str  # "match" or "new"
    matched_entry_id: str = ""
    matched_entry_ids: List[str] = Field(default_factory=list)
    new_entry: Dict[str, Any] = {}
    auto_post: bool = False
    # 流水所属期间已关账时，用户在前端确认「补记到 X 月」后才带上
    allowBackfill: bool = False
    backfillReason: Optional[str] = None
    # 用户确认"确实是两笔独立付款"时跳过防重复检查
    skipDuplicateCheck: bool = False
    # 记住此规则：后续同对方户名/摘要特征自动归入当前科目
    rememberRule: bool = False


class BatchConfirmRulesRequest(BaseModel):
    statement_ids: Optional[List[str]] = None
    month: Optional[str] = None
    allowBackfill: bool = False
    backfillReason: Optional[str] = None


@router.post("/batch-confirm-rules")
async def batch_confirm_rules(
    req: BatchConfirmRulesRequest,
    ctx: AuthContext = Depends(require_auth),
    request: Request = None,
    session: AsyncSession = Depends(get_db_session),
):
    """批量确认高置信度规则流水（平台提现、结息、实名认证等）"""
    from services.database import get_conn
    from services.entry_service import save_entry

    await ensure_bank_schema()
    conn = await get_conn()
    try:
        sql = (
            "SELECT * FROM bank_statements WHERE company_id = ? "
            "AND (matched_entry_id IS NULL OR matched_entry_id = '') "
        )
        params: list[Any] = [ctx.company_id]
        if req.statement_ids:
            placeholders = ",".join("?" for _ in req.statement_ids)
            sql += f"AND id IN ({placeholders}) "
            params.extend(req.statement_ids)
        elif req.month:
            start = date.fromisoformat(f"{req.month}-01")
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            sql += "AND transaction_date >= ? AND transaction_date < ? "
            params.extend([start.isoformat(), end.isoformat()])
        sql += "ORDER BY transaction_date ASC"

        cursor = await conn.execute(sql, tuple(params))
        statements = [dict(r) for r in await cursor.fetchall()]
    finally:
        await conn.close()

    if not statements:
        return {"status": "ok", "confirmedCount": 0, "confirmedIds": []}

    confirmed_ids = []
    now = datetime.now(timezone.utc).isoformat()

    for stmt in statements:
        fast_rule = identify_statement_fast_rule(stmt)
        if not fast_rule or fast_rule.get("confidence", 0) < 0.95:
            continue

        stmt_id = stmt["id"]
        stmt_amount = safe_money(stmt["amount"])
        entry_id = f"bank-{stmt_id}"
        entry_data = {
            "id": entry_id,
            "title": fast_rule["title"],
            "category": fast_rule["category"],
            "amount": abs(stmt_amount),
            "direction": "income" if stmt_amount > ZERO else "expense",
            "occurDate": str(stmt["transaction_date"] or "")[:10],
            "paymentDate": str(stmt["transaction_date"] or "")[:10],
            "billingStatus": "reconciled",
            "source": "bank_statement_rule",
            "reconciliationType": fast_rule.get("rule", "rule_auto"),
            "linkedStatementId": stmt_id,
            "payer": "company_account",
        }

        try:
            res = await save_entry(
                entry_data,
                user_id=ctx.user_id,
                company_id=ctx.company_id,
                confirm=True,
                allow_backfill=req.allowBackfill,
                backfill_reason=req.backfillReason,
            )
            saved_id = res.get("id", entry_id)

            conn = await get_conn()
            try:
                await conn.execute(
                    "UPDATE bank_statements SET matched_entry_id = ? WHERE id = ? AND company_id = ?",
                    (saved_id, stmt_id, ctx.company_id),
                )
                await conn.execute(
                    "INSERT OR IGNORE INTO bank_statement_matches "
                    "(statement_id, company_id, entry_id, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (stmt_id, ctx.company_id, saved_id, now),
                )
                await conn.commit()
            finally:
                await conn.close()

            if isinstance(session, AsyncSession):
                req_id = (request.headers.get("X-Request-ID") if request else None) or str(uuid4())
                await sync_bank_reconciliation(
                    session,
                    company_id=ctx.company_id,
                    user_id=ctx.user_id,
                    statement_id=stmt_id,
                    entry_ids=[saved_id],
                    transaction_date=stmt["transaction_date"],
                    description=str(stmt["description"] or ""),
                    signed_amount=stmt["amount"],
                    request_id=req_id,
                )
            confirmed_ids.append(stmt_id)
        except Exception:
            logger.exception("batch_confirm_rules failed for statement %s", stmt_id)
            continue

    return {
        "status": "ok",
        "confirmedCount": len(confirmed_ids),
        "confirmedIds": confirmed_ids,
    }


@router.post("/confirm-suggestion")
async def confirm_suggestion(
    req: ConfirmSuggestionRequest,
    ctx: AuthContext = Depends(require_auth),
    request: Request = None,
    session: AsyncSession = Depends(get_db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """确认 AI 建议：匹配已有记录或新建记录"""
    from services.database import get_conn
    from services.entry_service import save_entry

    request_payload = req.model_dump()
    operation = f"confirm_bank_suggestion:{req.statement_id}"
    if isinstance(session, AsyncSession):
        cached = await get_idempotent_response(
            session, company_id=ctx.company_id, key=idempotency_key,
            operation=operation, payload=request_payload,
        )
        if cached is not None:
            return cached

    async def respond(response: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(session, AsyncSession):
            await save_idempotent_response(
                session, company_id=ctx.company_id, key=idempotency_key,
                operation=operation, payload=request_payload, status=200, response=response,
            )
        return response

    async def sync_core(statement: Any, entry_ids: List[str]) -> None:
        # Direct unit calls retain their isolated legacy fixture. HTTP requests
        # always receive the authoritative SQLAlchemy session.
        if not isinstance(session, AsyncSession):
            return
        await sync_bank_reconciliation(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user_id,
            statement_id=req.statement_id,
            entry_ids=entry_ids,
            transaction_date=statement["transaction_date"],
            description=str(statement["description"] or ""),
            signed_amount=statement["amount"],
            request_id=(request.headers.get("X-Request-ID") if request else None) or str(uuid4()),
        )

    return await confirm_suggestion_flow(
        req=req, session=session, company_id=ctx.company_id, user_id=ctx.user_id,
        request=request, respond=respond, sync_core=sync_core,
    )


class ClassifyStatementRequest(BaseModel):
    statement_id: str
    business_context: str = ""


@router.post("/classify-statement")
async def classify_statement(req: ClassifyStatementRequest, ctx: AuthContext = Depends(require_auth)):
    """实时对单笔银行流水进行 AI 分类建议"""
    res = await classify_single_statement(ctx.company_id, req.statement_id, req.business_context, user_id=ctx.user_id)
    if not res:
        raise HTTPException(status_code=404, detail="流水不存在")
    return {"suggestedEntry": res}


@router.delete("/statements/{id}")
async def delete_statement(id: str, ctx: AuthContext = Depends(require_auth)):
    """删除单笔银行流水"""
    await ensure_bank_schema()
    deleted = await delete_statements(ctx.company_id, [id], user_id=ctx.user_id)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="流水不存在")
    return {"status": "success", "message": "流水删除成功"}


class BatchDeleteRequest(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=1000)


@router.post("/statements/batch-delete")
async def batch_delete_statements(req: BatchDeleteRequest, ctx: AuthContext = Depends(require_auth)):
    """批量删除银行流水，并同步解除匹配关系、清理来源事实"""
    await ensure_bank_schema()
    deleted = await delete_statements(ctx.company_id, req.ids, user_id=ctx.user_id)
    return {"status": "success", "deleted": deleted, "message": f"已删除 {deleted} 条银行流水"}
