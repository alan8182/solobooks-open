# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Agent 工具注册表：对现有 services 的薄包装。

- 只读工具直接执行；
- 写工具（propose_voucher）只产生"待确认动作"，不入账。
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from datetime import datetime
from decimal import Decimal
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from agent import actions as agent_actions
from agent.memory import get_profile
from core.errors import AccountingError

logger = logging.getLogger(__name__)

MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
DATE_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"
PERIOD_PATTERN = r"^\d{4}-(Q[1-4]|0[1-9]|1[0-2])$"

# ponytail: 结账检查/申报包等复杂报表短时间（15s）进程内缓存，防止 agent 在单轮多步对话中高频重算拖垮 DB
_ANALYSIS_CACHE: Dict[tuple[str, str, str], tuple[float, Any]] = {}
_ANALYSIS_CACHE_TTL = 15.0


def _get_analysis_cache(tool_name: str, company_id: str, key_arg: str) -> Any | None:
    cache_key = (tool_name, company_id, key_arg)
    if cache_key in _ANALYSIS_CACHE:
        cached_at, val = _ANALYSIS_CACHE[cache_key]
        if time.monotonic() - cached_at < _ANALYSIS_CACHE_TTL:
            return val
    return None


def _set_analysis_cache(tool_name: str, company_id: str, key_arg: str, val: Any) -> None:
    now = time.monotonic()
    if len(_ANALYSIS_CACHE) > 64:
        stale = [k for k, (ts, _) in _ANALYSIS_CACHE.items() if now - ts >= _ANALYSIS_CACHE_TTL]
        for k in stale:
            _ANALYSIS_CACHE.pop(k, None)
    _ANALYSIS_CACHE[(tool_name, company_id, key_arg)] = (now, val)


@dataclass
class ToolContext:
    user_id: str
    company_id: str
    source: str = "product"
    conversation_id: Optional[str] = None


