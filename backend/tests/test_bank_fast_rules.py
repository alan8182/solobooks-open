# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Unit tests for bank statement fast rule identification."""
from decimal import Decimal
import pytest
from services.bank_service import (
    identify_statement_fast_rule,
    extract_statement_rule_pattern,
    save_company_category_rule,
    ai_suggest_matches,
)
from services.database import get_conn


def test_wechat_withdrawal_rule():
    stmt = {
        "id": "stmt-1",
        "description": "财付通支付科技有限公司 | 附言: 提现",
        "amount": Decimal("19.82"),
        "transaction_date": "2026-06-03 10:00:00",
    }
    rule = identify_statement_fast_rule(stmt)
    assert rule is not None
    assert rule["category"] == "微信支付结算"
    assert rule["confidence"] == 1.0
    assert rule["rule"] == "wechat_withdrawal"
    assert "2026-06-03" in rule["title"]


def test_alipay_withdrawal_rule():
    stmt = {
        "id": "stmt-2",
        "description": "支付宝（中国）网络技术有限公司 | 附言: 提现",
        "amount": Decimal("500.00"),
        "transaction_date": "2026-06-05 14:00:00",
    }
    rule = identify_statement_fast_rule(stmt)
    assert rule is not None
    assert rule["category"] == "支付宝结算"
    assert rule["confidence"] == 1.0
    assert rule["rule"] == "alipay_withdrawal"


def test_bank_interest_rule():
    stmt = {
        "id": "stmt-3",
        "description": "活期结息 | 利息入账",
        "amount": Decimal("12.70"),
        "transaction_date": "2026-06-21 00:00:00",
    }
    rule = identify_statement_fast_rule(stmt)
    assert rule is not None
    assert rule["category"] == "利息收入"
    assert rule["confidence"] == 1.0
    assert rule["rule"] == "bank_interest"


def test_micro_auth_rule():
    stmt = {
        "id": "stmt-4",
        "description": "网银打款验证 | 腾讯企业认证",
        "amount": Decimal("0.08"),
        "transaction_date": "2026-05-15 09:30:00",
    }
    rule = identify_statement_fast_rule(stmt)
    assert rule is not None
    assert rule["category"] == "营业外收入"
    assert rule["confidence"] == 1.0
    assert rule["rule"] == "micro_auth"


def test_general_business_expense_not_fast_rule():
    stmt = {
        "id": "stmt-5",
        "description": "北京深度求索人工智能基础技术研究有限公司 | DeepSeek充值",
        "amount": Decimal("-500.00"),
        "transaction_date": "2026-06-10 11:20:00",
    }
    rule = identify_statement_fast_rule(stmt)
    assert rule is None


def test_extract_statement_rule_pattern():
    desc = "汇兑 | 附言：小程序注册验证 (对方: 招商银行股份有限公司深圳高新园科创支行)"
    assert extract_statement_rule_pattern(desc) == "招商银行股份有限公司深圳高新园科创支行"
    assert extract_statement_rule_pattern("转出-云服务费 20260618") == "转出-云服务费"


@pytest.mark.asyncio
async def test_company_memory_rule_persistence_and_suggestion():
    company_id = "test_rule_memory_co"
    desc = "汇兑 | 附言: 小程序注册验证 (对方: 招商银行股份有限公司深圳高新园科创支行)"
    pattern = await save_company_category_rule(company_id, desc, Decimal("-0.74"), "其他应收款")
    assert pattern == "招商银行股份有限公司深圳高新园科创支行"

    conn = await get_conn()
    try:
        await conn.execute("DELETE FROM bank_statements WHERE company_id = ?", (company_id,))
        await conn.execute(
            "INSERT INTO bank_statements "
            "(id, company_id, transaction_date, description, amount, balance, statement_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("stmt-mem-1", company_id, "2026-06-04", desc, "-0.74", "1000.00", "batch-1", "2026-06-04T00:00:00Z"),
        )
        await conn.commit()
    finally:
        await conn.close()

    suggestions = await ai_suggest_matches(company_id)
    assert len(suggestions) == 1
    sug = suggestions[0]
    assert sug["statementId"] == "stmt-mem-1"
    assert sug["rule"] == "company_memory_rule"
    assert sug["confidence"] == 0.98
    assert sug["suggestedEntry"]["category"] == "其他应收款"
