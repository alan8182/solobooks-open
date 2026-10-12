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


def test_boss_personal_invoice_import_generates_single_todo():
    """发票导入 + 股东垫付只出一张卡：发票待付款承载生命周期，性质确认不再重复催问。"""
    todos = _derive_entry_todos([
        _entry(id="boss-inv", direction="expense", amount="481.33",
               source="invoice_import", payer="boss_personal", billingStatus="has_invoice"),
    ])
    rules = _rule_ids(todos)
    assert "invoice_unpaid" in rules
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
        async def fake_entries(user_id, company_id):
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
        "entertainment_limit", "ad_limit", "owner_transfer_nature", "unmatched_statement",
        "filing_deadline",
    }
    assert set(TODO_CATEGORIES) == known_rules
    assert set(TODO_CATEGORIES.values()) == {"filing", "match", "receipt", "reimburse", "billing", "risk"}
