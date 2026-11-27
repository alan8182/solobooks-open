# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from decimal import Decimal

from services.bank_service import (
    build_balance_reconciliation,
    build_group_reconciliation_check,
    diagnose_matched_statement_effect,
    extract_statement_counterparty,
    normalize_statement_description,
)


def test_extract_statement_counterparty():
    assert extract_statement_counterparty("汇兑 | 投资款 (对方: 中国工商银行)") == "中国工商银行"
    assert extract_statement_counterparty("入账 | 附言: 订单 123 (对方: 客户A)") == "客户A"
    assert extract_statement_counterparty("网银转账") == ""
    assert extract_statement_counterparty(None) == ""


def test_normalize_statement_description_removes_repeated_comment():
    assert normalize_statement_description(
        "汇兑 | 投资款 | 附言: 投资款 (对方: 中国工商银行)"
    ) == "汇兑 | 投资款 (对方: 中国工商银行)"


def test_normalize_statement_description_keeps_distinct_comment_and_drops_null():
    assert normalize_statement_description(
        "入账 | null | 附言: 订单 123 (对方: null)"
    ) == "入账 | 附言: 订单 123"


def test_balance_reconciliation_uses_first_pre_transaction_balance_as_opening():
    result = build_balance_reconciliation([
        {"id": "2", "transaction_date": "2026-06-02", "amount": -30, "balance": 120},
        {"id": "1", "transaction_date": "2026-06-01", "amount": 50, "balance": 150},
    ], book_bank_balance=120)

    assert result == {
        "has_data": True,
        "opening_balance": 100.0,
        "income_total": 50.0,
        "expense_total": 30.0,
        "calculated_closing_balance": 120.0,
        "bank_closing_balance": 120.0,
        "flow_difference": 0.0,
        "book_bank_balance": 120,
        "book_difference": 0,
        "flow_balanced": True,
    }


def test_balance_reconciliation_orders_same_day_rows_by_file_row_index():
    """同日多笔按文件行序还原时序，与 id 大小无关（倒序导出的真实顺序在行序里）。"""
    result = build_balance_reconciliation([
        {"id": "import-10", "transaction_date": "2026-06-04", "amount": 0.74, "balance": 100.0, "source_row_index": 1},
        {"id": "import-11", "transaction_date": "2026-06-04", "amount": -0.74, "balance": 99.26, "source_row_index": 0},
        {"id": "import-09", "transaction_date": "2026-06-06", "amount": 1.0, "balance": 101.0, "source_row_index": 2},
    ], book_bank_balance=101)

    assert result["opening_balance"] == 100.0
    assert result["bank_closing_balance"] == 101.0
    assert result["flow_difference"] == 0.0
    assert result["flow_balanced"] is True


def test_group_reconciliation_bank_effect_wins_over_clearing_fallthrough():
    """结算链整组对账按银行影响，清算户净额不得与结算凭证重复计入（2026-11 生产）。

    [结算凭证（借 1002 / 贷 1012001 各 58.47）+ 收入计提（借 1012001 / 贷 5001 各
    59.00）+ 手续费（贷 1012001 0.53）] 三张一起挂到 58.47 的到账流水时，旧口径
    把清算户净额（59 - 0.53）与结算凭证的银行影响叠加成 116.94，结账向导
    「高风险对账问题」误锁关账。组内存在 1002 影响时必须整组只按银行影响；
    该组完全没有 1002 影响时仍按清算净额（毛收入 - 手续费 = 到账净额）对账。
    """
    settlement = {"id": "settle", "title": "微信支付结算到账", "voucherLines": {
        "debit": [{"accountCode": "1002", "amount": "58.47"}],
        "credit": [{"accountCode": "1012001", "amount": "58.47"}],
    }}
    accrual = {"id": "accrual", "title": "签小抄-人工审核-基础版", "voucherLines": {
        "debit": [{"accountCode": "1012001", "amount": "59.00"}],
        "credit": [{"accountCode": "5001", "amount": "59.00"}],
    }}
    fee = {"id": "fee", "title": "微信手续费", "voucherLines": {
        "debit": [{"accountCode": "5603002", "amount": "0.53"}],
        "credit": [{"accountCode": "1012001", "amount": "0.53"}],
    }}

    check = build_group_reconciliation_check("58.47", [settlement, accrual, fee])
    assert check["amountMatches"] is True
    assert check["directionMatches"] is True
    assert str(check["entryBankEffect"]) == "58.47"

    check_no_bank = build_group_reconciliation_check("58.47", [accrual, fee])
    assert check_no_bank["amountMatches"] is True
    assert str(check_no_bank["entryBankEffect"]) == "58.47"


def test_diagnose_missing_wechat_settlement_voucher():
    issue = diagnose_matched_statement_effect(
        {"id": "stmt-1", "transaction_date": "2026-06-18", "amount": 58.47, "description": "微信结算"},
        [
            {
                "id": "sale",
                "title": "销售收入",
                "voucherLines": {
                    "debit": [{"accountCode": "1012001", "amount": 59}],
                    "credit": [{"accountCode": "5001", "amount": 59}],
                },
            },
            {
                "id": "fee",
                "title": "微信手续费",
                "voucherLines": {
                    "debit": [{"accountCode": "5603002", "amount": 0.53}],
                    "credit": [{"accountCode": "1012001", "amount": 0.53}],
                },
            },
        ],
    )

    assert issue["type"] == "missing_settlement_voucher"
    assert issue["bankEffect"] == 0
    assert issue["settlementEffect"] == Decimal("58.47")
    assert issue["difference"] == Decimal("58.47")
    assert issue["repairable"] is True


def test_diagnose_reversed_bank_direction_for_imported_entry():
    issue = diagnose_matched_statement_effect(
        {"id": "stmt-2", "transaction_date": "2026-06-04", "amount": -0.74, "description": "验证付款"},
        [{
            "id": "bank-entry",
            "source": "bank_import",
            "title": "验证付款",
            "voucherLines": {
                "debit": [{"accountCode": "1002", "amount": 0.74}],
                "credit": [{"accountCode": "1221", "amount": 0.74}],
            },
        }],
    )

    assert issue["type"] == "reversed_bank_direction"
    assert issue["bankEffect"] == Decimal("0.74")
    assert issue["difference"] == Decimal("-1.48")
    assert issue["repairable"] is True
