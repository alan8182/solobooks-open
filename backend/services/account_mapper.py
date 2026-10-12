"""
会计科目映射表（硬编码）
用户分类 → 会计科目代码/名称/类型
基于《小企业会计准则》简化版
"""

# 科目映射（按用户分类索引）
CATEGORY_TO_ACCOUNT = {
    # ── 收入类 ──
    "主营业务收入": {
        "accountCode": "6001",
        "accountName": "主营业务收入",
        "accountType": "损益-收入",
        "defaultCounterparty": "1002"  # 银行存款
    },
    "其他业务收入": {
        "accountCode": "6051",
        "accountName": "其他业务收入",
        "accountType": "损益-收入",
        "defaultCounterparty": "1002"
    },
    "营业外收入": {
        "accountCode": "6301",
        "accountName": "营业外收入",
        "accountType": "损益-收入",
        "defaultCounterparty": "1002"
    },
    "利息收入": {
        "accountCode": "6603",
        "accountName": "财务费用-利息收入",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },

    # ── 支出类 ──
    "研发支出-费用化支出": {
        "accountCode": "5301",
        "accountName": "研发支出-费用化支出",
        "accountType": "损益-成本",
        "defaultCounterparty": "1002"
    },
    "原材料采购": {
        "accountCode": "1403",
        "accountName": "原材料",
        "accountType": "资产",
        "defaultCounterparty": "1002"
    },
    "生产成本": {
        "accountCode": "5101",
        "accountName": "主营业务成本-生产",
        "accountType": "损益-成本",
        "defaultCounterparty": "1002"
    },
    "差旅费": {
        "accountCode": "5601",
        "accountName": "管理费用-差旅费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "差旅": {
        "accountCode": "5601",
        "accountName": "管理费用-差旅费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "交通": {
        "accountCode": "5601",
        "accountName": "管理费用-差旅费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "办公费": {
        "accountCode": "5602",
        "accountName": "管理费用-办公费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "办公": {
        "accountCode": "5602",
        "accountName": "管理费用-办公费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "业务招待费": {
        "accountCode": "5603",
        "accountName": "管理费用-业务招待费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "业务招待": {
        "accountCode": "5603",
        "accountName": "管理费用-业务招待费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "餐饮": {
        "accountCode": "5603",
        "accountName": "管理费用-业务招待费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "固定资产": {
        "accountCode": "1601",
        "accountName": "固定资产",
        "accountType": "资产",
        "defaultCounterparty": "1002"
    },
    "人力成本": {
        "accountCode": "5001",
        "accountName": "主营业务成本-人力",
        "accountType": "损益-成本",
        "defaultCounterparty": "1002"
    },
    "员工福利": {
        "accountCode": "5609",
        "accountName": "管理费用-职工福利费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "员工福利费": {
        "accountCode": "5609",
        "accountName": "管理费用-职工福利费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "租金物业": {
        "accountCode": "5604",
        "accountName": "管理费用-租赁物业",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "房租物业": {
        "accountCode": "5604",
        "accountName": "管理费用-租赁物业",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "信息技术服务费": {
        "accountCode": "5605",
        "accountName": "管理费用-信息技术服务费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "运费及物流": {
        "accountCode": "5606",
        "accountName": "管理费用-运费及物流",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "水电通讯费": {
        "accountCode": "5607",
        "accountName": "管理费用-水电通讯费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "银行及金融手续费": {
        "accountCode": "6602",
        "accountName": "财务费用-手续费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "商品采购": {
        "accountCode": "1405",
        "accountName": "库存商品",
        "accountType": "资产",
        "defaultCounterparty": "1002"
    },
    "税费缴纳": {
        "accountCode": "2221",
        "accountName": "应交税费",
        "accountType": "负债",
        "defaultCounterparty": "1002"
    },
    "推广费": {
        "accountCode": "6601",
        "accountName": "销售费用-推广费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "其他支出": {
        "accountCode": "5699",
        "accountName": "管理费用-其他",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "设备采购": {
        "accountCode": "1601",
        "accountName": "固定资产",
        "accountType": "资产",
        "defaultCounterparty": "1002"
    },
    "预付账款": {
        "accountCode": "1123",
        "accountName": "预付账款",
        "accountType": "资产",
        "defaultCounterparty": "1002"
    },

    # ── 资本与往来 ──
    "股东投入": {
        "accountCode": "4001",
        "accountName": "实收资本",
        "accountType": "权益",
        "defaultCounterparty": "1002"
    },
    "资本公积": {
        "accountCode": "4002",
        "accountName": "资本公积-股本溢价",
        "accountType": "权益",
        "defaultCounterparty": "1002"
    },
    "股东垫付": {
        "accountCode": "2241",
        "accountName": "其他应付款-股东",
        "accountType": "负债",
        "defaultCounterparty": "1002"
    },
    "股东取现": {
        "accountCode": "1221",
        "accountName": "其他应收款-股东",
        "accountType": "资产",
        "defaultCounterparty": "1002"
    },
    "其他应收款": {
        "accountCode": "1221",
        "accountName": "其他应收款-其他",
        "accountType": "资产",
        "defaultCounterparty": "1002"
    },
    "其他应付款": {
        "accountCode": "2241",
        "accountName": "其他应付款-其他",
        "accountType": "负债",
        "defaultCounterparty": "1002"
    },
    "往来款": {
        "accountCode": "1221",
        "accountName": "其他应收款-其他",
        "accountType": "资产",
        "defaultCounterparty": "1002"
    },
    "股东垫付报销": {
        "accountCode": "2241",
        "accountName": "其他应付款-股东",
        "accountType": "负债",
        "defaultCounterparty": "1002"
    },
    "员工垫付报销": {
        "accountCode": "2241",
        "accountName": "其他应付款-员工",
        "accountType": "负债",
        "defaultCounterparty": "1002"
    },
}

# 银行/现金科目
COUNTERPARTY_ACCOUNTS = {
    "1002": {"accountName": "银行存款", "accountType": "资产"},
    "1012": {"accountName": "微信支付待结算", "accountType": "资产"},
    "1001": {"accountName": "库存现金", "accountType": "资产"},
    "1122": {"accountName": "应收账款", "accountType": "资产"},
    "1221": {"accountName": "其他应收款", "accountType": "资产"},
}


from typing import Any, Optional

from core.money import money

def lookup(category: str) -> Optional[dict]:
    """根据用户分类查找科目"""
    # 兼容口语化老数据
    if category == "老板投入":
        category = "股东投入"
    elif category == "老板垫付" or category == "老板垫付报销":
        category = "股东垫付"
    elif category == "老板取现":
        category = "股东取现"
    return CATEGORY_TO_ACCOUNT.get(category)


def get_voucher_hint(direction: str, category: str, amount: Any, payer: str = "company_account") -> dict:
    """
    生成借贷提示（凭证草稿原始数据）
    返回格式：{ debit: [...], credit: [...] }
    """
    amount = money(amount)
    # 兼容口语化老数据
    if category == "老板投入":
        category = "股东投入"
    elif category == "老板垫付":
        category = "股东垫付"
    elif category == "老板垫付报销":
        category = "股东垫付报销"
    elif category == "老板取现":
        category = "股东取现"
    elif category == "往来款":
        category = "其他应收款" if direction == "expense" else "其他应付款"

    # 报销结算分类只用于付款冲销。若旧客户端误将其用于费用确认，
    # 至少落入通用费用科目，避免生成 2241 借贷互抵、费用漏记的凭证。
    if direction == "expense" and category in ("股东垫付报销", "员工垫付报销"):
        category = "其他支出"

    acct = CATEGORY_TO_ACCOUNT.get(category, {})
    acct_code = acct.get("accountCode", "5699")
    acct_name = acct.get("accountName", "管理费用-其他")
    counterparty = acct.get("defaultCounterparty", "1002")

    # ponytail: 2241 standard sub-account naming dynamically resolved by parsing payer string
    payable_name = "其他应付款-股东"
    if payer == "boss_personal":
        payable_name = "其他应付款-股东"
    elif payer and (payer == "employee_personal" or payer.startswith("employee_personal:")):
        emp_name = payer.split(":")[-1].strip() if ":" in payer else ""
        payable_name = f"其他应付款-员工往来({emp_name})" if emp_name else "其他应付款-员工往来"
    else:
        payable_name = acct_name if acct_code == "2241" else "其他应付款-其他"

    # 如果是股东或员工个人支付，贷方改为其他应付款
    if direction == "expense":
        if payer == "boss_personal" or (payer and (payer == "employee_personal" or payer.startswith("employee_personal:"))):
            counterparty = "2241"
    
    cp_name = COUNTERPARTY_ACCOUNTS.get(counterparty, {}).get("accountName", "银行存款")
    if counterparty == "2241":
        cp_name = payable_name

    if direction == "income":
        # 借: 银行存款  贷: 主营业务收入
        return {
            "debit": [{"accountCode": counterparty, "accountName": cp_name, "amount": amount}],
            "credit": [{"accountCode": acct_code, "accountName": acct_name, "amount": amount}]
        }
    elif direction == "expense":
        # 借: 管理费用-XX  贷: 银行存款 / 其他应付款
        return {
            "debit": [{"accountCode": acct_code, "accountName": acct_name, "amount": amount}],
            "credit": [{"accountCode": counterparty, "accountName": cp_name, "amount": amount}]
        }
    elif direction == "owner_transfer":
        outflow = amount < 0 or category in ("股东取现", "股东垫付报销", "员工垫付报销")
        amount_abs = abs(amount)
        if outflow:
            # 借: 往来科目 (1221/2241), 贷: 银行存款 (1002)
            # 这样可以拉平其他应付款，减少银行存款
            return {
                "debit": [{"accountCode": acct_code, "accountName": payable_name if acct_code == "2241" else acct_name, "amount": amount_abs}],
                "credit": [{"accountCode": counterparty, "accountName": cp_name, "amount": amount_abs}]
            }
        return {
            "debit": [{"accountCode": counterparty, "accountName": cp_name, "amount": amount_abs}],
            "credit": [{"accountCode": acct_code, "accountName": acct_name, "amount": amount_abs}]
        }
    return {"debit": [], "credit": []}
