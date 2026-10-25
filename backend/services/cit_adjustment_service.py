# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""企业所得税纳税调整的确定性计算（A105000 简化口径）。

会计利润 ≠ 应纳税所得额。税会差异里最常见、且完全可以从账上算出来的几项：
- 业务招待费：按发生额 60% 扣除，且不超过当年销售（营业）收入的 5‰（实施条例第 43 条）
- 广告费和业务宣传费：不超过当年销售（营业）收入 15%，超出部分结转以后年度（第 44 条）
- 罚款、滞纳金：不得扣除（企业所得税法第 10 条）
- 无票支出：账上已计费用但没有合规扣除凭证，汇算时必须调增

这些数字必须由规则算出，不能交给 LLM 猜——税率与扣除限额是法定值。
计算结果作为 A200000 第 19-24 行的"系统测算默认值"，用户仍可修改并确认。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from core.money import ZERO, money

ENTERTAINMENT_RATE = Decimal("0.6")
ENTERTAINMENT_REVENUE_CAP = Decimal("0.005")  # 5‰
ADVERTISING_REVENUE_CAP = Decimal("0.15")
SMALL_MICRO_CIT_RATE = Decimal("0.05")
STANDARD_CIT_RATE = Decimal("0.25")

FINE_KEYWORDS = ("罚款", "罚金", "滞纳金")
ENTERTAINMENT_KEYWORDS = ("招待", "餐饮")
ADVERTISING_KEYWORDS = ("广告", "宣传", "推广")


@dataclass
class CitAdjustmentItem:
    code: str
    label: str
    amount: Decimal
    basis: str
    detail: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "amount": money(self.amount),
            "basis": self.basis,
            "detail": self.detail,
        }


def _entry_amount(entry: dict[str, Any]) -> Decimal:
    return money(entry.get("amount") or ZERO)


def _entry_text(entry: dict[str, Any], *keys: str) -> str:
    return " ".join(str(entry.get(key) or "") for key in keys)


def _is_fine(entry: dict[str, Any]) -> bool:
    text = _entry_text(entry, "category", "title")
    return any(keyword in text for keyword in FINE_KEYWORDS)


def _missing_document(entry: dict[str, Any]) -> bool:
    if entry.get("direction") != "expense":
        return False
    billing = str(entry.get("billingStatus") or "")
    if billing in {"has_invoice", "has_receipt", "reconciled", "issued"}:
        return False
    if entry.get("invoiceType") and entry.get("invoiceNumber"):
        return False
    return True


def _entry_kind(entry: dict[str, Any]) -> str:
    """entertainment / advertising / other（用于区分限额规则适用对象）。"""
    text = _entry_text(entry, "category", "title")
    if any(keyword in text for keyword in ENTERTAINMENT_KEYWORDS):
        return "entertainment"
    if any(keyword in text for keyword in ADVERTISING_KEYWORDS):
        return "advertising"
    return "other"


def _in_period(entry: dict[str, Any], period_start: Any, period_end: Any) -> bool:
    occurred = str(entry.get("occurDate") or "")[:10]
    if period_start and occurred and occurred < str(period_start)[:10]:
        return False
    if period_end and occurred and occurred > str(period_end)[:10]:
        return False
    return True


