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
    """规则全集都必须有归属分类（未匹配银行流水归结账向导处理，不再派生待办）。"""
    known_rules = {
        "opening_balance_required",
        "missing_document", "invoice_unpaid", "income_invoice", "fixed_asset_depreciation",
        "intangible_asset_amortization", "entertainment_limit", "ad_limit",
        "owner_transfer_nature", "filing_deadline", "personal_proxy_withholding",
        "intangible_review", "intangible_expiry",
    }
    assert set(TODO_CATEGORIES) == known_rules
    assert set(TODO_CATEGORIES.values()) == {
        "setup", "filing", "receipt", "reimburse", "billing", "risk", "intangible",
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


def _payable_shape_pair(*, invoice_overrides: dict) -> list[dict]:
    """跨期"发票先到、付款后确认"的典型凭证对：5 月公户付款 + 6 月同额供应商发票。"""
    payment = _entry(
        id="e-pay", direction="expense", amount="500.00",
        title="支付杭州深度求索人工智能基础技术研究有限公司往来款",
        occurDate="2026-05-31", source="bank_import", billingStatus="no_document",
    )
    invoice = _entry(
        id="e-inv", direction="expense", amount="500.00",
        title="发票-杭州深度求索人工智能基础技术研究有限公司",
        merchant="杭州深度求索人工智能基础技术研究有限公司",
        occurDate="2026-06-02", source="invoice_import", billingStatus="has_invoice",
    )
    invoice.update(invoice_overrides)
    return [payment, invoice]


def test_posted_archive_only_invoice_still_matches_missing_doc_expense():
    """已过账的 archive-only 发票（平台手续费误归档后确认/跨期确认）仍进撮合池，
    否则付款侧「待补票据」无人认领——发票列表有票、待办却永远报缺（2026-10-31 实测）。"""
    entries = _payable_shape_pair(invoice_overrides={"isArchiveOnly": True, "voucherStatus": "posted"})
    todos = _derive_entry_todos(entries)
    rules = [t["meta"]["ruleId"] for t in todos]
    assert rules == ["invoice_unpaid"]
    assert todos[0]["meta"]["candidateExpenseId"] == "e-pay"
    assert todos[0]["meta"]["aiSuggestion"]["action"] == "link_invoice_expense"


def test_draft_archive_only_invoice_stays_out_of_matching_pool():
    """draft 底稿（纯证据未记账）不进撮合池：付款侧待补票据保留，发票侧不催付款。"""
    entries = _payable_shape_pair(invoice_overrides={"isArchiveOnly": True, "voucherStatus": "draft"})
    todos = _derive_entry_todos(entries)
    rules = [t["meta"]["ruleId"] for t in todos]
    assert rules == ["missing_document"]


def test_list_todos_excludes_unmatched_bank_statements():
    """未匹配银行流水收敛在月度结账向导集中认领，list_todos 不再派生流水待办。"""
    import asyncio

    from services import todo_service
    from services.bank_service import get_matched_entry_ids
    from services.database import get_conn

    company_id = "c-no-stmt-todo"

    async def scenario():
        await get_matched_entry_ids(company_id)
        conn = await get_conn()
        try:
            await conn.execute("DELETE FROM bank_statements WHERE company_id = ?", (company_id,))
            await conn.execute(
                "INSERT INTO bank_statements "
                "(id, company_id, transaction_date, description, amount, balance, statement_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("stmt-unmatched-1", company_id, "2026-06-18", "入账 | 附言: 微信收款", "58.47", "1058.47", "batch-1", "2026-06-18T00:00:00Z"),
            )
            await conn.commit()
        finally:
            await conn.close()

        async def fake_entries(user_id, cid, *, lite=False):
            return []

        orig = todo_service.list_entries
        todo_service.list_entries = fake_entries
        try:
            return await todo_service.list_todos("u1", company_id)
        finally:
            todo_service.list_entries = orig

    todos = asyncio.run(scenario())
    assert all(t.get("type") != "bank" for t in todos)
    assert all((t.get("meta") or {}).get("ruleId") != "unmatched_statement" for t in todos)
    assert all(not str(t.get("todoKey") or "").startswith("stmt:") for t in todos)


def test_non_invoice_expenses_silently_exempt_from_missing_document_todo():
    """税费、罚款/滞纳金、银行手续费、社保与工资模块凭证不生成「补充票据」待办；手工人力成本仍要凭证。"""
    from services.todo_service import derive_silent_resolved_items

    entries = [
        _entry(
            id="exp-tax",
            title="税费缴纳（金库杭州市余杭区支库）",
            category="税费缴纳",
            accountCode="2221026",
            amount="200.00",
            billingStatus="reconciled",
        ),
        _entry(
            id="exp-fine",
            title="未及时申报个税罚款",
            category="罚款支出",
            accountCode="5711011",
            amount="200.00",
            billingStatus="reconciled",
        ),
        _entry(
            id="exp-late",
            title="逾期缴税滞纳金",
            category="税收滞纳金",
            accountCode="5711010",
            amount="120.00",
            billingStatus="reconciled",
        ),
        _entry(
            id="exp-bank-fee",
            title="对公账户管理费",
            category="银行手续费",
            accountCode="5603002",
            amount="80.00",
            billingStatus="reconciled",
        ),
        _entry(
            id="exp-ss",
            title="缴纳社保公积金",
            category="社保公积金",
            accountCode="221102",
            amount="1200.00",
            billingStatus="reconciled",
        ),
        _entry(
            id="exp-office",
            title="购买办公耗材",
            category="办公费",
            accountCode="5602001",
            amount="320.00",
            billingStatus="no_document",
        ),
        _entry(
            id="exp-salary-accrual",
            title="2026-10月计提员工工资及社保",
            category="人力成本",
            accountCode="5602",
            sourceType="salary_accrual",
            amount="19025.88",
            billingStatus="no_document",
        ),
        # 同样记「人力成本」，但手工录入的临时劳务费不是工资模块凭证，
        # 仍需要合同/发票，不能被来源豁免顺带放过。
        _entry(
            id="exp-labor-manual",
            title="临时劳务费",
            category="人力成本",
            accountCode="5401001",
            sourceType="manual",
            amount="2000.00",
            billingStatus="no_document",
        ),
    ]

    todos = _derive_entry_todos(entries)
    missing_doc_ids = {
        t["meta"]["entryId"] for t in todos if t["meta"]["ruleId"] == "missing_document"
    }
    assert missing_doc_ids == {"exp-office", "exp-labor-manual"}

    silent_items = derive_silent_resolved_items(entries)
    by_id = {item["entryId"]: item for item in silent_items}
    assert set(by_id) == {
        "exp-tax", "exp-fine", "exp-late", "exp-bank-fee", "exp-ss", "exp-salary-accrual",
    }
    assert all(item["categoryLabel"] == "自动免补票核验" for item in silent_items)
    assert "回单" in by_id["exp-tax"]["reason"]
    assert "罚款" in by_id["exp-fine"]["reason"]
    assert "工资模块" in by_id["exp-salary-accrual"]["reason"]


def test_deterministic_todos_skip_llm_advice():
    """个税扣缴等法定申报、期初建账、固定资产折旧与无形资产类待办不调 LLM 生成建议。"""
    import asyncio
    from unittest.mock import patch

    from services import todo_service

    assert not todo_service._is_llm_advice_eligible(
        {"category": "filing", "meta": {"ruleId": "filing_deadline"}}
    )
    assert not todo_service._is_llm_advice_eligible(
        {"category": "setup", "meta": {"ruleId": "opening_balance_required"}}
    )
    assert not todo_service._is_llm_advice_eligible(
        {"category": "intangible", "meta": {"ruleId": "intangible_expiry"}}
    )
    assert not todo_service._is_llm_advice_eligible(
        {"category": "risk", "meta": {"ruleId": "fixed_asset_depreciation"}}
    )
    assert todo_service._is_llm_advice_eligible(
        {"category": "receipt", "meta": {"ruleId": "missing_document"}}
    )
    assert todo_service._is_llm_advice_eligible(
        {"category": "reimburse", "meta": {"ruleId": "invoice_unpaid"}}
    )

    filing_only = [
        {
            "todoKey": "filing:dl-iit-2026-11",
            "type": "filing",
            "category": "filing",
            "title": "个税扣缴（2026-11）",
            "desc": "每月必须申报；没有员工或没发工资时，也要做零申报。",
            "done": False,
            "dismissed": False,
            "meta": {"ruleId": "filing_deadline"},
        }
    ]

    async def fake_list_todos(_uid: str, _cid: str):
        return filing_only

    orig_list = todo_service.list_todos
    todo_service.list_todos = fake_list_todos
    try:
        with patch("services.llm.safe_chat_text_json") as mock_llm:
            result = asyncio.run(todo_service.advise_todos_with_llm("u1", "c1"))
            mock_llm.assert_not_called()
            assert "aiSuggestion" not in (result[0].get("meta") or {})
    finally:
        todo_service.list_todos = orig_list


def test_personal_proxy_withholding_invoice_detection_and_tiers():
    """自然人代开进项发票按 2026-11-01 政策节点及 800/1000 元三档精准生成扣缴待办且不调 LLM。"""
    from datetime import date
    from decimal import Decimal

    from services import todo_service

    assert not todo_service._is_llm_advice_eligible(
        {"category": "filing", "meta": {"ruleId": "personal_proxy_withholding"}}
    )

    assert todo_service._estimate_labor_iit(Decimal("300")) == Decimal("0.00")
    assert todo_service._estimate_labor_iit(Decimal("2000")) == Decimal("240.00")
    assert todo_service._estimate_labor_iit(Decimal("5000")) == Decimal("800.00")

    invoices = [
        # 1) 身份证号 + 个人姓名，2026-11-05，¥300（<=800 免征增值税+个税参考0，零/免税申报）
        {
            "id": "inv-1",
            "direction": "expense",
            "invoiceStatus": "normal",
            "invoiceNumber": "26330001",
            "invoiceDate": date(2026, 11, 5),
            "sellerName": "王翼",
            "sellerTaxId": "330106199001011234",
            "amountInclTax": Decimal("300.00"),
            "remark": "",
        },
        # 2) XML withholdingNotice=True，2026-11-05，¥900（800-1000：个税20，增值税免征）
        {
            "id": "inv-2",
            "direction": "expense",
            "invoiceStatus": "normal",
            "invoiceNumber": "26330002",
            "invoiceDate": date(2026, 11, 5),
            "sellerName": "李四",
            "sellerTaxId": "",
            "amountInclTax": Decimal("900.00"),
            "remark": "",
        },
        # 3) 备注含「代开」，2026-11-05，¥2020（不含税2000：个税244，增值税20）
        {
            "id": "inv-3",
            "direction": "expense",
            "invoiceStatus": "normal",
            "invoiceNumber": "26330003",
            "invoiceDate": date(2026, 11, 5),
            "sellerName": "张伟",
            "sellerTaxId": "",
            "amountExclTax": Decimal("2000.00"),
            "amountInclTax": Decimal("2020.00"),
            "remark": "税务机关代开",
        },
        # 4) 2026-10-31（新政前）：仅提示劳务报酬个税
        {
            "id": "inv-4",
            "direction": "expense",
            "invoiceStatus": "normal",
            "invoiceNumber": "26330004",
            "invoiceDate": date(2026, 10, 31),
            "sellerName": "王翼",
            "sellerTaxId": "330106199001011234",
            "amountInclTax": Decimal("300.00"),
            "remark": "依法代扣代缴个人所得税",
        },
        # 5) 普通企业进项发票：不触发
        {
            "id": "inv-corp",
            "direction": "expense",
            "invoiceStatus": "normal",
            "invoiceNumber": "26330099",
            "invoiceDate": date(2026, 11, 5),
            "sellerName": "深圳市腾讯计算机系统有限公司",
            "sellerTaxId": "91440300708461136T",
            "amountInclTax": Decimal("300.00"),
            "remark": "",
        },
    ]

    todos = todo_service._build_personal_proxy_withholding_todos(invoices, {"inv-2"})
    assert [t["meta"]["invoiceNumber"] for t in todos] == [
        "26330001", "26330002", "26330003", "26330004"
    ]

    t1, t2, t3, t4 = todos
    assert t1["meta"]["dueDate"] == "2026-12-15"
    assert t1["meta"]["vatWithholdingApplicable"] is True
    assert t1["meta"]["vatOverThreshold"] is False
    assert t1["meta"]["estimatedIit"] == "0.00"
    assert t1["meta"]["estimatedVat"] == "0.00"
    assert "零/免税申报" in t1["desc"]

    assert t2["meta"]["vatOverThreshold"] is False
    assert t2["meta"]["estimatedIit"] == "20.00"
    assert t2["meta"]["estimatedVat"] == "0.00"
    assert "未达 1,000 元" in t2["desc"]

    assert t3["meta"]["vatOverThreshold"] is True
    assert t3["meta"]["estimatedIit"] == "244.00"
    assert t3["meta"]["estimatedVat"] == "20.00"
    assert "已达 1,000 元" in t3["desc"]
    assert "七类服务" in t3["desc"]

    assert t4["meta"]["dueDate"] == "2026-11-15"
    assert t4["meta"]["vatWithholdingApplicable"] is False
    assert "劳务报酬所得" in t4["desc"]
