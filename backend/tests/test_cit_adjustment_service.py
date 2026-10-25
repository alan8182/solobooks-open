# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""企业所得税纳税调整（A105000 简化口径）的确定性计算测试。

计划 §8 验收：收入 100 万 / 招待 1 万 → 调增 5000；广宣 20 万 → 调增 5 万。
这些数字是法定限额，必须由规则算出、可精确复算，不能交给 LLM。
"""
from __future__ import annotations

from decimal import Decimal

from services.cit_adjustment_service import build_cit_adjustments, evaluate_deductibility


def _expense(entry_id: str, amount: str, title: str, *, occur: str = "2026-08-10",
             billing: str | None = None, invoice: tuple[str, str] | None = None) -> dict:
    entry = {
        "id": entry_id,
        "direction": "expense",
        "amount": amount,
        "title": title,
        "category": title,
        "occurDate": occur,
    }
    if billing is not None:
        entry["billingStatus"] = billing
    if invoice is not None:
        entry["invoiceType"], entry["invoiceNumber"] = invoice
    return entry


def _profit(revenue: str) -> dict:
    return {"revenue": revenue}


class TestEntertainmentCap:
    def test_plan_acceptance_10k_on_1m_revenue(self):
        """收入 100 万、有票招待 1 万：可扣 min(6000, 5000)=5000 → 调增 5000。"""
        entries = [_expense("ent-1", "10000.00", "业务招待费", billing="has_invoice")]
        result = build_cit_adjustments(entries, _profit("1000000.00"))
        assert result["totalIncrease"] == Decimal("5000.00")
        assert result["items"][0]["code"] == "A105000-14"

    def test_under_both_caps_small_amount(self):
        """收入 100 万、有票招待 1000：60%=600 低于 5‰=5000 → 只调增 400。"""
        entries = [_expense("ent-2", "1000.00", "客户招待餐费", billing="has_invoice")]
        result = build_cit_adjustments(entries, _profit("1000000.00"))
        assert result["totalIncrease"] == Decimal("400.00")

    def test_zero_entertainment_no_increase(self):
        """无招待支出 → 限额项不生成。"""
        result = build_cit_adjustments([], _profit("1000000.00"))
        assert result["totalIncrease"] == Decimal("0.00")
        assert result["items"] == []

    def test_60_percent_is_lower_cap(self):
        """收入 1000 万、招待 1 万：5‰=50000 高于 60%=6000 → 调增 4000。"""
        entries = [_expense("ent-3", "10000.00", "业务招待费", billing="has_invoice")]
        result = build_cit_adjustments(entries, _profit("10000000.00"))
        assert result["totalIncrease"] == Decimal("4000.00")


class TestAdvertisingCap:
    def test_plan_acceptance_200k_on_1m_revenue(self):
        """收入 100 万、有票广宣 20 万：限额 15 万 → 调增 5 万（可结转）。"""
        entries = [_expense("ad-1", "200000.00", "广告宣传费", billing="has_invoice")]
        result = build_cit_adjustments(entries, _profit("1000000.00"))
        assert result["totalIncrease"] == Decimal("50000.00")
        assert "结转" in result["items"][0]["label"]

    def test_within_cap_no_increase(self):
        entries = [_expense("ad-2", "100000.00", "推广费", billing="has_invoice")]
        result = build_cit_adjustments(entries, _profit("1000000.00"))
        assert result["totalIncrease"] == Decimal("0.00")


class TestFineAndMissingDocument:
    def test_fine_fully_added_back(self):
        """罚款、滞纳金全额调增，不受任何限额保护。"""
        entries = [_expense("fine-1", "3000.00", "税收滞纳金", billing="has_invoice")]
        result = build_cit_adjustments(entries, _profit("1000000.00"))
        assert result["totalIncrease"] == Decimal("3000.00")
        assert result["items"][0]["code"] == "A105000-19"

    def test_missing_document_fully_added_back(self):
        """无票支出全额调增，不再叠加限额（有票招待才走限额规则）。"""
        entries = [_expense("nodoc-1", "8000.00", "业务招待费", billing="no_document")]
        result = build_cit_adjustments(entries, _profit("1000000.00"))
        assert result["totalIncrease"] == Decimal("8000.00")
        assert result["items"][0]["code"] == "A105000-30"

    def test_documented_by_invoice_number_even_without_billing_status(self):
        """有票号即视为有票，走限额而非全额调增。"""
        entries = [_expense("doc-1", "10000.00", "业务招待费",
                            invoice=("增值税专用发票", "25619000000123456"))]
        result = build_cit_adjustments(entries, _profit("1000000.00"))
        assert result["totalIncrease"] == Decimal("5000.00")
        assert result["items"][0]["code"] == "A105000-14"

    def test_out_of_period_expense_excluded(self):
        entries = [_expense("old-1", "10000.00", "业务招待费", occur="2025-01-10",
                            billing="has_invoice")]
        result = build_cit_adjustments(
            entries, _profit("1000000.00"),
            period_start="2026-01-01", period_end="2026-12-31",
        )
        assert result["totalIncrease"] == Decimal("0.00")


class TestEvaluateDeductibility:
    """三态判定：True 可扣除 / False 不得扣除 / None 待确认，绝不默认 True。"""

    def test_invoice_present(self):
        assert evaluate_deductibility("invoice", "has_invoice", False) is True
        assert evaluate_deductibility("invoice", "no_invoice", True) is True

    def test_explicit_no_document(self):
        assert evaluate_deductibility("invoice", "no_document", False) is False
        assert evaluate_deductibility("invoice", "no_invoice", False) is False

    def test_receipt_alone_is_pending_for_invoice_required(self):
        """规则要求发票时，收据/对账不构成合规扣除凭证 → 待确认。"""
        assert evaluate_deductibility("invoice", "has_receipt", False) is None
        assert evaluate_deductibility("invoice", "reconciled", False) is None

    def test_unknown_status_is_pending(self):
        assert evaluate_deductibility("invoice", "", False) is None
        assert evaluate_deductibility("invoice", None, False) is None

    def test_contract_based_expense(self):
        assert evaluate_deductibility("contract", "has_receipt", False) is True
        assert evaluate_deductibility("contract", "", False) is None
