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
# filing_deadline 在各自构造处直接写死 category；未匹配银行流水归月度结账向导统一认领，不派生独立待办。
TODO_CATEGORIES: Dict[str, str] = {
    "opening_balance_required": "setup",
    "filing_deadline": "filing",
    "personal_proxy_withholding": "filing",
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

# 确定性法定申报 / 建账引导 / 固定规则类待办，说明本身已明确，不调用 LLM 生成建议
_SKIP_LLM_ADVICE_CATEGORIES = frozenset({"filing", "setup", "intangible"})
_SKIP_LLM_ADVICE_RULE_IDS = frozenset({
    "filing_deadline",
    "personal_proxy_withholding",
    "tax_registration_unsynced",
    "opening_balance_required",
    "fixed_asset_depreciation",
    "intangible_review",
    "intangible_expiry",
    "intangible_asset_amortization",
})


def _is_llm_advice_eligible(todo: Dict[str, Any]) -> bool:
    """仅需结合业务上下文判断的待办才走 LLM 建议。"""
    category = str(todo.get("category") or "")
    rule_id = str((todo.get("meta") or {}).get("ruleId") or "")
    if category in _SKIP_LLM_ADVICE_CATEGORIES or rule_id in _SKIP_LLM_ADVICE_RULE_IDS:
        return False
    return True


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
    "营业外收入",
    "政府补助",
    "捐赠收益",
    "盘盈收益",
}
NON_INVOICING_ACCOUNT_PREFIXES = ("1012", "5603", "4001", "4002", "2241", "1221")
NON_INVOICING_TITLE_RE = re.compile(r"提现到账|平台提现|银行结息|存款利息|股东投入|股东注资|退税")

# 无发票义务的支出分类：税费、罚没、银行金融、薪酬社保、内部划转与营业外损失均不需要供应商发票
TAX_PAYMENT_EXPENSE_CATEGORIES: Set[str] = {"税费缴纳", "所得税费用"}
PENALTY_EXPENSE_CATEGORIES: Set[str] = {"罚款支出", "税收滞纳金"}
NON_OPERATING_LOSS_CATEGORIES: Set[str] = {
    "营业外支出", "捐赠支出", "赞助支出", "非流动资产处置净损失", "盘亏损失",
    "坏账损失", "存货毁损报废损失", "长期债券投资损失", "长期股权投资损失", "自然灾害损失",
}
BANK_FINANCE_EXPENSE_CATEGORIES: Set[str] = {
    "银行手续费", "银行及金融手续费", "利息费用", "汇兑损益", "现金折扣",
}
PAYROLL_EXPENSE_CATEGORIES: Set[str] = {"工资薪金", "工资发放", "社保公积金", "住房公积金"}
INTERNAL_TRANSFER_EXPENSE_CATEGORIES: Set[str] = {
    "股东取现", "老板取现", "微信支付结算", "支付宝结算", "平台提现结算", "往来退款",
}

_NON_INVOICE_EXPENSE_RULES: Tuple[Tuple[Set[str], Tuple[str, ...], str], ...] = (
    (
        PENALTY_EXPENSE_CATEGORIES,
        ("5711010", "5711011"),
        "属于税务或行政罚款/滞纳金，税务机关不另开增值税发票，凭银行回单或处罚决定书留存即可，已自动豁免补票",
    ),
    (
        TAX_PAYMENT_EXPENSE_CATEGORIES,
        ("2221", "5801", "5403"),
        "属于依法缴纳税款（增值税/附加税/所得税等），凭银行扣款回单或完税证明即可入账，无需向税务局索要发票",
    ),
    (
        NON_OPERATING_LOSS_CATEGORIES,
        ("5711",),
        "属于营业外支出或公益捐赠/资产损失等非采购支出，凭银行回单或收据/批文留存即可，已自动豁免补票",
    ),
    (
        BANK_FINANCE_EXPENSE_CATEGORIES,
        ("5603",),
        "属于银行扣收手续费或利息/汇兑费用，通常凭银行电子回单或利息清单入账，已自动豁免补票",
    ),
    (
        PAYROLL_EXPENSE_CATEGORIES,
        ("2211",),
        "属于工资发放或社保公积金缴纳，凭工资表与银行/税务扣款回单入账，无需取得供应商发票",
    ),
    (
        INTERNAL_TRANSFER_EXPENSE_CATEGORIES,
        ("1012",),
        "属于内部资金划转、往来退款或股东取现，不属于采购商品或服务支出，无需发票",
    ),
)

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