ToolHandler = Callable[..., Awaitable[Any]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: ToolHandler

    def openai_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ---------------------------------------------------------------------------
# 结果裁剪：控制进入上下文的体积
# ---------------------------------------------------------------------------

def _pick(item: Dict[str, Any], keys: tuple, fallback_limit: int = 400) -> Dict[str, Any]:
    out = {k: item[k] for k in keys if item.get(k) not in (None, "")}
    if not out:  # 字段名与预期不符时，退回原始数据（截断长值）
        for k, v in item.items():
            out[k] = v if not isinstance(v, str) or len(v) <= fallback_limit else v[:fallback_limit] + "…"
    return out


def _truncate(value: Any, limit: int) -> Any:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return value
    return text[:limit] + "…（结果过长已截断）"


def _month_prefix(value: Any) -> str:
    text = str(value or "")
    return text[:7].replace("/", "-")


# ---------------------------------------------------------------------------
# 只读工具
# ---------------------------------------------------------------------------

async def _get_month_summary(ctx: ToolContext, month: str) -> Any:
    from services.ledger_service import get_balance_summary

    return await get_balance_summary(ctx.company_id, month)


async def _list_entries(
    ctx: ToolContext,
    month: Optional[str] = None,
    keyword: Optional[str] = None,
    billing_status: Optional[str] = None,
    limit: int = 30,
) -> Any:
    from services.entry_service import list_entries

    rows = await list_entries(ctx.user_id, ctx.company_id)
    out: List[Dict[str, Any]] = []
    for row in rows:
        row_month = _month_prefix(row.get("occurDate") or row.get("occur_date") or row.get("createdAt") or row.get("created_at"))
        if month and month != row_month:
            continue
        if keyword:
            hay = " ".join(str(row.get(k) or "") for k in ("title", "category", "merchant", "note", "remark")).lower()
            if keyword.lower() not in hay:
                continue
        if billing_status is not None and str(row.get("billingStatus") or "") != billing_status:
            continue
        out.append(_pick(row, ("id", "direction", "amount", "title", "category", "occurDate", "createdAt", "payer", "status", "invoiceNumber", "billingStatus")))
    cap = max(1, min(int(limit or 30), 100))
    return {"count": len(out[:cap]), "total": len(out), "entries": out[:cap]}


async def _list_invoice_entries(
    ctx: ToolContext, status: Optional[str] = None, limit: int = 30
) -> Any:
    """发票信息随账目 payload 存储（无独立发票表），此处过滤带发票字段的账目。"""
    from services.entry_service import list_entries

    rows = await list_entries(ctx.user_id, ctx.company_id)
    out: List[Dict[str, Any]] = []
    for row in rows:
        has_invoice = bool(row.get("invoiceNumber") or row.get("invoiceType") or row.get("billingStatus"))
        if not has_invoice:
            continue
        if status and str(row.get("billingStatus") or "") != status:
            continue
        out.append(_pick(row, ("id", "title", "amount", "direction", "category", "invoiceNumber", "invoiceType", "billingStatus", "occurDate", "sellerName", "buyerName")))
        if len(out) >= max(1, min(int(limit or 30), 100)):
            break
    return {"count": len(out), "invoices": out}


async def _list_bank_statements(
    ctx: ToolContext,
    month: Optional[str] = None,
    matched: Optional[bool] = None,
    limit: int = 30,
) -> Any:
    from services.bank_service import get_statements

    rows = await get_statements(ctx.company_id)
    out: List[Dict[str, Any]] = []
    for row in rows:
        if month and month != _month_prefix(row.get("transaction_date")):
            continue
        matched_ids = row.get("matched_entry_ids") or ([row["matched_entry_id"]] if row.get("matched_entry_id") else [])
        is_matched = bool(matched_ids)
        if matched is not None and is_matched is not matched:
            continue
        out.append({"id": row.get("id"), "date": str(row.get("transaction_date") or "")[:10],
                    "amount": row.get("amount"), "description": row.get("description"),
                    "counterparty": row.get("counterparty") or "",
                    "direction": "income" if Decimal(str(row.get("amount") or 0)) >= 0 else "expense",
                    "matched": is_matched, "matchedEntryIds": matched_ids})
    cap = max(1, min(int(limit or 30), 100))
    return {"count": len(out[:cap]), "total": len(out), "statements": out[:cap]}


async def _list_fixed_assets(
    ctx: ToolContext,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> Any:
    """固定资产台账：回答「有哪些资产/每月折旧多少/净值还剩多少」，结账账实核对入口。"""
    from db.session import session_scope
    from services.fixed_asset_service import list_assets

    safe_page = max(1, min(int(page or 1), 1000))
    safe_size = max(1, min(int(page_size or 50), 100))
    async with session_scope() as session:
        result = await list_assets(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user_id,
            status=status or None,
            category=category or None,
            keyword=keyword or None,
            page=safe_page,
            page_size=safe_size,
        )
    # 投影：卡片 25+ 字段只留台账盘点/折旧核对所需，控上下文体积
    items = [
        _pick(a, (
            "id", "code", "name", "category", "categoryName", "status",
            "acquisitionDate", "originalValue", "accumulatedDepreciation",
            "netValue", "monthlyDepreciation", "remainingMonths",
            "depreciationMethod", "lastDepreciatedPeriod",
        ))
        for a in result.get("items", [])
    ]
    return {
        "total": result.get("total", 0),
        "count": len(items),
        "page": result.get("page", safe_page),
        "pageSize": result.get("pageSize", safe_size),
        "assets": items,
    }


async def _list_intangible_assets(
    ctx: ToolContext,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    expiring_in_days: Optional[int] = None,
    page: int = 1,
    page_size: int = 50,
) -> Any:
    """无形资产台账：查询软件著作权、商标、专利、域名等无形资产原值、累计摊销、净值与权利到期日。"""
    from db.session import session_scope
    from services.intangible_asset_service import list_assets

    safe_page = max(1, min(int(page or 1), 1000))
    safe_size = max(1, min(int(page_size or 50), 100))
    safe_days = max(1, min(int(expiring_in_days), 3650)) if expiring_in_days is not None else None
    async with session_scope() as session:
        result = await list_assets(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user_id,
            status=status or None,
            category=category or None,
            keyword=keyword or None,
            expiring_in_days=safe_days,
            page=safe_page,
            page_size=safe_size,
        )
    # 投影：只保留台账盘点、摊销核对与权利到期预警所需字段，控上下文体积
    items = [
        _pick(
            a,
            (
                "id",
                "code",
                "name",
                "category",
                "categoryName",
                "status",
                "originalValue",
                "accumulatedAmortization",
                "netValue",
                "usefulLifeMonths",
                "remainingMonths",
                "lastAmortizedPeriod",
                "amortizationEndPeriod",
                "legalCertNo",
                "expiryDate",
                "rdProjectId",
            ),
        )
        for a in result.get("items", [])
    ]
    return {
        "total": result.get("total", 0),
        "count": len(items),
        "page": result.get("page", safe_page),
        "pageSize": result.get("pageSize", safe_size),
        "assets": items,
    }


async def _get_advance_balances(ctx: ToolContext, month: Optional[str] = None) -> Any:
    """股东/员工垫付台账：按垫付人汇总其他应付款的期初、本期发生、报销冲销与期末未报余额。"""
    from datetime import datetime, timezone
    from services.ledger_service import query_advance_balances

    target_month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    return await query_advance_balances(ctx.company_id, target_month)


async def _list_private_expenses(
    ctx: ToolContext,
    owner_name: Optional[str] = None,
    direction: Optional[str] = None,
    invoice_status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> Any:
    """查询老板小私账台账与收支汇总（老板个人垫付与代收备忘）。"""
    from services.private_expense_service import get_expense_summary, list_expenses

    safe_page = max(1, min(int(page or 1), 1000))
    safe_size = max(1, min(int(page_size or 50), 100))
    resolved_owner = str(owner_name or "").strip()
    result = await list_expenses(
        company_id=ctx.company_id,
        page=safe_page,
        page_size=safe_size,
        owner_name=resolved_owner,
        direction=str(direction or "").strip(),
        invoice_status=str(invoice_status or "").strip(),
        start_date=str(start_date or "").strip(),
        end_date=str(end_date or "").strip(),
    )
    summary = await get_expense_summary(company_id=ctx.company_id, owner_name=resolved_owner)

    # 投影：保留私账核心字段，控制上下文体积
    items = [
        _pick(
            it,
            (
                "id",
                "title",
                "amount",
                "direction",
                "category",
                "occur_date",
                "payer",
                "owner_name",
                "private_account",
                "invoice_status",
                "substitute_entry_id",
                "substitute_memo",
                "memo",
            ),
        )
        for it in result.get("items", [])
    ]
    return {
        "total": result.get("total", 0),
        "count": len(items),
        "page": safe_page,
        "pageSize": safe_size,
        "items": items,
        "summary": {
            "totalCount": summary.get("count", 0),
            "expenseAmount": summary.get("expense_amount", "0.00"),
            "incomeAmount": summary.get("income_amount", "0.00"),
            "availableIncomeAmount": summary.get("available_income_amount", "0.00"),
            "offsetAmount": summary.get("offset_amount", "0.00"),
            "balanceAmount": summary.get("balance_amount", "0.00"),
        },
    }


async def _record_private_expense(
    ctx: ToolContext,
    title: str,
    amount: float,
    direction: str = "expense",
    category: Optional[str] = None,
    occur_date: Optional[str] = None,
    owner_name: Optional[str] = None,
    private_account: Optional[str] = None,
    invoice_status: str = "no_invoice",
    memo: Optional[str] = None,
) -> Any:
    """直接记入老板小私账（老板个人垫付或代收备忘台账）。"""
    from datetime import date
    from services.private_expense_service import create_expense

    target_date = str(occur_date or date.today().isoformat())[:10]
    data = {
        "title": str(title).strip(),
        "amount": amount,
        "direction": str(direction or "expense").strip(),
        "category": str(category or "").strip(),
        "occur_date": target_date,
        "payer": "boss_personal",
        "owner_name": str(owner_name or "本人").strip() or "本人",
        "private_account": str(private_account or "").strip(),
        "is_available": True,
        "memo": str(memo or "").strip(),
        "invoice_status": str(invoice_status or "no_invoice").strip(),
    }
    created = await create_expense(company_id=ctx.company_id, user_id=ctx.user_id, data=data)
    return {
        "success": True,
        "expense": created,
        "message": f"已记入小私账：{created['title']} {created['amount']} 元（{target_date}）",
    }


async def _get_aging(ctx: ToolContext, kind: str = "ar", group_by: str = "none") -> Any:
    from services.aging_service import query_ap_aging, query_ar_aging

    # group_by=partner 时按往来单位分组（P0 WP2 升级），默认 none 保持旧结构
    resolved = "partner" if str(group_by or "").lower() == "partner" else "none"
    if str(kind).lower() == "ap":
        return await query_ap_aging(ctx.company_id, group_by=resolved)
    return await query_ar_aging(ctx.company_id, group_by=resolved)


async def _list_todos(
    ctx: ToolContext,
    type: Optional[str] = None,
    level: Optional[str] = None,
    limit: int = 50,
) -> Any:
    from services.todo_service import list_todos

    rows = await list_todos(ctx.user_id, ctx.company_id)
    filtered: List[Dict[str, Any]] = []
    for t in rows:
        if type and str(t.get("type") or "") != type:
            continue
        if level and str(t.get("level") or "") != level:
            continue
        filtered.append(_pick(t, ("rule_id", "type", "level", "title", "desc", "amount", "entryId"), fallback_limit=200))
    cap = max(1, min(int(limit or 50), 200))
    return {"count": len(filtered[:cap]), "total": len(filtered), "todos": filtered[:cap]}


async def _search_books(ctx: ToolContext, query: str) -> Any:
    from services.search_service import search_all

    return _truncate(await search_all(ctx.company_id, str(query or "")), 6000)


async def _get_company_profile(ctx: ToolContext) -> Any:
    profile = await get_profile(ctx.company_id)
    # 员工名单：让 agent 能核实"员工小李垫的"里的姓名是否真实存在，
    # 避免它凭空编一个名字写进「其他应付款-员工往来」。员工模块没启用时留空数组。
    try:
        from services.salary_service import list_employees

        employees = await list_employees(ctx.company_id)
        profile["employees"] = [
            {
                "name": e.get("name"),
                "department": e.get("department"),
                "isOwner": bool(e.get("is_owner")),
            }
            for e in employees
        ]
    except Exception:  # 员工档案读取失败不阻塞画像查询
        profile["employees"] = []
    return profile


async def _update_company_profile(
    ctx: ToolContext,
    industry: Optional[str] = None,
    taxpayer_type: Optional[str] = None,
    merchant_category: Optional[dict] = None,
    notes: Optional[str] = None,
) -> Any:
    """更新公司画像（低风险记忆写操作，直接生效并记审计）。

    merchant_category 形如 {"UU跑腿": "交通费"}，表示"以后这类商家就记这个分类"。
    """
    from agent.memory import save_profile, get_profile

    profile = await get_profile(ctx.company_id)
    changed = []
    if industry:
        profile["industry"] = industry
        changed.append("行业")
    if taxpayer_type:
        profile["taxpayerType"] = taxpayer_type
        changed.append("纳税人身份")
    if notes:
        profile["notes"] = notes
        changed.append("备注")
    if merchant_category and isinstance(merchant_category, dict):
        prefs = dict(profile.get("categoryPreferences") or {})
        for merchant, category in merchant_category.items():
            entry = dict(prefs.get(str(merchant)) or {})
            entry["category"] = str(category)
            entry["count"] = int(entry.get("count") or 0) + 2
            prefs[str(merchant)] = entry
        profile["categoryPreferences"] = prefs
        changed.append("分类习惯")
    if not changed:
        return {"updated": False, "message": "没有提供任何可更新的字段"}
    await save_profile(ctx.company_id, profile)
    from services.audit_service import log_action

    await log_action(
        ctx.company_id, ctx.user_id, "agent_profile_update", "company_profile", ctx.company_id,
        {"changed": changed},
    )
    return {"updated": True, "changed": changed, "profile": profile}


async def _get_entry_detail(ctx: ToolContext, entry_id: str) -> Any:
    from services.entry_service import get_entry

    row = await get_entry(str(entry_id), user_id=ctx.user_id, company_id=ctx.company_id)
    if row is None:
        return {"found": False, "message": f"未找到条目 {entry_id}"}
    return {"found": True, "entry": _truncate(row, 5000)}


# ---------------------------------------------------------------------------
# 结账工具（M2）
# ---------------------------------------------------------------------------

async def _run_close_checks(
    ctx: ToolContext,
    month: str,
    findingLimit: int = 100,
    findingCursor: int = 0,
) -> Any:
    """运行结账五项检查（账证/账账/账实/账表/税表）。"""
    month_str = str(month)

    from db.session import session_scope
    from services.closing_workflow_service import get_closing_snapshot

    async with session_scope() as session:
        return await get_closing_snapshot(
            session,
            ctx.company_id,
            month_str,
            user_id=ctx.user_id,
            compact=True,
            finding_limit=findingLimit,
            finding_cursor=findingCursor,
        )


async def _list_unmatched(
    ctx: ToolContext,
    month: Optional[str] = None,
    limit: int = 20,
    snapshotHash: Optional[str] = None,
    findingLimit: int = 20,
    findingCursor: int = 0,
) -> Any:
    """票/流/账差异：未匹配流水清单（带 ID）+ AI 对账建议（带可直接用的 ID 对）。"""
    from datetime import datetime, timezone
    from services.bank_service import get_difference_report, ai_suggest_matches, get_statements
    from services.accounting_core_service import period_start_for

    target_month = str(month or "").strip() or datetime.now(timezone.utc).strftime("%Y-%m")
    month = period_start_for(target_month).strftime("%Y-%m")
    from db.session import session_scope
    from services.closing_workflow_service import get_closing_snapshot, validate_snapshot_hash

    async with session_scope() as session:
        if snapshotHash:
            snapshot = await validate_snapshot_hash(
                session,
                ctx.company_id,
                month,
                snapshotHash,
                user_id=ctx.user_id,
            )
        else:
            snapshot = await get_closing_snapshot(
                session,
                ctx.company_id,
                month,
                user_id=ctx.user_id,
            )
    diff = await get_difference_report(ctx.company_id, month)

    # 本月未匹配流水的真实 ID 清单（difference report 只有摘要，没有 ID）
    unmatched_rows = sorted([
        row for row in await get_statements(ctx.company_id)
        if str(row.get("transaction_date") or "")[:7] == month
        and not row.get("matched_entry_ids") and not row.get("matched_entry_id")
    ], key=lambda row: (str(row.get("transaction_date") or ""), str(row.get("id") or "")))

    # Preserve the complete group: income plus fees may settle one statement.
    suggestions_error: Optional[str] = None
    try:
        raw_suggestions = await ai_suggest_matches(ctx.company_id, limit=max(1, min(int(limit or 20), 10)), month=month, user_id=ctx.user_id)
    except AccountingError:
        raise  # 配额超限对 MCP 客户端可见（工具报错），不能伪装成「无建议」
    except Exception:
        # 降级必须对 agent 可见：空数组与「无可匹配」从外部看一模一样，会误导对账决策
        logger.exception("ai_suggest_matches failed")
        raw_suggestions = []
        suggestions_error = "AI 对账建议生成失败（已跳过），请人工核对或稍后重试 list_unmatched"
    suggestions = [
        {
            "statementId": s.get("statementId"),
            "statement": s.get("statement"),
            "matchedEntryId": s.get("matchedEntryId"),
            "matchedEntry": s.get("matchedEntry"),
            "matchedEntryIds": s.get("matchedEntryIds") or ([s["matchedEntryId"]] if s.get("matchedEntryId") else []),
            "matchedEntries": s.get("matchedEntries"),
            "type": s.get("type"),
            "reason": s.get("reason"),
            "amountDifference": s.get("amountDifference"),
        }
        for s in raw_suggestions
        if isinstance(s, dict) and s.get("statementId") in {row["id"] for row in unmatched_rows}
    ]

    book_findings = [
        item
        for item in (snapshot.get("findings") or [])
        if item.get("checkCode") == "BOOK_TO_ACTUAL"
    ]
    cursor = max(0, int(findingCursor or 0))
    page_size = max(1, min(int(findingLimit or 20), 100))
    page = book_findings[cursor:cursor + page_size]
    next_cursor = cursor + len(page)

    async with session_scope() as session:
        await validate_snapshot_hash(
            session,
            ctx.company_id,
            month,
            snapshot.get("snapshotHash"),
            user_id=ctx.user_id,
        )

    return {
        "month": month,
        "schemaVersion": snapshot.get("schemaVersion"),
        "snapshotHash": snapshot.get("snapshotHash"),
        "periodVersion": snapshot.get("periodVersion"),
        "generatedAt": snapshot.get("generatedAt"),
        "findings": page,
        "findingsTotal": len(book_findings),
        "nextFindingCursor": next_cursor if next_cursor < len(book_findings) else None,
        "unmatchedStatements": [
            {
                "statementId": r["id"],
                "date": str(r["transaction_date"]),
                "description": r["description"],
                "amount": float(r["amount"] or 0),
            }
            for r in unmatched_rows
        ],
        "aiSuggestions": suggestions,
        "aiSuggestionsError": suggestions_error,
        "usage": (
            "AI 建议只用于展示，执行时必须使用 findings[].findingId、snapshotHash 和权威 statementId。"
            "已有凭证的匹配将完整 matchedEntryIds 传入 propose_match；没有对应凭证的流水使用 "
            "propose_statement_vouchers，逐笔传 statementId 与服务端有效 category。"
            "其他类型需人工核对；金额、方向、日期、摘要一律以工具返回为准。"
        ),
        "differenceSummary": _truncate(diff, 3000),
    }


async def _preview_accruals(
    ctx: ToolContext,
    month: str,
    snapshotHash: Optional[str] = None,
) -> Any:
    """预览本月待计提事项：折旧（未计提的在用固定资产）、摊销（未计提的在用无形资产）与工资（工资表状态）。"""
    from services.database import get_conn

    period = str(month)
    from db.session import session_scope
    from services.closing_workflow_service import get_closing_snapshot, validate_snapshot_hash

    async with session_scope() as session:
        if snapshotHash:
            snapshot = await validate_snapshot_hash(
                session,
                ctx.company_id,
                period,
                snapshotHash,
                user_id=ctx.user_id,
            )
        else:
            snapshot = await get_closing_snapshot(
                session,
                ctx.company_id,
                period,
                user_id=ctx.user_id,
            )
    result: Dict[str, Any] = {
        "month": period,
        "schemaVersion": snapshot.get("schemaVersion"),
        "snapshotHash": snapshot.get("snapshotHash"),
        "periodVersion": snapshot.get("periodVersion"),
        "findings": [
            item
            for item in (snapshot.get("findings") or [])
            if item.get("checkCode")
            in {
                "FIXED_ASSET_DEPRECIATION",
                "INTANGIBLE_AMORTIZATION",
                "PAYROLL_ACCRUAL",
            }
        ],
    }

    # 折旧：在用固定资产中本期 planned 且未过账的（P1 WP-B 起读核心 ORM）
    pending_depreciation: List[Dict[str, Any]] = []
    dep_total = Decimal("0")
    try:
        from sqlalchemy import select

        from db.models import FixedAsset, FixedAssetDepreciationSchedule
        from db.session import session_scope

        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(FixedAssetDepreciationSchedule, FixedAsset)
                    .join(FixedAsset, FixedAsset.id == FixedAssetDepreciationSchedule.asset_id)
                    .where(
                        FixedAssetDepreciationSchedule.company_id == ctx.company_id,
                        FixedAssetDepreciationSchedule.period_start == period,
                        FixedAssetDepreciationSchedule.status == "planned",
                        FixedAsset.deleted_at.is_(None),
                        FixedAsset.status.in_(("active", "idle")),
                    )
                    .order_by(FixedAssetDepreciationSchedule.sequence_no.asc())
                )
            ).all()
            for row, asset in rows:
                amount = Decimal(str(row.amount or 0))
                dep_total += amount
                pending_depreciation.append({"assetId": asset.id, "name": asset.name, "monthly": float(amount)})
    except Exception:
        logger.exception("fixed asset depreciation preview failed")
    result["depreciation"] = {
        "pendingCount": len(pending_depreciation),
        "pendingTotal": float(dep_total),
        "assets": pending_depreciation[:10],
    }

    # 摊销：在用无形资产中本期 planned 且未过账的
    pending_amortization: List[Dict[str, Any]] = []
    amort_total = Decimal("0")
    try:
        from sqlalchemy import select

        from db.models import IntangibleAsset, IntangibleAmortizationSchedule
        from db.session import session_scope

        async with session_scope() as session:
            amort_rows = (
                await session.execute(
                    select(IntangibleAmortizationSchedule, IntangibleAsset)
                    .join(IntangibleAsset, IntangibleAsset.id == IntangibleAmortizationSchedule.asset_id)
                    .where(
                        IntangibleAmortizationSchedule.company_id == ctx.company_id,
                        IntangibleAmortizationSchedule.period_start == period,
                        IntangibleAmortizationSchedule.status == "planned",
                        IntangibleAsset.deleted_at.is_(None),
                        IntangibleAsset.status.in_(("active", "idle")),
                    )
                    .order_by(IntangibleAmortizationSchedule.sequence_no.asc())
                )
            ).all()
            for row, asset in amort_rows:
                amount = Decimal(str(row.amount or 0))
                amort_total += amount
                pending_amortization.append({"assetId": asset.id, "name": asset.name, "monthly": float(amount)})
    except Exception:
        logger.exception("intangible asset amortization preview failed")
    result["amortization"] = {
        "pendingCount": len(pending_amortization),
        "pendingTotal": float(amort_total),
        "assets": pending_amortization[:10],
    }

    # 工资：工资表状态
    try:
        conn = await get_conn()
        try:
            cursor = await conn.execute(
                "SELECT status FROM salary_sheets WHERE company_id = ? AND month = ?",
                (ctx.company_id, period),
            )
            sheet = await cursor.fetchone()
        finally:
            await conn.close()
        # 与个税扣缴/小型微利同口径：只算在职员工（is_active = 1）
        from services.headcount_service import count_active_employees

        emp_count = await count_active_employees(ctx.company_id)
        result["salary"] = {
            "sheetExists": bool(sheet),
            "status": str(sheet["status"]) if sheet else None,
            "employeeCount": emp_count,
            "message": (
                f"工资表状态 {sheet['status']}" if sheet
                else ("有员工但本月工资表未创建，请先在薪酬管理页创建工资表" if emp_count else "未配置员工，无需工资计提")
            ),
        }
    except Exception:
        logger.exception("salary preview failed")
        result["salary"] = {"sheetExists": False, "status": None, "employeeCount": 0, "message": "工资模块未初始化"}
    async with session_scope() as session:
        await validate_snapshot_hash(
            session,
            ctx.company_id,
            period,
            snapshot.get("snapshotHash"),
            user_id=ctx.user_id,
        )
    return result


async def _propose_match(
    ctx: ToolContext,
    statement_id: str,
    entry_ids: List[str],
    month: str,
    snapshotHash: str,
    findingId: str,
    note: Optional[str] = None,
) -> Any:
    """创建银行流水↔账目对账建议（待用户确认后落账）。"""
    from agent.actions import create_action
    from services.database import get_conn

    statement_id = str(statement_id or "").strip()
    clean_ids = [str(e or "").strip() for e in (entry_ids or []) if str(e or "").strip()]
    if not statement_id or not clean_ids:
        return {"error": "需要 statement_id 与至少一笔 entry_id"}

    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT transaction_date, description, amount FROM bank_statements WHERE id = ? AND company_id = ?",
            (statement_id, ctx.company_id),
        )
        stmt = await cursor.fetchone()
        if not stmt:
            return {"error": f"流水 {statement_id} 不存在"}
        placeholders = ",".join("?" for _ in clean_ids)
        cursor = await conn.execute(
            f"SELECT id, payload FROM entries WHERE company_id = ? AND id IN ({placeholders})",
            (ctx.company_id, *clean_ids),
        )
        entry_rows = await cursor.fetchall()
    finally:
        await conn.close()

    entries_display = []
    for row in entry_rows:
        try:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            entries_display.append({
                "id": str(row["id"]),
                "title": payload.get("title"),
                "amount": payload.get("amount"),
                "direction": payload.get("direction"),
            })
    if not entries_display:
        return {"error": "账目记录不存在"}

    payload = {
        "statementId": statement_id,
        "entryIds": clean_ids,
        "statement": {
            "date": str(stmt["transaction_date"]),
            "description": stmt["description"],
            "amount": float(stmt["amount"] or 0),
        },
        "entries": entries_display,
    }
    from db.session import session_scope
    from services.closing_workflow_service import (
        closing_action_binding,
        validate_closing_action_binding,
    )

    binding_request = {
        "month": str(month),
        "findingId": str(findingId),
        "baseSnapshotHash": str(snapshotHash),
        "periodVersion": None,
        "surface": ctx.source,
    }
    async with session_scope() as session:
        snapshot, finding = await validate_closing_action_binding(
            session,
            ctx.company_id,
            "propose_match",
            payload,
            binding_request,
            user_id=ctx.user_id,
        )
    closing_binding = closing_action_binding(snapshot, finding, surface=ctx.source)

    action = await create_action(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        action_type="propose_match",
        title="银行流水对账建议",
        payload=payload,
        evidence={
            "note": note or "",
            "created_via": "agent_closing_repair",
            "closingBinding": closing_binding,
            "actionVersion": 1,
        },
        conversation_id=ctx.conversation_id,
    )
    return {
        "actionId": action["id"],
        "status": action["status"],
        "snapshotHash": snapshotHash,
        "findingId": findingId,
        "message": "对账草稿已创建，等待用户确认；当前尚未执行匹配",
    }


