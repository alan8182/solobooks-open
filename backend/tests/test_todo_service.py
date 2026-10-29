# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""待办生成规则测试：冲销排除、被冲销原条目排除、小额豁免。"""
from __future__ import annotations

from services.todo_service import TODO_CATEGORIES, _derive_entry_todos


def _entry(**overrides) -> dict:
    base = {
        "id": "e1",
        "title": "测试条目",
        "direction": "expense",
        "amount": "320",
        "category": "办公费",
        "payer": "company_account",
        "source": "manual",
        "entryType": "standard",
        "billingStatus": "no_document",
    }
    base.update(overrides)
    return base


def _rule_ids(todos: list[dict]) -> set[str]:
    return {t["meta"]["ruleId"] for t in todos}


def test_reversal_entries_generate_no_todos():
    todos = _derive_entry_todos([
        _entry(id="rev-1", entryType="reversal", title="冲销：购买办公用品", amount="-320", reversalOf="e1"),
    ])
    assert todos == []


def test_reversed_original_entries_generate_no_todos():
    """原条目已有红字冲销对，票据/开票待办应随之消失。"""
    todos = _derive_entry_todos([
        _entry(id="e1", amount="320"),
        _entry(id="rev-1", entryType="reversal", title="冲销：购买办公用品", amount="-320", reversalOf="e1"),
    ])
    assert todos == []


def test_small_amounts_are_exempt_from_document_and_invoice_todos():
    todos = _derive_entry_todos([
        _entry(id="fee", direction="expense", amount="0.05", category="银行及金融手续费", billingStatus="no_document"),
        _entry(id="income", direction="income", amount="5", category="主营业务收入", billingStatus="reconciled"),
    ])
    rules = _rule_ids(todos)
    assert "missing_document" not in rules
    assert "income_invoice" not in rules


def test_large_expense_still_requires_document():
    todos = _derive_entry_todos([_entry(id="big", amount="320", billingStatus="no_document")])
    assert "missing_document" in _rule_ids(todos)


def test_large_income_still_requires_invoice_confirmation():
    todos = _derive_entry_todos([
        _entry(id="big-income", direction="income", amount="85", category="主营业务收入", billingStatus="reconciled"),
    ])
    assert "income_invoice" in _rule_ids(todos)


def test_no_invoice_income_is_closed_from_invoice_todo():
    """明确"不开票"的收入视为开票状态已确认，不再重复提醒。"""
    todos = _derive_entry_todos([
        _entry(id="no-inv", direction="income", amount="85", category="主营业务收入", billingStatus="no_invoice"),
    ])
    assert "income_invoice" not in _rule_ids(todos)


def test_owner_transfer_nature_not_exempt_by_small_amount():
    """小额豁免只管票据/开票，股东垫付性质确认仍然提醒。"""
    todos = _derive_entry_todos([
        _entry(id="boss", direction="expense", amount="5", payer="boss_personal", billingStatus="has_invoice"),
    ])
    assert "owner_transfer_nature" in _rule_ids(todos)


def test_boss_personal_invoice_import_skips_payment_and_nature_todos():
    """发票导入 + 股东垫付：垫付去向已确认，不再催付款——核销由公户打款给垫付人时完成，
    性质确认也不再重复催问（source=invoice_import 本就豁免）。"""
    todos = _derive_entry_todos([
        _entry(id="boss-inv", direction="expense", amount="481.33",
               source="invoice_import", payer="boss_personal", billingStatus="has_invoice"),
    ])
    rules = _rule_ids(todos)
    assert "invoice_unpaid" not in rules
    assert "owner_transfer_nature" not in rules


def test_reconciled_boss_personal_is_closed_from_nature_todo():
    """垫付已配对报销（reconciled）后性质自明，不再问报销/借款/资本投入。"""
    todos = _derive_entry_todos([
        _entry(id="boss-paid", direction="expense", amount="5",
               payer="boss_personal", billingStatus="reconciled"),
    ])
    assert "owner_transfer_nature" not in _rule_ids(todos)