def build_cit_adjustments(
    entries: list[dict[str, Any]],
    profit: dict[str, Any],
    period_start: Any = None,
    period_end: Any = None,
) -> dict[str, Any]:
    """根据已过账分录与利润表科目余额计算纳税调增明细。

    ``entries`` 需要是已过账的权威分录（posted_entries_for_reports 的输出）。
    无分录上下文（entries 为空）时退化为仅按利润表科目余额测算限额类调增。
    """
    revenue = money(profit.get("revenue") or ZERO)
    profit_entertainment = money(profit.get("entertainment") or ZERO)
    profit_advertising = money(profit.get("advertising") or ZERO)

    # 逐笔区分"有票"与"无票"：
    # - 有票的招待/广宣走限额规则；无票的全额不得扣除，不再叠加限额（避免重复调增）
    documented: dict[str, Decimal] = {"entertainment": ZERO, "advertising": ZERO}
    undocumented = ZERO
    fine_amount = ZERO
    fine_detail: list[dict[str, Any]] = []
    no_document_detail: list[dict[str, Any]] = []

    for entry in entries:
        if entry.get("direction") != "expense" or not _in_period(entry, period_start, period_end):
            continue
        amount = _entry_amount(entry)
        if amount == ZERO:
            continue
        kind = _entry_kind(entry)
        is_fine = _is_fine(entry)
        if is_fine:
            fine_amount += amount
            fine_detail.append({
                "entryId": str(entry.get("id") or ""),
                "title": str(entry.get("title") or ""),
                "occurDate": str(entry.get("occurDate") or "")[:10],
                "amount": amount,
            })
            continue
        if _missing_document(entry):
            undocumented += amount
            no_document_detail.append({
                "entryId": str(entry.get("id") or ""),
                "title": str(entry.get("title") or ""),
                "occurDate": str(entry.get("occurDate") or "")[:10],
                "amount": amount,
            })
            continue
        if kind in documented:
            documented[kind] += amount

    items: list[CitAdjustmentItem] = []

    # 1. 业务招待费：按发生额 60% 扣除，且不超过当年销售（营业）收入 5‰
    entertainment = documented["entertainment"] if entries else profit_entertainment
    if entertainment > ZERO:
        allowed = min(
            money(entertainment * ENTERTAINMENT_RATE),
            money(revenue * ENTERTAINMENT_REVENUE_CAP),
        )
        increase = money(entertainment - allowed)
        if increase > ZERO:
            items.append(CitAdjustmentItem(
                code="A105000-14",
                label="业务招待费超限额调增",
                amount=increase,
                basis=(
                    f"有票发生额 {entertainment}，可扣除 "
                    f"min(60%={money(entertainment * ENTERTAINMENT_RATE)}, 收入5‰={money(revenue * ENTERTAINMENT_REVENUE_CAP)})={allowed}"
                ),
            ))

    # 2. 广告费和业务宣传费：收入 15% 限额，超出部分结转以后年度
    advertising = documented["advertising"] if entries else profit_advertising
    if advertising > ZERO:
        allowed = money(revenue * ADVERTISING_REVENUE_CAP)
        increase = money(advertising - allowed)
        if increase > ZERO:
            items.append(CitAdjustmentItem(
                code="A105000-16",
                label="广告费和业务宣传费超限额调增（可结转）",
                amount=increase,
                basis=f"有票发生额 {advertising}，限额 收入×15%={allowed}，超出部分可结转以后年度",
            ))

    # 3. 罚款、滞纳金：不得扣除，全额调增
    if fine_amount > ZERO:
        items.append(CitAdjustmentItem(
            code="A105000-19",
            label="罚款、滞纳金不得扣除调增",
            amount=money(fine_amount),
            basis="《企业所得税法》第十条：罚款、罚金和滞纳金不得税前扣除",
            detail=fine_detail,
        ))

    # 4. 无票支出：账上已计费用但缺少合规扣除凭证（招待/广宣无票时在此全额调增）
    if undocumented > ZERO:
        items.append(CitAdjustmentItem(
            code="A105000-30",
            label="无合规扣除凭证支出调增",
            amount=money(undocumented),
            basis="取得发票等合规凭证前不得税前扣除，补票后可转回",
            detail=no_document_detail,
        ))

    total_increase = money(sum((item.amount for item in items), ZERO))
    return {
        "items": [item.as_dict() for item in items],
        "totalIncrease": total_increase,
        "revenue": revenue,
        "entertainmentExpense": profit_entertainment,
        "advertisingExpense": profit_advertising,
        "pendingDocumentAmount": ZERO,
    }


def evaluate_deductibility(
    required_billing: str | None,
    billing_status: str | None,
    has_invoice_number: bool,
) -> bool | None:
    """按扣除凭证规则判断单笔支出当前是否可税前扣除。

    返回三态：True 可扣除 / False 不得扣除 / None 待确认（缺单据信息）。
    绝不默认 True——把无票支出当成可扣除是所得税底稿最典型的错报；
    也不武断返回 False——用户只是还没录入票据状态时，标成"待确认"。
    无票支出在汇算口径仍会被 build_cit_adjustments 全额调增（_missing_document），
    这里三态只决定单据层标记，两套口径互不掩盖。
    """
    billing = str(billing_status or "")
    if required_billing == "invoice":
        # 规则要求发票：收据/对账不构成合规扣除凭证，只能算待确认
        if billing in {"has_invoice", "issued"} or has_invoice_number:
            return True
        if billing in {"no_document", "no_invoice"}:
            return False
        return None
    if billing in {"has_invoice", "has_receipt", "issued", "reconciled"}:
        return True
    return None
