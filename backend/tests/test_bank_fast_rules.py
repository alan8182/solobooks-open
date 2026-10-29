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


def test_micro_income_without_verification_keyword():
    """微额收款即便摘要无「验证/认证」字样也兜底归营业外收入（银行常不写关键词）。"""
    stmt = {
        "id": "stmt-micro-1",
        "description": "银联商务结算 | 第三方支付",
        "amount": Decimal("0.30"),
        "transaction_date": "2026-05-31 10:00:00",
    }
    rule = identify_statement_fast_rule(stmt)
    assert rule is not None
    assert rule["category"] == "营业外收入"
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


@pytest.mark.asyncio
async def test_company_memory_rule_upsert_updates_category():
    """同 pattern 二次保存应覆盖旧分类（INSERT OR REPLACE 的双方言等价写法）。"""
    company_id = "test_rule_upsert_co"
    desc = "汇兑 | 附言: 规则更新测试 (对方: 测试银行C)"
    pattern = await save_company_category_rule(company_id, desc, Decimal("-1.00"), "其他应收款")
    assert pattern == "测试银行C"
    pattern2 = await save_company_category_rule(company_id, desc, Decimal("-1.00"), "办公费")
    assert pattern2 == "测试银行C"

    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT category FROM bank_category_rules "
            "WHERE company_id = ? AND match_pattern = ?",
            (company_id, "测试银行C"),
        )
        row = await cursor.fetchone()
    finally:
        await conn.close()
    assert row is not None
    assert row["category"] == "办公费"