async def _propose_accrual(
    ctx: ToolContext,
    kind: str,
    month: str,
    snapshotHash: str,
    findingId: str,
    note: Optional[str] = None,
) -> Any:
    """创建计提确认草稿（折旧/摊销/工资），用户确认后执行现有计提服务。"""
    from agent.actions import create_action

    kind = str(kind or "").strip().lower()
    month = str(month or "").strip()
    if kind not in ("depreciation", "amortization", "salary"):
        return {"error": "kind 必须是 depreciation、amortization 或 salary"}
    if not month:
        return {"error": "需要月份（YYYY-MM）"}

    preview = await _preview_accruals(ctx, month, snapshotHash=snapshotHash)
    kind_preview = preview.get(kind) or {}

    title_map = {
        "depreciation": "折旧计提",
        "amortization": "无形资产摊销",
        "salary": "工资计提",
    }
    title = f"{title_map.get(kind, '计提')}（{month}）"

    payload = {
        "kind": kind,
        "month": month,
        "preview": kind_preview,
    }
    from db.session import session_scope
    from services.closing_workflow_service import (
        closing_action_binding,
        validate_closing_action_binding,
    )

    binding_request = {
        "month": month,
        "findingId": str(findingId),
        "baseSnapshotHash": str(snapshotHash),
        "periodVersion": None,
        "surface": ctx.source,
    }
    async with session_scope() as session:
        snapshot, finding = await validate_closing_action_binding(
            session,
            ctx.company_id,
            "propose_accrual",
            payload,
            binding_request,
            user_id=ctx.user_id,
        )
    closing_binding = closing_action_binding(snapshot, finding, surface=ctx.source)

    action = await create_action(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        action_type="propose_accrual",
        title=title,
        payload=payload,
        evidence={
            "note": note or "",
            "created_via": "agent_closing_repair",
            "closingBinding": closing_binding,
            "actionVersion": 1,
        },
        conversation_id=ctx.conversation_id,
    )
    return {
        "actionId": action["id"],
        "status": action["status"],
        "snapshotHash": snapshotHash,
        "findingId": findingId,
        "preview": kind_preview,
        "message": "计提草稿已创建，等待用户确认；当前尚未执行计提",
    }


async def _preview_asset_actions(
    ctx: ToolContext,
    month: str,
    snapshotHash: Optional[str] = None,
) -> Any:
    """预览资产处理：缺卡交易、待提折旧/摊销、阻塞项。"""
    from services.asset_closure_service import preview_asset_closure
    from db.session import session_scope
    from services.closing_workflow_service import get_closing_snapshot, validate_snapshot_hash

    async with session_scope() as session:
        if snapshotHash:
            snapshot = await validate_snapshot_hash(
                session,
                ctx.company_id,
                str(month),
                snapshotHash,
                user_id=ctx.user_id,
            )
        else:
            snapshot = await get_closing_snapshot(
                session,
                ctx.company_id,
                str(month),
                user_id=ctx.user_id,
            )
    preview = await preview_asset_closure(ctx.company_id, ctx.user_id, str(month))
    async with session_scope() as session:
        await validate_snapshot_hash(
            session,
            ctx.company_id,
            str(month),
            snapshot.get("snapshotHash"),
            user_id=ctx.user_id,
        )
    return {
        **preview,
        "schemaVersion": snapshot.get("schemaVersion"),
        "snapshotHash": snapshot.get("snapshotHash"),
        "periodVersion": snapshot.get("periodVersion"),
        "findings": [
            item
            for item in (snapshot.get("findings") or [])
            if item.get("checkCode")
            in {
                "ASSET_CLOSURE",
                "FIXED_ASSET_DEPRECIATION",
                "INTANGIBLE_AMORTIZATION",
                "FIXED_ASSET_RECONCILIATION",
                "IMPAIRMENT_TEST_DUE",
            }
        ],
    }


