# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Unit tests for bank statement fast rule identification."""
from decimal import Decimal
import pytest
from services.bank_service import (
    _needs_business_purpose_confirmation,
    guard_statement_classification,
    identify_explicit_it_service_rule,
    identify_statement_fast_rule,
    extract_statement_rule_pattern,
    extract_statement_purpose_key,
    save_company_category_rule,
    ai_suggest_matches,
    list_company_category_rules,
    match_company_category_rule,
    update_company_category_rule,
    delete_company_category_rule,
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


def test_generic_wanglai_with_it_company_uses_low_confidence_it_fallback():
    """模型误判应付账款时，可根据交易对手行业词降置信度改判信息技术服务费。"""
    stmt = {
        "id": "stmt-generic-wanglai",
        "description": (
            "汇兑 | 附言: 往来款 "
            "(对方: 杭州深度求索人工智能基础技术研究有限公司)"
        ),
        "amount": Decimal("-500.00"),
        "transaction_date": "2026-05-31",
    }

    assert _needs_business_purpose_confirmation(stmt) is True

    guarded = guard_statement_classification(
        stmt,
        {
            "direction": "expense",
            "category": "供应商付款",
            "accountCode": "2202",
            "accountName": "应付账款",
            "confidence": 0.92,
        },
    )
    assert guarded["category"] == "信息技术服务费"
    assert guarded["accountCode"] == "5602015"
    assert guarded["confidence"] == 0.72
    assert guarded["requiresConfirmation"] is True
    assert "人工智能" in guarded["reason"]
    assert "供应商付款" in guarded["reason"]


def test_generic_wanglai_with_ordinary_company_remains_pending():
    """普通公司名没有行业或采购证据时，仍不能猜测应付账款。"""
    stmt = {
        "id": "stmt-generic-ordinary-company",
        "description": "汇兑 | 附言: 往来款 (对方: 任意商务服务有限公司)",
        "amount": Decimal("-500.00"),
        "transaction_date": "2026-05-31",
    }

    guarded = guard_statement_classification(
        stmt,
        {
            "direction": "expense",
            "category": "供应商付款",
            "accountCode": "2202",
            "accountName": "应付账款",
            "confidence": 0.92,
        },
    )
    assert guarded["category"] == "其他支出"
    assert guarded["accountCode"] == "5602999"
    assert guarded["confidence"] == 0.45
    assert guarded["requiresConfirmation"] is True
    assert "没有货款、采购款或应付账款等采购证据" in guarded["reason"]


def test_explicit_api_service_maps_to_it_service_without_vendor_allowlist():
    """显式业务词足够时可按信息技术服务费入账，且规则不依赖具体供应商。"""
    stmt = {
        "id": "stmt-explicit-api",
        "description": "汇兑 | 附言: API调用服务费 (对方: 任意技术服务公司)",
        "amount": Decimal("-500.00"),
        "transaction_date": "2026-05-31",
    }

    rule = identify_explicit_it_service_rule(stmt)
    assert rule is not None
    assert rule["category"] == "信息技术服务费"
    assert rule["confidence"] == 0.94
    assert rule["rule"] == "explicit_it_service_rule"


def test_ordinary_service_fee_is_not_bank_fee_without_bank_charge_evidence():
    """普通“服务费”没有手续费、转账费等证据时，不能归为银行手续费。"""
    stmt = {
        "id": "stmt-service-fee",
        "description": "汇兑 | 附言: 技术服务费 (对方: 任意技术服务公司)",
        "amount": Decimal("-300.00"),
        "transaction_date": "2026-05-31",
    }

    guarded = guard_statement_classification(
        stmt,
        {
            "direction": "expense",
            "category": "银行及金融手续费",
            "accountCode": "5603002",
            "accountName": "财务费用-手续费",
            "confidence": 0.88,
        },
    )
    assert guarded["category"] == "其他支出"
    assert guarded["requiresConfirmation"] is True
    assert "没有银行手续费、转账费或账户管理费证据" in guarded["reason"]


def test_corporate_ebanking_service_fee_passes_bank_fee_guard():
    """银行自身网银/对公中间业务费用应通过银行手续费守卫，不被打回兜底。"""
    stmt = {
        "id": "stmt-ebank-fee",
        "description": "企业银行各项费用 | 网上企业银行-网上企业银行服务费 (对方: 对公中间业务收入-网上其他收入)",
        "amount": Decimal("-25.00"),
        "transaction_date": "2026-10-02",
    }

    guarded = guard_statement_classification(
        stmt,
        {
            "direction": "expense",
            "category": "银行及金融手续费",
            "accountCode": "5603002",
            "accountName": "财务费用-手续费",
            "confidence": 0.95,
        },
    )
    assert guarded["category"] == "银行及金融手续费"
    assert guarded["accountCode"] == "5603002"
    assert guarded["confidence"] == 0.95
    assert extract_statement_purpose_key(stmt["description"]) == "银行手续费"


def test_prepayment_with_api_wording_is_not_forced_to_it_expense():
    """预付款性质优先按往来处理，不能仅因摘要出现 API 就费用化。"""
    stmt = {
        "id": "stmt-prepaid-api",
        "description": "汇兑 | 附言: 预付API服务费 (对方: 任意技术服务公司)",
        "amount": Decimal("-500.00"),
        "transaction_date": "2026-05-31",
    }

    assert identify_explicit_it_service_rule(stmt) is None


def test_extract_statement_rule_pattern():
    desc = "汇兑 | 附言：小程序注册验证 (对方: 招商银行股份有限公司深圳高新园科创支行)"
    assert extract_statement_rule_pattern(desc) == "招商银行股份有限公司深圳高新园科创支行"
    assert extract_statement_rule_pattern("转出-云服务费 20260618") == "转出-云服务费"
    assert extract_statement_purpose_key(desc) == "小程序注册验证"
    assert extract_statement_purpose_key(
        "汇兑 | 附言: 往来款 (对方: 任意信息技术有限公司)"
    ) == ""


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
            "WHERE company_id = ? AND counterparty_key = ?",
            (company_id, "测试银行c"),
        )
        row = await cursor.fetchone()
    finally:
        await conn.close()
    assert row is not None
    assert row["category"] == "办公费"