def test_owner_loan_reclassification_closes_nature_todo():
    """「记为往来」改分类后必须真的消卡（规则要看 category，不能只看 payer）。"""
    todos = _derive_entry_todos([
        _entry(id="boss-loan", direction="expense", amount="300",
               payer="boss_personal", category="其他应收款", billingStatus="has_invoice"),
    ])
    assert "owner_transfer_nature" not in _rule_ids(todos)


def test_invoice_unpaid_not_affected_by_small_threshold():
    """发票待付款确认与票据豁免无关（发票本身是真实欠款线索）。"""
    todos = _derive_entry_todos([
        _entry(id="inv", direction="expense", amount="0.87", source="invoice_import"),
    ])
    assert "invoice_unpaid" in _rule_ids(todos)


def test_reconciled_invoice_import_is_closed_from_unpaid_todo():
    """垫付报销只把「报销垫付款」新条目挂进匹配表，原发票条目仅回写
    billingStatus=reconciled——已报销的发票不能再催办。"""
    todos = _derive_entry_todos([
        _entry(id="inv", direction="expense", amount="300", source="invoice_import",
               billingStatus="reconciled"),
    ])
    assert "invoice_unpaid" not in _rule_ids(todos)


def test_unreconciled_invoice_import_still_prompts():
    """有发票但未确认付款/报销的条目仍要提醒（对照上一条防过度豁免）。"""
    todos = _derive_entry_todos([
        _entry(id="inv", direction="expense", amount="481.33", source="invoice_import",
               billingStatus="has_invoice"),
    ])
    assert "invoice_unpaid" in _rule_ids(todos)


def test_linked_intangible_asset_card_closes_amortization_todo():
    """来源凭证已建立无形资产卡片后，摊销待办必须消失。"""
    todos = _derive_entry_todos(
        [_entry(id="e-intangible", direction="expense", amount="120000", category="无形资产", billingStatus="has_invoice")],
        linked_intangible_entry_ids={"e-intangible"},
    )
    assert "intangible_asset_amortization" not in _rule_ids(todos)


def test_intangible_todo_has_its_own_category():
    todos = _derive_entry_todos([
        _entry(id="e-intangible", direction="expense", amount="120000", category="无形资产", billingStatus="has_invoice"),
    ])
    by_rule = {t["meta"]["ruleId"]: t for t in todos}
    assert by_rule["intangible_asset_amortization"]["category"] == "intangible"


def test_linked_fixed_asset_card_closes_depreciation_todo():
    """来源凭证已建立资产卡片后，固定资产待办必须消失。"""
    todos = _derive_entry_todos(
        [_entry(id="e-asset", direction="expense", amount="16800", category="固定资产", billingStatus="has_invoice")],
        linked_asset_entry_ids={"e-asset"},
    )
    assert "fixed_asset_depreciation" not in _rule_ids(todos)


def test_derived_todos_carry_category():
    """派生待办携带事项分类，前端按此分组展示。"""
    todos = _derive_entry_todos([
        _entry(id="e-doc", direction="expense", amount="32", billingStatus="no_document", voucherStatus="posted"),
        _entry(id="e-inv", direction="expense", amount="0.87", source="invoice_import", voucherStatus="draft"),
        _entry(id="e-income", direction="income", amount="85", category="主营业务收入", billingStatus="reconciled"),
        _entry(id="e-asset", direction="expense", amount="4999", category="固定资产", billingStatus="has_invoice"),
        _entry(id="e-boss", direction="expense", amount="5", payer="boss_personal", billingStatus="has_invoice"),
    ])
    by_rule = {t["meta"]["ruleId"]: t for t in todos}
    assert {rule: t["category"] for rule, t in by_rule.items()} == {
        "missing_document": "receipt",
        "invoice_unpaid": "reimburse",
        "income_invoice": "billing",
        "fixed_asset_depreciation": "risk",
        "owner_transfer_nature": "reimburse",
    }
    # 凭证状态透传给前端，用于控制编辑类按钮（已过账只能查看）
    assert by_rule["missing_document"]["meta"]["entryStatus"] == "posted"
    assert by_rule["invoice_unpaid"]["meta"]["entryStatus"] == "draft"
    assert by_rule["income_invoice"]["meta"]["entryStatus"] == ""


