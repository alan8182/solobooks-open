"""ActionQueue：agent 写操作一律落成"待确认动作"，人确认后才走现有写路径。

approve 路径复用 record/confirm 的确定性管线（compute_vat → get_voucher_hint →
create_entry(confirm=True)），保证 agent 发起的账与手工记账走完全相同的校验与过账逻辑。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import select

from agent.prompts import ACTION_EXPIRE_HOURS
from db.models import AgentAction, new_id
from db.session import session_scope
from services.account_mapper import get_voucher_hint
from services.audit_service import log_action
from services.tax_rules import compute_vat, get_risk_tags, is_tax_deductible

logger = logging.getLogger(__name__)

# propose_voucher 允许进入确定性管线的业务字段白名单（与 ConfirmRequest 对齐）
_VOUCHER_PAYLOAD_KEYS = (
    "direction", "amount", "title", "category", "accountCode", "accountName",
    "billingStatus", "invoiceType", "invoiceNumber", "buyerTaxId", "sellerTaxId",
    "merchant", "occurDate", "paymentDate", "sourceType", "dedupFingerprint", "payer",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def serialize_action(row: AgentAction) -> Dict[str, Any]:
    return {
        "id": row.id,
        "actionType": row.action_type,
        "title": row.title,
        "payload": row.payload,
        "evidence": row.evidence,
        "result": row.result,
        "status": row.status,
        "conversationId": row.conversation_id,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "resolvedAt": row.resolved_at.isoformat() if row.resolved_at else None,
    }


def _batch_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """批量动作的草稿列表；单笔动作统一包装成单元素列表处理。"""
    items = payload.get("items")
    if isinstance(items, list) and items:
        return [it for it in items if isinstance(it, dict)]
    return [payload]


async def create_action(
    *,
    company_id: str,
    user_id: str,
    action_type: str,
    title: str,
    payload: Dict[str, Any],
    evidence: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    if action_type not in ("propose_voucher", "propose_vouchers", "propose_match", "propose_accrual"):
        raise ValueError(f"unsupported action_type: {action_type}")
    if action_type == "propose_vouchers" and not _batch_items(payload):
        raise ValueError("propose_vouchers payload items is empty")
    if action_type == "propose_match" and not (payload.get("statementId") and payload.get("entryIds")):
        raise ValueError("propose_match 需要 statementId 与 entryIds")
    if action_type == "propose_accrual" and payload.get("kind") not in ("depreciation", "salary"):
        raise ValueError("propose_accrual.kind 必须是 depreciation 或 salary")
    async with session_scope() as session:
        row = AgentAction(
            id=new_id(),
            company_id=company_id,
            user_id=user_id,
            conversation_id=conversation_id,
            action_type=action_type,
            title=(title or "")[:255],
            payload=payload,
            evidence=evidence or {},
            status="pending",
        )
        session.add(row)
        await session.flush()
        return serialize_action(row)


async def list_actions(
    company_id: str, status: Optional[str] = None, limit: int = 50
) -> list[Dict[str, Any]]:
    async with session_scope() as session:
        stmt = select(AgentAction).where(AgentAction.company_id == company_id)
        if status:
            stmt = stmt.where(AgentAction.status == status)
        stmt = stmt.order_by(AgentAction.created_at.desc()).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
        return [serialize_action(r) for r in rows]


async def _expire_stale(row: AgentAction) -> bool:
    if row.status == "pending" and row.created_at:
        created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - created > timedelta(hours=ACTION_EXPIRE_HOURS):
            row.status = "expired"
            row.resolved_at = _now()
            return True
    return False


async def get_action(action_id: str, *, company_id: str) -> Dict[str, Any]:
    async with session_scope() as session:
        row = (
            await session.execute(
                select(AgentAction).where(
                    AgentAction.id == action_id, AgentAction.company_id == company_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="动作不存在")
        await _expire_stale(row)
        return serialize_action(row)


async def approve_action(
    action_id: str,
    *,
    user_id: str,
    company_id: str,
    exclude_indexes: Optional[List[int]] = None,
    edits: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """人工确认动作。

    - exclude_indexes：批量清单中用户取消勾选的行（不入账，记录为 excluded）；
    - edits：用户在卡片上修正的字段（按行索引），修正后的分类会作为更高权重
      的信号写回公司画像（agent 越用越准的来源）。
    """
    exclude_set = {int(i) for i in (exclude_indexes or [])}
    edit_map = {int(k): dict(v) for k, v in (edits or {}).items()}
    profile_updates: List[Dict[str, str]] = []

    async with session_scope() as session:
        row = (
            await session.execute(
                select(AgentAction).where(
                    AgentAction.id == action_id, AgentAction.company_id == company_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="动作不存在")
        if await _expire_stale(row):
            raise HTTPException(status_code=409, detail="动作已过期，请让 AI 重新发起")
        if row.status != "pending":
            raise HTTPException(status_code=409, detail=f"动作已处理（{row.status}）")

        if row.action_type in ("propose_voucher", "propose_vouchers"):
            result = await _approve_voucher_items(
                session,
                row,
                _batch_items(row.payload),
                user_id=user_id,
                company_id=company_id,
                exclude_set=exclude_set,
                edit_map=edit_map,
                on_profile_update=profile_updates.append,
            )
        elif row.action_type == "propose_match":
            from services.bank_service import apply_statement_match

            payload = row.payload or {}
            try:
                match_result = await apply_statement_match(
                    company_id,
                    user_id,
                    str(payload.get("statementId")),
                    list(payload.get("entryIds") or []),
                    session=session,
                    request_id=f"agent-action-{row.id}",
                )
                result = {"match": match_result}
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        elif row.action_type == "propose_accrual":
            payload = row.payload or {}
            kind = str(payload.get("kind"))
            month = str(payload.get("month") or "")
            if not month:
                raise HTTPException(status_code=422, detail="缺少计提月份")
            if kind == "depreciation":
                from services.depreciation_engine import run_depreciation

                summary = await run_depreciation(company_id, month, user_id=user_id)
            else:
                from services.salary_service import accrue_salary_sheet

                summary = await accrue_salary_sheet(company_id, user_id, month)
            from services.accounting_core_service import _json_safe

            result = {"accrual": {"kind": kind, "month": month, "summary": _json_safe(summary)}}
        else:  # pragma: no cover — create_action 已限制类型
            raise HTTPException(status_code=422, detail=f"不支持的动作类型：{row.action_type}")

        row.status = "approved"
        row.resolved_at = _now()
        row.resolved_by = user_id
        row.result = result
        action_snapshot = {
            "action_type": row.action_type,
            "title": row.title,
            "payload": row.payload,
            "evidence": row.evidence,
            "exclude_indexes": sorted(exclude_set),
            "edits": {str(k): v for k, v in edit_map.items()},
        }

    # 事务已提交，现在安全地回写公司画像（agent 越用越准的来源；失败不阻塞确认结果）
    for update in profile_updates:
        try:
            from agent.memory import record_category_correction

            await record_category_correction(
                company_id, update["merchant"], update["proposed"], update["final"]
            )
        except Exception:
            logger.exception("profile write-back failed for %s", update["merchant"])

    await log_action(
        company_id, user_id, "agent_action_approve", "agent_action", action_id, action_snapshot
    )
    return {"id": action_id, "status": "approved", "result": result}


async def reject_action(
    action_id: str, *, user_id: str, company_id: str, reason: Optional[str] = None
) -> Dict[str, Any]:
    async with session_scope() as session:
        row = (
            await session.execute(
                select(AgentAction).where(
                    AgentAction.id == action_id, AgentAction.company_id == company_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="动作不存在")
        if row.status != "pending":
            raise HTTPException(status_code=409, detail=f"动作已处理（{row.status}）")
        row.status = "rejected"
        row.resolved_at = _now()
        row.resolved_by = user_id
        row.result = {"reason": reason or ""}
        action_snapshot = {"action_type": row.action_type, "title": row.title, "reason": reason or ""}

    await log_action(
        company_id, user_id, "agent_action_reject", "agent_action", action_id, action_snapshot
    )
    return {"id": action_id, "status": "rejected"}


async def _approve_voucher_items(
    session,
    row: AgentAction,
    items: List[Dict[str, Any]],
    *,
    user_id: str,
    company_id: str,
    exclude_set: set,
    edit_map: Dict[int, Dict[str, Any]],
    on_profile_update,
) -> Dict[str, Any]:
    """凭证类动作（单笔/批量）的逐行确定性入账；返回 result.items。"""
    booked_results: List[Dict[str, Any]] = []
    for index, raw in enumerate(items):
        if index in exclude_set:
            booked_results.append({"index": index, "status": "excluded"})
            continue
        edited = dict(edit_map.get(index) or {})
        try:
            merged = {**raw, **{k: v for k, v in edited.items() if v not in (None, "")}}
            booking = await _book_voucher_payload(
                session,
                merged,
                user_id=user_id,
                company_id=company_id,
                request_id=f"agent-action-{row.id}-{index}",
            )
            booking.update({"index": index, "status": "booked"})
            booked_results.append(booking)
            # 画像回写延迟到事务提交之后（见 approve_action 末尾），避免事务内嵌套写库
            merchant = str(merged.get("merchant") or merged.get("title") or "").strip()
            final_category = str(merged.get("category") or "").strip()
            proposed_category = str(raw.get("category") or "").strip()
            if merchant and final_category:
                on_profile_update(
                    {"merchant": merchant, "proposed": proposed_category, "final": final_category}
                )
        except HTTPException as exc:
            booked_results.append({"index": index, "status": "failed", "error": exc.detail})
        except Exception as exc:  # 单行失败不拖垮整批
            logger.exception("batch item %s booking failed", index)
            booked_results.append({"index": index, "status": "failed", "error": str(exc)})
    return {"items": booked_results}


async def _book_voucher_payload(
    session, raw: Dict[str, Any], *, user_id: str, company_id: str, request_id: str
) -> Dict[str, Any]:
    """确定性管线入账：与 POST /api/record/confirm 完全同构。"""
    from services.accounting_core_service import create_entry, ensure_tenant
    from db.models import new_id

    payload = {k: raw[k] for k in _VOUCHER_PAYLOAD_KEYS if raw.get(k) not in (None, "")}
    direction = payload.get("direction") or ""
    if direction not in ("income", "expense", "owner_transfer"):
        raise HTTPException(status_code=422, detail="草稿缺少有效的收支方向")
    category = payload.get("category") or "其他支出"
    payer = payload.get("payer") or "company_account"

    vat = compute_vat(payload.get("amount"), category, direction)
    voucher = get_voucher_hint(direction, category, payload.get("amount"), payer=payer)
    payload.update(
        {
            "id": new_id(),
            "payer": payer,
            "vatRate": vat.get("vatRate"),
            "vatAmount": vat.get("vatAmount"),
            "taxDeductible": is_tax_deductible(category, direction),
            "riskTags": get_risk_tags(category, direction),
            "voucherLines": voucher,
            "source": "agent",
        }
    )

    await ensure_tenant(session, company_id, user_id)
    entry = await create_entry(
        session,
        payload,
        company_id=company_id,
        user_id=user_id,
        request_id=request_id,
        confirm=True,
    )
    return {
        "entryId": entry.id,
        "voucherStatus": entry.status,
        "voucherRef": entry.voucher_no,
        "category": payload.get("category"),
        "title": payload.get("title"),
    }
