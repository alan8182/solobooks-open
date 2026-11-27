# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Tests for account_mapper.py — pure function tests, no DB, no mock."""
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from core.chart_policy import chart_is_leaf
from services.account_mapper import (
    CATEGORY_TO_ACCOUNT,
    COUNTERPARTY_ACCOUNTS,
    get_voucher_hint,
    lookup,
)

COA_JSON = Path(__file__).resolve().parent.parent / "data" / "chart_of_accounts.json"


def _load_coa() -> dict[str, dict]:
    doc = json.loads(COA_JSON.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for _group, accounts in doc.get("accounts", {}).items():
        for code, info in accounts.items():
            out[str(code)] = info
    return out


class TestLookup:
    def test_known_category_returns_account(self):
        result = lookup("办公费")
        assert result is not None
        assert result["accountCode"] == "5602004"
        assert result["accountName"] == "管理费用-办公费"
        assert result["accountType"] == "损益-费用"

    @pytest.mark.parametrize(
        ("category", "account_code", "account_name"),
        [
            ("餐饮", "5602002", "管理费用-业务招待费"),
            ("餐饮费", "5602002", "管理费用-业务招待费"),
            ("交通", "5602006", "管理费用-差旅费"),
            # agent 提示词建议的口语分类（交通费/餐饮费/通讯费）必须能命中，
            # 否则全部落到「管理费用-其他」兜底（历史缺陷）
            ("交通费", "5602006", "管理费用-差旅费"),
            ("办公", "5602004", "管理费用-办公费"),
            ("业务招待", "5602002", "管理费用-业务招待费"),
            ("差旅", "5602006", "管理费用-差旅费"),
            ("房租物业", "5602013", "管理费用-租赁费"),
            ("员工福利", "5602014", "管理费用-职工福利费"),
            ("通讯费", "5602005", "管理费用-水电费"),
        ],
    )
    def test_ui_category_aliases_return_specific_accounts(self, category, account_code, account_name):
        result = lookup(category)
        assert result is not None
        assert result["accountCode"] == account_code
        assert result["accountName"] == account_name
    def test_income_category(self):
        result = lookup("主营业务收入")
        assert result is not None
        assert result["accountCode"] == "5001"
        assert result["accountType"] == "损益-收入"

    def test_owner_transfer(self):
        result = lookup("老板投入")
        assert result is not None
        assert result["accountCode"] == "3001"

    def test_unknown_category_returns_none(self):
        assert lookup("不存在的分类") is None

    def test_all_categories_are_defined(self):
        known = {"办公费", "办公", "差旅费", "差旅", "交通", "业务招待费", "业务招待", "餐饮", "主营业务收入", "固定资产",
                 "人力成本", "员工福利", "员工福利费", "租金物业", "房租物业", "推广费", "商品采购", "原材料采购",
                 "其他支出", "股东投入", "资本公积", "股东取现", "股东垫付", "设备采购",
                 "信息技术服务费", "运费及物流", "水电通讯费", "银行及金融手续费",
                 "生产成本", "研发支出-费用化支出", "税费缴纳", "预付账款", "营业外收入", "其他业务收入", "利息收入",
                 "股东垫付报销", "员工垫付报销"}
        defined = set(CATEGORY_TO_ACCOUNT.keys())
        missing = known - defined
        assert not missing, f"Missing category mappings: {missing}"


class TestContainerCodeGuard:
    """容器码守护：映射表 / 对手方科目 / AI 提示词文案中的科目码必须全部末级。

    背景：1221/2211/2221 容器化后不能直接记账（create_entry 报
    INVALID_ACCOUNT）。历史上税费类提示词三处（bank_ai_prompts / classifier /
    ocr / account_mapper）都指向容器 2221，AI 生成的凭证全部 422。
    """

    def test_category_and_counterparty_accounts_are_leaf(self):
        coa = _load_coa()
        problems = []
        for category, info in CATEGORY_TO_ACCOUNT.items():
            code = str(info.get("accountCode") or "")
            if not code:
                continue
            if code not in coa:
                problems.append(f"分类「{category}」指向 COA 不存在的科目 {code}")
            elif not chart_is_leaf(code, coa[code]):
                problems.append(f"分类「{category}」指向非末级科目 {code}")
        for code in COUNTERPARTY_ACCOUNTS:
            if code not in coa:
                problems.append(f"对手方科目 {code} 不在 COA 中")
            elif not chart_is_leaf(code, coa[code]):
                problems.append(f"对手方科目 {code} 是非末级容器")
        assert not problems, "；".join(problems)

    def test_tax_payment_category_uses_vat_leaf_account(self):
        result = lookup("税费缴纳")
        assert result is not None
        assert result["accountCode"] == "2221026"
        assert result["accountName"] == "应交增值税（小规模纳税人专用）"

    def test_ai_prompt_modules_reference_only_leaf_accounts(self):
        """classifier / ocr / bank_ai_prompts 源码中的科目码必须末级。"""
        coa = _load_coa()
        problems = []
        for name in ("bank_ai_prompts.py", "classifier.py", "ocr.py"):
            source = (Path(__file__).resolve().parent.parent / "services" / name).read_text(encoding="utf-8")
            for match in re.finditer(r"\b(\d{4,7})\b", source):
                code = match.group(1)
                if code in coa and not chart_is_leaf(code, coa[code]):
                    line = source[: match.start()].count("\n") + 1
                    problems.append(f"{name}:{line} 引用非末级科目 {code}")
        assert not problems, "；".join(problems)


class TestGetVoucherHint:
    def test_income_direction(self):
        hint = get_voucher_hint("income", "主营业务收入", 1000)
        assert len(hint["debit"]) == 1
        assert len(hint["credit"]) == 1

        debit = hint["debit"][0]
        credit = hint["credit"][0]

        assert debit["accountCode"] == "1002"  # 银行存款
        assert debit["amount"] == 1000
        assert credit["accountCode"] == "5001"  # 主营业务收入
        assert credit["amount"] == 1000

    def test_interest_income_uses_financial_expense_account(self):
        hint = get_voucher_hint("income", "利息收入", 4.15)
        assert hint["debit"][0]["accountCode"] == "1002"
        assert hint["credit"][0]["accountCode"] == "5603005"
        assert hint["credit"][0]["accountName"] == "财务费用-利息收入"

    def test_wechat_settlement_hint(self):
        hint = get_voucher_hint("income", "微信支付结算", 19.82)
        assert hint["debit"][0]["accountCode"] == "1002"
        assert hint["debit"][0]["accountName"] == "银行存款"
        assert hint["credit"][0]["accountCode"] == "1012001"
        assert hint["credit"][0]["accountName"] == "微信支付待结算"
        assert hint["credit"][0]["amount"] == Decimal("19.82")

    def test_wanglai_expense_lands_in_neutral_receivable(self):
        """非员工的往来暂付（平台验证款/供应商往来）不进员工往来 122103。"""
        hint = get_voucher_hint("expense", "往来款", Decimal("0.15"))
        assert hint["credit"][0]["accountCode"] == "1002"
        assert hint["debit"][0]["accountCode"] == "122199"
        assert hint["debit"][0]["accountName"] == "其他应收款-待收款项"

    def test_wanglai_income_lands_in_payable(self):
        hint = get_voucher_hint("income", "往来款", Decimal("500"))
        assert hint["debit"][0]["accountCode"] == "1002"
        assert hint["credit"][0]["accountCode"] == "2241"
        assert hint["credit"][0]["accountName"] == "其他应付款"

    def test_alipay_settlement_hint(self):
        hint = get_voucher_hint("income", "支付宝结算", 100.00)
        assert hint["debit"][0]["accountCode"] == "1002"
        assert hint["credit"][0]["accountCode"] == "1012002"
        assert hint["credit"][0]["accountName"] == "支付宝待结算"

    def test_expense_direction(self):
        hint = get_voucher_hint("expense", "办公费", 500)
        assert hint["debit"][0]["accountCode"] == "5602004"
        assert hint["credit"][0]["accountCode"] == "1002"

    def test_expense_boss_personal(self):
        hint = get_voucher_hint("expense", "办公费", 500, payer="boss_personal")
        assert hint["credit"][0]["accountCode"] == "2241"
        # 科目名只取科目表（2241 单一末级），垫付人身份不再拼进名字
        assert hint["credit"][0]["accountName"] == "其他应付款"

    def test_expense_employee_personal(self):
        hint = get_voucher_hint("expense", "办公费", 500, payer="employee_personal:张三")
        assert hint["credit"][0]["accountCode"] == "2241"
        assert hint["credit"][0]["accountName"] == "其他应付款"

        hint2 = get_voucher_hint("expense", "差旅费", 300, payer="employee_personal")
        assert hint2["credit"][0]["accountCode"] == "2241"
        assert hint2["credit"][0]["accountName"] == "其他应付款"

    def test_legacy_payer_personal_means_boss_advance(self):
        """早期 agent 参数枚举给的 personal 也必须是老板垫付，不能贷银行存款。"""
        hint = get_voucher_hint("expense", "办公费", 500, payer="personal")
        assert hint["credit"][0]["accountCode"] == "2241"
        assert hint["credit"][0]["accountName"] == "其他应付款"

    def test_normalize_payer(self):
        from services.account_mapper import normalize_payer

        assert normalize_payer("personal") == "boss_personal"
        assert normalize_payer("boss_personal") == "boss_personal"
        assert normalize_payer("employee_personal: 张三 ") == "employee_personal:张三"
        assert normalize_payer("employee_personal:") == "employee_personal"
        assert normalize_payer(None) == "company_account"
        # 未知值回退公司账户，绝不静默落进个人往来
        assert normalize_payer("随便写的") == "company_account"

    def test_expense_reimbursement_category_falls_back_to_an_expense_account(self):
        hint = get_voucher_hint(
            "expense", "员工垫付报销", 300, payer="employee_personal:杨云飞",
        )

        assert hint["debit"][0]["accountCode"] == "5602999"
        assert hint["credit"][0]["accountCode"] == "2241"
        assert hint["credit"][0]["accountName"] == "其他应付款"

    def test_reimbursement_owner_transfer(self):
        hint = get_voucher_hint("owner_transfer", "股东垫付报销", 500, payer="boss_personal")
        assert hint["debit"][0]["accountCode"] == "2241"
        assert hint["debit"][0]["accountName"] == "其他应付款"
        assert hint["credit"][0]["accountCode"] == "1002"
        assert hint["credit"][0]["accountName"] == "银行存款"

        hint2 = get_voucher_hint("owner_transfer", "员工垫付报销", 600, payer="employee_personal:李四")
        assert hint2["debit"][0]["accountCode"] == "2241"
        assert hint2["debit"][0]["accountName"] == "其他应付款"
        assert hint2["credit"][0]["accountCode"] == "1002"
        assert hint2["credit"][0]["accountName"] == "银行存款"

    def test_owner_transfer_investment(self):
        hint = get_voucher_hint("owner_transfer", "股东投入", 10000)
        assert hint["debit"][0]["accountCode"] == "1002"
        assert hint["credit"][0]["accountCode"] == "3001"

    def test_owner_transfer_investment_legacy_alias(self):
        hint = get_voucher_hint("owner_transfer", "老板投入", 10000)
        assert hint["debit"][0]["accountCode"] == "1002"
        assert hint["credit"][0]["accountCode"] == "3001"

    def test_owner_transfer_withdrawal(self):
        hint = get_voucher_hint("owner_transfer", "股东取现", 2000)
        # 1221 已拆出子级，其他应收款改用末级明细 122102
        assert hint["debit"][0]["accountCode"] == "122102"
        assert hint["credit"][0]["accountCode"] == "1002"

    def test_negative_owner_transfer_is_account_receivable_outflow(self):
        hint = get_voucher_hint("owner_transfer", "其他应收款", -0.74)
        assert hint == {
            "debit": [{"accountCode": "122103", "accountName": "其他应收款-员工往来", "amount": Decimal("0.74")}],
            "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": Decimal("0.74")}],
        }

    def test_unknown_direction_returns_empty(self):
        hint = get_voucher_hint("unknown", "办公费", 100)
        assert hint["debit"] == []
        assert hint["credit"] == []

    def test_unknown_category_uses_default(self):
        hint = get_voucher_hint("expense", "不存在的分类", 100)
        assert hint["debit"][0]["accountCode"] == "5602999"  # 管理费用-其他
        assert hint["credit"][0]["accountCode"] == "1002"