def test_list_todos_output_carries_key_alias():
    """输出必须带 key 字段（前端唯一标识），与 todoKey 同值——
    缺了它前端 acting/updating/dismiss 全部串台（undefined 命中所有卡片）。"""
    import asyncio

    from services import todo_service

    async def scenario():
        async def fake_entries(user_id, company_id, *, lite=False):
            return [_entry(id="e1", direction="expense", amount="32", billingStatus="no_document")]

        orig = todo_service.list_entries
        todo_service.list_entries = fake_entries
        try:
            return await todo_service.list_todos("u1", "c1")
        finally:
            todo_service.list_entries = orig

    todos = asyncio.run(scenario())
    assert todos, "应至少派生出一条待办"
    for t in todos:
        assert t["key"] == t["todoKey"]


def test_todo_categories_cover_all_known_rules():
    """规则全集都必须有归属分类，未知规则由构造处兜底 other。"""
    known_rules = {
        "missing_document", "invoice_unpaid", "income_invoice", "fixed_asset_depreciation",
        "intangible_asset_amortization", "entertainment_limit", "ad_limit",
        "owner_transfer_nature", "unmatched_statement", "filing_deadline",
        "intangible_review", "intangible_expiry",
    }
    assert set(TODO_CATEGORIES) == known_rules
    assert set(TODO_CATEGORIES.values()) == {
        "filing", "match", "receipt", "reimburse", "billing", "risk", "intangible",
    }


def test_non_operating_inflows_silently_exempt_from_invoice_confirmation():
    """微信提现、银行结息等非经营流入静默豁免待开票确认，并被收入自动留痕清单。"""
    from services.todo_service import derive_silent_resolved_items

    entries = [
        _entry(id="w1", direction="income", amount="58.47", title="微信支付提现到账-2026-06-18",
               category="微信支付结算", accountCode="1012001"),
        _entry(id="i1", direction="income", amount="12.30", title="银行结息-2026-06-21",
               category="利息收入", accountCode="5603005"),
        _entry(id="sales", direction="income", amount="8800.00", title="软件开发技术服务费",
               category="主营业务收入", accountCode="5001"),
    ]
    todos = _derive_entry_todos(entries)
    rules = {t["meta"]["ruleId"] for t in todos}
    # 微信提现和银行结息不产生待办
    assert "income_invoice" in rules
    assert len([t for t in todos if t["meta"]["ruleId"] == "income_invoice"]) == 1
    assert todos[0]["meta"]["entryId"] == "sales"

    # 留痕清单包含 2 项
    silent_items = derive_silent_resolved_items(entries)
    assert len(silent_items) == 2
    assert {item["entryId"] for item in silent_items} == {"w1", "i1"}


def test_same_amount_invoice_and_expense_merge_into_single_paired_todo():
    """研发相关支出与同金额发票（500元）自动撮合为单张发票卡片，并带有一键核销建议。"""
    entries = [
        _entry(id="e-exp", direction="expense", amount="500.00", title="研发相关支出",
               remark="支付的 deepseek api 调用费用，用于研发", occurDate="2026-05-31",
               source="bank_import", billingStatus="no_document"),
        _entry(id="e-inv", direction="expense", amount="500.00",
               title="发票-杭州深度求索人工智能基础技术研究有限公司",
               merchant="杭州深度求索人工智能基础技术研究有限公司",
               occurDate="2026-06-02", source="invoice_import", billingStatus="has_invoice"),
    ]
    todos = _derive_entry_todos(entries)
    # 不再产生两个割裂的待办，只保留一个待报销/付款卡片
    rules = [t["meta"]["ruleId"] for t in todos]
    assert rules == ["invoice_unpaid"]
    inv_todo = todos[0]
    meta = inv_todo["meta"]
    assert meta["candidateExpenseId"] == "e-exp"
    assert meta["candidateExpenseTitle"] == "研发相关支出"
    assert meta["aiSuggestion"]["action"] == "link_invoice_expense"
    assert "研发相关支出" in meta["aiSuggestion"]["label"]
    assert "deepseek" in meta["aiSuggestion"]["reason"].lower() or "深度求索" in meta["aiSuggestion"]["reason"]
