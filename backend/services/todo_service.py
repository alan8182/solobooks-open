# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Server-derived todo list with persisted handling state (async SQLite)."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from decimal import Decimal

from config import TODO_SMALL_AMOUNT_THRESHOLD
from core.errors import AccountingError
from services.database import get_conn
from services.entry_service import list_entries
from core.money import ZERO, money, money_text

logger = logging.getLogger(__name__)

# 待办按事项分类（rule_id -> category），前端据此分组展示。
# unmatched_statement / filing_deadline 在各自构造处直接写死 category。
TODO_CATEGORIES: Dict[str, str] = {
    "filing_deadline": "filing",
    "unmatched_statement": "match",
    "missing_document": "receipt",
    "invoice_unpaid": "reimburse",
    "owner_transfer_nature": "reimburse",
    "income_invoice": "billing",
    "entertainment_limit": "risk",
    "ad_limit": "risk",
    "fixed_asset_depreciation": "risk",
    "intangible_asset_amortization": "intangible",
    "intangible_review": "intangible",
    "intangible_expiry": "intangible",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_schema() -> None:
    conn = await get_conn()
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS todo_states ("
            "  todo_key TEXT PRIMARY KEY,"
            "  user_id TEXT NOT NULL,"
            "  company_id TEXT NOT NULL,"
            "  done INTEGER NOT NULL DEFAULT 0,"
            "  dismissed INTEGER NOT NULL DEFAULT 0,"
            "  created_at TEXT NOT NULL,"
            "  updated_at TEXT NOT NULL"
            ")"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_todo_states_user_company ON todo_states(user_id, company_id)"
        )
        await conn.commit()
    finally:
        await conn.close()


async def _ensure_db_ready() -> None:
    await _ensure_schema()


def _amount(value: Any) -> Decimal:
    try:
        return money(value or ZERO)
    except (TypeError, ValueError):
        return ZERO


def _has_document(entry: Dict[str, Any]) -> bool:
    status = str(entry.get("billingStatus") or "")
    if status in {"issued", "has_invoice", "has_receipt", "no_invoice"}:
        return True
    return bool(entry.get("invoiceType") or entry.get("invoiceNumber"))


def _has_invoice(entry: Dict[str, Any]) -> bool:
    status = str(entry.get("billingStatus") or "")
    if status in {"issued", "has_invoice"}:
        return True
    return bool(entry.get("invoiceType") or entry.get("invoiceNumber"))


# ponytail: 平台提现、银行结息、注资及往来款不属于增值税应税销售收入，静默豁免开票确认，避免误导无财务背景老板
NON_INVOICING_INCOME_CATEGORIES: Set[str] = {
    "微信支付结算",
    "支付宝结算",
    "平台提现结算",
    "利息收入",
    "股东投入",
    "老板投入",
    "资本公积",
    "收到退税",
    "税费返还",
    "其他应付款",
    "其他应收款",
    "往来款",
    "政府补助",
}
NON_INVOICING_ACCOUNT_PREFIXES = ("1012", "5603", "4001", "4002", "2241", "1221")
NON_INVOICING_TITLE_RE = re.compile(r"提现到账|平台提现|银行结息|存款利息|股东投入|股东注资|退税")

# 常见商户简称与发票开票方全称同义词映射（用于无票支出 ↔ 待报销发票自动撮合）
VENDOR_SYNONYM_GROUPS: Tuple[Tuple[str, ...], ...] = (
    ("deepseek", "深度求索"),
    ("微信", "腾讯", "财付通", "wechat", "tenpay"),
    ("支付宝", "蚂蚁", "阿里巴巴", "阿里云", "淘宝", "天猫"),
    ("飞书", "字节", "火山引擎", "抖音"),
    ("高德", "滴滴", "打车", "出行"),
    ("京东", "网银在线"),
    ("百度", "文心"),
    ("智谱", "华章", "bigmodel", "glm"),
)

# 进程级 LLM 建议缓存：(company_id, todo_key) -> aiSuggestion dict（免 DDL 迁移，兼容 SQLite/PG）
_AI_SUGGESTION_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}


def _silent_non_invoice_income_reason(entry: Dict[str, Any]) -> Optional[str]:
    """若该笔收入属于内部资金划转/结息/注资等非开票收入，返回静默处理原因；否则返回 None。"""
    category = str(entry.get("category") or "").strip()
    account_code = str(entry.get("accountCode") or "").strip()
    title = str(entry.get("title") or entry.get("merchant") or "").strip()
    if (
        category in ("微信支付结算", "支付宝结算", "平台提现结算")
        or account_code.startswith("1012")
        or re.search(r"提现到账|平台提现", title)
    ):
        return "属于第三方支付平台提现到银行卡的内部资金划转，并非新增销售收入，已自动豁免开票确认"
    if (
        category == "利息收入"
        or account_code.startswith("5603")
        or re.search(r"银行结息|存款利息", title)
    ):
        return "属于银行存款结息收入，无需向银行开具发票，已自动静默核验"
    if (
        category in NON_INVOICING_INCOME_CATEGORIES
        or account_code.startswith(NON_INVOICING_ACCOUNT_PREFIXES)
        or NON_INVOICING_TITLE_RE.search(title)
    ):
        return "属于非经营性往来或资本金流入，不属于应税销售收入，已自动豁免开票确认"
    return None


