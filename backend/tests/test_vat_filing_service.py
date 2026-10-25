# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from decimal import Decimal

from services.vat_filing_service import build_small_scale_vat_assist


def entry(entry_id, occurred_on, debit_code, credit_code, amount, **extra):
    return {
        "id": entry_id,
        "occurDate": occurred_on,
        "category": extra.pop("category", "主营业务收入"),
        "title": extra.pop("title", entry_id),
        "voucherLines": {
            "debit": [{"accountCode": debit_code, "amount": amount}],
            "credit": [{"accountCode": credit_code, "amount": amount}],
        },
        **extra,
    }


def test_vat_assist_uses_posted_revenue_accounts_and_excludes_cash_transfers_and_interest():
    entries = [
        entry("sale", "2026-06-10", "1012", "5001", "78.00"),
        entry("settlement", "2026-06-18", "1002", "1012", "77.30", category="微信支付结算"),
        entry("interest", "2026-06-21", "1002", "5603005", "4.15", category="利息收入"),
        entry("verification", "2026-06-06", "1002", "5301", "0.01", category="营业外收入"),
    ]

    result = build_small_scale_vat_assist(entries, "2026-Q2", "quarter")

    # 申报表销售额是不含税口径：78.00 含税按 1% 还原为 77.23
    assert result["serviceSales"] == Decimal("77.23")
    assert result["goodsLaborSales"] == Decimal("0.00")
    assert result["totalSales"] == Decimal("77.23")
    assert result["isBelowThreshold"] is True
    assert result["vatExemption"] == Decimal("2.32")
    assert result["totalVatPayable"] == Decimal("0.00")
    assert result["sourceEntryCount"] == 1
    assert result["readyToFile"] is False
    assert "缺少发票类型或号码" in result["blockingIssues"][0]
    fields = {field["code"]: field for field in result["filingFields"]}
    assert fields["simple.services.rate_1"]["amount"] == Decimal("77.23")
    assert fields["simple.services.rate_1"]["editable"] is True
    assert fields["simple.services.rate_3"]["amount"] == Decimal("0.00")
    assert fields["simple.services.exempt"]["amount"] == Decimal("77.23")
    assert fields["simple.services.exempt"]["editable"] is False


def test_vat_assist_marks_complete_below_threshold_sales_ready_for_review():
    entries = [entry(
        "sale", "2026-06-10", "1012", "5001", "78.00",
        invoiceType="增值税普通发票", invoiceNumber="INV-001", vatSalesCategory="services",
    )]

    result = build_small_scale_vat_assist(entries, "2026-06", "month")

    assert result["readyToFile"] is True
    assert result["warnings"] == []
    assert result["totalVatPayable"] == Decimal("0.00")


def test_vat_assist_applies_one_percent_rate_above_threshold():
    entries = [entry(
        "sale", "2026-06-10", "1002", "5001", "101000.00",
        invoiceType="增值税普通发票", invoiceNumber="INV-002", vatSalesCategory="services",
    )]

    result = build_small_scale_vat_assist(entries, "2026-06", "month")

    assert result["isBelowThreshold"] is False
    assert result["taxableBase"] == Decimal("100000.00")
    assert result["vatBeforeRelief"] == Decimal("3000.00")
    assert result["vatReduction"] == Decimal("2000.00")
    assert result["totalVatPayable"] == Decimal("1000.00")
    assert result["cityMaintenanceTax"] == Decimal("35.00")
    assert result["educationSurcharge"] == Decimal("15.00")
    assert result["localEducationSurcharge"] == Decimal("10.00")
    fields = {field["code"]: field for field in result["filingFields"]}
    assert fields["simple.services.rate_1"]["amount"] == Decimal("100000.00")
    assert fields["simple.services.exempt"]["amount"] == Decimal("0.00")


def test_vat_assist_respects_confirmed_billing_status():
    """待办里已给出开票结论的销售（未开票收入/已开票）不再计入缺票拦截。

    口径脱节回归（2026-10-25）：老板在待办把销售标为未开票收入/已开票后，
    待办消失，但申报页仍提示「N笔销售缺少发票类型或号码」反复拦截。
    """
    entries = [
        entry("confirmed-no-invoice", "2026-06-10", "1012", "5001", "50.00",
              billingStatus="no_invoice", vatSalesCategory="services"),
        entry("confirmed-issued", "2026-06-11", "1012", "5001", "30.00",
              billingStatus="issued", vatSalesCategory="services"),
        entry("unconfirmed", "2026-06-12", "1012", "5001", "20.00",
              vatSalesCategory="services"),
    ]

    result = build_small_scale_vat_assist(entries, "2026-Q2", "quarter")

    assert result["blockingIssues"] == [
        "1笔销售缺少发票类型或号码，需确认是否开具专票、放弃免税或未开票"
    ]
    assert result["readyToFile"] is False

    # 全部给出结论后不再拦截
    entries[2]["billingStatus"] = "has_invoice"
    result = build_small_scale_vat_assist(entries, "2026-Q2", "quarter")
    assert result["blockingIssues"] == []
    assert result["readyToFile"] is True
