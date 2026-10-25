# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Tests for tax_rules.py — pure function tests, no DB, no mock."""
import pytest
from decimal import Decimal

from services.tax_rules import (
    lookup_tax,
    compute_vat,
    sanitize_risk_tags,
    is_tax_deductible,
)


class TestLookupTax:
    def test_expense_returns_rules(self):
        rules = lookup_tax("办公费", "expense")
        assert rules["vatRate"] == 0.06
        assert rules["citDeductible"] is True
        assert rules["deductibilityLimit"] == 1.0

    def test_entertainment_has_limit(self):
        rules = lookup_tax("业务招待费", "expense")
        assert rules["deductibilityLimit"] == 0.6
        assert "60%限额扣除" in rules["riskTags"]

    def test_income_returns_rules(self):
        rules = lookup_tax("主营业务收入", "income")
        assert rules["vatRate"] == 0.06
        assert rules["citTaxable"] is True

    def test_unknown_category_returns_default(self):
        rules = lookup_tax("不存在的分类", "expense")
        assert rules["vatRate"] == 0.06
        assert rules["deductibilityLimit"] == 1.0

    def test_unknown_income_category(self):
        rules = lookup_tax("不存在的分类", "income")
        assert rules == {}


class TestComputeVat:
    def test_vat_calculation(self):
        result = compute_vat(106.0, "办公费", "expense")
        # 含税金额 106, 税率 6% => 不含税 100, 税额 6
        assert result["amountExcludingTax"] == 100.0
        assert result["vatAmount"] == 6.0
        assert result["vatRate"] == Decimal("0.060000")
        assert result["vatExempt"] is False

    def test_vat_exempt_category(self):
        result = compute_vat(100.0, "人力成本", "expense")
        # 人力成本为免税
        assert result["vatExempt"] is True
        assert result["vatRate"] == 0
        assert result["vatAmount"] == 0.0
        assert result["amountExcludingTax"] == 100.0

    def test_zero_amount(self):
        result = compute_vat(0.0, "办公费", "expense")
        assert result["vatAmount"] == 0.0
        assert result["amountExcludingTax"] == 0.0

    def test_income_vat(self):
        result = compute_vat(106.0, "主营业务收入", "income")
        assert result["vatRate"] == Decimal("0.060000")
        assert result["vatAmount"] == 6.0


class TestSanitizeRiskTags:
    def test_removes_deduction_terms(self):
        tags = ["60%限额扣除", "进项可抵扣", "需取得合规发票"]
        result = sanitize_risk_tags(tags)
        assert "进项可抵扣" not in result
        assert "60%限额扣除" in result
        assert "需取得合规发票" in result

    def test_empty_list(self):
        assert sanitize_risk_tags([]) == []

    def test_none_input(self):
        assert sanitize_risk_tags(None) == []

    def test_string_input(self):
        result = sanitize_risk_tags("进项可抵扣")
        assert result == []

    def test_all_banned_terms(self):
        tags = ["进项可抵扣", "专票可抵", "可抵进项", "抵扣进项"]
        assert sanitize_risk_tags(tags) == []


class TestIsTaxDeductible:
    def test_invoice_present_is_deductible(self):
        assert is_tax_deductible("办公费", "expense", billing_status="has_invoice") is True
        assert is_tax_deductible("办公费", "expense", has_invoice_number=True) is True

    def test_no_document_is_not_deductible(self):
        assert is_tax_deductible("办公费", "expense", billing_status="no_document") is False
        assert is_tax_deductible("办公费", "expense", billing_status="no_invoice") is False

    def test_unknown_billing_status_is_pending(self):
        # 规则要求发票而状态不明 → 待确认，绝不默认 True（计划 P1-4）
        assert is_tax_deductible("办公费", "expense") is None
        assert is_tax_deductible("办公费", "expense", billing_status="") is None
        assert is_tax_deductible("办公费", "expense", billing_status="has_receipt") is None

    def test_income_deductibility_not_applicable(self):
        # income 没有"税前扣除"概念 → 待确认而非 True
        assert is_tax_deductible("主营业务收入", "income") is None

    def test_unknown_category_is_pending(self):
        assert is_tax_deductible("不存在的分类", "expense") is None