async def _propose_asset_closure(
    ctx: ToolContext,
    month: str,
    snapshotHash: str,
    findingId: str,
    note: Optional[str] = None,
) -> Any:
    """把建卡和折旧/摊销补提合成一张待确认动作。"""
    from services.asset_closure_service import (
        build_asset_closure_payload,
        preview_asset_closure,
    )

    await _preview_asset_actions(ctx, str(month), snapshotHash=snapshotHash)
    preview = await preview_asset_closure(ctx.company_id, ctx.user_id, str(month))
    payload = build_asset_closure_payload(preview)
    has_processable = bool(
        payload["fixedCards"]
        or payload["intangibleCards"]
        or payload["runFixedDepreciation"]
        or payload["runIntangibleAmortization"]
    )
    if not has_processable:
        return {
            "actionId": None,
            "status": "nothing_to_do",
            "preview": preview,
            "message": "当前没有可处理的资产建卡或待补提折旧/摊销事项。",
        }

    from db.session import session_scope
    from services.closing_workflow_service import (
        closing_action_binding,
        validate_closing_action_binding,
    )

    binding_request = {
        "month": str(month),
        "findingId": str(findingId),
        "baseSnapshotHash": str(snapshotHash),
        "periodVersion": None,
        "surface": ctx.source,
    }
    async with session_scope() as session:
        snapshot, finding = await validate_closing_action_binding(
            session,
            ctx.company_id,
            "propose_accrual",
            payload,
            binding_request,
            user_id=ctx.user_id,
        )
    closing_binding = closing_action_binding(snapshot, finding, surface=ctx.source)

    action = await agent_actions.create_action(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        action_type="propose_accrual",
        title=f"资产处理确认（{month}）",
        payload=payload,
        evidence={
            "note": note or "",
            "created_via": "agent_closing_repair",
            "closingBinding": closing_binding,
            "actionVersion": 1,
        },
        conversation_id=ctx.conversation_id,
    )
    return {
        "actionId": action["id"],
        "status": action["status"],
        "snapshotHash": snapshotHash,
        "findingId": findingId,
        "preview": preview,
        "message": "资产处理草稿已创建，等待老板确认；当前尚未建卡或计提。",
    }


async def _generate_close_report(ctx: ToolContext, month: str) -> Any:
    """生成结账检查报告（markdown）：五项检查 + 差异 + 计提状态 + 下一步建议。"""
    month = str(month)
    checks = await _run_close_checks(ctx, month)
    try:
        unmatched = await _list_unmatched(
            ctx,
            month,
            snapshotHash=checks.get("snapshotHash"),
        )
    except Exception:
        logger.exception("list_unmatched for report failed")
        unmatched = {"status": "unavailable", "message": "票流账差异查询失败，未完成核验，请重试"}
    try:
        accruals = await _preview_accruals(
            ctx,
            month,
            snapshotHash=checks.get("snapshotHash"),
        )
    except Exception:
        logger.exception("preview_accruals for report failed")
        accruals = {"status": "unavailable", "message": "计提状态查询失败，未完成核验，请重试"}
    report_can_close = (
        bool(checks.get("canClose"))
        and unmatched.get("status") != "unavailable"
        and accruals.get("status") != "unavailable"
    )

    lines: List[str] = [f"# 结账检查报告（{month}）", ""]
    lines.append(f"期间状态：{checks.get('periodStatus')}；可结账：{'是' if report_can_close else '否'}（核心检查 {checks.get('blockingCount')} 项阻塞）")
    lines.append("")
    lines.append("| 检查项 | 状态 | 说明 |")
    lines.append("|---|---|---|")
    for check in checks.get("checks", []):
        status = "✅ 通过" if check.get("status") == "passed" else "⛔ 阻塞" if check.get("status") == "blocking" else "⏳ 待核验"
        detail_parts = []
        for key, label in (
            ("draftCount", "待过账"),
            ("pendingReviewCount", "待复核"),
            ("orphanVoucherCount", "孤立凭证"),
            ("unreconciledBankCount", "未对账流水"),
            ("missingRuleVersionCount", "缺规则版本"),
            ("blockedByPendingCount", "尚未过账"),
            ("issueCount", "银行核对问题"),
        ):
            if check.get(key):
                detail_parts.append(f"{label} {check[key]}")
        if check.get("code") == "LEDGER_TO_LEDGER":
            detail_parts.append(f"借 {check.get('debit')} / 贷 {check.get('credit')}")
        lines.append(f"| {check.get('name')} | {status} | {'；'.join(detail_parts) or '—'} |")
    if unmatched.get("status") == "unavailable":
        lines.extend(["", "## 票流账差异：未完成核验", "", unmatched["message"], "不能将查询失败理解为没有差异。"])

    diff = unmatched.get("differenceSummary") or {}
    if isinstance(diff, dict) and (diff.get("bank_only") or diff.get("book_only")):
        lines.extend(["", "## 票流账差异", ""])
        lines.append("详细清单见下方 differenceSummary 数据（bank_only=银行有账上没有；book_only=账上有银行没有）。")
    if unmatched.get("aiSuggestions"):
        lines.append(f"AI 对账建议 {len(unmatched['aiSuggestions'])} 条，可用 propose_match 发起确认。")
    if unmatched.get("unmatchedStatements"):
        lines.append(f"未匹配银行流水 {len(unmatched['unmatchedStatements'])} 笔。")

    findings = checks.get("findings") or []
    if findings:
        lines.extend(["", "## 可处理问题", "", "| findingId | 问题 | 笔数 | 金额 | 可发起动作 |", "|---|---|---:|---:|---|"])
        for finding in findings[:30]:
            repairs = "、".join(finding.get("allowedRepairs") or []) or "需人工处理"
            lines.append(
                f"| {finding.get('findingId')} | {finding.get('title')} | "
                f"{finding.get('count', 0)} | {finding.get('amount') or '—'} | {repairs} |"
            )

    dep = accruals.get("depreciation") or {}
    amo = accruals.get("amortization") or {}
    sal = accruals.get("salary") or {}
    lines.extend(["", "## 计提状态", ""])
    lines.append(f"- 折旧：待计提 {dep.get('pendingCount', 0)} 项，合计 {dep.get('pendingTotal', 0)} 元")
    lines.append(f"- 摊销：待计提 {amo.get('pendingCount', 0)} 项，合计 {amo.get('pendingTotal', 0)} 元")
    lines.append(f"- 工资：{sal.get('message', '未知')}")

    lines.extend(["", "## 下一步", ""])
    if not report_can_close:
        lines.append("1. 处理上方阻塞项（对账/待处理凭证），完成后重新运行检查")
    lines.append("1. 需要计提的先发起计提确认")
    from services.closing_workflow_service import manual_close_guidance

    # Report-level readiness can be stricter than the core snapshot (for
    # example when a supplementary query failed), so align the link text with
    # the report's final conclusion while keeping the same authoritative URL.
    manual_close = manual_close_guidance(month, report_can_close)
    lines.append(
        f"1. 全部通过后由用户打开[月度结账向导]({manual_close['url']})点击锁账"
        "（AI 和 MCP 不会代为锁账）"
    )
    lines.extend(["", "## 网页锁账入口", "", f"[{manual_close['label']}]({manual_close['url']})", "", manual_close["message"]])

    from services.audit_service import log_action
    from db.session import session_scope
    from services.closing_workflow_service import validate_snapshot_hash

    await log_action(ctx.company_id, ctx.user_id, "agent_close_report", "period", month, {"canClose": checks.get("canClose")})
    async with session_scope() as session:
        await validate_snapshot_hash(
            session,
            ctx.company_id,
            month,
            checks.get("snapshotHash"),
            user_id=ctx.user_id,
        )
    return {
        "month": month,
        "schemaVersion": checks.get("schemaVersion"),
        "snapshotHash": checks.get("snapshotHash"),
        "periodVersion": checks.get("periodVersion"),
        "canClose": report_can_close,
        "differenceStatus": unmatched.get("status", "available"),
        "report": "\n".join(lines),
        "manualClose": manual_close,
        "checks": checks,
        "accruals": accruals,
    }


# ---------------------------------------------------------------------------
# 写工具：批量起草（一张清单确认）
# ---------------------------------------------------------------------------

_PAYER_SCHEMA = {
    "type": "string",
    "description": (
        "付款方/资金渠道：company_account=公司对公账户（默认）；"
        "boss_personal=老板/股东个人垫付（贷「其他应付款-股东」）；"
        "employee_personal:<姓名>=员工个人垫付（贷「其他应付款-员工往来(姓名)」）；"
        "receivable=待到账/挂应收账款（收入类款项尚未到账、跨期到账或待银行流水核销时使用，借「应收账款」）。"
        "员工垫付但用户没说是谁时只传 employee_personal，让用户在确认卡里选人。"
        "用户说“我垫的/我自己付的/老板付的”用 boss_personal；"
        "绝不要编造员工姓名，不确定就只传 employee_personal。"
    ),
}

_VOUCHER_PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "direction": {"type": "string", "enum": ["income", "expense", "owner_transfer"]},
        "amount": {"type": "number", "description": "金额（元），正数", "exclusiveMinimum": 0},
        "title": {"type": "string", "description": "事项摘要"},
        "category": {
            "type": "string",
            "description": "费用/收入一级分类（如：交通费、餐饮费、办公费、通讯费、差旅费、咨询服务收入、技术服务收入）。不确定时可先调用 get_company_profile 读取公司分类习惯",
        },
        "occur_date": {"type": "string", "description": "业务发生日期 YYYY-MM-DD，可选", "pattern": DATE_PATTERN},
        "payer": _PAYER_SCHEMA,
        "merchant": {"type": "string", "description": "对方商户/单位，可选"},
        "note": {"type": "string", "description": "给用户的备注，可选"},
        "statementId": {
            "type": "string",
            "description": "仅供服务端拒绝误用；银行流水必须调用 propose_statement_vouchers，不要使用本工具",
        },
    },
    "required": ["direction", "amount", "title", "category"],
}

_STATEMENT_VOUCHER_PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "statementId": {
            "type": "string",
            "description": "list_unmatched 返回的银行流水 statementId；必须原样传入，禁止改写",
        },
        "category": {
            "type": "string",
            "description": (
                "流水对应的有效分类，如办公费、营业外收入、股东投入、往来款。"
                "分类方向必须与流水收支方向一致"
            ),
        },
        "note": {"type": "string", "description": "给用户的分类依据说明，可选"},
    },
    "required": ["statementId", "category"],
}


def _assert_real_date(raw: Optional[str], prefix: str = "") -> None:
    """DATE_PATTERN 只能保证形状（2026-02-30 也匹配），真实历法日期在此校验。"""
    value = str(raw or "").strip()
    if not value:
        return
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"{prefix}occur_date 必须是真实存在的日期（YYYY-MM-DD），收到 {value!r}")


