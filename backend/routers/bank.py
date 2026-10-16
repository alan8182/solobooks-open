# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""银行对账路由"""
from datetime import datetime, timezone
from typing import Any, Dict, List
import json
import csv
import io
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from db.session import get_db_session
from deps.auth import AuthContext, require_auth
from pydantic import BaseModel, Field
from services.bank_service import (
    import_statements, import_statements_json, get_statements, auto_match,
    get_difference_report, get_statement_ids, ai_suggest_matches,
    classify_single_statement, _direction_matches_statement,
    build_group_reconciliation_check, ensure_bank_schema, get_reconciliation_health,
    _bank_effect_from_voucher, repair_matched_statement_effect,
    import_statements_core,
    get_wechat_settlement_candidates,
)
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


class ImportRequest(BaseModel):
    csv_content: str
    statement_id: str = ""


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
    result = safe_chat_text_json(
        "你是一个银行流水数据导入助手。请返回JSON。",
        prompt,
        fallback=fallback,
        max_retries=1,
        scene="bank_statement",
        user_id=ctx.user_id,
        company_id=ctx.company_id,
    )
    return {"mappings": result}


@router.post("/ai-suggest")
async def ai_suggest(ctx: AuthContext = Depends(require_auth)):
    """AI 智能识别未匹配银行流水"""
    suggestions = await ai_suggest_matches(ctx.company_id)
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

    if req.type == "match":
        await ensure_bank_schema()
        requested_raw = req.matched_entry_ids if req.matched_entry_ids else [req.matched_entry_id]
        requested_ids: List[str] = []
        for raw_id in requested_raw:
            entry_id = str(raw_id or "").strip()
            if entry_id and entry_id not in requested_ids:
                requested_ids.append(entry_id)
        if len([entry_id for entry_id in requested_ids
                if entry_id != f"bank-settlement-{req.statement_id}"]) > 500:
            raise HTTPException(status_code=400, detail="一次最多合并 500 笔账目记录")

        if isinstance(session, AsyncSession):
            from services.accounting_core_service import get_journal, serialize_journal
            from services.bank_settlement_service import confirm_statement_match
            personal = False
            for entry_id in requested_ids:
                entry = await get_journal(session, entry_id, company_id=ctx.company_id)
                payload = await serialize_journal(session, entry)
                payer = str(payload.get("payer") or "")
                if (payer in {"boss_personal", "employee_personal"} or payer.startswith("employee_personal:")) and \
                        _bank_effect_from_voucher(payload.get("voucherLines") or {}) == ZERO:
                    personal = True
            if not personal:
                return await respond(await confirm_statement_match(session,
                    company_id=ctx.company_id, user_id=ctx.user_id, statement_id=req.statement_id,
                    entry_ids=requested_ids,
                    request_id=(request.headers.get("X-Request-ID") if request else None) or str(uuid4()),
                    auto_post=bool(req.auto_post)))

        conn = await get_conn()
        try:
            cursor = await conn.execute(
                "SELECT id, amount, matched_entry_id, transaction_date, description "
                "FROM bank_statements WHERE id = ? AND company_id = ?",
                (req.statement_id, ctx.company_id),
            )
            stmt = await cursor.fetchone()
            if not stmt:
                raise HTTPException(status_code=404, detail="流水不存在")

            cursor = await conn.execute(
                "SELECT entry_id, previous_billing_status, previous_payment_date FROM bank_statement_matches "
                "WHERE statement_id = ? AND company_id = ? ORDER BY created_at ASC, entry_id ASC",
                (req.statement_id, ctx.company_id),
            )
            existing_rows = [dict(row) for row in await cursor.fetchall()]
            existing_ids = [str(row["entry_id"]) for row in existing_rows]
            legacy_id = str(stmt["matched_entry_id"] or "").strip()
            if legacy_id and legacy_id not in existing_ids:
                existing_ids.insert(0, legacy_id)

            if not requested_ids:
                previous_statuses = {
                    str(row["entry_id"]): row.get("previous_billing_status")
                    for row in existing_rows
                }
                previous_payment_dates = {
                    str(row["entry_id"]): row.get("previous_payment_date")
                    for row in existing_rows
                }
                await conn.execute(
                    "DELETE FROM bank_statement_matches WHERE statement_id = ? AND company_id = ?",
                    (req.statement_id, ctx.company_id),
                )
                await conn.execute(
                    "UPDATE bank_statements SET matched_entry_id = NULL WHERE id = ? AND company_id = ?",
                    (req.statement_id, ctx.company_id),
                )

                for entry_id in existing_ids:
                    cursor = await conn.execute(
                        "SELECT 1 FROM bank_statement_matches "
                        "WHERE company_id = ? AND entry_id = ? AND statement_id != ? LIMIT 1",
                        (ctx.company_id, entry_id, req.statement_id),
                    )
                    still_matched = await cursor.fetchone()
                    if not still_matched:
                        cursor = await conn.execute(
                            "SELECT 1 FROM bank_statements "
                            "WHERE company_id = ? AND matched_entry_id = ? AND id != ? LIMIT 1",
                            (ctx.company_id, entry_id, req.statement_id),
                        )
                        still_matched = await cursor.fetchone()
                    if still_matched:
                        continue

                    cursor = await conn.execute(
                        "SELECT payload FROM entries WHERE id = ? AND company_id = ?",
                        (entry_id, ctx.company_id),
                    )
                    entry_row = await cursor.fetchone()
                    if not entry_row:
                        continue
                    payload = json.loads(entry_row["payload"]) if isinstance(entry_row["payload"], str) else entry_row["payload"]
                    if (
                        isinstance(payload, dict)
                        and payload.get("source") == "bank_reconciliation_repair"
                        and payload.get("linkedStatementId") == req.statement_id
                    ):
                        await conn.execute(
                            "DELETE FROM entries WHERE id = ? AND company_id = ?",
                            (entry_id, ctx.company_id),
                        )
                        continue
                    previous_status = previous_statuses.get(entry_id)
                    current_status = payload.get("billingStatus") if isinstance(payload, dict) else None
                    if previous_status is None and current_status != "reconciled":
                        previous_status = current_status
                    if previous_status is None and isinstance(payload, dict):
                        if payload.get("source") == "invoice_import" or payload.get("invoiceNumber"):
                            previous_status = "has_invoice"
                    if previous_status:
                        await conn.execute(
                            "UPDATE entries SET payload = json_set(payload, '$.billingStatus', ?) "
                            "WHERE id = ? AND company_id = ?",
                            (previous_status, entry_id, ctx.company_id),
                        )
                    else:
                        await conn.execute(
                            "UPDATE entries SET payload = json_remove(payload, '$.billingStatus') "
                            "WHERE id = ? AND company_id = ?",
                            (entry_id, ctx.company_id),
                        )
                    previous_payment_date = previous_payment_dates.get(entry_id)
                    if previous_payment_date:
                        await conn.execute(
                            "UPDATE entries SET payload = json_set(payload, '$.paymentDate', ?) "
                            "WHERE id = ? AND company_id = ?",
                            (previous_payment_date, entry_id, ctx.company_id),
                        )
                    else:
                        await conn.execute(
                            "UPDATE entries SET payload = json_remove(payload, '$.paymentDate') "
                            "WHERE id = ? AND company_id = ?",
                            (entry_id, ctx.company_id),
                        )
                await conn.commit()
                await sync_core(stmt, [])
                return await respond({"status": "unmatched", "entryId": None, "entryIds": existing_ids})

            if existing_ids and set(existing_ids) != set(requested_ids):
                raise HTTPException(status_code=409, detail="该流水已匹配其他账目记录")

            placeholders = ",".join("?" for _ in requested_ids)
            cursor = await conn.execute(
                f"SELECT id, payload FROM entries WHERE company_id = ? AND id IN ({placeholders})",
                (ctx.company_id, *requested_ids),
            )
            entry_rows = await cursor.fetchall()
            payloads_by_id: Dict[str, Dict[str, Any]] = {}
            for entry_row in entry_rows:
                payload = json.loads(entry_row["payload"]) if isinstance(entry_row["payload"], str) else entry_row["payload"]
                if isinstance(payload, dict):
                    payloads_by_id[str(entry_row["id"])] = payload
            missing_ids = [entry_id for entry_id in requested_ids if entry_id not in payloads_by_id]
            if missing_ids:
                raise HTTPException(status_code=404, detail={"message": "账目记录不存在", "entryIds": missing_ids})
            payloads = [payloads_by_id[entry_id] for entry_id in requested_ids]

            cursor = await conn.execute(
                f"SELECT entry_id FROM bank_statement_matches "
                f"WHERE company_id = ? AND statement_id != ? AND entry_id IN ({placeholders}) LIMIT 1",
                (ctx.company_id, req.statement_id, *requested_ids),
            )
            conflict = await cursor.fetchone()
            if not conflict:
                cursor = await conn.execute(
                    f"SELECT matched_entry_id AS entry_id FROM bank_statements "
                    f"WHERE company_id = ? AND id != ? AND matched_entry_id IN ({placeholders}) LIMIT 1",
                    (ctx.company_id, req.statement_id, *requested_ids),
                )
                conflict = await cursor.fetchone()
            if conflict:
                raise HTTPException(status_code=409, detail="其中一笔账目已匹配其他银行流水")

            advancement_payloads = []
            for payload in payloads:
                payer = str(payload.get("payer") or "")
                voucher = payload.get("voucherLines") or {}
                bank_effect = _bank_effect_from_voucher(voucher) if voucher else ZERO
                is_personal_payer = payer in ("boss_personal", "employee_personal") or payer.startswith("employee_personal:")
                if is_personal_payer and abs(bank_effect) < money("0.01"):
                    advancement_payloads.append(payload)
            if advancement_payloads and len(payloads) > 1:
                raise HTTPException(status_code=400, detail="垫付报销暂不支持与其他条目合并对账")

            stmt_amount = money(stmt["amount"] or ZERO)
            if advancement_payloads:
                payload = advancement_payloads[0]
                entry_id = requested_ids[0]
                payer = str(payload.get("payer") or "")
                if stmt_amount >= 0:
                    raise HTTPException(status_code=400, detail="垫付报销流水必须为付款流出")
                if abs(abs(stmt_amount) - money(payload.get("amount") or ZERO)) > money("0.05"):
                    raise HTTPException(status_code=400, detail="金额不一致，请核对垫付金额与打款金额")

                reim_entry_id = f"reim-{req.statement_id}-{entry_id}"
                reim_category = "股东垫付报销" if payer == "boss_personal" else "员工垫付报销"
                reim_data = {
                    "id": reim_entry_id,
                    "title": f"报销垫付款-{payload.get('title', '')}",
                    "amount": abs(stmt_amount),
                    "direction": "owner_transfer",
                    "category": reim_category,
                    "payer": payer,
                    "billingStatus": "reconciled",
                    "occurDate": str(stmt["transaction_date"][:10]),
                    "paymentDate": str(stmt["transaction_date"][:10]),
                    "source": "bank_import",
                }
                await save_entry(reim_data, user_id=ctx.user_id, company_id=ctx.company_id)
                if isinstance(session, AsyncSession):
                    await assert_bank_reconciliable(session, company_id=ctx.company_id, entry_ids=[reim_entry_id])
                now = datetime.now(timezone.utc).isoformat()
                await conn.execute(
                    "INSERT OR IGNORE INTO bank_statement_matches "
                    "(statement_id, company_id, entry_id, previous_billing_status, previous_payment_date, created_at) "
                    "VALUES (?, ?, ?, NULL, NULL, ?)",
                    (req.statement_id, ctx.company_id, reim_entry_id, now),
                )
                await conn.execute(
                    "UPDATE bank_statements SET matched_entry_id = ? WHERE id = ? AND company_id = ?",
                    (reim_entry_id, req.statement_id, ctx.company_id),
                )
                await conn.execute(
                    "UPDATE entries SET payload = json_set(payload, '$.billingStatus', 'reconciled') "
                    "WHERE id = ? AND company_id = ?",
                    (entry_id, ctx.company_id),
                )
                await conn.commit()
                await sync_core(stmt, [reim_entry_id])
                return await respond({"status": "matched", "entryId": reim_entry_id})

            check = build_group_reconciliation_check(stmt_amount, payloads)
            if not check["directionMatches"]:
                raise HTTPException(status_code=400, detail={
                    "message": "所选账目合计方向与银行流水方向不一致",
                    **check,
                })
            if not check["amountMatches"]:
                raise HTTPException(status_code=400, detail={
                    "message": "所选账目合计与银行流水金额不一致",
                    **check,
                })

            if existing_ids and set(existing_ids) == set(requested_ids):
                if isinstance(session, AsyncSession):
                    await assert_bank_reconciliable(session, company_id=ctx.company_id, entry_ids=requested_ids)
                await conn.execute(
                    f"UPDATE entries SET payload = json_set(payload, '$.billingStatus', 'reconciled') "
                    f"WHERE company_id = ? AND id IN ({placeholders})",
                    (ctx.company_id, *requested_ids),
                )
                await conn.commit()
                await sync_core(stmt, requested_ids)
                result: Dict[str, Any] = {"status": "already_matched", "entryId": requested_ids[0]}
                if len(requested_ids) > 1:
                    result["entryIds"] = requested_ids
                return await respond(result)

            if isinstance(session, AsyncSession):
                await assert_bank_reconciliable(session, company_id=ctx.company_id, entry_ids=requested_ids)
            now = datetime.now(timezone.utc).isoformat()
            for entry_id, payload in zip(requested_ids, payloads):
                await conn.execute(
                    "INSERT INTO bank_statement_matches "
                    "(statement_id, company_id, entry_id, previous_billing_status, previous_payment_date, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        req.statement_id,
                        ctx.company_id,
                        entry_id,
                        payload.get("billingStatus"),
                        payload.get("paymentDate"),
                        now,
                    ),
                )
            await conn.execute(
                "UPDATE bank_statements SET matched_entry_id = ? WHERE id = ? AND company_id = ?",
                (requested_ids[0], req.statement_id, ctx.company_id),
            )
            await conn.execute(
                f"UPDATE entries SET payload = json_set(payload, '$.billingStatus', 'reconciled', '$.paymentDate', ?) "
                f"WHERE company_id = ? AND id IN ({placeholders})",
                (str(stmt["transaction_date"][:10]), ctx.company_id, *requested_ids),
            )
            await conn.commit()
        finally:
            await conn.close()

        result = {"status": "matched", "entryId": requested_ids[0]}
        await sync_core(stmt, requested_ids)
        if len(requested_ids) > 1:
            result["entryIds"] = requested_ids
        return await respond(result)

    elif req.type == "new" and req.new_entry:
        entry_data = req.new_entry.copy()
        entry_data.pop("voucherLines", None)
        entry_data.pop("voucherHint", None)
        if entry_data.get("category") == "研发支出-费用化支出" and not (
            entry_data.get("rdProjectId") and entry_data.get("rdExpenseType")
        ):
            raise HTTPException(status_code=422, detail="研发费用必须选择研发项目和费用类别")
        conn = await get_conn()
        try:
            cursor = await conn.execute(
                "SELECT amount, matched_entry_id, transaction_date, description FROM bank_statements WHERE id = ? AND company_id = ?",
                (req.statement_id, ctx.company_id),
            )
            stmt = await cursor.fetchone()
            if not stmt:
                raise HTTPException(status_code=404, detail="流水不存在")

            existing_match = str(stmt["matched_entry_id"] or "").strip()
            if existing_match:
                if isinstance(session, AsyncSession):
                    await assert_bank_reconciliable(session, company_id=ctx.company_id, entry_ids=[existing_match])
                await conn.execute(
                    "UPDATE entries SET payload = json_set(payload, '$.billingStatus', 'reconciled') WHERE id = ? AND company_id = ?",
                    (existing_match, ctx.company_id),
                )
                await conn.commit()
                await sync_core(stmt, [existing_match])
                return await respond({"status": "already_matched", "entryId": existing_match})
        finally:
            await conn.close()

        settlement_candidates = await get_wechat_settlement_candidates(ctx.company_id, dict(stmt))
        if settlement_candidates:
            raise HTTPException(status_code=409, detail={
                "code": "EXISTING_WECHAT_ENTRIES",
                "message": "该微信结算已有收入及手续费账目，请合并核销原账目，不能再次记为收入或支出",
                "candidateEntryGroups": [[entry["id"] for entry in group] for group in settlement_candidates],
            })

        stmt_amount = money(stmt["amount"] or ZERO)
        direction = str(entry_data.get("direction") or "")
        if not _direction_matches_statement(stmt_amount, direction):
            if stmt_amount > 0:
                raise HTTPException(status_code=400, detail="收款流水只能记为收入或股东投入/往来")
            if stmt_amount < 0:
                raise HTTPException(status_code=400, detail="付款流水只能记为支出或资本/往来")

        entry_id = f"bank-{req.statement_id}"
        entry_data["id"] = entry_id
        entry_data["source"] = "bank_import"
        entry_data["payer"] = entry_data.get("payer", "company_account")
        entry_data["billingStatus"] = "reconciled"
        entry_data["paymentDate"] = str(stmt["transaction_date"][:10])
        if "amount" in entry_data:
            raw_amount = money(entry_data["amount"])
            # income/expense 用方向表意，取绝对值；owner_transfer 保留原始符号表达现金流向
            if entry_data.get("direction") in ("income", "expense"):
                entry_data["amount"] = abs(raw_amount)
            else:
                entry_data["amount"] = raw_amount

        await save_entry(entry_data, user_id=ctx.user_id, company_id=ctx.company_id)
        if isinstance(session, AsyncSession):
            await assert_bank_reconciliable(session, company_id=ctx.company_id, entry_ids=[entry_id])

        # Link statement to new entry
        conn = await get_conn()
        try:
            await conn.execute(
                "INSERT OR IGNORE INTO bank_statement_matches "
                "(statement_id, company_id, entry_id, previous_billing_status, previous_payment_date, created_at) "
                "VALUES (?, ?, ?, NULL, NULL, ?)",
                (req.statement_id, ctx.company_id, entry_id, datetime.now(timezone.utc).isoformat()),
            )
            await conn.execute(
                "UPDATE bank_statements SET matched_entry_id = ? WHERE id = ? AND company_id = ?",
                (entry_id, req.statement_id, ctx.company_id),
            )
            await conn.commit()
        finally:
            await conn.close()

        await log_action(ctx.company_id, ctx.user_id, "create", "entry", entry_id,
                         {"source": "bank_ai_suggest", "title": entry_data.get("title", "")})
        await sync_core(stmt, [entry_id])
        return await respond({"status": "created", "entryId": entry_id})

    return await respond({"status": "error", "message": "无效的请求"})


class ClassifyStatementRequest(BaseModel):
    statement_id: str
    business_context: str = ""


@router.post("/classify-statement")
async def classify_statement(req: ClassifyStatementRequest, ctx: AuthContext = Depends(require_auth)):
    """实时对单笔银行流水进行 AI 分类建议"""
    res = await classify_single_statement(ctx.company_id, req.statement_id, req.business_context)
    if not res:
        raise HTTPException(status_code=404, detail="流水不存在")
    return {"suggestedEntry": res}


@router.delete("/statements/{id}")
async def delete_statement(id: str, ctx: AuthContext = Depends(require_auth)):
    """删除单笔银行流水"""
    from services.database import get_conn
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT 1 FROM bank_statements WHERE id = ? AND company_id = ?",
            (id, ctx.company_id)
        )
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="流水不存在")
            
        await conn.execute(
            "DELETE FROM bank_statements WHERE id = ? AND company_id = ?",
            (id, ctx.company_id)
        )
        await conn.commit()
    finally:
        await conn.close()
    return {"status": "success", "message": "流水删除成功"}
