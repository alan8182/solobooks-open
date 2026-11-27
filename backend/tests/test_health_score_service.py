# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Standalone unit tests for Deterministic Health Score Engine.
Can be run directly with python3 without pytest/external fixtures.
"""

import sys
from decimal import Decimal
from pathlib import Path

# Ensure backend root is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from services.health_score_service import calculate_health_score


def test_clean_healthy_entries():
    entries = [
        {"id": "e1", "direction": "income", "amount": 20000, "category": "技术服务费", "billingStatus": "has_invoice"},
        {"id": "e2", "direction": "expense", "amount": 3000, "category": "办公费", "billingStatus": "has_invoice"},
        {"id": "e3", "direction": "expense", "amount": 2000, "category": "研发费", "billingStatus": "has_receipt"},
    ]
    checks = {"canClose": True, "checks": {"trial_balance": True, "numbering_gap": False, "profit_transfer": True}}
    result = calculate_health_score(entries, target_month="2026-06", checks=checks)

    assert result["totalScore"] >= 90, f"Expected >= 90, got {result['totalScore']}"
    assert result["level"] == "优秀"
    assert result["levelCode"] == "excellent"
    assert len(result["penalties"]) == 0
    assert result["dimensions"]["发票合规度"] == 100
    assert result["dimensions"]["账务完整性"] == 100


def test_unbilled_expenses_penalty():
    entries = [
        {"id": "e1", "direction": "income", "amount": 10000, "category": "技术服务费", "billingStatus": "has_invoice"},
        {"id": "e2", "direction": "expense", "amount": 4500, "category": "咨询费", "billingStatus": "none"}, # 大额无票
        {"id": "e3", "direction": "expense", "amount": 200, "category": "餐饮费", "billingStatus": "none"},
    ]
    result = calculate_health_score(entries, target_month="2026-06")

    # 应该有无票扣分
    assert result["totalScore"] < 90
    invoice_penalties = [p for p in result["penalties"] if p["dimensionCode"] == "invoice"]
    assert len(invoice_penalties) >= 1
    # 包含了大额无票提示
    assert any("超千元" in p["title"] for p in invoice_penalties)


def test_tax_risk_tags():
    entries = [
        {"id": "e1", "direction": "income", "amount": 10000, "category": "技术服务费", "billingStatus": "has_invoice"},
        {
            "id": "e2",
            "direction": "expense",
            "amount": 5000,
            "category": "其他往来",
            "billingStatus": "has_receipt",
            "riskTags": ["股东借款", "大额个人往来"],
        },
    ]
    result = calculate_health_score(entries, target_month="2026-06")
    tax_penalties = [p for p in result["penalties"] if p["dimensionCode"] == "tax"]
    assert len(tax_penalties) >= 1
    assert "敏感涉税交易" in tax_penalties[0]["title"]
    assert result["taxRiskLevel"] in ("中", "高")


def test_close_checks_failure():
    entries = [
        {"id": "e1", "direction": "income", "amount": 10000, "category": "服务费", "billingStatus": "has_invoice"}
    ]
    checks = {
        "canClose": False,
        "checks": {
            "trial_balance": False,
            "numbering_gap": True,
            "profit_transfer": False,
        }
    }
    result = calculate_health_score(entries, target_month="2026-06", checks=checks)
    integrity_penalties = [p for p in result["penalties"] if p["dimensionCode"] == "integrity"]
    assert len(integrity_penalties) >= 2
    assert result["totalScore"] < 85


def test_empty_and_boundaries():
    # 空账套
    res_empty = calculate_health_score([], target_month="2026-06")
    assert 0 <= res_empty["totalScore"] <= 100
    assert "dimensionDetails" in res_empty
    assert len(res_empty["dimensionDetails"]) == 5

    # 极端亏损
    entries_loss = [
        {"id": f"loss-{i}", "direction": "expense", "amount": 10000, "billingStatus": "none", "riskTags": ["罚款"]}
        for i in range(10)
    ]
    res_loss = calculate_health_score(entries_loss, target_month="2026-06")
    assert 0 <= res_loss["totalScore"] <= 100
    assert res_loss["levelCode"] in ("fair", "poor")


def test_reconciled_invoice_number_and_salary_depreciation_exemptions():
    """已对账但携带 invoiceNumber 的支出应计为有票；工资折旧与手续费不触发无票扣分。"""
    entries = [
        {"id": "inc-1", "direction": "income", "amount": 20000, "category": "主营业务收入", "billingStatus": "reconciled", "invoiceNumber": "265100000001"},
        {"id": "exp-rent", "direction": "expense", "amount": 2000, "category": "房租物业", "billingStatus": "reconciled", "invoiceNumber": "265100000002"},
        {"id": "exp-fee", "direction": "expense", "amount": 20, "category": "银行及金融手续费", "billingStatus": "reconciled"},
        {"id": "sal-accrual", "direction": "expense", "amount": 8350, "category": "人力成本", "title": "计提工资", "sourceType": "salary_accrual", "billingStatus": "no_invoice"},
        {"id": "sal-pay", "direction": "expense", "amount": 5375, "category": "人力成本", "sourceType": "salary_pay", "billingStatus": "reconciled"},
        {"id": "sal-ss", "direction": "expense", "amount": 1775, "category": "人力成本", "sourceType": "salary_social", "billingStatus": "reconciled"},
        {"id": "sal-hf", "direction": "expense", "amount": 1200, "category": "人力成本", "sourceType": "salary_housing_fund", "billingStatus": "reconciled"},
        {"id": "asset-dep", "direction": "expense", "amount": 443.33, "category": "固定资产折旧", "sourceType": "fixed_asset_depreciation", "billingStatus": "none"},
    ]
    result = calculate_health_score(
        entries,
        target_month="2026-06",
        checks={"canClose": True, "checks": {"trial_balance": True, "period_status": True}},
        todos=[],
    )
    metrics = {m["label"]: m["value"] for m in result["keyMetrics"]}
    assert metrics["发票合规率"] == "100%"
    # 工资发放/社保/公积金清偿不重复计入当期费用（净收支 20000 - (2000 + 20 + 8350 + 443.33) = +9186.67）
    assert metrics["当月净收支"] == "+¥9,186.67"
    assert result["dimensions"]["发票合规度"] == 100
    assert result["penalties"] == []
    assert result["totalScore"] >= 85


def test_period_close_checks_list_shape_does_not_crash():
    """period_close_checks 返回列表结构的 checks 且 canClose=False 时不得抛 AttributeError。"""
    entries = [
        {"id": "inc-1", "direction": "income", "amount": 20000, "category": "主营业务收入", "billingStatus": "has_invoice"},
        {"id": "exp-1", "direction": "expense", "amount": 2000, "category": "房租物业", "billingStatus": "has_invoice"},
    ]
    checks = {
        "canClose": False,
        "checks": [
            {"code": "VOUCHER_TO_SOURCE", "status": "blocking"},
            {"code": "LEDGER_TO_LEDGER", "status": "passed"},
            {"code": "BOOK_TO_ACTUAL", "status": "blocking"},
            {"code": "PERIOD_ORDER", "status": "blocking"},
        ],
    }
    result = calculate_health_score(entries, target_month="2026-11", checks=checks, todos=[])
    assert 0 <= result["totalScore"] <= 100
    assert result["dimensions"]["账务完整性"] == 60
    assert len([p for p in result["penalties"] if p["dimensionCode"] == "integrity"]) == 2


if __name__ == "__main__":
    test_clean_healthy_entries()
    test_unbilled_expenses_penalty()
    test_tax_risk_tags()
    test_close_checks_failure()
    test_empty_and_boundaries()
    test_reconciled_invoice_number_and_salary_depreciation_exemptions()
    test_period_close_checks_list_shape_does_not_crash()
    print("ALL HEALTH SCORE ENGINE UNIT TESTS PASSED!")