async def _propose_vouchers(ctx: ToolContext, proposals: List[dict], note: Optional[str] = None) -> Any:
    """批量创建记账凭证草稿（一张清单，用户一次性确认/排除行/改分类）。"""
    from services.account_mapper import normalize_payer

    items = []
    for i, p in enumerate(proposals):
        if not isinstance(p, dict):
            raise ValueError(f"proposals[{i}] 应为对象")
        cleaned = _enforce_schema(p, _VOUCHER_PROPOSAL_SCHEMA, f"proposals[{i}]")
        if cleaned.get("statementId"):
            raise ValueError(
                f"proposals[{i}] 来自银行流水，必须调用 propose_statement_vouchers，不能使用 propose_vouchers"
            )
        _assert_real_date(cleaned.get("occur_date"), f"proposals[{i}].")
        item = {
            "direction": cleaned.get("direction"),
            "amount": cleaned.get("amount"),
            "title": cleaned.get("title"),
            "category": cleaned.get("category"),
            "payer": normalize_payer(cleaned.get("payer")),
        }
        for optional_key, payload_key in (
            ("occur_date", "occurDate"),
            ("merchant", "merchant"),
        ):
            if cleaned.get(optional_key):
                item[payload_key] = cleaned[optional_key]
        if cleaned.get("note"):
            item["note"] = cleaned["note"]
        items.append(item)
    if not items:
        return {"error": "proposals 为空"}

    from agent.actions import create_action

    action = await create_action(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        action_type="propose_vouchers",
        title=f"批量记账（{len(items)} 笔）",
        payload={"items": items},
        evidence={"note": note or "", "created_via": "agent_chat_batch"},
    )
    income_total = sum(
        (Decimal(str(item.get("amount") or 0)) for item in items if item.get("direction") == "income"),
        Decimal("0"),
    )
    expense_total = sum(
        (Decimal(str(item.get("amount") or 0)) for item in items if item.get("direction") == "expense"),
        Decimal("0"),
    )
    return {
        "actionId": action["id"],
        "status": action["status"],
        "count": len(items),
        "totals": {
            "income": float(income_total),
            "expense": float(expense_total),
        },
        "items": [
            {
                "index": index,
                "title": item.get("title"),
                "direction": item.get("direction"),
                "amount": item.get("amount"),
                "occurDate": item.get("occurDate"),
            }
            for index, item in enumerate(items)
        ],
        "message": "待确认清单已创建，尚未生成凭证，也未入账；用户确认后才执行",
    }


async def _propose_statement_vouchers(
    ctx: ToolContext,
    proposals: List[dict],
    month: Optional[str] = None,
    snapshotHash: Optional[str] = None,
    findingId: Optional[str] = None,
    note: Optional[str] = None,
) -> Any:
    """按银行流水 ID 创建待确认凭证清单。

    金额、方向、日期、摘要和对方均由数据库原始流水决定。模型只负责分类，
    因此无法通过自由改写 amount/title/occur_date 制造不存在的流水凭证。
    """
    from services.bank_service import (
        extract_statement_counterparty,
        get_statements,
        normalize_statement_description,
    )
    from services.category_service import list_categories

    if not proposals:
        return {"error": "proposals 为空"}

    statement_rows = await get_statements(ctx.company_id)
    statements = {str(row.get("id") or ""): row for row in statement_rows}
    categories = {str(row.get("name") or ""): row for row in await list_categories(ctx.company_id)}

    items: List[Dict[str, Any]] = []
    statement_ids: List[str] = []
    signed_net = Decimal("0")
    inflow_total = Decimal("0")
    outflow_total = Decimal("0")
    absolute_total = Decimal("0")

    for index, raw in enumerate(proposals):
        cleaned = _enforce_schema(
            raw,
            _STATEMENT_VOUCHER_PROPOSAL_SCHEMA,
            f"proposals[{index}]",
        )
        statement_id = str(cleaned.get("statementId") or "").strip()
        if not statement_id:
            raise ValueError(f"proposals[{index}] 缺少 statementId")
        if statement_id in statement_ids:
            raise ValueError(f"银行流水 {statement_id} 在同一清单中重复出现")
        statement = statements.get(statement_id)
        if statement is None:
            raise ValueError(f"银行流水 {statement_id} 不存在或不属于当前账套")
        if statement.get("matched_entry_ids") or statement.get("matched_entry_id"):
            raise ValueError(f"银行流水 {statement_id} 已完成对账，不能再次生成凭证")

        category = str(cleaned.get("category") or "").strip()
        category_row = categories.get(category)
        if category_row is None:
            raise ValueError(
                f"分类 {category!r} 不在当前账套的有效分类中；"
                "请使用系统分类（如股东投入、办公费、营业外收入、往来款）"
            )
        direction = str(category_row.get("direction") or "").strip()
        if direction not in ("income", "expense", "owner_transfer"):
            raise ValueError(f"分类 {category!r} 缺少有效方向")

        signed_amount = Decimal(str(statement.get("amount") or 0))
        if signed_amount == 0:
            raise ValueError(f"银行流水 {statement_id} 金额为 0，不能生成凭证")
        if direction == "income" and signed_amount <= 0:
            raise ValueError(f"流水 {statement_id} 是支出，不能使用收入分类 {category!r}")
        if direction == "expense" and signed_amount >= 0:
            raise ValueError(f"流水 {statement_id} 是收入，不能使用支出分类 {category!r}")

        normalized = normalize_statement_description(str(statement.get("description") or ""))
        title_parts = [part.strip() for part in normalized.split("|") if part.strip()]
        remarks = [
            re.sub(r"^(?:附言|备注|用途)\s*[:：]\s*", "", part)
            for part in title_parts
            if re.match(r"^(?:附言|备注|用途)\s*[:：]", part)
        ]
        title = (remarks[-1] if remarks else (title_parts[0] if title_parts else normalized)).strip()
        title = re.sub(r"\s*\(对方\s*[:：].*\)\s*$", "", title).strip()
        if not title or title == "-":
            title = f"银行流水 {str(statement.get('transaction_date') or '')[:10]}"
        merchant = extract_statement_counterparty(str(statement.get("description") or ""))
        occur_date = str(statement.get("transaction_date") or "")[:10]

        amount = signed_amount if direction == "owner_transfer" else abs(signed_amount)
        item: Dict[str, Any] = {
            "direction": direction,
            "amount": float(amount),
            "title": title[:120],
            "category": category,
            "payer": "company_account",
            "occurDate": occur_date,
            "paymentDate": occur_date,
            "statementId": statement_id,
            "sourceType": "bank_statement",
            "dedupFingerprint": statement_id,
            "linkedStatementId": statement_id,
        }
        if merchant:
            item["merchant"] = merchant
        if cleaned.get("note"):
            item["note"] = cleaned["note"]
        items.append(item)
        statement_ids.append(statement_id)

        signed_net += signed_amount
        if signed_amount > 0:
            inflow_total += signed_amount
        else:
            outflow_total += abs(signed_amount)
        absolute_total += abs(signed_amount)

    binding_inputs = [month, snapshotHash, findingId]
    if any(binding_inputs) and not all(binding_inputs):
        raise ValueError("结账修复必须同时提供 month、snapshotHash 和 findingId")

    closing_binding = None
    evidence: Dict[str, Any] = {
        "note": note or "",
        "created_via": "agent_chat_bank_statement_batch",
        "sourceType": "bank_statement",
        "statementIds": statement_ids,
        "totals": {
            "signedNet": float(signed_net),
            "inflow": float(inflow_total),
            "outflow": float(outflow_total),
            "absolute": float(absolute_total),
        },
    }
    if all(binding_inputs):
        from db.session import session_scope
        from services.closing_workflow_service import (
            closing_action_binding,
            validate_closing_action_binding,
        )

        payload = {"items": items}
        binding_request = {
            "month": str(month),
            "findingId": str(findingId),
            "baseSnapshotHash": str(snapshotHash),
            "periodVersion": None,
            "surface": ctx.source,
        }
        async with session_scope() as session:
            snapshot, finding = await validate_closing_action_binding(
                session,
                ctx.company_id,
                "propose_vouchers",
                payload,
                binding_request,
                user_id=ctx.user_id,
            )
        closing_binding = closing_action_binding(snapshot, finding, surface=ctx.source)
        evidence.update({
            "created_via": "agent_closing_repair",
            "closingBinding": closing_binding,
            "actionVersion": 1,
        })

    action = await agent_actions.create_action(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        action_type="propose_vouchers",
        title=f"银行流水待确认记账（{len(items)} 笔）",
        payload={"items": items},
        evidence=evidence,
        conversation_id=ctx.conversation_id,
    )
    return {
        "actionId": action["id"],
        "status": action["status"],
        "source": "bank_statement",
        "snapshotHash": snapshotHash if closing_binding else None,
        "findingId": findingId if closing_binding else None,
        "count": len(items),
        "totals": {
            "signedNet": float(signed_net),
            "inflow": float(inflow_total),
            "outflow": float(outflow_total),
            "absolute": float(absolute_total),
        },
        "items": [
            {
                "index": index,
                "statementId": item["statementId"],
                "title": item["title"],
                "direction": item["direction"],
                "amount": item["amount"],
                "occurDate": item["occurDate"],
                "category": item["category"],
            }
            for index, item in enumerate(items)
        ],
        "message": "待确认清单已创建，尚未生成凭证，也未入账",
        "onApproval": "仅在用户确认后生成凭证，并同步匹配清单内对应的银行流水；放弃则不生成凭证，流水保持未匹配",
    }


# ---------------------------------------------------------------------------
# 写工具（唯一）：产生待确认动作
# ---------------------------------------------------------------------------

async def _propose_voucher(
    ctx: ToolContext,
    direction: str,
    amount: float,
    title: str,
    category: str,
    occur_date: Optional[str] = None,
    payer: str = "company_account",
    merchant: Optional[str] = None,
    note: Optional[str] = None,
    evidence: Optional[dict] = None,
) -> Any:
    """创建凭证草稿（不入账），等待用户在界面上确认。"""
    from services.account_mapper import normalize_payer

    _assert_real_date(occur_date)
    payload: Dict[str, Any] = {
        "direction": direction,
        "amount": amount,
        "title": title,
        "category": category,
        "payer": normalize_payer(payer),
    }
    if occur_date:
        payload["occurDate"] = occur_date
    if merchant:
        payload["merchant"] = merchant
    action = await agent_actions.create_action(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        action_type="propose_voucher",
        title=title,
        payload=payload,
        evidence={
            "note": note or "",
            "user_request": evidence or "",
            "created_via": "agent_chat",
        },
    )
    return {
        "actionId": action["id"],
        "status": action["status"],
        "message": "草稿已创建，等待用户确认后才会入账",
    }


# ---------------------------------------------------------------------------
# 建账工具（新公司从零建账 / 代账旧账迁入；期初余额走网页工作台，不由对话生成）
# ---------------------------------------------------------------------------



