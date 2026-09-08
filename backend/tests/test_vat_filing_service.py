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
        entry("sale", "2026-06-10", "1012", "6001", "78.00"),
        entry("settlement", "2026-06-18", "1002", "1012", "77.30", category="微信支付结算"),
        entry("interest", "2026-06-21", "1002", "6603", "4.15", category="利息收入"),
        entry("verification", "2026-06-06", "1002", "6301", "0.01", category="营业外收入"),
    ]

    result = build_small_scale_vat_assist(entries, "2026-Q2", "quarter")

    assert result["serviceSales"] == Decimal("78.00")
    assert result["goodsLaborSales"] == Decimal("0.00")
    assert result["totalSales"] == Decimal("78.00")
    assert result["isBelowThreshold"] is True
    assert result["vatExemption"] == Decimal("2.34")
    assert result["totalVatPayable"] == Decimal("0.00")
    assert result["sourceEntryCount"] == 1
    assert result["readyToFile"] is False
    assert "缺少发票类型或号码" in result["blockingIssues"][0]
    fields = {field["code"]: field for field in result["filingFields"]}
    assert fields["simple.services.rate_1"]["amount"] == Decimal("78.00")
    assert fields["simple.services.rate_1"]["editable"] is True
    assert fields["simple.services.rate_3"]["amount"] == Decimal("0.00")
    assert fields["simple.services.exempt"]["amount"] == Decimal("78.00")
    assert fields["simple.services.exempt"]["editable"] is False


def test_vat_assist_marks_complete_below_threshold_sales_ready_for_review():
    entries = [entry(
        "sale", "2026-06-10", "1012", "6001", "78.00",
        invoiceType="增值税普通发票", invoiceNumber="INV-001", vatSalesCategory="services",
    )]

    result = build_small_scale_vat_assist(entries, "2026-06", "month")

    assert result["readyToFile"] is True
    assert result["warnings"] == []
    assert result["totalVatPayable"] == Decimal("0.00")


def test_vat_assist_applies_one_percent_rate_above_threshold():
    entries = [entry(
        "sale", "2026-06-10", "1002", "6001", "101000.00",
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