@pytest.mark.asyncio
async def test_company_rules_are_tenant_isolated_and_keep_business_purposes_separate():
    desc_cloud = "汇兑 | 附言: API调用服务费 (对方: 通用服务有限公司)"
    desc_rent = "汇兑 | 附言: 办公用房租金 (对方: 通用服务有限公司)"

    await save_company_category_rule(
        "test_rule_tenant_a", desc_cloud, Decimal("-500"), "信息技术服务费"
    )
    await save_company_category_rule(
        "test_rule_tenant_a", desc_rent, Decimal("-8000"), "租金物业"
    )
    await save_company_category_rule(
        "test_rule_tenant_b", desc_cloud, Decimal("-500"), "办公费"
    )

    company_a = await list_company_category_rules("test_rule_tenant_a")
    company_b = await list_company_category_rules("test_rule_tenant_b")
    assert company_a["total"] == 2
    assert company_b["total"] == 1
    assert {item["category"] for item in company_a["items"]} == {
        "信息技术服务费",
        "租金物业",
    }
    assert company_b["items"][0]["category"] == "办公费"

    cloud_match = match_company_category_rule(
        {
            "description": desc_cloud,
            "amount": Decimal("-500"),
            "transaction_date": "2026-06-01",
        },
        company_a["items"],
    )
    rent_match = match_company_category_rule(
        {
            "description": desc_rent,
            "amount": Decimal("-8000"),
            "transaction_date": "2026-06-02",
        },
        company_a["items"],
    )
    assert cloud_match and cloud_match["category"] == "信息技术服务费"
    assert rent_match and rent_match["category"] == "租金物业"
    assert cloud_match["matchKind"] == rent_match["matchKind"] == "exact"