async def _propose_company_setup(
    ctx: ToolContext,
    onboarding_type: str,
    name: str,
    tax_id: Optional[str] = None,
    company_type: str = "有限责任公司",
    taxpayer_type: str = "小规模纳税人",
    start_date: Optional[str] = None,
    industry: Optional[str] = None,
    vat_filing_cycle: str = "quarter",
    note: Optional[str] = None,
) -> Any:
    """创建建账/迁入主体配置草稿（待老板在卡片中确认后生效）。"""
    from services.account_service import get_company

    clean_name = str(name or "").strip()
    if not clean_name:
        return {"error": "缺少公司名称（name）"}

    resolved_start = str(start_date or "").strip() or f"{datetime.now().strftime('%Y-%m')}-01"
    _assert_real_date(resolved_start, "start_date ")

    current = await get_company(ctx.company_id) or {}
    is_from_demo = bool(current.get("isDemo"))
    clean_tax_id = str(tax_id or current.get("taxId") or "").strip().upper()

    payload: Dict[str, Any] = {
        "onboardingType": onboarding_type,
        "name": clean_name,
        "taxId": clean_tax_id,
        "type": company_type or "有限责任公司",
        "taxpayerType": taxpayer_type or "小规模纳税人",
        "startDate": resolved_start,
        "industry": str(industry or current.get("industry") or "").strip(),
        "vatFilingCycle": vat_filing_cycle or "quarter",
        "isFromDemo": is_from_demo,
    }
    title = "新公司从零建账确认" if onboarding_type == "new" else "代账/旧账迁入建账确认"
    action = await agent_actions.create_action(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        action_type="propose_company_setup",
        title=title,
        payload=payload,
        evidence={"note": note or "", "created_via": "agent_onboarding"},
    )
    return {
        "actionId": action["id"],
        "status": action["status"],
        "onboardingType": onboarding_type,
        "isFromDemo": is_from_demo,
        "message": "主体建账确认卡已创建，等待老板在卡片中核对并点击确认",
    }


# ---------------------------------------------------------------------------
# 报税作业包工具（M3，只读）
# ---------------------------------------------------------------------------

async def _get_filing_deadlines(ctx: ToolContext, upcoming_days: int = 45) -> Any:
    from services.filing_deadline_service import list_deadlines, refresh_deadlines

    try:
        await refresh_deadlines(ctx.company_id)
    except Exception:
        logger.exception("refresh deadlines failed")
    rows = await list_deadlines(ctx.company_id, upcoming_days=max(1, min(int(upcoming_days or 45), 120)))
    pending = [d for d in rows if d["status"] == "pending"]
    return {
        "pendingCount": len(pending),
        "deadlines": rows,
        "usage": "用户要报税时：根据税种匹配当期作业包（generate_filing_package），按作业包字段引导申报。",
    }


async def _generate_filing_package(ctx: ToolContext, period: str) -> Any:
    """生成申报作业包（实时计算）。period 形如 2026-Q3（季报）或 2026-09（月度）。"""
    period_str = str(period)
    cached = _get_analysis_cache("generate_filing_package", ctx.company_id, period_str)
    if cached is not None:
        return cached

    from services.filing_package_service import build_filing_package

    package = await build_filing_package(ctx.user_id, ctx.company_id, period_str)
    # 精简给模型的视图：保留决策需要的关键数据
    vat = package.get("vat") or {}
    cit = package.get("cit") or {}
    iit = package.get("businessIncomeIit") or {}
    result = {
        "period": package["period"],
        "summary": package["summary"],
        "vat": {
            "totalSales": vat.get("totalSales"),
            "isBelowThreshold": vat.get("isBelowThreshold"),
            "totalVatPayable": vat.get("totalVatPayable"),
            "surchargeTotal": vat.get("surchargeTotal"),
            "filingFields": vat.get("filingFields"),
            "blockingIssues": vat.get("blockingIssues"),
            "warnings": vat.get("warnings"),
            "deadline": vat.get("deadline"),
        },
        "cit": (
            {
                k: cit.get(k)
                for k in (
                    "revenue",
                    "cost",
                    "totalProfit",
                    "autoAdjustmentTotal",
                    "taxableIncome",
                    "preferentialTax",
                    "fields",
                    "note",
                    "deadline",
                )
            }
            if cit
            else None
        ),
        "citNotice": package.get("citNotice"),
        # 经营所得个税（个体户/个独）：查账征收 A 表预缴测算，5%-35% 五级累进
        "businessIncomeIit": (
            {
                k: iit.get(k)
                for k in (
                    "applicable", "period", "filingCycle", "entityType",
                    "revenue", "expenses", "profitTotal", "taxableIncome",
                    "tax", "halvingReduction", "halvingApplied",
                    "suggestedPayable", "previouslyPaid", "notes", "deadline",
                )
            }
            if iit
            else None
        ),
        "crossCheck": package.get("crossCheck"),
        "preferences": package.get("preferences"),
        "checklist": [
            {k: item.get(k) for k in ("taxKind", "name", "guide", "hint", "status", "dueDate", "deadlineId")}
            for item in package.get("checklist", [])
        ],
        "usage": (
            "引导用户报税：先报 summary 与核对结果；逐字段给申报表草稿（vat.filingFields）；"
            "清单事项完成后引导用户在「税务申报」页标记完成（AI 不能代操作）。"
            "若 citNotice.citApplicable 为 false（个人独资企业/个体工商户），"
            "不要报企业所得税，也不要按企业所得税预缴表引导；"
            "改用 businessIncomeIit 卡按 A 表（自然人电子税务局经营所得预缴）引导，"
            "数字为累计口径且个体户已含减半优惠（halvingApplied），"
            "口径声明：仅查账征收，核定征收不适用本系统。"
            "年度汇算（B 表）走 POST /api/tax-filing/iit-annual-assist 测算，"
            "截止次年 3 月 31 日；两处以上经营所得需合并 C 表。"
            "提交申报必须由用户本人在电子税务局完成。"
        ),
    }
    _set_analysis_cache("generate_filing_package", ctx.company_id, period_str, result)
    return result




# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

async def _search_tax_policy(ctx: ToolContext, query: str, include_content: bool = False) -> Any:
    from services.web_search_service import search_tax_policy

    return await search_tax_policy(
        query,
        count=5,
        need_content=include_content,
        user_id=ctx.user_id,
        company_id=ctx.company_id,
    )


