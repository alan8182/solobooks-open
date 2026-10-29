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


if __name__ == "__main__":
    test_clean_healthy_entries()
    test_unbilled_expenses_penalty()
    test_tax_risk_tags()
    test_close_checks_failure()
    test_empty_and_boundaries()
    print("ALL HEALTH SCORE ENGINE UNIT TESTS PASSED!")