@pytest.mark.asyncio
async def test_specific_business_rule_beats_counterparty_only_rule():
    company_id = "test_rule_specificity_co"
    vendor = "通用云服务有限公司"
    broad = f"汇兑 | 附言: 往来款 (对方: {vendor})"
    specific = f"汇兑 | 附言: 云服务订阅费 (对方: {vendor})"
    await save_company_category_rule(
        company_id, broad, Decimal("-100"), "其他应收款"
    )
    await save_company_category_rule(
        company_id, specific, Decimal("-100"), "信息技术服务费"
    )
    listing = await list_company_category_rules(company_id)
    assert listing["total"] == 2

    match = match_company_category_rule(
        {
            "description": specific,
            "amount": Decimal("-100"),
            "transaction_date": "2026-06-01",
        },
        listing["items"],
    )
    assert match is not None
    assert match["category"] == "信息技术服务费"
    assert match["matchKind"] == "exact"
    assert match["requiresReview"] is False

    broad_match = match_company_category_rule(
        {
            "description": broad,
            "amount": Decimal("-100"),
            "transaction_date": "2026-06-01",
        },
        listing["items"],
    )
    assert broad_match is not None
    assert broad_match["matchKind"] == "counterparty_only"
    assert broad_match["requiresReview"] is True
    assert broad_match["confidence"] <= 0.78


@pytest.mark.asyncio
async def test_company_rule_can_be_disabled_edited_and_deleted():
    company_id = "test_rule_management_co"
    desc = "汇兑 | 附言: 云服务订阅费 (对方: 规则管理测试公司)"
    await save_company_category_rule(
        company_id, desc, Decimal("-300"), "信息技术服务费"
    )
    listing = await list_company_category_rules(company_id)
    rule = listing["items"][0]

    updated = await update_company_category_rule(
        company_id,
        rule["id"],
        {"enabled": False, "category": "办公费"},
        user_id="u-rule-test",
    )
    assert updated is not None
    assert updated["enabled"] is False
    assert updated["category"] == "办公费"
    assert updated["accountCode"] == "5602004"

    enabled = await list_company_category_rules(company_id, enabled=True)
    assert enabled["total"] == 0
    all_rules = await list_company_category_rules(company_id)
    assert all_rules["total"] == 1
    assert await delete_company_category_rule(
        company_id, rule["id"], user_id="u-rule-test"
    ) is True
    assert (await list_company_category_rules(company_id))["total"] == 0


@pytest.mark.asyncio
async def test_category_rule_router_uses_authenticated_company_only():
    from deps.auth import AuthContext
    from routers.bank import (
        CategoryRuleUpdateRequest,
        list_category_rules,
        patch_category_rule,
        remove_category_rule,
    )

    desc = "汇兑 | 附言: API调用服务费 (对方: 路由隔离测试公司)"
    await save_company_category_rule(
        "test_rule_route_a", desc, Decimal("-10"), "信息技术服务费"
    )
    await save_company_category_rule(
        "test_rule_route_b", desc, Decimal("-10"), "办公费"
    )
    ctx = AuthContext(user_id="u-route-test", company_id="test_rule_route_a")

    listing = await list_category_rules(
        q="",
        direction="",
        enabled=None,
        limit=100,
        offset=0,
        ctx=ctx,
    )
    assert listing["total"] == 1
    rule = listing["items"][0]
    assert rule["category"] == "信息技术服务费"

    updated = await patch_category_rule(
        rule["id"],
        CategoryRuleUpdateRequest(enabled=False),
        ctx=ctx,
    )
    assert updated["enabled"] is False
    assert await remove_category_rule(rule["id"], ctx=ctx) == {"status": "deleted"}
    assert (await list_company_category_rules("test_rule_route_b"))["total"] == 1


@pytest.mark.asyncio
async def test_company_rule_hit_count_is_idempotent_per_statement():
    company_id = "test_rule_hit_count_co"
    desc = "汇兑 | 附言: API调用服务费 (对方: 命中计数服务有限公司)"
    await save_company_category_rule(
        company_id, desc, Decimal("-88"), "信息技术服务费"
    )
    await _insert_statements(company_id, [
        (
            "stmt-rule-hit-1",
            company_id,
            "2026-06-04",
            desc,
            "-88.00",
            "1000.00",
            "batch-rule-hit",
            "2026-06-04T00:00:00Z",
        ),
    ])

    first = await ai_suggest_matches(company_id, month="2026-06")
    second = await ai_suggest_matches(company_id, month="2026-06")
    assert first[0]["rule"] == second[0]["rule"] == "company_memory_rule"

    rules = await list_company_category_rules(company_id)
    assert rules["items"][0]["hitCount"] == 1
    assert rules["items"][0]["lastHitStatementId"] == "stmt-rule-hit-1"