class TestIncomeVatSeparation:
    """销售收入必须价税分离；历史行为（含税入收入）只在未传税率时保留。"""

    def test_income_with_vat_rate_splits_tax(self):
        hint = get_voucher_hint("income", "主营业务收入", "1010.00", vat_rate="0.01")

        assert len(hint["debit"]) == 1
        assert hint["debit"][0]["accountCode"] == "1002"
        assert hint["debit"][0]["amount"] == Decimal("1010.00")

        credits = {line["accountCode"]: line["amount"] for line in hint["credit"]}
        assert credits["5001"] == Decimal("1000.00")
        assert credits["2221026"] == Decimal("10.00")
        assert sum(credits.values()) == hint["debit"][0]["amount"]

    def test_income_tax_uses_difference_so_entry_always_balances(self):
        # 含税额除不尽时，税额取差额，避免两条贷方分录相加差 1 分钱
        hint = get_voucher_hint("income", "主营业务收入", "100.00", vat_rate="0.03")

        credit_total = sum(line["amount"] for line in hint["credit"])
        assert credit_total == Decimal("100.00")

    def test_explicit_invoice_tax_wins(self):
        hint = get_voucher_hint(
            "income", "主营业务收入", "10300.00", vat_amount="300.00",
        )
        credits = {line["accountCode"]: line["amount"] for line in hint["credit"]}
        assert credits["5001"] == Decimal("10000.00")
        assert credits["2221026"] == Decimal("300.00")

    def test_non_sales_income_is_not_levied(self):
        # 营业外收入、利息收入不属于增值税销售行为
        hint = get_voucher_hint("income", "营业外收入", "500.00", vat_rate="0.01")
        assert {line["accountCode"] for line in hint["credit"]} == {"5301"}

        interest = get_voucher_hint("income", "利息收入", "4.15", vat_rate="0.01")
        assert {line["accountCode"] for line in interest["credit"]} == {"5603005"}

    def test_unknown_income_category_falls_back_to_revenue_not_expense(self):
        # 历史缺陷：未知收入分类落到 5602999 管理费用-其他，收入被记成负的费用
        hint = get_voucher_hint("income", "AI 新造的分类", "100.00", vat_rate="0.01")
        assert hint["credit"][0]["accountCode"] == "5001"

    def test_legacy_call_without_rate_keeps_gross(self):
        hint = get_voucher_hint("income", "主营业务收入", "1000.00")
        assert len(hint["credit"]) == 1
        assert hint["credit"][0]["amount"] == Decimal("1000.00")


