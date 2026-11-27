# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Regression tests for /report/archive-checklist compliance classification."""
from __future__ import annotations

from decimal import Decimal

from routers.report import _classify_archive_compliance


def test_internal_closing_entries_do_not_require_external_docs():
    comp, status, reason = _classify_archive_compliance(
        {
            "id": "closing-pnl-c1-2026-06-v1",
            "title": "2026 年 6 月期末损益结转",
            "category": "期末损益结转",
            "billingStatus": "no_document",
        },
        attachment_count=0,
        amt=Decimal("300.70"),
    )
    assert status == "compliant"
    assert comp == "无需外部单据"
    assert "内部结转" in reason

    comp2, status2, _ = _classify_archive_compliance(
        {
            "id": "v-vat-exempt",
            "title": "2026 年第 2 季度小规模纳税人免征增值税转出",
            "category": "免征增值税转入",
            "billingStatus": "no_document",
        },
        attachment_count=0,
        amt=Decimal("0.77"),
    )
    assert status2 == "compliant"
    assert comp2 == "无需外部单据"


def test_reconciled_entry_with_invoice_number_or_attachment_is_compliant():
    comp, status, reason = _classify_archive_compliance(
        {
            "id": "v-inv",
            "title": "发票-深圳市腾讯计算机系统有限公司",
            "category": "办公费",
            "billingStatus": "reconciled",
            "invoiceNumber": "26957000000148730058",
        },
        attachment_count=0,
        amt=Decimal("300.00"),
    )
    assert status == "compliant"
    assert comp == "合规"
    assert "已有发票" in reason

    comp2, status2, reason2 = _classify_archive_compliance(
        {
            "id": "v-att",
            "title": "购买办公设备",
            "category": "办公费",
            "billingStatus": "reconciled",
        },
        attachment_count=1,
        amt=Decimal("600.00"),
    )
    assert status2 == "compliant"
    assert comp2 == "合规"
    assert "已附 1 份原件" in reason2


def test_bank_native_and_income_entries_are_compliant():
    for title, category, direction in (
        ("银行结息-2026-06-21", "利息收入", "income"),
        ("微信支付提现到账-2026-06-18", "微信支付结算", "transfer"),
        ("报销垫付款-发票-深圳市腾讯计算机系统有限公司", "股东垫付报销", "expense"),
        ("微信支付月度汇总-2026-06", "主营业务收入", "income"),
    ):
        comp, status, _ = _classify_archive_compliance(
            {
                "id": "v-bank",
                "title": title,
                "category": category,
                "direction": direction,
                "billingStatus": "reconciled",
            },
            attachment_count=0,
            amt=Decimal("78.00"),
        )
        assert status == "compliant", title
        assert comp == "合规", title


def test_missing_expense_documents_explain_what_to_upload():
    comp_big, status_big, reason_big = _classify_archive_compliance(
        {
            "id": "v-exp-big",
            "title": "采购服务器托管费",
            "category": "服务费",
            "direction": "expense",
            "billingStatus": "reconciled",
        },
        attachment_count=0,
        amt=Decimal("600.00"),
    )
    assert status_big == "missing"
    assert comp_big == "待补"
    assert "缺合规发票" in reason_big

    comp_small, status_small, reason_small = _classify_archive_compliance(
        {
            "id": "v-exp-small",
            "title": "快递费",
            "category": "办公费",
            "direction": "expense",
            "billingStatus": "reconciled",
        },
        attachment_count=0,
        amt=Decimal("200.00"),
    )
    assert status_small == "missing"
    assert comp_small == "待补"
    assert "缺发票或收款凭据" in reason_small