async def _insert_statements(company_id: str, rows: list[tuple]) -> None:
    conn = await get_conn()
    try:
        await conn.execute("DELETE FROM bank_statements WHERE company_id = ?", (company_id,))
        for row in rows:
            await conn.execute(
                "INSERT INTO bank_statements "
                "(id, company_id, transaction_date, description, amount, balance, statement_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
        await conn.commit()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_ai_suggest_month_scope_only_returns_target_month():
    """month 参数必须把建议范围限定在账期内，否则历史账期向导拿不到建议。"""
    company_id = "test_rule_month_scope_co"
    await _insert_statements(company_id, [
        ("stmt-scope-5", company_id, "2026-05-21", "活期结息 | 利息入账", "12.70", "1000.00", "batch-1", "2026-05-21T00:00:00Z"),
        ("stmt-scope-6", company_id, "2026-06-05", "财付通支付科技有限公司 | 附言: 提现", "-19.82", "980.00", "batch-1", "2026-06-05T00:00:00Z"),
    ])

    scoped = await ai_suggest_matches(company_id, month="2026-05")
    assert {s["statementId"] for s in scoped} == {"stmt-scope-5"}

    unscoped = await ai_suggest_matches(company_id)
    assert {s["statementId"] for s in unscoped} == {"stmt-scope-5", "stmt-scope-6"}


def test_ambiguity_groups_members_are_valid_categories():
    """模糊组成员必须是真实分类且包含自身，防止候选 chips 给出无效分类。"""
    from services.account_mapper import CATEGORY_TO_ACCOUNT
    from services.category_service import AMBIGUITY_GROUPS, get_ambiguity_group

    for cat, group in AMBIGUITY_GROUPS.items():
        assert cat in CATEGORY_TO_ACCOUNT, cat
        assert cat in group
        for member in group:
            assert member in CATEGORY_TO_ACCOUNT, member
    assert get_ambiguity_group("股东投入") == ["股东投入", "资本公积", "股东垫付"]
    # 往来款组：收入候选对比「是否确认为收入」，支出候选对比「暂付还是费用」
    assert get_ambiguity_group("往来款") == ["往来款", "主营业务收入", "营业外收入", "其他支出"]
    assert get_ambiguity_group("不存在的分类") == []


def test_wechat_refund_income_not_hijacked_by_settlement_rule():
    """「微信公众号注册退款」是自有资金退回，绝不能进平台结算。"""
    stmt = {
        "id": "stmt-refund-1",
        "description": "汇兑 | 附言: 微信公众号注册退款 (对方: 深圳市腾讯计算机系统有限公司)",
        "amount": Decimal("0.15"),
        "transaction_date": "2026-05-30",
    }
    assert identify_statement_fast_rule(stmt) is None


def test_wechat_micro_verification_income_falls_to_micro_auth():
    """微信平台的微额打款验证走 micro_auth，不被平台关键词截胡。"""
    stmt = {
        "id": "stmt-ver-1",
        "description": "微信支付 | 企业打款验证",
        "amount": Decimal("0.12"),
        "transaction_date": "2026-06-01",
    }
    rule = identify_statement_fast_rule(stmt)
    assert rule is not None
    assert rule["rule"] == "micro_auth"


@pytest.mark.asyncio
async def test_refund_pair_suggests_offsetting_wanglai():
    """一付一退（平台验证款）：两侧都挂往来对冲，退款不得进平台结算。"""
    company_id = "test_rule_refund_pair_co"
    await _insert_statements(company_id, [
        ("stmt-pay-1", company_id, "2026-05-30", "汇兑 | 附言: 微信开放平台注册验证 (对方: 深圳市腾讯计算机系统有限公司)", "-0.15", "1000.00", "batch-1", "2026-05-30T00:00:00Z"),
        ("stmt-ref-1", company_id, "2026-05-30", "汇兑 | 附言: 微信公众号注册退款 (对方: 深圳市腾讯计算机系统有限公司)", "0.15", "1000.15", "batch-1", "2026-05-30T00:00:00Z"),
    ])

    suggestions = await ai_suggest_matches(company_id, month="2026-05")
    by_id = {s["statementId"]: s for s in suggestions}
    assert set(by_id) == {"stmt-pay-1", "stmt-ref-1"}
    assert by_id["stmt-pay-1"]["rule"] == "refund_pair"
    assert by_id["stmt-pay-1"]["suggestedEntry"]["category"] == "往来款"
    assert by_id["stmt-pay-1"]["suggestedEntry"]["accountCode"] == "122199"
    assert by_id["stmt-ref-1"]["rule"] == "refund_pair"
    assert by_id["stmt-ref-1"]["suggestedEntry"]["category"] == "往来退款"
    assert by_id["stmt-ref-1"]["suggestedEntry"]["accountCode"] == "122199"


@pytest.mark.asyncio
async def test_company_memory_rule_beats_fast_rule():
    """用户确认过的记忆规则压过系统关键词快规则。"""
    company_id = "test_rule_memory_over_fast_co"
    desc = "财付通支付科技有限公司 | 提现 (对方: 财付通支付科技有限公司)"
    await save_company_category_rule(company_id, desc, Decimal("100"), "其他应付款")
    await _insert_statements(company_id, [
        ("stmt-mem-fast", company_id, "2026-06-04", desc, "100.00", "1000.00", "batch-1", "2026-06-04T00:00:00Z"),
    ])

    suggestions = await ai_suggest_matches(company_id, month="2026-06")
    assert len(suggestions) == 1
    assert suggestions[0]["rule"] == "company_memory_rule"
    assert suggestions[0]["suggestedEntry"]["category"] == "其他应付款"


@pytest.mark.asyncio
async def test_ai_suggest_survives_missing_category_rules_table(monkeypatch):
    """Postgres 上规则表缺失时必须降级为无记忆规则，而不是整个接口 500。"""
    from services import bank_service

    class _RulesTableBrokenConn:
        """仅 bank_category_rules 查询失败，其余透传真实连接。"""

        def __init__(self, real):
            self._real = real

        async def execute(self, sql, params=None):
            if "FROM bank_category_rules" in sql:
                raise RuntimeError("relation bank_category_rules does not exist")
            return await self._real.execute(sql, params)

        async def executemany(self, sql, seq):
            return await self._real.executemany(sql, seq)

        async def commit(self):
            await self._real.commit()

        async def close(self):
            await self._real.close()

    async def fake_get_conn():
        return _RulesTableBrokenConn(await get_conn())

    monkeypatch.setattr(bank_service, "get_conn", fake_get_conn)

    company_id = "test_rule_missing_table_co"
    await _insert_statements(company_id, [
        ("stmt-broken-1", company_id, "2026-05-21", "活期结息 | 利息入账", "12.70", "1000.00", "batch-1", "2026-05-21T00:00:00Z"),
    ])

    suggestions = await ai_suggest_matches(company_id, month="2026-05")
    assert len(suggestions) == 1
    assert suggestions[0]["statementId"] == "stmt-broken-1"
    assert suggestions[0]["rule"] == "bank_interest"