class TestOptionASecondaryAccountsAndPromptSync:
    """选项 A 全量 27 个业务二级科目 + 单一真相源 AI Prompt / 接口元数据同步测试。"""

    OPTION_A_MAPPINGS = {
        # 营业外支出 11 个
        "罚款支出": ("expense", "5711011"),
        "税收滞纳金": ("expense", "5711010"),
        "捐赠支出": ("expense", "5711003"),
        "赞助支出": ("expense", "5711002"),
        "非流动资产处置净损失": ("expense", "5711001"),
        "盘亏损失": ("expense", "5711004"),
        "坏账损失": ("expense", "5711005"),
        "存货毁损报废损失": ("expense", "5711006"),
        "长期债券投资损失": ("expense", "5711007"),
        "长期股权投资损失": ("expense", "5711008"),
        "自然灾害损失": ("expense", "5711009"),
        # 营业外收入 4 个
        "政府补助": ("income", "5301002"),
        "非流动资产处置净收益": ("income", "5301001"),
        "捐赠收益": ("income", "5301003"),
        "盘盈收益": ("income", "5301004"),
        # 销售费用特色 6 个
        "广告费": ("expense", "5601015"),
        "展览费": ("expense", "5601009"),
        "商品维修费": ("expense", "5601010"),
        "装卸费": ("expense", "5601012"),
        "包装费": ("expense", "5601013"),
        "保险费": ("expense", "5601014"),
        # 管理费用补齐 3 个
        "修理费": ("expense", "5602003"),
        "开办费": ("expense", "5602009"),
        "研究费用": ("expense", "5602010"),
        # 财务费用补齐 3 个
        "利息费用": ("expense", "5603001"),
        "汇兑损益": ("expense", "5603003"),
        "现金折扣": ("expense", "5603004"),
    }

    def test_all_27_secondary_categories_mapped_and_balanced(self):
        coa = _load_coa()
        assert len(self.OPTION_A_MAPPINGS) == 27
        for category, (direction, expected_code) in self.OPTION_A_MAPPINGS.items():
            info = lookup(category)
            assert info is not None, f"缺少二级科目映射: {category}"
            assert info["accountCode"] == expected_code
            assert expected_code in coa
            assert chart_is_leaf(expected_code, coa[expected_code])
            hint = get_voucher_hint(direction, category, "123.45", vat_rate="0.01")
            assert len(hint["debit"]) == 1
            assert len(hint["credit"]) == 1
            if direction == "expense":
                assert hint["debit"][0]["accountCode"] == expected_code
                assert hint["credit"][0]["accountCode"] == "1002"
            else:
                assert hint["debit"][0]["accountCode"] == "1002"
                assert hint["credit"][0]["accountCode"] == expected_code

    def test_all_ai_prompts_sync_secondary_accounts_and_exclude_aliases(self):
        from services.bank_ai_prompts import BANK_CLASSIFY_PROMPT
        from services.classifier import BATCH_SPLIT_V3_PROMPT, CLASSIFIER_V3_PROMPT
        from services.ocr import _CATEGORY_TABLE

        coa = _load_coa()
        for prompt in (BANK_CLASSIFY_PROMPT, CLASSIFIER_V3_PROMPT, BATCH_SPLIT_V3_PROMPT):
            for code in ("5711011", "5711010", "5301002", "5601015", "5602003", "5603001", "5603003"):
                assert code in prompt, f"Prompt 缺少科目代码 {code}"
            assert "| 餐饮 |" not in prompt
            assert "| 交通 |" not in prompt

        for code in ("5711011", "5711010", "5601015", "5602003", "5603001"):
            assert code in _CATEGORY_TABLE
        assert "5301002" not in _CATEGORY_TABLE  # OCR 支出表不含营业外收入

        # 渲染后的所有 Prompt 也必须只引用末级科目
        for name, rendered in (
            ("BANK_CLASSIFY_PROMPT", BANK_CLASSIFY_PROMPT),
            ("CLASSIFIER_V3_PROMPT", CLASSIFIER_V3_PROMPT),
            ("BATCH_SPLIT_V3_PROMPT", BATCH_SPLIT_V3_PROMPT),
            ("OCR_CATEGORY_TABLE", _CATEGORY_TABLE),
        ):
            for match in re.finditer(r"\b(\d{4,7})\b", rendered):
                code = match.group(1)
                if code in coa:
                    assert chart_is_leaf(code, coa[code]), f"{name} 渲染出非末级科目 {code}"

    def test_list_account_mapping_exports_group_alias_and_sort_order(self):
        from services.account_mapper import GROUP_ORDER
        from services.category_service import list_account_mapping

        items = {item["category"]: item for item in list_account_mapping()}
        assert set(GROUP_ORDER) >= {item["group"] for item in items.values()}
        assert items["罚款支出"]["group"] == "营业外支出"
        assert items["罚款支出"]["isAlias"] is False
        assert items["政府补助"]["direction"] == "income"
        assert items["政府补助"]["group"] == "营业外收入"
        assert items["餐饮"]["isAlias"] is True
        assert isinstance(items["罚款支出"]["sortOrder"], int)
