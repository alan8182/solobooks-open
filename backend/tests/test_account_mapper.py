"""Tests for account_mapper.py — pure function tests, no DB, no mock."""
from decimal import Decimal

import pytest
from services.account_mapper import lookup, get_voucher_hint, CATEGORY_TO_ACCOUNT


class TestLookup:
    def test_known_category_returns_account(self):
        result = lookup("办公费")
        assert result is not None
        assert result["accountCode"] == "5602"
        assert result["accountName"] == "管理费用-办公费"
        assert result["accountType"] == "损益-费用"

    @pytest.mark.parametrize(
        ("category", "account_code", "account_name"),
        [
            ("餐饮", "5603", "管理费用-业务招待费"),
            ("交通", "5601", "管理费用-差旅费"),
            ("办公", "5602", "管理费用-办公费"),
            ("业务招待", "5603", "管理费用-业务招待费"),
            ("差旅", "5601", "管理费用-差旅费"),
            ("房租物业", "5604", "管理费用-租赁物业"),
            ("员工福利", "5609", "管理费用-职工福利费"),
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
        assert result["accountCode"] == "6001"
        assert result["accountType"] == "损益-收入"

    def test_owner_transfer(self):
        result = lookup("老板投入")
        assert result is not None
        assert result["accountCode"] == "4001"

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


class TestGetVoucherHint:
    def test_income_direction(self):
        hint = get_voucher_hint("income", "主营业务收入", 1000)
        assert len(hint["debit"]) == 1
        assert len(hint["credit"]) == 1

        debit = hint["debit"][0]
        credit = hint["credit"][0]

        assert debit["accountCode"] == "1002"  # 银行存款
        assert debit["amount"] == 1000
        assert credit["accountCode"] == "6001"  # 主营业务收入
        assert credit["amount"] == 1000

    def test_interest_income_uses_financial_expense_account(self):
        hint = get_voucher_hint("income", "利息收入", 4.15)
        assert hint["debit"][0]["accountCode"] == "1002"
        assert hint["credit"][0]["accountCode"] == "6603"
        assert hint["credit"][0]["accountName"] == "财务费用-利息收入"
    def test_expense_direction(self):
        hint = get_voucher_hint("expense", "办公费", 500)
        assert hint["debit"][0]["accountCode"] == "5602"
        assert hint["credit"][0]["accountCode"] == "1002"

    def test_expense_boss_personal(self):
        hint = get_voucher_hint("expense", "办公费", 500, payer="boss_personal")
        assert hint["credit"][0]["accountCode"] == "2241"  # 其他应付款-股东
        assert hint["credit"][0]["accountName"] == "其他应付款-股东"

    def test_expense_employee_personal(self):
        hint = get_voucher_hint("expense", "办公费", 500, payer="employee_personal:张三")
        assert hint["credit"][0]["accountCode"] == "2241"
        assert hint["credit"][0]["accountName"] == "其他应付款-员工往来(张三)"

        hint2 = get_voucher_hint("expense", "差旅费", 300, payer="employee_personal")
        assert hint2["credit"][0]["accountCode"] == "2241"
        assert hint2["credit"][0]["accountName"] == "其他应付款-员工往来"

    def test_expense_reimbursement_category_falls_back_to_an_expense_account(self):
        hint = get_voucher_hint(
            "expense", "员工垫付报销", 300, payer="employee_personal:杨云飞",
        )

        assert hint["debit"][0]["accountCode"] == "5699"
        assert hint["credit"][0]["accountCode"] == "2241"
        assert hint["credit"][0]["accountName"] == "其他应付款-员工往来(杨云飞)"

    def test_reimbursement_owner_transfer(self):
        hint = get_voucher_hint("owner_transfer", "股东垫付报销", 500, payer="boss_personal")
        assert hint["debit"][0]["accountCode"] == "2241"
        assert hint["debit"][0]["accountName"] == "其他应付款-股东"
        assert hint["credit"][0]["accountCode"] == "1002"
        assert hint["credit"][0]["accountName"] == "银行存款"

        hint2 = get_voucher_hint("owner_transfer", "员工垫付报销", 600, payer="employee_personal:李四")
        assert hint2["debit"][0]["accountCode"] == "2241"
        assert hint2["debit"][0]["accountName"] == "其他应付款-员工往来(李四)"
        assert hint2["credit"][0]["accountCode"] == "1002"
        assert hint2["credit"][0]["accountName"] == "银行存款"

    def test_owner_transfer_investment(self):
        hint = get_voucher_hint("owner_transfer", "股东投入", 10000)
        assert hint["debit"][0]["accountCode"] == "1002"
        assert hint["credit"][0]["accountCode"] == "4001"

    def test_owner_transfer_investment_legacy_alias(self):
        hint = get_voucher_hint("owner_transfer", "老板投入", 10000)
        assert hint["debit"][0]["accountCode"] == "1002"
        assert hint["credit"][0]["accountCode"] == "4001"

    def test_owner_transfer_withdrawal(self):
        hint = get_voucher_hint("owner_transfer", "股东取现", 2000)
        assert hint["debit"][0]["accountCode"] == "1221"
        assert hint["credit"][0]["accountCode"] == "1002"

    def test_negative_owner_transfer_is_account_receivable_outflow(self):
        hint = get_voucher_hint("owner_transfer", "其他应收款", -0.74)
        assert hint == {
            "debit": [{"accountCode": "1221", "accountName": "其他应收款-其他", "amount": Decimal("0.74")}],
            "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": Decimal("0.74")}],
        }

    def test_unknown_direction_returns_empty(self):
        hint = get_voucher_hint("unknown", "办公费", 100)
        assert hint["debit"] == []
        assert hint["credit"] == []

    def test_unknown_category_uses_default(self):
        hint = get_voucher_hint("expense", "不存在的分类", 100)
        assert hint["debit"][0]["accountCode"] == "5699"  # 管理费用-其他
        assert hint["credit"][0]["accountCode"] == "1002"