def _obj(properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


TOOLS: List[Tool] = [
    Tool(
        name="get_month_summary",
        description="查询某个月份的科目余额汇总（收入/支出/各科目借贷发生额）。month 格式 YYYY-MM。",
        parameters=_obj({"month": {"type": "string", "description": "月份，格式 YYYY-MM", "pattern": MONTH_PATTERN}}, ["month"]),
        handler=_get_month_summary,
    ),
    Tool(
        name="list_entries",
        description=(
            "查询账目记录列表，可按月份过滤、按关键词（标题/分类/商户）搜索、按开票状态过滤。"
            "total 为过滤后总命中数（count 是本次返回数），超出 limit 时可调大 limit 或加过滤条件分页看。"
        ),
        parameters=_obj(
            {
                "month": {"type": "string", "description": "可选，月份格式 YYYY-MM", "pattern": MONTH_PATTERN},
                "keyword": {"type": "string", "description": "可选，关键词"},
                "billing_status": {"type": "string", "description": "可选，按开票/对账状态过滤，如 has_invoice/no_invoice/reconciled；传空字符串可只看未标记状态的账目"},
                "limit": {"type": "integer", "description": "返回条数上限，默认 30"},
            }
        ),
        handler=_list_entries,
    ),
    Tool(
        name="list_invoice_entries",
        description="查询带发票信息的账目（发票号、开票状态），可按开票状态过滤。",
        parameters=_obj(
            {
                "status": {"type": "string", "description": "可选，开票状态，如 invoiced/uninvoiced"},
                "limit": {"type": "integer", "description": "返回条数上限，默认 30"},
            }
        ),
        handler=_list_invoice_entries,
    ),
    Tool(
        name="list_bank_statements",
        description=(
            "查询银行/微信流水及与账目的匹配状态，可按月份、匹配状态过滤。"
            "对账场景用 matched=false 只看未匹配流水，避免全量拉取。"
        ),
        parameters=_obj(
            {
                "month": {"type": "string", "description": "可选，月份格式 YYYY-MM", "pattern": MONTH_PATTERN},
                "matched": {"type": "boolean", "description": "可选，true 只看已匹配 / false 只看未匹配；不传返回全部"},
                "limit": {"type": "integer", "description": "返回条数上限，默认 30"},
            }
        ),
        handler=_list_bank_statements,
    ),
    Tool(
        name="get_aging",
        description="查询应收(ar)/应付(ap)账龄汇总，回答'谁欠我钱/我欠谁钱'；group_by=partner 可按往来单位分组。",
        parameters=_obj(
            {
                "kind": {"type": "string", "enum": ["ar", "ap"], "description": "ar=应收，ap=应付"},
                "group_by": {
                    "type": "string",
                    "enum": ["none", "partner"],
                    "description": "可选，partner=按往来单位分组（返回 groups 数组）；默认 none",
                },
            },
            ["kind"],
        ),
        handler=_get_aging,
    ),
    Tool(
        name="get_advance_balances",
        description=(
            "查询股东/员工垫付台账（期初/垫付/报销冲销/期末未报余额），回答「老板/员工垫了多少钱、"
            "还有多少没报销、本期报销了多少」；用于清点 2241 其他应付款个人往来。month 格式 YYYY-MM，默认当前月。"
        ),
        parameters=_obj(
            {
                "month": {
                    "type": "string",
                    "description": "可选，账期月份 YYYY-MM，默认当前月",
                    "pattern": MONTH_PATTERN,
                },
            }
        ),
        handler=_get_advance_balances,
    ),
    Tool(
        name="list_private_expenses",
        description=(
            "查询老板小私账台账（老板个人垫付与代收明细及汇总统计）。"
            "支持按收支方向(expense垫付/income代收)、发票状态(no_invoice/has_invoice/substitute)、日期范围及垫付人过滤。"
        ),
        parameters=_obj(
            {
                "owner_name": {"type": "string", "description": "可选，记账人/垫付人姓名，默认本人"},
                "direction": {"type": "string", "enum": ["expense", "income"], "description": "可选，expense=个人垫付，income=代收款"},
                "invoice_status": {
                    "type": "string",
                    "enum": ["no_invoice", "has_invoice", "substitute"],
                    "description": "可选，发票状态：no_invoice=无发票，has_invoice=有发票，substitute=需冲抵",
                },
                "start_date": {"type": "string", "description": "起始日期 YYYY-MM-DD，可选", "pattern": DATE_PATTERN},
                "end_date": {"type": "string", "description": "截止日期 YYYY-MM-DD，可选", "pattern": DATE_PATTERN},
                "page": {"type": "integer", "description": "页码，默认 1"},
                "page_size": {"type": "integer", "description": "每页条数，默认 50"},
            }
        ),
        handler=_list_private_expenses,
    ),
    Tool(
        name="record_private_expense",
        description=(
            "记入老板小私账台账（老板个人垫付或代收备忘，非正式凭证）。"
            "当老板明确说'记私账'、'记小私账'、'个人垫付备忘'或无发票暂不报销时调用此工具。"
        ),
        parameters=_obj(
            {
                "title": {"type": "string", "description": "事项摘要，如'个人微信付打车费'"},
                "amount": {"type": "number", "description": "金额（元），正数", "exclusiveMinimum": 0},
                "direction": {"type": "string", "enum": ["expense", "income"], "description": "方向：expense=个人垫付（默认），income=代收款"},
                "category": {"type": "string", "description": "费用分类，如 交通费、餐饮费、办公费，可选"},
                "occur_date": {"type": "string", "description": "业务发生日期 YYYY-MM-DD，可选，默认当天", "pattern": DATE_PATTERN},
                "owner_name": {"type": "string", "description": "垫付人/记账人，默认'本人'，可选"},
                "private_account": {"type": "string", "description": "个人支付账户/工具，如'个人微信'、'个人支付宝'、'招行私卡'，可选"},
                "invoice_status": {
                    "type": "string",
                    "enum": ["no_invoice", "has_invoice", "substitute"],
                    "description": "发票状态，默认 no_invoice（无发票），可选",
                },
                "memo": {"type": "string", "description": "补充备注，可选"},
            },
            ["title", "amount"],
        ),
        handler=_record_private_expense,
    ),
    Tool(
        name="list_todos",
        description=(
            "查询当前待办与风险事项（缺票、待对账、待处理等），可按类型/级别过滤。"
            "只需某一类待办时务必带过滤参数，避免全量拉取。"
        ),
        parameters=_obj(
            {
                "type": {"type": "string", "description": "可选，按类型过滤：filing 申报 / bank 流水待处理 / receipt 补票据 / tax 开票确认 / task 任务 / risk 风险"},
                "level": {"type": "string", "enum": ["high", "medium", "low"], "description": "可选，按级别过滤"},
                "limit": {"type": "integer", "description": "返回条数上限，默认 50"},
            }
        ),
        handler=_list_todos,
    ),
    Tool(
        name="list_fixed_assets",
        description=(
            "查询固定资产台账（原值/累计折旧/净值/月折旧/剩余期数），回答「公司有哪些资产、"
            "每月折旧多少、某资产还剩几期折旧」；结账账实核对先拉这里。支持名称/编号关键词、状态、类别过滤与分页。"
        ),
        parameters=_obj(
            {
                "keyword": {"type": "string", "description": "可选，按资产名称或编号模糊匹配"},
                "status": {
                    "type": "string",
                    "enum": [
                        "in_use", "active", "idle", "fully_depreciated",
                        "pending_review", "draft", "disposing", "disposed", "scrapped",
                    ],
                    "description": "可选，in_use=在用（含正常/闲置/已提完）",
                },
                "category": {"type": "string", "description": "可选，按资产类别代码过滤（如 electronics）"},
                "page": {"type": "integer", "description": "可选，页码，默认 1"},
                "page_size": {"type": "integer", "description": "可选，每页条数（≤100），默认 50"},
            }
        ),
        handler=_list_fixed_assets,
    ),
    Tool(
        name="list_intangible_assets",
        description=(
            "查询无形资产台账（软件著作权/专利/商标/域名等，含原值/累计摊销/净值/剩余月数/到期日），"
            "回答「公司有哪些无形资产、每月摊销多少、权利证书何时到期、研发资本化资产」；结账账实核对入口。"
            "支持关键词、状态、分类、临期天数（expiring_in_days）过滤与分页。"
        ),
        parameters=_obj(
            {
                "keyword": {"type": "string", "description": "可选，按资产名称、编号或证书号模糊匹配"},
                "status": {
                    "type": "string",
                    "enum": [
                        "in_use",
                        "active",
                        "idle",
                        "fully_amortized",
                        "pending_review",
                        "draft",
                        "disposed",
                    ],
                    "description": "可选，in_use=在用（含正常/闲置/已提完）",
                },
                "category": {
                    "type": "string",
                    "description": "可选，按无形资产分类代码过滤（如 software, trademark, patent, copyright）",
                },
                "expiring_in_days": {
                    "type": "integer",
                    "description": "可选，筛选未来 N 天内权利到期的在用资产（如 30、90）",
                },
                "page": {"type": "integer", "description": "可选，页码，默认 1"},
                "page_size": {"type": "integer", "description": "可选，每页条数（≤100），默认 50"},
            }
        ),
        handler=_list_intangible_assets,
    ),
    Tool(
        name="search_books",
        description="按关键词全局搜索账目与会计科目。",
        parameters=_obj({"query": {"type": "string", "description": "关键词"}}, ["query"]),
        handler=_search_books,
    ),
    Tool(
        name="get_entry_detail",
        description="查看单条账目的完整详情（用于补全证据、核对发票/流水关联）。",
        parameters=_obj({"entry_id": {"type": "string", "description": "条目 ID"}}, ["entry_id"]),
        handler=_get_entry_detail,
    ),
    Tool(
        name="run_close_checks",
        description=(
            "运行指定月份的确定性结账检查，返回 closing.v1 快照、snapshotHash、periodVersion "
            "、可处理 findings 和 manualClose 网页入口。金额、笔数、状态与权威 ID 只能原样引用；"
            "最终锁账必须由用户在网页完成。"
        ),
        parameters=_obj(
            {
                "month": {"type": "string", "description": "月份，格式 YYYY-MM", "pattern": MONTH_PATTERN},
                "findingLimit": {"type": "integer", "description": "问题分页条数，默认 100，最大 100"},
                "findingCursor": {"type": "integer", "description": "问题分页游标，默认 0"},
            },
            ["month"],
        ),
        handler=_run_close_checks,
    ),
    Tool(
        name="list_unmatched",
        description="列出票/流/账差异：银行流水差异报告（银行有账上没有、账上有银行没有）+ AI 对账建议。",
        parameters=_obj(
            {
                "month": {"type": "string", "description": "月份，格式 YYYY-MM，缺省取当月", "pattern": MONTH_PATTERN},
                "limit": {"type": "integer", "description": "建议条数上限，默认 20"},
                "snapshotHash": {"type": "string", "description": "可选；传入 run_close_checks 返回的哈希时校验数据是否已变化"},
                "findingLimit": {"type": "integer", "description": "问题分页条数，默认 20，最大 100"},
                "findingCursor": {"type": "integer", "description": "问题分页游标，默认 0"},
            },
        ),
        handler=_list_unmatched,
    ),
    Tool(
        name="preview_accruals",
        description="预览本月待计提事项：折旧（未计提的在用固定资产及金额）、摊销（未计提的在用无形资产及金额）与工资（工资表状态）。",
        parameters=_obj(
            {
                "month": {"type": "string", "description": "月份，格式 YYYY-MM", "pattern": MONTH_PATTERN},
                "snapshotHash": {"type": "string", "description": "可选；结账修复前应传最新快照哈希"},
            },
            ["month"],
        ),
        handler=_preview_accruals,
    ),
    Tool(
        name="preview_asset_actions",
        description=(
            "预览资产处理事项：缺固定资产/无形资产卡片的原交易、待补提折旧/摊销期间、"
            "以及不能处理的原因。用于零基础老板一键确认资产处理前的检查。"
        ),
        parameters=_obj(
            {
                "month": {"type": "string", "description": "月份，格式 YYYY-MM", "pattern": MONTH_PATTERN},
                "snapshotHash": {"type": "string", "description": "可选；结账修复前应传最新快照哈希"},
            },
            ["month"],
        ),
        handler=_preview_asset_actions,
    ),
    Tool(
        name="propose_match",
        description=(
            "基于当前结账快照发起银行流水↔账目的待确认对账草稿。"
            "必须原样传 snapshotHash、findingId 和 list_unmatched 返回的权威 ID；"
            "创建后仍未匹配、未入账，用户确认后才执行。"
        ),
        parameters=_obj(
            {
                "statement_id": {"type": "string", "description": "银行流水 ID"},
                "entry_ids": {"type": "array", "items": {"type": "string"}, "description": "要匹配的账目 ID 列表"},
                "month": {"type": "string", "description": "结账月份 YYYY-MM", "pattern": MONTH_PATTERN},
                "snapshotHash": {"type": "string", "description": "run_close_checks 返回的 snapshotHash"},
                "findingId": {"type": "string", "description": "run_close_checks 返回的 findingId"},
                "note": {"type": "string", "description": "给用户的说明，可选"},
            },
            ["statement_id", "entry_ids", "month", "snapshotHash", "findingId"],
        ),
        handler=_propose_match,
    ),
    Tool(
        name="propose_accrual",
        description=(
            "基于当前结账快照发起计提确认草稿（kind=depreciation / amortization / salary）。"
            "必须传 snapshotHash 和 findingId；用户确认后才执行，当前不会生成任何凭证。"
        ),
        parameters=_obj(
            {
                "kind": {"type": "string", "enum": ["depreciation", "amortization", "salary"]},
                "month": {"type": "string", "description": "月份，格式 YYYY-MM", "pattern": MONTH_PATTERN},
                "snapshotHash": {"type": "string", "description": "run_close_checks 返回的 snapshotHash"},
                "findingId": {"type": "string", "description": "对应计提问题的 findingId"},
                "note": {"type": "string", "description": "给用户的说明，可选"},
            },
            ["kind", "month", "snapshotHash", "findingId"],
        ),
        handler=_propose_accrual,
    ),
    Tool(
        name="propose_asset_closure",
        description=(
            "基于当前结账快照发起资产处理确认草稿：缺卡建卡与折旧/摊销补提。"
            "必须传 snapshotHash 和 ASSET_CLOSURE finding 的 findingId；"
            "只生成待确认动作，老板确认后才执行。"
        ),
        parameters=_obj(
            {
                "month": {"type": "string", "description": "月份，格式 YYYY-MM", "pattern": MONTH_PATTERN},
                "snapshotHash": {"type": "string", "description": "run_close_checks 返回的 snapshotHash"},
                "findingId": {"type": "string", "description": "资产/折旧/摊销问题的 findingId"},
                "note": {"type": "string", "description": "给老板的说明，可选"},
            },
            ["month", "snapshotHash", "findingId"],
        ),
        handler=_propose_asset_closure,
    ),
    Tool(
        name="generate_close_report",
        description="生成指定月份的结账检查报告（markdown）：五项检查结果 + 票流账差异 + 计提状态 + 下一步建议。",
        parameters=_obj({"month": {"type": "string", "description": "月份，格式 YYYY-MM", "pattern": MONTH_PATTERN}}, ["month"]),
        handler=_generate_close_report,
    ),
    Tool(
        name="get_filing_deadlines",
        description="查询未来征期（默认 45 天内）：税种、期间、截止日、剩余天数。用户问'什么时候报税/该报什么'时调用。",
        parameters=_obj({"upcoming_days": {"type": "integer", "description": "向前看的天数，默认 45"}}),
        handler=_get_filing_deadlines,
    ),
    Tool(
        name="search_tax_policy",
        description=(
            "检索税务政策（官方白名单：税务总局/财政部/国务院/国家法律法规数据库，服务端强制，无法绕过）。"
            "仅两个用途：核对「政策要点卡」之外的税务口径；临期政策查延续公告。"
            "query 只传政策关键词（税种/主题/优惠名/年份，如'设备器具 一次性扣除 2027 延续'），"
            "严禁包含公司名、金额、人名等经营信息。include_content=true 时结果附带网页正文（默认关闭省 token）。"
            "ok=false（disabled/timeout/empty/rate_limited 等）时按'未核实'处理：明确告知用户并给自查入口，禁止编造。"
        ),
        parameters=_obj(
            {
                "query": {"type": "string", "description": "政策关键词，多个用空格分隔；禁止公司名/金额/人名等经营信息"},
                "include_content": {"type": "boolean", "description": "可选，true 时结果附带网页正文；默认 false"},
            },
            ["query"],
        ),
        handler=_search_tax_policy,
    ),
    Tool(
        name="generate_filing_package",
        description=(
            "生成申报作业包：增值税申报表逐字段草稿、企业所得税预缴估算、账↔票↔流水三方核对、"
            "优惠勾选建议、申报事项清单。period 形如 2026-Q3（小规模季报）或 2026-09。"
        ),
        parameters=_obj({"period": {"type": "string", "description": "期间，YYYY-Qn（如 2026-Q3）或 YYYY-MM", "pattern": PERIOD_PATTERN}}, ["period"]),
        handler=_generate_filing_package,
    ),
    Tool(
        name="get_company_profile",
        description="读取本公司画像（行业、纳税人身份、常用分类习惯、往来单位名单、在职员工名单）。",
        parameters=_obj({}),
        handler=_get_company_profile,
    ),
    Tool(
        name="update_company_profile",
        description=(
            "更新公司画像（长期记忆，用户说'以后XX都记YY'时调用）。"
            "merchant_category 形如 {\"UU跑腿\": \"交通费\"}；industry/ taxpayer_type / notes 可选。"
        ),
        parameters=_obj(
            {
                "industry": {"type": "string", "description": "行业描述，可选"},
                "taxpayer_type": {"type": "string", "enum": ["small_scale", "general"], "description": "纳税人身份，可选"},
                "merchant_category": {"type": "object", "description": "商家→分类映射，可选"},
                "notes": {"type": "string", "description": "其他要记住的偏好，可选"},
            }
        ),
        handler=_update_company_profile,
    ),
    Tool(
        name="propose_voucher",
        description=(
            "创建记账凭证草稿（不会直接入账，用户确认后才生效）。"
            "direction=income/expense/owner_transfer；amount 为正数；"
            "category 使用系统常用分类（如 交通费、办公费、餐饮费、咨询服务收入 等）；"
            "occur_date 为业务发生日期 YYYY-MM-DD。信息不齐时先问用户，不要猜。"
        ),
        parameters=_obj(
            {
                "direction": {"type": "string", "enum": ["income", "expense", "owner_transfer"]},
                "amount": {"type": "number", "description": "金额（元），正数", "exclusiveMinimum": 0},
                "title": {"type": "string", "description": "事项摘要，如'打车去客户处'"},
                "category": {
                    "type": "string",
                    "description": "费用/收入一级分类（如：交通费、餐饮费、办公费、通讯费、差旅费、咨询服务收入、技术服务收入）。不确定时可先调用 get_company_profile 读取公司分类习惯",
                },
                "occur_date": {"type": "string", "description": "业务发生日期 YYYY-MM-DD，可选", "pattern": DATE_PATTERN},
                "payer": _PAYER_SCHEMA,
                "merchant": {"type": "string", "description": "对方商户/单位，可选"},
                "note": {"type": "string", "description": "给用户的备注说明，可选"},
                "evidence": {"type": "string", "description": "用户原话中与该笔相关的关键信息，可选"},
            },
            ["direction", "amount", "title", "category"],
        ),
        handler=_propose_voucher,
    ),
    Tool(
        name="propose_vouchers",
        description=(
            "批量创建用户口述或单据识别出的多条记账清单（一张清单，用户一次性确认；可逐行排除或修改分类）。"
            "仅适用于用户明确说出的业务；禁止用于银行流水。银行流水必须使用 propose_statement_vouchers，"
            "否则金额和日期可能被改写。"
            "每个元素字段与 propose_voucher 相同。信息不齐或没把握的行不要混进来，"
            "先单独向用户确认后再补一份草稿。"
        ),
        parameters=_obj(
            {
                "proposals": {
                    "type": "array",
                    "description": "草稿列表，每项含 direction/amount/title/category 等字段",
                    "items": _VOUCHER_PROPOSAL_SCHEMA,
                },
                "note": {"type": "string", "description": "给用户的整体备注，可选"},
            },
            ["proposals"],
        ),
        handler=_propose_vouchers,
    ),
    Tool(
        name="propose_statement_vouchers",
        description=(
            "按未匹配银行流水创建待确认记账清单。每个元素只需传 statementId 和有效 category；"
            "金额、收支方向、日期、摘要和对方均由服务端从银行流水原始记录读取，模型不得也不能改写。"
            "创建后仍只是待确认动作，尚未生成凭证、尚未入账；用户确认后才生成凭证并同步匹配流水。"
            "凡准备处理 list_unmatched 中的流水，必须使用本工具。用于结账修复时必须同时传 "
            "month、snapshotHash、findingId；日常流水记账可省略这三个字段。"
        ),
        parameters=_obj(
            {
                "proposals": {
                    "type": "array",
                    "description": "银行流水分类列表，每项含 statementId/category，可选 note",
                    "items": _STATEMENT_VOUCHER_PROPOSAL_SCHEMA,
                },
                "month": {"type": "string", "description": "结账修复时必填，YYYY-MM", "pattern": MONTH_PATTERN},
                "snapshotHash": {"type": "string", "description": "结账修复时必填，run_close_checks 返回的快照哈希"},
                "findingId": {"type": "string", "description": "结账修复时必填，对应流水问题的 findingId"},
                "note": {"type": "string", "description": "给用户的整体备注，可选"},
            },
            ["proposals"],
        ),
        handler=_propose_statement_vouchers,
    ),
    Tool(
        name="propose_company_setup",
        description=(
            "发起新公司从零建账或从其他代账/软件转入的主体建账确认卡（老板在卡片上核对/补填并确认后生效；"
            "若当前为演示账套会自动新建正式账套并切换）。"
            "onboarding_type=new（新成立公司从零记账）或 migration（从其他代账或财务软件转入）；"
            "start_date 为启用日期 YYYY-MM-DD（新公司取成立日或当月1日，代账转入取交接起账月1日，如 2026-10-01）。"
        ),
        parameters=_obj(
            {
                "onboarding_type": {
                    "type": "string",
                    "enum": ["new", "migration"],
                    "description": "new=新公司从零建账，migration=从其他代账/旧账转入",
                },
                "name": {"type": "string", "description": "公司/主体全称"},
                "tax_id": {"type": "string", "description": "18 位统一社会信用代码，可选（不确定可留空让老板在卡片补填）"},
                "company_type": {
                    "type": "string",
                    "enum": ["有限责任公司", "个人独资企业", "个体工商户"],
                    "description": "主体类型，默认 有限责任公司",
                },
                "taxpayer_type": {
                    "type": "string",
                    "enum": ["小规模纳税人", "一般纳税人"],
                    "description": "纳税人身份，默认 小规模纳税人",
                },
                "start_date": {
                    "type": "string",
                    "description": "启用日期 YYYY-MM-DD，可选（默认当月1日）",
                    "pattern": DATE_PATTERN,
                },
                "industry": {"type": "string", "description": "所属行业，可选"},
                "vat_filing_cycle": {
                    "type": "string",
                    "enum": ["quarter", "month"],
                    "description": "增值税申报周期：quarter=按季（小规模默认），month=按月",
                },
                "note": {"type": "string", "description": "给老板的建账提示，可选"},
            },
            ["onboarding_type", "name"],
        ),
        handler=_propose_company_setup,
    ),
]

TOOLS_BY_NAME: Dict[str, Tool] = {t.name: t for t in TOOLS}
TOOL_SCHEMAS: List[Dict[str, Any]] = [t.openai_schema() for t in TOOLS]

# 产品内 AI 与 MCP 共用同一组结账检查/修复草稿工具。最终锁账没有注册为工具，
# 仍只能由登录用户在 /closing-wizard 发起。
PRODUCT_AGENT_DISABLED_TOOLS = frozenset()
PRODUCT_AGENT_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    schema
    for schema in TOOL_SCHEMAS
    if schema["function"]["name"] not in PRODUCT_AGENT_DISABLED_TOOLS
]