@pytest.mark.asyncio
async def test_rule_match_requires_review_for_refund_closed_period_and_amount_jump():
    company_id = "test_rule_guard_co"
    desc = "汇兑 | 附言: API调用服务费 (对方: 风控测试服务有限公司)"
    await save_company_category_rule(
        company_id, desc, Decimal("-100"), "信息技术服务费"
    )
    rules = (await list_company_category_rules(company_id))["items"]

    refund_match = match_company_category_rule(
        {
            "description": "汇兑 | 附言: 云服务退款 (对方: 风控测试服务有限公司)",
            "amount": Decimal("100"),
            "transaction_date": "2026-06-10",
        },
        rules,
    )
    assert refund_match is None  # 方向不同，不能串用

    closed_match = match_company_category_rule(
        {
            "description": desc,
            "amount": Decimal("-100"),
            "transaction_date": "2026-05-10",
        },
        rules,
        closed_months={"2026-05"},
    )
    assert closed_match is not None
    assert closed_match["requiresReview"] is True
    assert any("已关账" in reason for reason in closed_match["reviewReasons"])

    amount_match = match_company_category_rule(
        {
            "description": desc,
            "amount": Decimal("-50000"),
            "transaction_date": "2026-06-10",
        },
        rules,
    )
    assert amount_match is not None
    assert amount_match["requiresReview"] is True
    assert any("历史规则范围" in reason for reason in amount_match["reviewReasons"])


async def _ensure_core_schema() -> None:
    """工资/凭证/银行表单独跑本文件时也要在场（conftest 每轮重建测试库）。"""
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine
    from services.bank_service import ensure_bank_schema

    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await ensure_bank_schema()


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
    # 税费缴纳组：金库通用通道仅凭摘要无法区分正常缴税、社保、个税、罚款、滞纳金或所得税
    assert get_ambiguity_group("税费缴纳") == [
        "税费缴纳", "社保公积金", "个税代缴", "罚款支出", "税收滞纳金", "所得税费用",
    ]
    assert get_ambiguity_group("不存在的分类") == []


@pytest.mark.asyncio
async def test_tax_channel_statement_falls_into_tax_ambiguity_group(monkeypatch):
    """回归 2026-10：「税费社保」通道 -200 是个税逾期申报罚款，不得被判社保。

    摘要只有通用通道词时，确定性规则直接给「税费缴纳」模糊组，让用户在
    社保公积金 / 个税代缴 / 罚款支出 / 税收滞纳金 / 所得税费用 里拍板；
    不调 LLM、也不挂「需工资模块核销」。
    """
    from services import llm

    company_id = "test_rule_tax_channel_co"
    await _ensure_core_schema()
    await _insert_statements(company_id, [
        ("stmt-tax-channel", company_id, "2026-10-15",
         "税费社保 (对方: 金库杭州市余杭区支库)", "-200.00", "1000.00",
         "batch-ch", "2026-10-15T00:00:00Z"),
    ])

    llm_calls: list[str] = []
    monkeypatch.setattr(llm, "safe_chat_text_json", lambda *a, **k: llm_calls.append("called"))

    suggestions = await ai_suggest_matches(company_id, month="2026-10")
    assert len(suggestions) == 1
    sug = suggestions[0]
    entry = sug["suggestedEntry"]
    assert entry["category"] == "税费缴纳"
    assert entry["accountCode"] == "2221026"
    assert entry["requiresConfirmation"] is True
    assert entry.get("requiresSalarySettlement") is not True
    assert "payrollSettlement" not in sug
    assert llm_calls == []