def _silent_non_invoice_expense_reason(entry: Dict[str, Any]) -> Optional[str]:
    """若该笔支出属于税费/罚款/银行费用/薪酬社保/内部划转等无需发票事项，返回豁免原因；否则返回 None。"""
    category = str(entry.get("category") or "").strip()
    account_code = str(entry.get("accountCode") or "").strip()
    source_type = str(entry.get("sourceType") or "").strip()
    # 工资模块生成的凭证（计提/发放/社保公积金/个税）是法定薪酬支出，凭工资表、
    # 申报记录和银行回单入账，不存在供应商发票。不能只靠 category 判断：
    # 工资凭证统一记「人力成本」，而手工录入的临时劳务费也可能用同一分类，
    # 后者仍需要合同/发票，所以这里只豁免工资模块的来源指纹。
    if source_type.startswith("salary_"):
        return "属于工资模块生成的计提/发放/社保公积金/个税凭证，凭工资表、申报记录与银行回单入账，无需供应商发票"
    for categories, prefixes, reason in _NON_INVOICE_EXPENSE_RULES:
        if category in categories or account_code.startswith(prefixes):
            return reason
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

        # 1. 待补票据（支出缺少发票）候选：税费/罚款/银行费用/薪酬社保/内部划转免补票
        if (direction == "expense" and source != "invoice_import"
                and not _has_document(entry) and not small_amount
                and not _silent_non_invoice_expense_reason(entry)):
            missing_doc_candidates.append(entry)

        # 2. 待报销/付款（发票未关联流水支出）候选
        # 垫付去向已确认（老板/员工垫付）的发票不再催付款——核销由公户打款给垫付人时完成，
        # 且结账向导「已确认入账去向」区已在跟踪，再催就是让用户重复确认同一件事
        payer_str = payer
        advancement_confirmed = payer_str == "boss_personal" or payer_str.startswith("employee_personal")
        # 已过账的 archive-only 发票（如"发票先到"被确认过账、或平台手续费误归档后确认）
        # 是真实费用凭证，必须参与与无票支出的跨单据撮合——排除它会让付款侧"待补票据"
        # 无人认领（2026-10-31 实测：发票列表里有票、待办却永远报缺）。draft 底稿仍是
        # 纯证据未记账，不进池。
        invoice_is_posted = str(entry.get("voucherStatus") or "") == "posted"
        if (direction == "expense" and source == "invoice_import" and entry_id not in matched_entry_ids
                and (not is_archive_only or invoice_is_posted) and not advancement_confirmed
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
    reversed_ids = {
        str(e.get("reversalOf") or "")
        for e in entries
        if str(e.get("entryType") or "") == "reversal"
    } - {""}
    for entry in entries:
        entry_id = str(entry.get("id") or "")
        if (
            str(entry.get("entryType") or "") in ("reversal", "opening", "closing")
            or entry.get("isClosingTransfer")
            or (entry_id and entry_id in reversed_ids)
        ):
            continue
        direction = str(entry.get("direction") or "")
        amount = float(abs(_amount(entry.get("amount"))))
        date_str = str(entry.get("occurDate") or entry.get("date") or "")[:10]
        if direction == "income":
            reason = _silent_non_invoice_income_reason(entry)
            if reason:
                title = str(entry.get("title") or entry.get("merchant") or "平台流入")
                items.append({
                    "id": f"silent:{entry_id}",
                    "entryId": entry.get("id"),
                    "title": title,
                    "amount": amount,
                    "date": date_str,
                    "reason": reason,
                    "category": str(entry.get("category") or "资金划转"),
                    "categoryLabel": "自动免开票核验",
                })
        elif (
            direction == "expense"
            and str(entry.get("source") or "") != "invoice_import"
            and not _has_document(entry)
        ):
            reason = _silent_non_invoice_expense_reason(entry)
            if reason:
                title = str(entry.get("title") or entry.get("merchant") or "免票支出")
                items.append({
                    "id": f"silent:{entry_id}",
                    "entryId": entry.get("id"),
                    "title": title,
                    "amount": amount,
                    "date": date_str,
                    "reason": reason,
                    "category": str(entry.get("category") or "免票支出"),
                    "categoryLabel": "自动免补票核验",
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


_CN_ID_RE = re.compile(r"^[1-9]\d{5}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]$")
_ORG_NAME_KEYWORDS = (
    "公司", "中心", "厂", "店", "馆", "行", "所", "社", "工作室", "经营部",
    "科技", "商贸", "有限", "合伙", "集团", "银行", "局", "院",
)
_VAT_WITHHOLDING_EFFECTIVE_DATE = date(2026, 11, 1)


def _is_natural_person_proxy_invoice(inv: Dict[str, Any], xml_withholding: bool = False) -> bool:
    """判定进项发票是否属于自然人代开发票（需提示代扣代缴）。"""
    if str(inv.get("direction") or "") != "expense":
        return False
    if str(inv.get("invoiceStatus") or inv.get("invoice_status") or "normal") != "normal":
        return False
    amt = money(inv.get("amountInclTax") or inv.get("amount_incl_tax") or 0)
    if amt <= ZERO:
        return False

    if xml_withholding:
        return True
    remark = str(inv.get("remark") or "")
    inv_type = str(inv.get("invoiceType") or inv.get("invoice_type") or "")
    if "代扣代缴" in remark or "代开" in remark or "代开" in inv_type:
        return True

    seller_tax_id = str(inv.get("sellerTaxId") or inv.get("seller_tax_id") or "").strip()
    seller_name = str(inv.get("sellerName") or inv.get("seller_name") or "").strip()
    if (
        _CN_ID_RE.match(seller_tax_id)
        and 2 <= len(seller_name) <= 4
        and not any(kw in seller_name for kw in _ORG_NAME_KEYWORDS)
    ):
        return True
    return False


def _estimate_labor_iit(gross: Decimal) -> Decimal:
    """按劳务报酬所得预扣预缴公式试算参考个人所得税。"""
    amt = money(gross)
    if amt <= Decimal("800"):
        return ZERO
    if amt <= Decimal("4000"):
        return money((amt - Decimal("800")) * Decimal("0.20"))
    taxable = money(amt * Decimal("0.80"))
    if taxable <= Decimal("20000"):
        return money(taxable * Decimal("0.20"))
    if taxable <= Decimal("50000"):
        return money(taxable * Decimal("0.30") - Decimal("2000"))
    return money(taxable * Decimal("0.40") - Decimal("7000"))


def _estimate_personal_vat(
    amount_incl_tax: Decimal, amount_excl_tax: Optional[Decimal] = None
) -> Tuple[Decimal, bool]:
    """按 2026 年 10 号 / 28 号公告 1,000 元起征点试算自然人参考增值税及是否达起征点。"""
    incl = money(amount_incl_tax)
    if amount_excl_tax is not None and money(amount_excl_tax) > ZERO:
        excl = money(amount_excl_tax)
    else:
        excl = money(incl / Decimal("1.01"))
    if excl < Decimal("1000"):
        return ZERO, False
    return money(excl * Decimal("0.01")), True


def _next_month_15(d: date) -> str:
    """返回交易所在月份次月 15 日 ISO 字符串。"""
    if d.month == 12:
        return f"{d.year + 1}-01-15"
    return f"{d.year}-{d.month + 1:02d}-15"


def _parse_invoice_date(raw: Any) -> date:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if raw:
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            pass
    return datetime.now(timezone.utc).date()


def _build_personal_proxy_withholding_todos(
    invoices: List[Dict[str, Any]], xml_withholding_ids: Set[str]
) -> List[Dict[str, Any]]:
    """把命中的自然人代开进项发票转化为确定性代扣代缴待办列表。"""
    todos: List[Dict[str, Any]] = []
    for inv in invoices:
        inv_id = str(inv.get("id") or "")
        xml_flag = inv_id in xml_withholding_ids or bool(inv.get("xmlWithholding"))
        if not _is_natural_person_proxy_invoice(inv, xml_withholding=xml_flag):
            continue

        inv_no = str(inv.get("invoiceNumber") or inv.get("invoice_number") or "").strip()
        seller_name = str(inv.get("sellerName") or inv.get("seller_name") or "").strip() or "个人"
        inv_date = _parse_invoice_date(inv.get("invoiceDate") or inv.get("invoice_date"))
        period = f"{inv_date.year}-{inv_date.month:02d}"
        due_date = _next_month_15(inv_date)

        amt = money(inv.get("amountInclTax") or inv.get("amount_incl_tax") or 0)
        excl_raw = inv.get("amountExclTax") if "amountExclTax" in inv else inv.get("amount_excl_tax")
        est_iit = _estimate_labor_iit(amt)
        est_vat, vat_over_threshold = _estimate_personal_vat(
            amt, money(excl_raw) if excl_raw is not None else None
        )
        vat_applicable = inv_date >= _VAT_WITHHOLDING_EFFECTIVE_DATE

        prefix = f"发票 {inv_no} · 含税 ¥{money_text(amt)}：" if inv_no else f"含税 ¥{money_text(amt)}："
        if not vat_applicable:
            if est_iit > ZERO:
                body = (
                    f"{prefix}劳务报酬参考预扣个税 ¥{money_text(est_iit)}；"
                    "请在次月15日前于自然人电子税务局（扣缴端）按「劳务报酬所得」完成代扣代缴申报"
                )
            else:
                body = (
                    f"{prefix}单次 ≤ 800 元参考预扣 ¥0，仍建议在扣缴端申报；"
                    "请在次月15日前于自然人电子税务局（扣缴端）按「劳务报酬所得」完成代扣代缴申报"
                )
        elif not vat_over_threshold and est_iit == ZERO:
            body = (
                f"{prefix}单次未达起征点，劳务报酬参考预扣个税 ¥0、增值税免征（¥0），"
                "请在次月15日前办理零/免税申报（如开票前已申报直接点「已申报办结」）"
            )
        elif not vat_over_threshold:
            body = (
                f"{prefix}劳务报酬参考预扣个税 ¥{money_text(est_iit)}；"
                "增值税未达 1,000 元起征点免征（做免税/零申报）；"
                "请在次月15日前核对扣缴申报"
            )
        else:
            body = (
                f"{prefix}已达 1,000 元增值税起征点，劳务报酬参考预扣个税 ¥{money_text(est_iit)}；"
                f"属研发/软件/设计/咨询/影视/文化/教育七类服务的，参考代扣增值税 ¥{money_text(est_vat)}（按 1% 减按，另计附加税费）；"
                "请在次月15日前核对两项扣缴申报（若付款时未扣税请及时与收款方核对税费承担；已由个人代开缴税的勿重复扣缴）"
            )

        todo_key = f"proxy-withholding:{inv_no or inv_id}"
        todos.append({
            "id": todo_key,
            "todoKey": todo_key,
            "type": "filing",
            "category": TODO_CATEGORIES["personal_proxy_withholding"],
            "level": "high",
            "priority": "high",
            "title": f"自然人代开扣缴申报 · {seller_name}（{period}）",
            "desc": body,
            "done": False,
            "dismissed": False,
            "amount": money_text(amt),
            "deadline": due_date,
            "meta": {
                "ruleId": "personal_proxy_withholding",
                "invoiceId": inv_id or None,
                "invoiceNumber": inv_no or None,
                "entryId": str(inv.get("entryId") or inv.get("entry_id") or "") or None,
                "sellerName": seller_name,
                "period": period,
                "dueDate": due_date,
                "estimatedIit": money_text(est_iit),
                "estimatedVat": money_text(est_vat),
                "vatOverThreshold": vat_over_threshold,
                "vatWithholdingApplicable": vat_applicable,
            },
        })
    return todos


async def _derive_personal_proxy_withholding_todos(company_id: str) -> List[Dict[str, Any]]:
    """查询本账套进项发票与 XML 原件元数据，派生自然人代开扣缴申报待办。"""
    from sqlalchemy import select

    from db.models import Attachment, Invoice
    from db.session import session_scope

    async with session_scope() as session:
        inv_rows = (
            await session.execute(
                select(Invoice).where(
                    Invoice.company_id == company_id,
                    Invoice.direction == "expense",
                    Invoice.invoice_status == "normal",
                )
            )
        ).scalars().all()
        att_rows = (
            await session.execute(
                select(Attachment.invoice_id, Attachment.parsed_json).where(
                    Attachment.company_id == company_id,
                    Attachment.status == "active",
                    Attachment.invoice_id.isnot(None),
                    Attachment.document_type == "einvoice_xml",
                )
            )
        ).all()

    xml_withholding_ids: Set[str] = set()
    for inv_id, parsed_json in att_rows:
        if not inv_id or not isinstance(parsed_json, dict):
            continue
        if parsed_json.get("withholdingNotice") or "AgentIssuing" in json.dumps(parsed_json, ensure_ascii=False):
            xml_withholding_ids.add(str(inv_id))

    invoices = [
        {
            "id": row.id,
            "entryId": row.entry_id,
            "direction": row.direction,
            "invoiceNumber": row.invoice_number,
            "invoiceType": row.invoice_type,
            "invoiceStatus": row.invoice_status,
            "invoiceDate": row.invoice_date,
            "sellerName": row.seller_name,
            "sellerTaxId": row.seller_tax_id,
            "amountExclTax": row.amount_excl_tax,
            "amountInclTax": row.amount_incl_tax,
            "remark": row.remark,
        }
        for row in inv_rows
    ]
    return _build_personal_proxy_withholding_todos(invoices, xml_withholding_ids)


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

    # 自然人代开进项发票：次月 15 日前劳务报酬个税 / 11-01 起七类服务增值税代扣代缴提示
    try:
        derived.extend(await _derive_personal_proxy_withholding_todos(company_id))
    except Exception as exc:
        logger.debug("自然人代开扣缴待办派生失败，跳过: %s", exc)

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

    # 从其他代账转入且尚未完成期初建账：高优必办前置待办，完成后自动消除
    try:
        from db.session import session_scope
        from services.setup_readiness_service import migration_opening_status

        async with session_scope() as session:
            opening_info = await migration_opening_status(session, company_id)
        if opening_info["required"]:
            todo_key = f"opening-balance-required:{company_id}"
            derived.append({
                "id": todo_key,
                "todoKey": todo_key,
                "type": "setup",
                "category": TODO_CATEGORIES["opening_balance_required"],
                "level": "high",
                "priority": "high",
                "title": "完成期初建账",
                "desc": (
                    f"{opening_info['detail']}。完成前暂不可导入流水、发票或日常记账；"
                    "如原代账无历史余额，可在期初页切换为从零起账。"
                ),
                "done": False,
                "dismissed": False,
                "amount": "0.00",
                "deadline": None,
                "companyId": company_id,
                "meta": {
                    "ruleId": "opening_balance_required",
                    "openingStatus": opening_info["status"],
                    "nonDismissible": True,
                    "deepLink": "/mine/opening",
                },
            })
    except Exception as exc:
        logger.debug("期初建账待办派生失败，跳过: %s", exc)

    merged: List[Dict[str, Any]] = []
    for todo in derived:
        non_dismissible = bool((todo.get("meta") or {}).get("nonDismissible"))
        state = {} if non_dismissible else states.get(todo["todoKey"], {})
        # 前端 TodoItem 以 key 为唯一标识（acting/updating/dismiss 都用它），
        # 与 todoKey 保持同值下发，否则 undefined 会让全部卡片的 loading/忽略互相串。
        item = {**todo, "key": todo["todoKey"], **state}
        if non_dismissible or not item.get("dismissed"):
            # 优先复用已缓存的深度 LLM 建议（确定性申报/建账类跳过）
            if _is_llm_advice_eligible(item):
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

    # 过滤出尚未获得 LLM 建议（仅有默认 rule 建议）且需要语义判断的条目
    need_llm = [
        t for t in pending_todos
        if _is_llm_advice_eligible(t)
        and (t.get("meta") or {}).get("aiSuggestion", {}).get("source") != "llm"
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
        "你是一个专为完全不懂财务的个体户/微型企业老板服务的AI财务顾问。"
        "请根据待办条目信息，给出对小白老板最省事、最合规的处置建议（推荐动作）和大白话理由（消除顾虑、解释为什么这么做）。"
        "严禁虚构页面上不存在的输入框、选项或「点此填写…」等操作文案，仅结合卡片已有动作按钮解释推荐理由。"
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


async def _repost_invoice_voucher(
    session,
    *,
    inv_journal,
    inv_meta: dict,
    settle_line,
    expense_entry_id: str,
    company_id: str,
    user_id: str,
) -> str:
    """红冲后的发票凭证按付款对手科目重挂并重新过账，返回新凭证 id。

    费用借方行原样保留；贷方挂账行改用付款凭证的对手科目（如 2241→2202）并
    沿用原往来单位辅助核算，与付款凭证相互对冲。dedupFingerprint 必须换新：
    否则 external_key=发票号会撞已被红冲原凭证占用的来源唯一键（DUPLICATE_SOURCE）。
    inv_meta 是 metadata+投影合并后的完整字段（发票号/税率等可能只在投影里）。
    """
    from services.accounting_core_service import create_entry, _entry_lines
    from uuid import uuid4

    old_lines = await _entry_lines(session, inv_journal.id)
    old_debits = [line for line in old_lines if line.direction == "debit"]
    old_credits = [line for line in old_lines if line.direction == "credit"]
    partner_auxiliary = next(
        (dict(line.auxiliary) for line in old_credits if line.auxiliary), None
    )
    total = sum((money(line.amount) for line in old_lines if line.direction == "debit"), ZERO)

    def _payload_line(line) -> dict:
        payload_line = {
            "amount": money_text(line.amount),
            "accountCode": str(line.account_code),
            "accountName": str(line.account_name or ""),
        }
        if line.auxiliary:
            payload_line["auxiliary"] = dict(line.auxiliary)
        return payload_line

    repost_title = str(inv_journal.summary or "")
    if repost_title.startswith("补记") and "｜" in repost_title[:12]:
        repost_title = repost_title.split("｜", 1)[1]

    new_credit = {
        "amount": money_text(total),
        "accountCode": str(settle_line.account_code),
        "accountName": str(settle_line.account_name or ""),
    }
    if partner_auxiliary:
        new_credit["auxiliary"] = partner_auxiliary

    payload = dict(inv_meta)
    for drop_key in ("id", "voucherLines", "voucherNo", "voucher_no", "dedupFingerprint",
                     "wechat_transaction_key", "is_archive_only", "isArchiveOnly",
                     "linkedExpenseEntryId", "linkedInvoiceEntryId", "billingStatus",
                     "allowBackfill", "backfillReason", "voucherRef", "reversalOfId"):
        payload.pop(drop_key, None)
    payload["dedupFingerprint"] = f"inv-relink-{uuid4().hex[:12]}"
    payload["billingStatus"] = "has_invoice"
    payload["linkedExpenseEntryId"] = expense_entry_id
    payload["occurDate"] = inv_journal.occurred_on.isoformat()
    payload["title"] = repost_title
    payload["voucherLines"] = {
        "debit": [_payload_line(line) for line in old_debits],
        "credit": [new_credit],
    }
    created = await create_entry(
        session, payload,
        company_id=company_id, user_id=user_id,
        request_id=f"repost-inv-{uuid4()}", confirm=True,
    )
    return str(created.id)


async def link_invoice_to_expense(
    invoice_entry_id: str,
    expense_entry_id: str,
    *,
    user_id: str,
    company_id: str,
) -> Dict[str, Any]:
    """将待报销发票凭证与已有支出流水凭证关联核销（解决一笔业务两张卡且重复挂账问题）。"""
    from db.session import session_scope
    from db.models import Invoice, LegacyEntryProjection
    from services.accounting_core_service import (
        get_journal,
        update_draft,
        reverse_journal,
        delete_draft,
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

        # 1. 处理发票凭证：草稿删除；已过账分两种形态（见下）。invoice_ref_id 是
        # 付款凭证 linkedInvoiceEntryId 应指向的"活"发票凭证。
        invoice_ref_id = invoice_entry_id
        if inv_journal.status in ("draft", "pending_review"):
            await delete_draft(
                session, invoice_entry_id, company_id=company_id, user_id=user_id,
                request_id=f"del-dup-inv-{uuid4()}",
            )
        elif inv_journal.status == "posted":
            # 检查发票凭证所属期间状态
            from services.accounting_core_service import get_or_create_period, _entry_lines
            period = await get_or_create_period(session, company_id, inv_journal.period_start)
            if period and period.status == "open":
                # 付款凭证借方形态决定核销方式（1002 是付款贷方，不算对手科目）：
                # - 借方是费用科目（5xxx/43）：发票凭证是重复记账 → 照旧红冲，费用由付款凭证承担；
                # - 借方是应付/往来科目（1xxx/2xxx，跨期"发票先到、付款后确认"时建议层落 2202
                #   就是这种）：发票凭证是唯一费用记账，直接红冲会让费用凭空消失、利润虚增。
                #   此时红冲后按原期间重记，贷方挂账行改用付款凭证的对手科目（2241→2202），
                #   与付款凭证相互对冲平账；付款凭证与其银行流水关联一律不动。
                exp_debit_lines = [
                    line for line in await _entry_lines(session, expense_entry_id)
                    if line.direction == "debit" and str(line.account_code) != "1002"
                ]
                payable_shaped = bool(exp_debit_lines) and all(
                    str(line.account_code).startswith(("1", "2")) for line in exp_debit_lines
                )
                await reverse_journal(
                    session,
                    invoice_entry_id,
                    company_id=company_id,
                    user_id=user_id,
                    request_id=f"rev-dup-inv-{uuid4()}",
                    reason=(
                        "已关联银行实际支出凭证，冲销后按付款对手科目重订发票挂账"
                        if payable_shaped else
                        "已关联银行实际支出凭证，自动冲销重复发票挂账"
                    ),
                )
                if payable_shaped:
                    invoice_ref_id = await _repost_invoice_voucher(
                        session,
                        inv_journal=inv_journal,
                        inv_meta=inv_meta,
                        settle_line=max(exp_debit_lines, key=lambda line: money(line.amount)),
                        expense_entry_id=expense_entry_id,
                        company_id=company_id,
                        user_id=user_id,
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

        # 2. 把发票元数据反写到实际支出凭证上（已过账凭证走更新跟踪投影字段）
        exp_updates = {
            "billingStatus": "has_invoice",
            "invoiceNumber": inv_number,
            "invoiceType": inv_type,
            "linkedInvoiceEntryId": invoice_ref_id,
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

        # 3. 将 invoices 表台账的 entry_id 切换指向实际支出凭证
        from sqlalchemy import update
        await session.execute(
            update(Invoice)
            .where(Invoice.company_id == company_id, Invoice.entry_id == invoice_entry_id)
            .values(entry_id=expense_entry_id)
        )

    # 4. 同步更新待办状态表中对应的 todoKey 为已完成
    await update_todo_state(f"entry:{invoice_entry_id}:invoice_unpaid", user_id=user_id, company_id=company_id, done=True)
    await update_todo_state(f"entry:{expense_entry_id}:missing_document", user_id=user_id, company_id=company_id, done=True)

    return {
        "status": "linked",
        "invoiceEntryId": invoice_entry_id,
        # 挂账重订形态下是重记后的新发票凭证 id（draft/普通形态等于 invoiceEntryId）
        "linkedInvoiceVoucherId": invoice_ref_id,
        "expenseEntryId": expense_entry_id,
        "invoiceNumber": inv_number,
    }


async def update_todo_state(
    todo_key: str, *, user_id: str, company_id: str,
    done: Optional[bool] = None, dismissed: Optional[bool] = None,
) -> Dict[str, Any]:
    if todo_key.startswith("opening-balance-required:") and (done is True or dismissed is True):
        raise AccountingError(
            "TODO_NON_DISMISSIBLE",
            "期初建账为必办前置事项，不可忽略或手动勾选，请在期初设置页完成后自动消除",
            422,
        )
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
