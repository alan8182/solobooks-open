# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""
会计科目映射表（硬编码）
用户分类 → 会计科目代码/名称/类型
编码对齐《小企业会计准则》（4位总账+7位明细+9位二级明细）
"""

# 科目映射（按用户分类索引）
CATEGORY_TO_ACCOUNT = {
    # ── 收入类 ──
    "主营业务收入": {
        "accountCode": "5001",
        "accountName": "主营业务收入",
        "accountType": "损益-收入",
        "defaultCounterparty": "1002"  # 银行存款
    },
    "咨询服务收入": {
        "accountCode": "5001001",
        "accountName": "主营业务收入-咨询服务",
        "accountType": "损益-收入",
        "defaultCounterparty": "1002"
    },
    "咨询服务": {
        "accountCode": "5001001",
        "accountName": "主营业务收入-咨询服务",
        "accountType": "损益-收入",
        "defaultCounterparty": "1002"
    },
    "技术服务收入": {
        "accountCode": "5001002",
        "accountName": "主营业务收入-技术服务",
        "accountType": "损益-收入",
        "defaultCounterparty": "1002"
    },
    "技术服务": {
        "accountCode": "5001002",
        "accountName": "主营业务收入-技术服务",
        "accountType": "损益-收入",
        "defaultCounterparty": "1002"
    },
    "信息技术服务": {
        "accountCode": "5001002",
        "accountName": "主营业务收入-技术服务",
        "accountType": "损益-收入",
        "defaultCounterparty": "1002"
    },
    "信息技术服务收入": {
        "accountCode": "5001002",
        "accountName": "主营业务收入-技术服务",
        "accountType": "损益-收入",
        "defaultCounterparty": "1002"
    },
    "其他业务收入": {
        "accountCode": "5051",
        "accountName": "其他业务收入",
        "accountType": "损益-收入",
        "defaultCounterparty": "1002"
    },
    "营业外收入": {
        "accountCode": "5301",
        "accountName": "营业外收入",
        "accountType": "损益-收入",
        "defaultCounterparty": "1002"
    },
    "利息收入": {
        "accountCode": "5603005",
        "accountName": "财务费用-利息收入",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },

    # ── 支出类 ──
    "研发支出-费用化支出": {
        "accountCode": "4301",
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
        "accountCode": "5401002",
        "accountName": "主营业务成本-生产",
        "accountType": "损益-成本",
        "defaultCounterparty": "1002"
    },
    "差旅费": {
        "accountCode": "5602006",
        "accountName": "管理费用-差旅费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "差旅": {
        "accountCode": "5602006",
        "accountName": "管理费用-差旅费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "交通": {
        "accountCode": "5602006",
        "accountName": "管理费用-差旅费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "交通费": {
        "accountCode": "5602006",
        "accountName": "管理费用-差旅费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "办公费": {
        "accountCode": "5602004",
        "accountName": "管理费用-办公费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "办公": {
        "accountCode": "5602004",
        "accountName": "管理费用-办公费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "咨询费": {
        "accountCode": "5602011",
        "accountName": "管理费用-咨询费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "咨询": {
        "accountCode": "5602011",
        "accountName": "管理费用-咨询费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "顾问费": {
        "accountCode": "5602011",
        "accountName": "管理费用-咨询费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "业务招待费": {
        "accountCode": "5602002",
        "accountName": "管理费用-业务招待费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "业务招待": {
        "accountCode": "5602002",
        "accountName": "管理费用-业务招待费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "餐饮": {
        "accountCode": "5602002",
        "accountName": "管理费用-业务招待费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "餐饮费": {
        "accountCode": "5602002",
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
    "无形资产": {
        "accountCode": "1701",
        "accountName": "无形资产",
        "accountType": "资产",
        "defaultCounterparty": "1002"
    },
    "人力成本": {
        "accountCode": "5401001",
        "accountName": "主营业务成本-人力",
        "accountType": "损益-成本",
        "defaultCounterparty": "1002"
    },
    # 仅用于识别；银行页会把工资/社保公积金流水拦回工资模块核销，避免重复列支。
    "工资发放": {
        "accountCode": "221101",
        "accountName": "应付职工薪酬-工资",
        "accountType": "负债",
        "defaultCounterparty": "1002"
    },
    "社保公积金": {
        "accountCode": "221102",
        "accountName": "应付职工薪酬-单位社保",
        "accountType": "负债",
        "defaultCounterparty": "1002"
    },
    "员工福利": {
        "accountCode": "5602014",
        "accountName": "管理费用-职工福利费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "员工福利费": {
        "accountCode": "5602014",
        "accountName": "管理费用-职工福利费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "租金物业": {
        "accountCode": "5602013",
        "accountName": "管理费用-租赁费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "房租物业": {
        "accountCode": "5602013",
        "accountName": "管理费用-租赁费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "信息技术服务费": {
        "accountCode": "5602015",
        "accountName": "管理费用-信息技术服务费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "运费及物流": {
        "accountCode": "5601011",
        "accountName": "销售费用-运输费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "水电通讯费": {
        "accountCode": "5602005",
        "accountName": "管理费用-水电费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "通讯费": {
        "accountCode": "5602005",
        "accountName": "管理费用-水电费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "银行及金融手续费": {
        "accountCode": "5603002",
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
        # 2221 已容器化，税金类流水一律落到末级「应交增值税（小规模纳税人专用）」，
        # 与 vat_service.VAT_PAYABLE_ACCOUNT / classifier / ocr 提示词保持同一口径
        "accountCode": "2221026",
        "accountName": "应交增值税（小规模纳税人专用）",
        "accountType": "负债",
        "defaultCounterparty": "1002"
    },
    "推广费": {
        "accountCode": "5601016",
        "accountName": "销售费用-业务宣传费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "其他支出": {
        "accountCode": "5602999",
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
    "供应商付款": {
        "accountCode": "2202",
        "accountName": "应付账款",
        "accountType": "负债",
        "defaultCounterparty": "1002"
    },
    "营业外支出": {
        "accountCode": "5711",
        "accountName": "营业外支出",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "所得税费用": {
        "accountCode": "5801",
        "accountName": "所得税费用",
        "accountType": "损益-费用",
        "defaultCounterparty": "2221006"  # 应交所得税
    },

    # ── 资本与往来 ──
    "股东投入": {
        "accountCode": "3001",
        "accountName": "实收资本",
        "accountType": "权益",
        "defaultCounterparty": "1002"
    },
    "资本公积": {
        "accountCode": "3002",
        "accountName": "资本公积",
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
        "accountCode": "122102",
        "accountName": "其他应收款-股东",
        "accountType": "资产",
        "defaultCounterparty": "1002"
    },
    "其他应收款": {
        "accountCode": "122103",
        "accountName": "其他应收款-员工往来",
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
        # 中性待收户：非股东/员工的暂付款（平台验证款、供应商往来）不能挤占
        # 122103 员工往来；员工报销/备用金走「其他应收款」分类仍落 122103
        "accountCode": "122199",
        "accountName": "其他应收款-待收款项",
        "accountType": "资产",
        "defaultCounterparty": "1002"
    },
    "往来退款": {
        # 前期暂付的退回（如平台验证款退款），贷记冲减待收款项，不进损益
        "accountCode": "122199",
        "accountName": "其他应收款-待收款项",
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
    "微信支付结算": {
        "accountCode": "1012001",
        "accountName": "微信支付待结算",
        "accountType": "资产",
        "defaultCounterparty": "1002"
    },
    "支付宝结算": {
        "accountCode": "1012002",
        "accountName": "支付宝待结算",
        "accountType": "资产",
        "defaultCounterparty": "1002"
    },
    "平台提现结算": {
        "accountCode": "1012",
        "accountName": "其他货币资金",
        "accountType": "资产",
        "defaultCounterparty": "1002"
    },
}

# 银行/现金科目
COUNTERPARTY_ACCOUNTS = {
    "1002": {"accountName": "银行存款", "accountType": "资产"},
    "1012": {"accountName": "其他货币资金", "accountType": "资产"},
    "1012001": {"accountName": "微信支付待结算", "accountType": "资产"},
    "1012002": {"accountName": "支付宝待结算", "accountType": "资产"},
    "1012099": {"accountName": "其他平台待结算", "accountType": "资产"},
    "1001": {"accountName": "库存现金", "accountType": "资产"},
    "1122": {"accountName": "应收账款", "accountType": "资产"},
    "1123": {"accountName": "预付账款", "accountType": "资产"},
    "2202": {"accountName": "应付账款", "accountType": "负债"},
    "2203": {"accountName": "预收账款", "accountType": "负债"},
    "2241": {"accountName": "其他应付款", "accountType": "负债"},
    "122101": {"accountName": "其他应收款-代扣代缴社保", "accountType": "资产"},
    "122102": {"accountName": "其他应收款-股东", "accountType": "资产"},
    "122103": {"accountName": "其他应收款-员工往来", "accountType": "资产"},
}


from typing import Any, Optional

from core.money import ZERO, money
from services.vat_service import (
    VAT_PAYABLE_ACCOUNT,
    VAT_PAYABLE_ACCOUNT_NAME,
    is_vat_sales_entry,
    split_income_vat,
)

# 未知分类兜底：支出落"管理费用-其他"，收入必须落收入科目。
# 历史缺陷：收入分类没命中映射时会落到 5602999，把收入记成负的费用。
INCOME_FALLBACK_ACCOUNT = {
    "accountCode": "5001",
    "accountName": "主营业务收入",
    "accountType": "损益-收入",
    "defaultCounterparty": "1002",
}
EXPENSE_FALLBACK_ACCOUNT = {
    "accountCode": "5602999",
    "accountName": "管理费用-其他",
    "accountType": "损益-费用",
    "defaultCounterparty": "1002",
}

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


PAYER_COMPANY = "company_account"
PAYER_BOSS = "boss_personal"
EMPLOYEE_PAYER_PREFIX = "employee_personal"


def normalize_payer(payer: Any) -> str:
    """把各种写法的付款方归一到三种标准值。

    历史入口（早期的 agent 参数枚举）给过一个 ``personal``，下游所有分支
    都不认识它，于是垫付凭证会贷「银行存款」——老板自己掏的钱被记成公司
    银行支出。这里统一收口，顺带清掉 ``employee_personal: 张三`` 里的空格。

    未知值回退公司账户：宁可记成对公（流水可核对、可追溯改），也不要静默
    落进个人往来把负债凭空做大。
    """
    raw = str(payer or "").strip()
    if not raw or raw == PAYER_COMPANY:
        return PAYER_COMPANY
    if raw in (PAYER_BOSS, "personal", "股东个人", "老板个人", "老板垫付"):
        return PAYER_BOSS
    if raw in ("prepaid_deduct", "预存款", "预付扣款", "余额扣除"):
        return "prepaid_deduct"
    if raw in ("platform_balance", "平台余额", "待提现", "平台未提现"):
        return "platform_balance"
    if raw in ("advance_offset", "预收冲抵", "前期预收"):
        return "advance_offset"
    if raw in ("receivable", "accounts_receivable", "待到账", "挂应收", "应收账款", "待收款", "挂账"):
        return "receivable"
    if raw == EMPLOYEE_PAYER_PREFIX:
        return EMPLOYEE_PAYER_PREFIX
    if raw.startswith(f"{EMPLOYEE_PAYER_PREFIX}:"):
        name = raw.split(":", 1)[1].strip()
        return f"{EMPLOYEE_PAYER_PREFIX}:{name}" if name else EMPLOYEE_PAYER_PREFIX
    return PAYER_COMPANY


def get_voucher_hint(
    direction: str,
    category: str,
    amount: Any,
    payer: str = "company_account",
    *,
    vat_rate: Any = None,
    vat_amount: Any = None,
    payment_status: Any = None,
) -> dict:
    """
    生成借贷提示（凭证草稿原始数据）
    返回格式：{ debit: [...], credit: [...] }

    收入方向在传入 ``vat_rate`` / ``vat_amount`` 时做价税分离：
      借 银行存款（含税全额）
      贷 主营业务收入（不含税）
      贷 应交税费-应交增值税（小规模纳税人专用）

    未传入税额参数时保持旧行为（含税全额入收入），用于兼容历史数据与
    未升级调用方；新记账路径必须传税率。
    """
    amount = money(amount)
    payer = normalize_payer(payer)
    # 兼容口语化老数据
    if category == "老板投入":
        category = "股东投入"
    elif category == "老板垫付":
        category = "股东垫付"
    elif category == "老板垫付报销":
        category = "股东垫付报销"
    elif category == "老板取现":
        category = "老板取现"
    elif category == "往来款":
        # 支出方向保留「往来款」→ 122199 待收款项；收入方向按暂收落 2241
        if direction != "expense":
            category = "其他应付款"

    # 报销结算分类只用于付款冲销。若旧客户端误将其用于费用确认，
    # 至少落入通用费用科目，避免生成 2241 借贷互抵、费用漏记的凭证。
    if direction == "expense" and category in ("股东垫付报销", "员工垫付报销"):
        category = "其他支出"

    acct = CATEGORY_TO_ACCOUNT.get(category)
    if direction == "income":
        # 收入方向若未命中或命中了支出/费用类科目（如分类名写成了"咨询费"、"服务费"），纠正为收入科目。
        # 利息收入是例外：贷记财务费用-利息收入（5603005）冲减财务费用是合法处理，不得纠正。
        if acct is None or (
            category != "利息收入"
            and (acct.get("accountType") == "损益-费用" or str(acct.get("accountCode", "")).startswith(("54", "56", "66")))
        ):
            acct = INCOME_FALLBACK_ACCOUNT
    elif acct is None:
        acct = EXPENSE_FALLBACK_ACCOUNT
    acct_code = acct.get("accountCode", "5602999")
    acct_name = acct.get("accountName", "管理费用-其他")
    counterparty = acct.get("defaultCounterparty", "1002")

    payment_status_str = str(payment_status or "").strip().lower()

    # ponytail: 动态判定借贷对手科目（支持未付挂账、老板/员工垫付、预存扣减、平台待结算等）
    if payment_status_str == "unpaid":
        if direction == "expense":
            counterparty = "2202" if acct_code in ("1403", "1405") else "2241"
            payable_name = "应付账款" if counterparty == "2202" else "其他应付款-发票待付款"
        elif direction == "income":
            counterparty = "1122"
            payable_name = "应收账款"
        else:
            counterparty = "2241"
            payable_name = "其他应付款"
    elif payer == PAYER_BOSS:
        if direction == "expense":
            counterparty = "2241"
            payable_name = "其他应付款-股东"
        elif direction == "income":
            counterparty = "122102"
            payable_name = "其他应收款-股东"
        else:
            counterparty = "2241"
            payable_name = "其他应付款-股东"
    elif payer == EMPLOYEE_PAYER_PREFIX or payer.startswith(f"{EMPLOYEE_PAYER_PREFIX}:"):
        emp_name = payer.split(":", 1)[1].strip() if ":" in payer else ""
        if direction == "expense":
            counterparty = "2241"
            payable_name = f"其他应付款-员工往来({emp_name})" if emp_name else "其他应付款-员工往来"
        elif direction == "income":
            counterparty = "122103"
            payable_name = f"其他应收款-员工往来({emp_name})" if emp_name else "其他应收款-员工往来"
        else:
            counterparty = "2241"
            payable_name = f"其他应付款-员工往来({emp_name})" if emp_name else "其他应付款-员工往来"
    elif payer == "prepaid_deduct":
        if direction == "expense":
            counterparty = "1123"
            payable_name = "预付账款"
        else:
            counterparty = acct.get("defaultCounterparty", "1002")
            payable_name = COUNTERPARTY_ACCOUNTS.get(counterparty, {}).get("accountName", "银行存款")
    elif payer == "platform_balance":
        if direction == "income":
            counterparty = "1012099"
            payable_name = "其他平台待结算"
        else:
            counterparty = acct.get("defaultCounterparty", "1002")
            payable_name = COUNTERPARTY_ACCOUNTS.get(counterparty, {}).get("accountName", "银行存款")
    elif payer == "advance_offset":
        if direction == "income":
            counterparty = "2203"
            payable_name = "预收账款"
        else:
            counterparty = acct.get("defaultCounterparty", "1002")
            payable_name = COUNTERPARTY_ACCOUNTS.get(counterparty, {}).get("accountName", "银行存款")
    elif payer == "receivable":
        if direction == "income":
            counterparty = "1122"
            payable_name = "应收账款"
        else:
            counterparty = acct.get("defaultCounterparty", "1002")
            payable_name = COUNTERPARTY_ACCOUNTS.get(counterparty, {}).get("accountName", "银行存款")
    else:
        counterparty = acct.get("defaultCounterparty", "1002")
        payable_name = acct_name if acct_code == "2241" else "其他应付款-其他"

    # 资本往来（股东投入/取现/垫付报销）本身就是公户收付，去向只决定往来科目名称，对手方保持默认
    if direction == "owner_transfer":
        counterparty = acct.get("defaultCounterparty", "1002")

    cp_name = COUNTERPARTY_ACCOUNTS.get(counterparty, {}).get("accountName", payable_name)
    if counterparty in ("2241", "122102", "122103", "1123", "1012099", "2203", "1122"):
        cp_name = payable_name

    if direction == "income":
        # 借: 银行存款（含税全额）  贷: 主营业务收入（不含税）+ 应交增值税
        net_amount = amount
        tax_amount = ZERO
        if is_vat_sales_entry(category, acct_code) and (vat_rate not in (None, "") or vat_amount not in (None, "")):
            net_amount, tax_amount = split_income_vat(amount, vat_rate, vat_amount)
        credit_lines = [{"accountCode": acct_code, "accountName": acct_name, "amount": net_amount}]
        if tax_amount > ZERO:
            credit_lines.append({
                "accountCode": VAT_PAYABLE_ACCOUNT,
                "accountName": VAT_PAYABLE_ACCOUNT_NAME,
                "amount": tax_amount,
            })
        return {
            "debit": [{"accountCode": counterparty, "accountName": cp_name, "amount": amount}],
            "credit": credit_lines,
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
            # 借: 往来科目 (122102/122103/2241), 贷: 银行存款 (1002)
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