@pytest.mark.asyncio
async def test_explicit_social_insurance_statement_still_routes_to_payroll():
    """摘要写明险种时仍归工资模块核销，不被税费通道规则吞掉。"""
    company_id = "test_rule_explicit_ss_co"
    await _ensure_core_schema()
    await _insert_statements(company_id, [
        ("stmt-pure-ss", company_id, "2026-10-16",
         "社会保险费扣款 (对方: 杭州市社会保险管理服务中心)", "-800.00", "1000.00",
         "batch-ss", "2026-10-16T00:00:00Z"),
    ])

    suggestions = await ai_suggest_matches(company_id, month="2026-10")
    assert len(suggestions) == 1
    entry = suggestions[0]["suggestedEntry"]
    assert entry["category"] == "社保公积金"
    assert entry["accountCode"] == "221102"
    assert entry["requiresSalarySettlement"] is True


def test_guard_rejects_social_insurance_guess_for_tax_channel():
    """模型偶发把通道词归社保时，确认前的闸门也要改回税费模糊组。"""
    stmt = {
        "id": "stmt-tax-channel-guard",
        "description": "税费社保 (对方: 金库杭州市余杭区支库)",
        "amount": Decimal("-200.00"),
        "transaction_date": "2026-10-15 10:00:00",
    }
    guarded = guard_statement_classification(
        stmt, {"direction": "expense", "category": "社保公积金"}
    )
    assert guarded["category"] == "税费缴纳"
    assert guarded["accountCode"] == "2221026"
    assert guarded["requiresConfirmation"] is True

    # 收入方向不参与通道纠偏，不能把税务退费翻成支出
    income_stmt = {**stmt, "amount": Decimal("200.00")}
    unchanged = guard_statement_classification(
        income_stmt, {"direction": "income", "category": "社保公积金"}
    )
    assert unchanged["category"] == "社保公积金"


@pytest.mark.asyncio
async def test_tax_payment_and_penalty_categories_do_not_save_auto_rule():
    """税费缴纳模糊组及一次性罚款/滞纳金不得沉淀为对手方自动规则。"""
    company_id = "test_no_tax_penalty_rule_co"
    desc = "税费社保 (对方: 金库杭州市余杭区支库)"
    for cat in ("税费缴纳", "罚款支出", "税收滞纳金"):
        saved = await save_company_category_rule(
            company_id,
            desc,
            Decimal("-200.00"),
            cat,
            source_statement_id="stmt-treasury-200",
        )
        assert saved is None, cat


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
    assert by_id["stmt-pay-1"]["pairedStatementId"] == "stmt-ref-1"
    assert by_id["stmt-pay-1"]["suggestedEntry"]["category"] == "往来款"
    assert by_id["stmt-pay-1"]["suggestedEntry"]["accountCode"] == "122199"
    assert by_id["stmt-ref-1"]["rule"] == "refund_pair"
    assert by_id["stmt-ref-1"]["pairedStatementId"] == "stmt-pay-1"
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


def test_entry_not_after_statement_period_guard():
    """跨期护栏：候选凭证晚于流水日即"先付款后收票"，不得建议跨期配对。"""
    from services.bank_service import _entry_not_after_statement as guard

    assert guard("2033-06-02", "2033-05-31") is False
    assert guard("2033-05-31", "2033-05-31") is True
    assert guard("2033-05-30", "2033-05-31") is True
    # 空日期无法判定 → 放行（不误伤日期缺失的历史凭证）
    assert guard("", "2033-05-31") is True
    assert guard("2033-06-02", "") is True
    assert guard(None, None) is True


def test_rule_match_demotes_unrelated_invoice_counterparty():
    """回归 2026-10：社保金库扣款 -200 曾以「金额完全匹配」高置信推荐给
    深度求索的发票——发票销方与流水对手方毫无关系时必须降置信并注明。"""
    from services.bank_service import _try_rule_match

    stmt = {
        "id": "stmt-shebao",
        "description": "税费社保 (对方: 金库杭州市余杭区支库)",
        "amount": Decimal("-200.00"),
        "transaction_date": "2026-10-15 10:00:00",
    }
    invoice_entry = {
        "id": "entry-deepseek",
        "title": "发票-杭州深度求索人工智能基础技术研究有限公司",
        "amount": Decimal("200.00"),
        "direction": "expense",
        "occurDate": "2026-10-20",
        "source": "invoice_import",
    }
    suggestion = _try_rule_match(stmt, [invoice_entry])

    assert suggestion is not None
    assert suggestion["confidence"] <= 0.60
    assert "流水对方与发票销方不一致" in suggestion["reason"]


