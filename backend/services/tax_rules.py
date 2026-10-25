# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""
税务规则引擎（硬编码规则）
基于中国小微企业常见税务口径
"""
from decimal import Decimal

from core.money import money, rate

# 每类支出的税务规则
TAX_RULES = {
    "差旅费": {
        "vatRate": 0.06,
        "vatExempt": False,
        "vatType": "general",
        "citDeductible": True,
        "deductibilityLimit": 1.0,       # 100%税前扣除
        "requiredBilling": "invoice",
        "riskTags": [],
        "taxNote": "差旅费可按规定税前扣除，需保留行程凭证和发票"
    },
    "办公费": {
        "vatRate": 0.06,
        "vatExempt": False,
        "vatType": "general",
        "citDeductible": True,
        "deductibilityLimit": 1.0,
        "requiredBilling": "invoice",
        "riskTags": [],
        "taxNote": "办公费可按规定税前扣除企业所得税"
    },
    "业务招待费": {
        "vatRate": 0.06,
        "vatExempt": False,
        "vatType": "general",
        "citDeductible": True,
        "deductibilityLimit": 0.6,        # 60%限额扣除
        "requiredBilling": "invoice",
        "riskTags": ["60%限额扣除", "需备注客户/业务目的"],
        "taxNote": "业务招待费按发生额60%扣除，最高不超过当年销售收入的5‰"
    },
    "固定资产": {
        "vatRate": 0.13,
        "vatExempt": False,
        "vatType": "general",
        "citDeductible": True,
        "deductibilityLimit": 1.0,
        "requiredBilling": "invoice",
        "riskTags": ["需折旧处理", "需取得合规发票"],
        "taxNote": "固定资产按使用年限计提折旧，不能一次性扣除。小规模纳税人取得专票仅作为合规入账凭证，不做进项税额抵扣"
    },
    "设备采购": {
        "vatRate": 0.13,
        "vatExempt": False,
        "vatType": "general",
        "citDeductible": True,
        "deductibilityLimit": 1.0,
        "requiredBilling": "invoice",
        "riskTags": ["需折旧处理", "需取得合规发票"],
        "taxNote": "设备采购按固定资产口径处理，小规模纳税人取得专票仅作为合规入账凭证，不做进项税额抵扣"
    },
    "人力成本": {
        "vatRate": 0.00,
        "vatExempt": True,
        "vatType": "simplified",
        "citDeductible": True,
        "deductibilityLimit": 1.0,
        "requiredBilling": "contract",
        "riskTags": ["需劳动合同或协议", "可能涉及个税代扣"],
        "taxNote": "人力成本不计增值税，但需合规劳动合同和付款凭证"
    },
    "租金物业": {
        "vatRate": 0.06,
        "vatExempt": False,
        "vatType": "general",
        "citDeductible": True,
        "deductibilityLimit": 1.0,
        "requiredBilling": "invoice",
        "riskTags": [],
        "taxNote": "租金需取得合规发票，小规模纳税人按征收率计算增值税"
    },
    "推广费": {
        "vatRate": 0.06,
        "vatExempt": False,
        "vatType": "general",
        "citDeductible": True,
        "deductibilityLimit": 0.15,       # 15%限额（广宣费）
        "requiredBilling": "invoice",
        "riskTags": ["15%税前扣除限额"],
        "taxNote": "广告宣传费按销售收入15%限额扣除，超出部分可结转"
    },
    "其他支出": {
        "vatRate": 0.06,
        "vatExempt": False,
        "vatType": "general",
        "citDeductible": True,
        "deductibilityLimit": 1.0,
        "requiredBilling": "invoice",
        "riskTags": ["需补充用途说明"],
        "taxNote": "请补充支出用途以便准确归类"
    },
}

# 收入类税务规则
INCOME_TAX_RULES = {
    "主营业务收入": {
        "vatRate": 0.06,
        "vatExempt": False,
        "vatType": "general",
        "citTaxable": True,
        "riskTags": [],
        "taxNote": "小规模纳税人按征收率计算增值税，现行优惠以主管税务机关要求为准"
    },
    "其他业务收入": {
        "vatRate": 0.06,
        "vatExempt": False,
        "vatType": "general",
        "citTaxable": True,
        "riskTags": [],
        "taxNote": "同上"
    },
    "营业外收入": {
        "vatRate": 0.06,
        "vatExempt": False,
        "vatType": "general",
        "citTaxable": True,
        "riskTags": ["非经常性收入"],
        "taxNote": "营业外收入也需并入应税收入"
    },
}


def lookup_tax(category: str, direction: str = "expense") -> dict:
    """查税务规则"""
    if direction == "income":
        return INCOME_TAX_RULES.get(category, {})
    return TAX_RULES.get(category, {
        "vatRate": 0.06,
        "vatExempt": False,
        "vatType": "general",
        "citDeductible": True,
        "deductibilityLimit": 1.0,
        "requiredBilling": "invoice",
        "riskTags": [],
        "taxNote": ""
    })


def compute_vat(amount, category: str, direction: str = "expense") -> dict:
    """
    计算增值税（价外税口径）
    中国增值税为价外税，用户录入金额默认为含税总价：
      不含税额 = 含税额 / (1 + 税率)
      税额     = 不含税额 × 税率
    """
    amount = money(amount)
    rules = lookup_tax(category, direction)
    vat_rate = rate(rules.get("vatRate", "0.06"))
    if rules.get("vatExempt"):
        vat_rate = rate("0")
    if vat_rate > 0:
        amount_excluding_tax = money(amount / (Decimal("1") + vat_rate))
        vat_amount = money(amount_excluding_tax * vat_rate)
    else:
        amount_excluding_tax = amount
        vat_amount = money("0")
    return {
        "vatRate": vat_rate,
        "vatAmount": vat_amount,
        "vatExempt": rules.get("vatExempt", False),
        "amountExcludingTax": amount_excluding_tax,
    }


def get_risk_tags(category: str, direction: str = "expense") -> list:
    """获取风险标签"""
    rules = lookup_tax(category, direction)
    return sanitize_risk_tags(rules.get("riskTags", []))


def sanitize_risk_tags(tags: list) -> list:
    """按小规模纳税人口径清理误导性的进项抵扣标签。"""
    banned_terms = ("进项可抵扣", "专票可抵", "可抵进项", "抵扣进项")
    if isinstance(tags, str):
        source = [tags]
    else:
        source = tags or []
    return [
        tag for tag in source
        if not any(term in str(tag) for term in banned_terms)
    ]


def is_tax_deductible(
    category: str,
    direction: str = "expense",
    billing_status: str | None = None,
    has_invoice_number: bool = False,
) -> bool | None:
    """是否可税前扣除（三态）。

    True 可扣除 / False 明确不得扣除（如声明无票）/ None 待确认（状态不明）。
    绝不默认 True——规则要求发票而单据状态不明时返回 None，
    由报表层归入 pending_deductibility，汇算底稿按无票调增兜底。
    """
    from services.cit_adjustment_service import evaluate_deductibility

    rules = lookup_tax(category, direction)
    return evaluate_deductibility(
        rules.get("requiredBilling"),
        billing_status,
        has_invoice_number,
    )