def _parse_iso_date(val: Any) -> Optional[date]:
    text = str(val or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _semantic_match_score(exp_entry: Dict[str, Any], inv_entry: Dict[str, Any]) -> Tuple[int, str]:
    """计算同金额支出与发票的语义匹配得分及匹配依据说明。"""
    exp_text = " ".join(
        str(exp_entry.get(k) or "") for k in ("title", "merchant", "remark", "category")
    ).lower()
    inv_text = " ".join(
        str(inv_entry.get(k) or "") for k in ("title", "merchant", "seller_name", "item_name", "remark", "category")
    ).lower()
    score = 10  # 金额完全相等基础分
    matched_cues: List[str] = []

    for group in VENDOR_SYNONYM_GROUPS:
        exp_hits = [w for w in group if w in exp_text]
        inv_hits = [w for w in group if w in inv_text]
        if exp_hits and inv_hits:
            score += 50
            matched_cues.append(f"业务关键词「{exp_hits[0]} ↔ {inv_hits[0]}」高度吻合")
            break

    # 中文二字以上词块重合
    exp_tokens = {m.group(0) for m in re.finditer(r"[\u4e00-\u9fa5]{2,4}|[a-z0-9]{3,}", exp_text)}
    stop_words = {"发票", "有限公司", "科技", "技术", "支出", "相关", "费用", "报销", "业务", "说明", "支付"}
    common_tokens = (exp_tokens - stop_words) & {
        m.group(0) for m in re.finditer(r"[\u4e00-\u9fa5]{2,4}|[a-z0-9]{3,}", inv_text)
    }
    if common_tokens:
        score += 20 * len(common_tokens)
        if not matched_cues:
            matched_cues.append(f"包含共同业务特征词「{'/'.join(sorted(common_tokens)[:2])}」")

    d_exp = _parse_iso_date(exp_entry.get("occurDate") or exp_entry.get("date"))
    d_inv = _parse_iso_date(inv_entry.get("occurDate") or inv_entry.get("invoice_date") or inv_entry.get("date"))
    if d_exp and d_inv:
        day_diff = abs((d_inv - d_exp).days)
        if day_diff > 65:
            return (-1, "")
        score += max(0, 30 - day_diff)
    return (score, "；".join(matched_cues))


def _default_ai_suggestion(todo: Dict[str, Any], entry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """为每条待办生成零延迟的默认智能建议与大白话理由（无 LLM 时亦可直接使用）。"""
    meta = todo.get("meta") or {}
    rule_id = str(meta.get("ruleId") or "")
    amount = str(todo.get("amount") or "0.00")
    title = str((entry or {}).get("title") or todo.get("title") or "")

    if rule_id == "invoice_unpaid":
        if meta.get("candidateExpenseId"):
            exp_title = str(meta.get("candidateExpenseTitle") or "同金额支出")
            exp_date = str(meta.get("candidateExpenseDate") or "")
            exp_remark = str(meta.get("candidateExpenseRemark") or "").strip()
            cue = str(meta.get("candidateMatchCue") or "").strip()
            remark_snippet = f"（备注：{exp_remark[:45]}）" if exp_remark else ""
            cue_snippet = f"，且{cue}" if cue else ""
            return {
                "action": "link_invoice_expense",
                "label": f"关联「{exp_title}」并核销",
                "reason": (
                    f"系统发现账套中有一笔同金额无票支出「{exp_title}」（¥{amount}，{exp_date}）{remark_snippet}"
                    f"与本张发票金额完全一致{cue_snippet}。一键关联后将自动把发票号补齐到该笔支出，并冲销重复的发票挂账。"
                ),
                "confidence": "high",
                "source": "rule",
            }
        return {
            "action": "set_payer_boss",
            "label": "记为老板垫付",
            "reason": (
                f"公户流水中未找到 ¥{amount} 的对应扣款，小微企业常见由老板个人微信/支付宝直接扫码支付；"
                "记为「老板垫付」后即完成核销，后续公户可随时免税打款报销给老板。"
            ),
            "confidence": "medium",
            "source": "rule",
        }

    if rule_id == "missing_document":
        return {
            "action": "mark_no_invoice",
            "label": "按无票支出入账",
            "reason": (
                f"该笔支出（¥{amount}）已从银行或账面发生，若短期内无法补开发票，可一键标记为「无票支出」凭付款记录入账，"
                "系统将不再反复催办（小微企业微利/亏损区间通常不影响当期现金税负）。"
            ),
            "confidence": "medium",
            "source": "rule",
        }

    if rule_id == "income_invoice":
        return {
            "action": "mark_no_invoice_income",
            "label": "按未开票收入申报（不开票）",
            "reason": (
                f"若客户未索要发票，可直接标记为「不开票（未开票收入）」。小规模纳税人季度销售额 30 万元以内，"
                "无论开具普票还是按未开票收入申报，均全额免征增值税。"
            ),
            "confidence": "high",
            "source": "rule",
        }

    if rule_id == "owner_transfer_nature":
        return {
            "action": "set_owner_loan",
            "label": "记为股东往来",
            "reason": f"将「{title}」定性为股东往来款，清晰记录老板与公司的资金拆借关系。",
            "confidence": "medium",
            "source": "rule",
        }

    if rule_id == "unmatched_statement":
        return {
            "action": "go_match_wizard",
            "label": "去结账向导一键确认",
            "reason": "该笔银行流水尚未生成记账凭证，前往结账向导点选用途即可完成入账。",
            "confidence": "medium",
            "source": "rule",
        }

    return {
        "action": "default",
        "label": "按指引处理",
        "reason": str(todo.get("desc") or "请根据实际业务情况确认该事项。"),
        "confidence": "medium",
        "source": "rule",
    }


def _todo(entry: Dict[str, Any], rule_id: str, type_: str, level: str, title: str, desc: str, **extra: Any) -> Dict[str, Any]:
    entry_id = str(entry.get("id") or "")
    todo_key = f"entry:{entry_id}:{rule_id}"
    return {
        "id": todo_key, "todoKey": todo_key, "type": type_,
        "category": TODO_CATEGORIES.get(rule_id, "other"),
        "level": level, "priority": level,
        "title": title, "desc": desc, "done": False, "dismissed": False,
        "amount": money_text(_amount(entry.get("amount"))),
        "deadline": extra.get("deadline"),
        "companyId": entry.get("companyId") or entry.get("company_id"),
        "meta": {
            "entryId": entry_id,
            "ruleId": rule_id,
            # 前端据此判断编辑类按钮是否可用（已过账凭证不能改，只能看）
            "entryStatus": str(entry.get("voucherStatus") or ""),
            **extra.get("meta", {}),
        },
    }


def _derive_entry_todos(
    entries: List[Dict[str, Any]],
    matched_entry_ids: Set[str] = None,
    linked_asset_entry_ids: Set[str] = None,
    linked_intangible_entry_ids: Set[str] = None,
) -> List[Dict[str, Any]]:
    if matched_entry_ids is None:
        matched_entry_ids = set()
    if linked_asset_entry_ids is None:
        linked_asset_entry_ids = set()
    if linked_intangible_entry_ids is None:
        linked_intangible_entry_ids = set()
    todos: List[Dict[str, Any]] = []
    # 冲销红字凭证与原凭证完全抵消：红字本身不产生待办，原凭证也不再催办
    reversed_ids = {
        str(e.get("reversalOf") or "")
        for e in entries
        if str(e.get("entryType") or "") == "reversal"
    } - {""}
    threshold = Decimal(str(TODO_SMALL_AMOUNT_THRESHOLD))

    # 预先收集潜在的无票支出候选与发票候选，用于同金额/跨名称语义智能撮合
    missing_doc_candidates: List[Dict[str, Any]] = []
    invoice_unpaid_candidates: List[Dict[str, Any]] = []

    for entry in entries:
        entry_id = str(entry.get("id") or "")
        if not entry_id:
            continue
        if (
            str(entry.get("entryType") or "") in ("reversal", "opening", "closing")
            or entry.get("isClosingTransfer")
            or entry_id in reversed_ids
        ):
            continue
        title = str(entry.get("title") or entry.get("merchant") or "这笔业务")
        category = str(entry.get("category") or "")
        direction = str(entry.get("direction") or "")
        payer = str(entry.get("payer") or "")
        amount = _amount(entry.get("amount"))
        account_code = str(entry.get("accountCode") or "")
        source = str(entry.get("source") or "")

        small_amount = abs(amount) < threshold
        is_archive_only = bool(entry.get("is_archive_only") or entry.get("isArchiveOnly"))

        # 1. 待补票据（支出缺少发票）候选
        if (direction == "expense" and source != "invoice_import"
                and not _has_document(entry) and not small_amount):
            missing_doc_candidates.append(entry)

        # 2. 待报销/付款（发票未关联流水支出）候选
        # 垫付去向已确认（老板/员工垫付）的发票不再催付款——核销由公户打款给垫付人时完成，
        # 且结账向导「已确认入账去向」区已在跟踪，再催就是让用户重复确认同一件事
        payer_str = payer
        advancement_confirmed = payer_str == "boss_personal" or payer_str.startswith("employee_personal")
        if (source == "invoice_import" and entry_id not in matched_entry_ids
                and not is_archive_only and not advancement_confirmed
                and str(entry.get("billingStatus") or "") != "reconciled"):
            invoice_unpaid_candidates.append(entry)

        # 3. 待开票（经营性收入确认）：静默豁免平台提现、银行结息、股东注资及往来款
        if direction == "income" and not _has_invoice(entry) \
                and str(entry.get("billingStatus") or "") != "no_invoice" and not small_amount:
            # 若属于非经营性流入，静默跳过不生成待办，防无财务背景老板误点开票
            if not _silent_non_invoice_income_reason(entry):
                t = _todo(entry, "income_invoice", "tax", "medium",
                          f"{title} - 开票状态确认", "收入流水需要确认是否已开票，便于后续申报。")
                t["meta"]["aiSuggestion"] = _default_ai_suggestion(t, entry)
                todos.append(t)

        if (category == "固定资产" or account_code.startswith("1601")) and entry_id not in linked_asset_entry_ids:
            t = _todo(entry, "fixed_asset_depreciation", "task", "high",
                      f"{title} - 固定资产需确认折旧方案", f"¥{amount:,.0f}，固定资产需要按使用年限计提折旧。",
                      meta={"action": "confirm_asset"})
            t["meta"]["aiSuggestion"] = _default_ai_suggestion(t, entry)
            todos.append(t)

        if (category == "无形资产" or account_code.startswith("1701")) and entry_id not in linked_intangible_entry_ids:
            t = _todo(entry, "intangible_asset_amortization", "task", "high",
                      f"{title} - 无形资产需确认摊销方案", f"¥{amount:,.0f}，无形资产需要按使用年限摊销。",
                      meta={"action": "confirm_intangible"})
            t["meta"]["aiSuggestion"] = _default_ai_suggestion(t, entry)
            todos.append(t)

        if "招待" in category and amount >= 500:
            t = _todo(entry, "entertainment_limit", "risk", "medium",
                      f"{title} - 招待费税前扣除复核", "招待费扣除比例受限，建议补充业务招待对象和事由。")
            t["meta"]["aiSuggestion"] = _default_ai_suggestion(t, entry)
            todos.append(t)

        if "广告" in category and amount >= 10000:
            t = _todo(entry, "ad_limit", "risk", "medium",
                      f"{title} - 广告费扣除比例复核", "广告宣传费金额较高，建议复核税前扣除比例。")
            t["meta"]["aiSuggestion"] = _default_ai_suggestion(t, entry)
            todos.append(t)

        if (payer == "boss_personal"
                and source != "invoice_import"
                and str(entry.get("billingStatus") or "") != "reconciled"
                and category not in ("其他应收款", "其他应付款", "往来款")):
            t = _todo(entry, "owner_transfer_nature", "risk", "medium",
                      f"{title} - 股东垫付性质确认", "请确认这笔款项是报销、借款还是资本投入。")
            t["meta"]["aiSuggestion"] = _default_ai_suggestion(t, entry)
            todos.append(t)

    # ── 跨单据智能撮合：无票支出 ↔ 待报销发票 ───────────────────────────────
    # 同金额且在合理时间窗口（<=65天）内，二合一为单一发票待办卡片，抑制重复的待补票据待办
    suppressed_missing_doc_ids: Set[str] = set()
    matched_expense_for_invoice: Dict[str, Tuple[Dict[str, Any], str]] = {}

    for inv in invoice_unpaid_candidates:
        inv_amt = _amount(inv.get("amount"))
        best_exp = None
        best_score = -1
        best_cue = ""
        for exp in missing_doc_candidates:
            if str(exp.get("id")) in suppressed_missing_doc_ids:
                continue
            if abs(inv_amt - _amount(exp.get("amount"))) <= Decimal("0.01"):
                score, cue = _semantic_match_score(exp, inv)
                if score > best_score:
                    best_score = score
                    best_exp = exp
                    best_cue = cue
        if best_exp is not None and best_score >= 10:
            suppressed_missing_doc_ids.add(str(best_exp.get("id")))
            matched_expense_for_invoice[str(inv.get("id"))] = (best_exp, best_cue)

    # 生成待付款/待报销发票待办（若撮合到无票支出，携带候选支出信息与一键关联建议）
    for inv in invoice_unpaid_candidates:
        inv_id = str(inv.get("id"))
        inv_title = str(inv.get("title") or inv.get("merchant") or "发票")
        inv_amount = _amount(inv.get("amount"))
        meta_extra: Dict[str, Any] = {}
        paired_info = matched_expense_for_invoice.get(inv_id)
        if paired_info:
            exp_entry, cue = paired_info
            exp_title = str(exp_entry.get("title") or exp_entry.get("merchant") or "支出")
            exp_date = str(exp_entry.get("occurDate") or exp_entry.get("date") or "")[:10]
            exp_remark = str(exp_entry.get("remark") or "").strip()
            meta_extra = {
                "candidateExpenseId": str(exp_entry.get("id")),
                "candidateExpenseTitle": exp_title,
                "candidateExpenseDate": exp_date,
                "candidateExpenseRemark": exp_remark,
                "candidateMatchCue": cue,
            }
            desc = f"¥{inv_amount:,.2f}，发现同金额无票支出「{exp_title}」（{exp_date}），疑似同一笔业务，可一键关联核销。"
        else:
            desc = f"¥{inv_amount:,.0f}，此发票在流水中未找到对应支出。请确认是否已付款，或将其与银行流水匹配。"

        t = _todo(inv, "invoice_unpaid", "task", "high",
                  f"{inv_title} - 发票待付款/报销", desc, meta=meta_extra)
        t["meta"]["aiSuggestion"] = _default_ai_suggestion(t, inv)
        todos.append(t)

    # 未被发票撮合的独立支出，正常保留待补票据待办
    for exp in missing_doc_candidates:
        exp_id = str(exp.get("id"))
        if exp_id in suppressed_missing_doc_ids:
            continue
        exp_title = str(exp.get("title") or exp.get("merchant") or "这笔业务")
        exp_amount = _amount(exp.get("amount"))
        t = _todo(exp, "missing_document", "receipt", "high",
                  f"{exp_title} - 补充票据", f"¥{exp_amount:,.0f}，这笔支出需要补齐发票或注明无票原因。")
        t["meta"]["aiSuggestion"] = _default_ai_suggestion(t, exp)
        todos.append(t)

    return todos


def derive_silent_resolved_items(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """提取已由系统确定性规则静默核验/豁免的事项清单（供前端轻量透出留痕）。"""
    items: List[Dict[str, Any]] = []
    for entry in entries:
        if str(entry.get("entryType") or "") in ("reversal", "opening", "closing") or entry.get("isClosingTransfer"):
            continue
        direction = str(entry.get("direction") or "")
        if direction == "income":
            reason = _silent_non_invoice_income_reason(entry)
            if reason:
                amount = float(abs(_amount(entry.get("amount"))))
                date_str = str(entry.get("occurDate") or entry.get("date") or "")[:10]
                title = str(entry.get("title") or entry.get("merchant") or "平台流入")
                items.append({
                    "id": f"silent:{entry.get('id')}",
                    "entryId": entry.get("id"),
                    "title": title,
                    "amount": amount,
                    "date": date_str,
                    "reason": reason,
                    "category": str(entry.get("category") or "资金划转"),
                    "categoryLabel": "自动免开票核验",
                })
    return items


async def _load_states(conn, user_id: str, company_id: str) -> Dict[str, Dict[str, Any]]:
    cursor = await conn.execute(
        "SELECT todo_key, done, dismissed, updated_at FROM todo_states WHERE user_id = ? AND company_id = ?",
        (user_id, company_id),
    )
    rows = await cursor.fetchall()
    return {
        str(row["todo_key"]): {"done": bool(row["done"]), "dismissed": bool(row["dismissed"]), "updatedAt": row["updated_at"]}
        for row in rows
    }




async def _derive_intangible_todos(company_id: str) -> List[Dict[str, Any]]:
    """无形资产待办（P2）：待复核资产卡 + 90 天内权利到期。

    与 designs 方案一致：状态只读派生，不新增手工入口；用户可 dismiss。
    """
    from datetime import date, timedelta

    deadline = (date.today() + timedelta(days=90)).isoformat()
    todos: List[Dict[str, Any]] = []
    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT id, code, name, updated_at FROM intangible_assets "
            "WHERE company_id = ? AND status = 'pending_review' AND deleted_at IS NULL "
            "ORDER BY updated_at ASC LIMIT 50",
            (company_id,),
        )
        for row in await cursor.fetchall():
            todo_key = f"intangible-review:{row['id']}"
            todos.append({
                "id": todo_key, "todoKey": todo_key, "type": "task",
                "category": TODO_CATEGORIES["intangible_review"],
                "level": "high", "priority": "high",
                "title": f"无形资产待复核 - {row['name']}（{row['code']}）",
                "desc": "资产卡已提交，取得凭证待复核过账；过账后开始按月摊销。",
                "done": False, "dismissed": False, "amount": "0.00",
                "deadline": None, "companyId": company_id,
                "meta": {"assetId": row["id"], "ruleId": "intangible_review", "action": "review_intangible"},
            })
        cursor = await conn.execute(
            "SELECT id, code, name, expiry_date FROM intangible_assets "
            "WHERE company_id = ? AND deleted_at IS NULL AND expiry_date IS NOT NULL "
            "AND expiry_date <= ? AND status IN ('active','idle','fully_amortized') "
            "ORDER BY expiry_date ASC LIMIT 50",
            (company_id, deadline),
        )
        for row in await cursor.fetchall():
            todo_key = f"intangible-expiry:{row['id']}"
            todos.append({
                "id": todo_key, "todoKey": todo_key, "type": "risk",
                "category": TODO_CATEGORIES["intangible_expiry"],
                "level": "medium", "priority": "medium",
                "title": f"无形资产权利即将到期 - {row['name']}（{row['code']}）",
                "desc": f"权利将于 {str(row['expiry_date'])[:10]} 到期，请评估续展/减值迹象或发起核销。",
                "done": False, "dismissed": False, "amount": "0.00",
                "deadline": str(row["expiry_date"])[:10], "companyId": company_id,
                "meta": {"assetId": row["id"], "ruleId": "intangible_expiry", "action": "view_intangible", "expiryDate": str(row["expiry_date"])[:10]},
            })
    finally:
        await conn.close()
    return todos


async def _load_linked_fixed_asset_entry_ids(company_id: str) -> Set[str]:
    conn = await get_conn()
    try:
        try:
            cursor = await conn.execute(
                "SELECT source_entry_id FROM fixed_assets "
                "WHERE company_id = ? AND source_entry_id IS NOT NULL AND deleted_at IS NULL",
                (company_id,),
            )
            rows = await cursor.fetchall()
        except Exception as exc:
            logger.debug("关联台账查询失败，按无关联处理 company: %s", exc)
            return set()
    finally:
        await conn.close()
    return {str(row["source_entry_id"]) for row in rows if row["source_entry_id"]}


async def _load_linked_intangible_entry_ids(company_id: str) -> Set[str]:
    conn = await get_conn()
    try:
        try:
            cursor = await conn.execute(
                "SELECT source_entry_id FROM intangible_assets "
                "WHERE company_id = ? AND source_entry_id IS NOT NULL AND deleted_at IS NULL",
                (company_id,),
            )
            rows = await cursor.fetchall()
        except Exception as exc:
            logger.debug("关联台账查询失败，按无关联处理 company: %s", exc)
            return set()
    finally:
        await conn.close()
    return {str(row["source_entry_id"]) for row in rows if row["source_entry_id"]}


async def list_todos(user_id: str, company_id: str) -> List[Dict[str, Any]]:
    # lite：待办派生只读 metadata 级字段（billingStatus/invoiceNumber/payer 等），
    # 不读 voucherLines——跳过分录行/研发明细/科目名装配，待办页不再为全量凭证
    # 序列化分录行（配合 list_journals 批量预取，此前为每条凭证 5-7 查询）
    entries = await list_entries(user_id, company_id, lite=True)
    from services.bank_service import get_matched_entry_ids

    matched_entry_ids = await get_matched_entry_ids(company_id)
    linked_asset_entry_ids = await _load_linked_fixed_asset_entry_ids(company_id)
    linked_intangible_entry_ids = await _load_linked_intangible_entry_ids(company_id)
    
    conn = await get_conn()
    try:
        await _ensure_db_ready()
        # Query unmatched bank statements
        cursor = await conn.execute(
            "SELECT id, transaction_date, description, amount FROM bank_statements "
            "WHERE company_id = ? AND (matched_entry_id IS NULL OR matched_entry_id = '')",
            (company_id,),
        )
        unmatched_statements = [dict(row) for row in await cursor.fetchall()]
        
        states = await _load_states(conn, user_id, company_id)
    finally:
        await conn.close()

    derived = _derive_entry_todos(
        entries, matched_entry_ids, linked_asset_entry_ids, linked_intangible_entry_ids
    )

    # 无形资产（P2）：待复核资产卡 + 权利即将到期提醒
    try:
        derived.extend(await _derive_intangible_todos(company_id))
    except Exception as exc:
        logger.debug("无形资产待办派生失败，跳过: %s", exc)  # 表未就绪不阻塞账务待办

    # 征期提醒（M3）：临近/逾期的申报事项注入待办
    try:
        from services import filing_deadline_service

        await filing_deadline_service.refresh_deadlines(company_id)
        deadlines = await filing_deadline_service.list_deadlines(company_id)
        derived.extend(filing_deadline_service.deadline_todos(deadlines, window_days=30))
    except Exception as exc:
        logger.debug("征期待办刷新/派生失败，可能展示过期征期: %s", exc)  # 不阻塞账务待办

    # 税费种认定未同步：强提醒。按内置清单生成的征期可能与税局实际认定不一致，
    # 同步后自动按真实税种生成申报义务，这条待办随同步自动消失。
    try:
        from services import tax_registration_service

        registrations = await tax_registration_service.list_registrations(company_id)
        if not registrations:
            derived.append({
                "id": "tax-registration-unsynced",
                "todoKey": "tax-registration-unsynced",
                "type": "filing",
                "category": "filing",
                "level": "high",
                "priority": "high",
                "title": "同步税费种认定表",
                "desc": "尚未同步税费种认定表，当前按内置税种清单生成征期，可能与税局实际认定不一致。同步后按真实税种生成申报义务与征期。",
                "done": False,
                "dismissed": False,
                "amount": 0,
                "meta": {"ruleId": "tax_registration_unsynced"},
            })
    except Exception as exc:
        logger.debug("税种认定同步/派生失败，跳过: %s", exc)  # 不阻塞账务待办


    # Derive todos from unmatched statements
    for stmt in unmatched_statements:
        stmt_id = stmt["id"]
        todo_key = f"stmt:{stmt_id}:unmatched"
        # PG numeric 经 asyncpg 返回 Decimal，直接透传会炸 MCP 的 JSON 序列化
        amount = float(abs(stmt["amount"]))
        direction_label = "收入" if stmt["amount"] >= 0 else "支出"
        title = f"流水待处理 - {stmt['description']}"
        desc = f"¥{amount:,.2f}（{direction_label}），发生于 {stmt['transaction_date'][:10]}，尚未匹配记账或补充发票。"
        
        derived.append({
            "id": todo_key,
            "todoKey": todo_key,
            "type": "bank",
            "category": "match",
            "level": "high",
            "priority": "high",
            "title": title,
            "desc": desc,
            "done": False,
            "dismissed": False,
            "amount": amount,
            "companyId": company_id,
            "meta": {
                "statementId": stmt_id,
                "ruleId": "unmatched_statement",
                "date": str(stmt["transaction_date"] or "")[:10],
            },
        })

    merged: List[Dict[str, Any]] = []
    for todo in derived:
        state = states.get(todo["todoKey"], {})
        # 前端 TodoItem 以 key 为唯一标识（acting/updating/dismiss 都用它），
        # 与 todoKey 保持同值下发，否则 undefined 会让全部卡片的 loading/忽略互相串。
        item = {**todo, "key": todo["todoKey"], **state}
        if not item.get("dismissed"):
            # 优先复用已缓存的深度 LLM 建议
            cached_ai = _AI_SUGGESTION_CACHE.get((company_id, item["todoKey"]))
            if cached_ai:
                item["meta"]["aiSuggestion"] = cached_ai
            merged.append(item)
    return merged


async def list_todos_bundle(user_id: str, company_id: str) -> Dict[str, Any]:
    """返回待办全量数据，包含待办列表与系统静默处理完成的留痕事项。"""
    todos = await list_todos(user_id, company_id)
    entries = await list_entries(user_id, company_id)
    auto_resolved = derive_silent_resolved_items(entries)
    return {
        "todos": todos,
        "autoResolved": auto_resolved,
        "autoResolvedCount": len(auto_resolved),
    }


async def advise_todos_with_llm(user_id: str, company_id: str) -> List[Dict[str, Any]]:
    """批量调用 LLM 为当前待办提供专业、大白话的处置建议与决策理由。"""
    from services.llm import safe_chat_text_json

    todos = await list_todos(user_id, company_id)
    pending_todos = [t for t in todos if not t.get("done") and not t.get("dismissed")]
    if not pending_todos:
        return todos

    # 过滤出尚未获得 LLM 建议（仅有默认 rule 建议）的条目
    need_llm = [
        t for t in pending_todos
        if (t.get("meta") or {}).get("aiSuggestion", {}).get("source") != "llm"
    ]
    if not need_llm:
        return todos

    items_prompt = []
    for t in need_llm[:10]:  # 控制单次请求规模
        meta = t.get("meta") or {}
        items_prompt.append({
            "todoKey": t["todoKey"],
            "title": t.get("title"),
            "amount": t.get("amount"),
            "ruleId": meta.get("ruleId"),
            "desc": t.get("desc"),
            "candidateExpenseTitle": meta.get("candidateExpenseTitle"),
            "candidateExpenseRemark": meta.get("candidateExpenseRemark"),
        })

    system_prompt = (
        "你是一个专为完全懂不懂财务的个体户/微型企业老板服务的AI财务顾问。"
        "请根据待办条目信息，给出对小白老板最省事、最合规的处置建议（推荐动作）和大白话理由（消除顾虑、解释为什么这么做）。"
        "返回格式为 JSON 字典，键为各条目的 todoKey，值为包含 action (推荐英文标识), label (按钮文案不超过10字), reason (大白话解释，50字左右) 的对象。"
    )
    user_prompt = f"待办清单：\n{json.dumps(items_prompt, ensure_ascii=False)}"

    try:
        res = await asyncio.to_thread(
            safe_chat_text_json,
            system_prompt,
            user_prompt,
            fallback={},
            max_retries=1,
            scene="todo_advice",
            user_id=user_id,
            company_id=company_id,
        )
        if isinstance(res, dict):
            for t in need_llm:
                k = t["todoKey"]
                advice = res.get(k)
                if isinstance(advice, dict) and advice.get("label") and advice.get("reason"):
                    enriched = {
                        "action": str(advice.get("action") or t.get("meta", {}).get("aiSuggestion", {}).get("action") or "default"),
                        "label": str(advice["label"]),
                        "reason": str(advice["reason"]),
                        "confidence": "high",
                        "source": "llm",
                    }
                    _AI_SUGGESTION_CACHE[(company_id, k)] = enriched
                    t["meta"]["aiSuggestion"] = enriched
    except AccountingError:
        raise  # 配额超限保持 402，不静默降级成默认建议
    except Exception as exc:
        logger.debug("AI 建议不可用，保留默认 rule 建议: %s", exc)

    return todos


async def link_invoice_to_expense(
    invoice_entry_id: str,
    expense_entry_id: str,
    *,
    user_id: str,
    company_id: str,
) -> Dict[str, Any]:
    """将待报销发票凭证与已有支出流水凭证关联核销（解决一笔业务两张卡且重复挂账问题）。"""
    from db.session import session_scope
    from db.models import Invoice, LegacyEntryProjection, JournalEntry
    from services.accounting_core_service import (
        get_journal,
        update_draft,
        reverse_journal,
        delete_draft,
        _locked_entry_context,
    )
    from core.errors import AccountingError
    from uuid import uuid4

    async with session_scope() as session:
        inv_journal = await get_journal(session, invoice_entry_id, company_id=company_id)
        exp_journal = await get_journal(session, expense_entry_id, company_id=company_id)
        if not inv_journal or not exp_journal:
            raise AccountingError("NOT_FOUND", "未找到对应的发票凭证或支出凭证", 404)

        # 提取发票凭证的关键发票信息
        inv_meta = dict(inv_journal.metadata_json or {})
        inv_proj = await session.get(LegacyEntryProjection, inv_journal.id)
        if inv_proj and inv_proj.payload:
            try:
                p_data = json.loads(inv_proj.payload) if isinstance(inv_proj.payload, str) else dict(inv_proj.payload)
                inv_meta.update(p_data)
            except (TypeError, ValueError):
                logger.debug("发票投影 payload 损坏，跳过元数据合并")

        inv_number = str(inv_meta.get("invoiceNumber") or inv_meta.get("invoice_number") or "")
        inv_type = str(inv_meta.get("invoiceType") or inv_meta.get("invoice_type") or "")
        seller = str(inv_meta.get("merchant") or inv_meta.get("seller_name") or "")

        # 1. 把发票元数据反写到实际支出凭证上（已过账凭证走更新跟踪投影字段）
        exp_updates = {
            "billingStatus": "has_invoice",
            "invoiceNumber": inv_number,
            "invoiceType": inv_type,
            "linkedInvoiceEntryId": invoice_entry_id,
        }
        # merchant 不在 POSTED_PROJECTION_FIELDS 里，如果已过账不能加进去，否则会触发已过账不可改拦截
        if exp_journal.status in ("draft", "pending_review") and seller and not (exp_journal.metadata_json or {}).get("merchant"):
            exp_updates["merchant"] = seller

        await update_draft(
            session,
            expense_entry_id,
            exp_updates,
            company_id=company_id,
            user_id=user_id,
            request_id=f"link-exp-{uuid4()}",
        )

        # 2. 将 invoices 表台账的 entry_id 切换指向实际支出凭证
        from sqlalchemy import select, update
        await session.execute(
            update(Invoice)
            .where(Invoice.company_id == company_id, Invoice.entry_id == invoice_entry_id)
            .values(entry_id=expense_entry_id)
        )

        # 3. 消除重复的发票凭证（若草稿则删除，若已过账且未冲销且期间未关则红冲，并在投影标归档）
        if inv_journal.status in ("draft", "pending_review"):
            await delete_draft(
                session, invoice_entry_id, company_id=company_id, user_id=user_id,
                request_id=f"del-dup-inv-{uuid4()}",
            )
        elif inv_journal.status == "posted":
            # 检查发票凭证所属期间状态
            from services.accounting_core_service import get_or_create_period
            period = await get_or_create_period(session, company_id, inv_journal.period_start)
            if period and period.status == "open":
                await reverse_journal(
                    session,
                    invoice_entry_id,
                    company_id=company_id,
                    user_id=user_id,
                    request_id=f"rev-dup-inv-{uuid4()}",
                    reason="已关联银行实际支出凭证，自动冲销重复发票挂账",
                )
            # 在发票投影上回写已核销与归档标记，确保待办和对账彻底消卡
            inv_updates = {
                "billingStatus": "reconciled",
                "is_archive_only": True,
                "isArchiveOnly": True,
                "linkedExpenseEntryId": expense_entry_id,
            }
            await update_draft(
                session,
                invoice_entry_id,
                inv_updates,
                company_id=company_id,
                user_id=user_id,
                request_id=f"archive-dup-inv-{uuid4()}",
            )

    # 4. 同步更新待办状态表中对应的 todoKey 为已完成
    await update_todo_state(f"entry:{invoice_entry_id}:invoice_unpaid", user_id=user_id, company_id=company_id, done=True)
    await update_todo_state(f"entry:{expense_entry_id}:missing_document", user_id=user_id, company_id=company_id, done=True)

    return {
        "status": "linked",
        "invoiceEntryId": invoice_entry_id,
        "expenseEntryId": expense_entry_id,
        "invoiceNumber": inv_number,
    }


async def update_todo_state(
    todo_key: str, *, user_id: str, company_id: str,
    done: Optional[bool] = None, dismissed: Optional[bool] = None,
) -> Dict[str, Any]:
    now = _now_iso()
    conn = await get_conn()
    try:
        await _ensure_db_ready()
        cursor = await conn.execute(
            "SELECT done, dismissed, created_at FROM todo_states WHERE todo_key = ? AND user_id = ? AND company_id = ?",
            (todo_key, user_id, company_id),
        )
        row = await cursor.fetchone()
        next_done = bool(row["done"]) if row else False
        next_dismissed = bool(row["dismissed"]) if row else False
        created_at = row["created_at"] if row else now
        if done is not None:
            next_done = bool(done)
        if dismissed is not None:
            next_dismissed = bool(dismissed)
        await conn.execute(
            "INSERT INTO todo_states (todo_key, user_id, company_id, done, dismissed, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(todo_key) DO UPDATE SET done=excluded.done, dismissed=excluded.dismissed, updated_at=excluded.updated_at",
            (todo_key, user_id, company_id, int(next_done), int(next_dismissed), created_at, now),
        )
        await conn.commit()
        return {"todoKey": todo_key, "done": next_done, "dismissed": next_dismissed, "updatedAt": now}
    finally:
        await conn.close()