def test_rule_match_keeps_related_invoice_counterparty():
    """对方就是销方（直接付款）：高置信保持不变。"""
    from services.bank_service import _try_rule_match

    stmt = {
        "id": "stmt-deepseek",
        "description": "汇兑 | 附言: deepseek调用服务费 (对方: 杭州深度求索人工智能基础技术研究有限公司)",
        "amount": Decimal("-200.00"),
        "transaction_date": "2026-10-20 10:00:00",
    }
    invoice_entry = {
        "id": "entry-deepseek",
        "title": "发票-杭州深度求索人工智能基础技术研究有限公司",
        "amount": Decimal("200.00"),
        "direction": "expense",
        "occurDate": "2026-10-20",
        "source": "invoice_import",
    }
    suggestion = _try_rule_match(stmt, [invoice_entry])

    assert suggestion is not None
    assert suggestion["confidence"] >= 0.95
    assert "不一致" not in suggestion["reason"]


def test_rule_match_income_invoice_matches_buyer_not_seller():
    """销项发票（income）应按购方（buyer_name）核对收款流水对方，不能拿本公司销方去比对。"""
    from services.bank_service import _try_rule_match

    stmt = {
        "id": "stmt-income-buyer-ok",
        "description": "提回对公户收款 | 26100900420688 (对方: 深圳市得同保险代理有限公司)",
        "amount": Decimal("8794.30"),
        "transaction_date": "2026-10-09 14:25:20",
    }
    invoice_entry = {
        "id": "entry-income-1",
        "title": "发票-杭州惠保信网络科技有限公司",
        "buyer_name": "深圳市得同保险代理有限公司",
        "amount": Decimal("8794.30"),
        "direction": "income",
        "occurDate": "2026-10-06",
        "source": "invoice_import",
    }
    suggestion = _try_rule_match(stmt, [invoice_entry])

    assert suggestion is not None
    assert suggestion["confidence"] >= 0.95
    assert suggestion["counterparty_mismatch"] is False
    assert "不一致" not in suggestion["reason"]


def test_rule_match_income_invoice_demotes_unrelated_buyer():
    """销项发票购方与收款流水对方无关时，应降置信并提示与发票购方不一致。"""
    from services.bank_service import _try_rule_match

    stmt = {
        "id": "stmt-income-buyer-mismatch",
        "description": "提回对公户收款 | 结算款 (对方: 某无关贸易有限公司)",
        "amount": Decimal("8794.30"),
        "transaction_date": "2026-10-09 14:25:20",
    }
    invoice_entry = {
        "id": "entry-income-2",
        "title": "发票-杭州惠保信网络科技有限公司",
        "buyer_name": "深圳市得同保险代理有限公司",
        "amount": Decimal("8794.30"),
        "direction": "income",
        "occurDate": "2026-10-06",
        "source": "invoice_import",
    }
    suggestion = _try_rule_match(stmt, [invoice_entry])

    assert suggestion is not None
    assert suggestion["confidence"] <= 0.60
    assert suggestion["counterparty_mismatch"] is True
    assert "流水对方与发票购方不一致" in suggestion["reason"]


def test_rule_match_reimbursement_flow_via_merchant_in_memo():
    """对方是报销人、但附言带商户名（≥3 字交集）：不误伤报销付款配对。"""
    from services.bank_service import _try_rule_match

    stmt = {
        "id": "stmt-baidu",
        "description": "汇兑 | 附言: 报销百度智能云云服务器3年续期费用 (对方: 杨云飞)",
        "amount": Decimal("-1794.00"),
        "transaction_date": "2026-10-26 10:00:00",
    }
    invoice_entry = {
        "id": "entry-baidu",
        "title": "发票-北京百度网讯科技有限公司",
        "amount": Decimal("1794.00"),
        "direction": "expense",
        "occurDate": "2026-10-17",
        "source": "invoice_import",
    }
    suggestion = _try_rule_match(stmt, [invoice_entry])

    assert suggestion is not None
    assert suggestion["confidence"] > 0.60
    assert "不一致" not in suggestion["reason"]