async def execute_tool(name: str, arguments: Dict[str, Any], ctx: ToolContext) -> Any:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise ValueError(f"unknown tool: {name}")
    return await tool.handler(ctx, **_enforce_schema(arguments, tool.parameters or {}))


def _enforce_schema(value: Any, schema: Dict[str, Any], path: str = "参数") -> Any:
    """按 JSON Schema 子集在服务端强制校验（required/未知字段/pattern/enum/type/有限性/下界）。

    schema 会随 tools/list 广播给客户端，但 FastMCP 不在服务端执行校验：
    不守约的客户端（或乱传参的 AI）会把脏参数直接打进 handler——枚举、负金额、
    非法月份、日历不存在的日期都会变成待确认草稿（2026-10-25 对抗测试实测）。
    MCP 与产品内 agent loop 均经 execute_tool，一处强制全部受益。
    """
    if "properties" in schema or schema.get("type") == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} 应为对象")
        props = schema.get("properties") or {}
        for key in schema.get("required") or []:
            if value.get(key) is None:
                raise ValueError(f"{path} 缺少必填字段: {key}")
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            spec = props.get(key)
            if spec is None:
                # 未声明字段：按 JSON Schema 惯例忽略，绝不透传给 handler
                continue
            cleaned[key] = _enforce_schema(item, spec, f"{path}.{key}") if item is not None else item
        return cleaned
    if schema.get("type") == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} 应为数组")
        items_spec = schema.get("items")
        if not items_spec:
            return value
        return [_enforce_schema(v, items_spec, f"{path}[{i}]") for i, v in enumerate(value)]
    if value is None:
        return value
    stype = schema.get("type")
    if stype == "string":
        if not isinstance(value, str):
            raise ValueError(f"{path} 应为字符串，收到 {type(value).__name__}")
        pattern = schema.get("pattern")
        if pattern and not re.fullmatch(pattern, value):
            raise ValueError(f"{path} 格式不符（应为 {pattern}）：{value!r}")
    elif stype == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{path} 必须是有限数字，收到 {value!r}")
        minimum = schema.get("exclusiveMinimum")
        if minimum is not None and value <= minimum:
            raise ValueError(f"{path} 必须大于 {minimum}，收到 {value!r}")
        min_inc = schema.get("minimum")
        if min_inc is not None and value < min_inc:
            raise ValueError(f"{path} 不能小于 {min_inc}，收到 {value!r}")
    elif stype == "integer":
        is_int = isinstance(value, int) and not isinstance(value, bool)
        if not is_int and not (isinstance(value, float) and value.is_integer()):
            raise ValueError(f"{path} 应为整数，收到 {value!r}")
    elif stype == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{path} 应为布尔值，收到 {type(value).__name__}")
    if schema.get("enum") and value not in schema["enum"]:
        raise ValueError(f"{path} 只能取 {'/'.join(str(x) for x in schema['enum'])}，收到 {value!r}")
    return value
