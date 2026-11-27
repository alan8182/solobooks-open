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


def _salary_accrual(
    entry_id: str,
    *,
    payable_salary: str,
    withheld_ss: str = "0.00",
    withheld_iit: str = "0.00",
    employer_ss: str = "0.00",
    occur: str = "2026-06-30",
) -> dict:
    total = (
        Decimal(payable_salary)
        + Decimal(withheld_ss)
        + Decimal(withheld_iit)
        + Decimal(employer_ss)
    )
    return {
        "id": entry_id,
        "direction": "expense",
        "amount": str(total),
        "title": "计提员工工资及社保",
        "category": "人力成本",
        "sourceType": "salary_accrual",
        "occurDate": occur,
        "billingStatus": "has_receipt",
        "voucherLines": {
            "credit": [
                {"accountCode": "221101", "amount": payable_salary},
                {"accountCode": "221104", "amount": withheld_ss},
                {"accountCode": "2221012", "amount": withheld_iit},
                {"accountCode": "221102", "amount": employer_ss},
            ],
        },
    }


class TestPayrollCappedExpenses:
    """三项经费（福利 14% / 工会 2% / 教育 8% 或个体户 2.5%）确定性计算。"""

    def test_salary_accrual_gross_excludes_employer_ss_and_skips_salary_pay(self):
        """应发工资基数 = 221101+221104+2221012，剔除 221102 单位社保且不与 salary_pay 重复。"""
        entries = [
            _salary_accrual(
                "sal-1",
                payable_salary="80000.00",
                withheld_ss="15000.00",
                withheld_iit="5000.00",
                employer_ss="25000.00",
            ),
            # salary_pay 实发不应重复计入基数
            {
                "id": "sal-pay-1",
                "direction": "expense",
                "amount": "80000.00",
                "title": "银行代发工资",
                "category": "人力成本",
                "sourceType": "salary_pay",
                "occurDate": "2026-07-05",
                "billingStatus": "has_receipt",
            },
            # 福利费 20,000：基数 100,000 × 14% = 14,000 → 调增 6,000
            _expense("wel-1", "20000.00", "员工福利费", billing="has_invoice"),
            # 工会经费 3,000：基数 100,000 × 2% = 2,000 → 调增 1,000
            _expense("uni-1", "3000.00", "工会经费", billing="has_invoice"),
            # 教育经费 10,000：企税 100,000 × 8% = 8,000 → 调增 2,000
            _expense("edu-1", "10000.00", "职工教育经费", billing="has_invoice"),
        ]
        result = build_cit_adjustments(entries, _profit("1000000.00"))
        assert result["salaryBase"] == Decimal("100000.00")
        by_code = {item["code"]: item for item in result["items"]}
        assert by_code["A105000-02"]["amount"] == Decimal("6000.00")
        assert by_code["A105000-03"]["amount"] == Decimal("1000.00")
        assert by_code["A105000-04"]["amount"] == Decimal("2000.00")
        assert "可结转" in by_code["A105000-04"]["label"]
        assert result["totalIncrease"] == Decimal("9000.00")

    def test_iit_education_cap_uses_2_5_percent(self):
        """个体户/个独职工教育经费按 2.5% 限额，且可剔除业主工资。"""
        entries = [
            _salary_accrual("sal-2", payable_salary="120000.00"),
            _expense("edu-2", "5000.00", "职工教育经费", billing="has_invoice"),
        ]
        # 剔除业主工资 20,000 → 从业人员基数 100,000 → 2.5% = 2,500 → 调增 2,500
        result = build_cit_adjustments(
            entries,
            _profit("500000.00"),
            entity_type="individual_business",
            exclude_salary=Decimal("20000.00"),
        )
        assert result["salaryBase"] == Decimal("100000.00")
        assert result["items"][0]["code"] == "A105000-04"
        assert result["items"][0]["amount"] == Decimal("2500.00")

    def test_zero_salary_base_adds_back_all_three_items(self):
        """当年工资基数为 0 时，三项经费全额调增并提示工资基数为 0。"""
        entries = [_expense("wel-0", "5000.00", "员工福利费", billing="has_invoice")]
        result = build_cit_adjustments(entries, _profit("200000.00"))
        assert result["salaryBase"] == Decimal("0.00")
        assert result["totalIncrease"] == Decimal("5000.00")
        assert "基数为 0" in result["items"][0]["basis"]

    def test_undocumented_welfare_only_goes_to_no_doc(self):
        """无票福利费只进 A105000-30 全额调增，不叠加 14% 限额。"""
        entries = [
            _salary_accrual("sal-3", payable_salary="100000.00"),
            _expense("wel-nodoc", "20000.00", "员工福利费", billing="no_document"),
        ]
        result = build_cit_adjustments(entries, _profit("500000.00"))
        assert result["totalIncrease"] == Decimal("20000.00")
        assert [item["code"] for item in result["items"]] == ["A105000-30"]


class TestDonationCap:
    """公益性捐赠支出：企税利润总额 12% / 个体户及个独应纳税所得额 30%。"""

    def test_cit_donation_12_percent_of_total_profit(self):
        entries = [_expense("don-1", "20000.00", "捐赠支出", billing="has_invoice")]
        result = build_cit_adjustments(
            entries,
            {"revenue": "500000.00", "total_profit": "100000.00"},
        )
        # 100,000 × 12% = 12,000 → 调增 8,000
        assert result["totalIncrease"] == Decimal("8000.00")
        assert result["items"][0]["code"] == "A105000-17"
        assert "公益捐赠票据" in result["items"][0]["basis"]

    def test_cit_loss_profit_makes_donation_fully_added_back(self):
        entries = [_expense("don-loss", "5000.00", "公益捐赠支出", billing="has_invoice")]
        result = build_cit_adjustments(
            entries,
            {"revenue": "50000.00", "total_profit": "-10000.00"},
        )
        assert result["totalIncrease"] == Decimal("5000.00")

    def test_iit_donation_30_percent_of_taxable_income_before_donation(self):
        entries = [_expense("don-iit", "50000.00", "捐赠支出", billing="has_invoice")]
        # 账面利润 110,000 + 已扣捐赠 50,000 - 减除费用 60,000 = 扣除捐赠前应纳税所得额 100,000
        # 30% 限额 = 30,000 → 调增 20,000
        result = build_cit_adjustments(
            entries,
            {"revenue": "500000.00", "total_profit": "110000.00"},
            entity_type="individual_business",
            pre_deductions=Decimal("60000.00"),
        )
        assert result["totalIncrease"] == Decimal("20000.00")
        assert result["items"][0]["code"] == "A105000-17"

    def test_undocumented_donation_only_goes_to_no_doc(self):
        entries = [_expense("don-nodoc", "20000.00", "捐赠支出", billing="no_document")]
        result = build_cit_adjustments(
            entries,
            {"revenue": "500000.00", "total_profit": "100000.00"},
        )
        assert result["totalIncrease"] == Decimal("20000.00")
        assert [item["code"] for item in result["items"]] == ["A105000-30"]
