# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.
"""Self-check for standard consulting revenue mapping and receivable channel."""
from decimal import Decimal
from services.account_mapper import get_voucher_hint, lookup, normalize_payer
from services.vat_service import is_vat_sales_entry


def test_standard_revenue_mapping():
    consulting = lookup("咨询服务收入")
    assert consulting is not None, "咨询服务收入 should be mapped"
    assert consulting["accountCode"] == "5001001"
    assert consulting["accountName"] == "主营业务收入-咨询服务"
    assert consulting["accountType"] == "损益-收入"

    tech = lookup("技术服务收入")
    assert tech is not None
    assert tech["accountCode"] == "5001002"
    assert tech["accountName"] == "主营业务收入-技术服务"

    assert is_vat_sales_entry("咨询服务收入") is True
    assert is_vat_sales_entry("技术服务收入") is True


def test_receivable_normalization():
    assert normalize_payer("receivable") == "receivable"
    assert normalize_payer("accounts_receivable") == "receivable"
    assert normalize_payer("待到账") == "receivable"
    assert normalize_payer("挂应收") == "receivable"
    assert normalize_payer("待收款") == "receivable"


def test_receivable_voucher_generation():
    voucher = get_voucher_hint("income", "咨询服务收入", 10100, payer="receivable", vat_rate=0.01)
    debit = voucher["debit"]
    credit = voucher["credit"]

    assert len(debit) == 1
    assert debit[0]["accountCode"] == "1122"
    assert debit[0]["accountName"] == "应收账款"
    assert debit[0]["amount"] == Decimal("10100.00")

    assert len(credit) == 2
    assert credit[0]["accountCode"] == "5001001"
    assert credit[0]["amount"] == Decimal("10000.00")
    assert credit[1]["accountCode"] in ("22210101", "2221026")
    assert credit[1]["amount"] == Decimal("100.00")


if __name__ == "__main__":
    test_standard_revenue_mapping()
    test_receivable_normalization()
    test_receivable_voucher_generation()
    print("All consulting income and receivable self-checks passed! ✓")
