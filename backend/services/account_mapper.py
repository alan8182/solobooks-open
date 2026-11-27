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
    "非流动资产处置净收益": {
        "accountCode": "5301001",
        "accountName": "营业外收入-非流动资产处置净收益",
        "accountType": "损益-收入",
        "defaultCounterparty": "1002"
    },
    "政府补助": {
        "accountCode": "5301002",
        "accountName": "营业外收入-政府补助",
        "accountType": "损益-收入",
        "defaultCounterparty": "1002"
    },
    "捐赠收益": {
        "accountCode": "5301003",
        "accountName": "营业外收入-捐赠收益",
        "accountType": "损益-收入",
        "defaultCounterparty": "1002"
    },
    "盘盈收益": {
        "accountCode": "5301004",
        "accountName": "营业外收入-盘盈收益",
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
    # 代扣代缴员工个税：走工资模块按流水核销 2221012，银行页直接记费用会与
    # 企业所得税费用（5801）混淆——个税是被代扣员工的钱，不是公司费用。
    "个税代缴": {
        "accountCode": "2221012",
        "accountName": "应交税费-应交个人所得税",
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
    "罚款支出": {
        "accountCode": "5711011",
        "accountName": "营业外支出-罚款支出",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "税收滞纳金": {
        "accountCode": "5711010",
        "accountName": "营业外支出-税收滞纳金",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "捐赠支出": {
        "accountCode": "5711003",
        "accountName": "营业外支出-捐赠支出",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "赞助支出": {
        "accountCode": "5711002",
        "accountName": "营业外支出-赞助支出",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "非流动资产处置净损失": {
        "accountCode": "5711001",
        "accountName": "营业外支出-非流动资产处置净损失",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "盘亏损失": {
        "accountCode": "5711004",
        "accountName": "营业外支出-盘亏损失",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "坏账损失": {
        "accountCode": "5711005",
        "accountName": "营业外支出-坏账损失",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "存货毁损报废损失": {
        "accountCode": "5711006",
        "accountName": "营业外支出-存货毁损报废损失",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "长期债券投资损失": {
        "accountCode": "5711007",
        "accountName": "营业外支出-无法收回的长期债券投资损失",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "长期股权投资损失": {
        "accountCode": "5711008",
        "accountName": "营业外支出-无法收回的长期股权投资损失",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "自然灾害损失": {
        "accountCode": "5711009",
        "accountName": "营业外支出-自然灾害等不可抗力因素造成的损失",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "广告费": {
        "accountCode": "5601015",
        "accountName": "销售费用-广告费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "展览费": {
        "accountCode": "5601009",
        "accountName": "销售费用-展览费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "商品维修费": {
        "accountCode": "5601010",
        "accountName": "销售费用-商品维修费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "装卸费": {
        "accountCode": "5601012",
        "accountName": "销售费用-装卸费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "包装费": {
        "accountCode": "5601013",
        "accountName": "销售费用-包装费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "保险费": {
        "accountCode": "5601014",
        "accountName": "销售费用-保险费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "修理费": {
        "accountCode": "5602003",
        "accountName": "管理费用-修理费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "开办费": {
        "accountCode": "5602009",
        "accountName": "管理费用-开办费",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "研究费用": {
        "accountCode": "5602010",
        "accountName": "管理费用-研究费用",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "利息费用": {
        "accountCode": "5603001",
        "accountName": "财务费用-利息费用",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "汇兑损益": {
        "accountCode": "5603003",
        "accountName": "财务费用-汇兑损益",
        "accountType": "损益-费用",
        "defaultCounterparty": "1002"
    },
    "现金折扣": {
        "accountCode": "5603004",
        "accountName": "财务费用-现金折扣",
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
        "accountName": "其他应付款",
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
        "accountName": "其他应付款",
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
        "accountName": "其他应付款",
        "accountType": "负债",
        "defaultCounterparty": "1002"
    },
    "员工垫付报销": {
        "accountCode": "2241",
        "accountName": "其他应付款",
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

# 口语/兼容别名：支持查表和前端兜底映射，但不重复写入 AI Prompt 与标准下拉列表
CATEGORY_ALIASES: set[str] = {
    "咨询服务",
    "技术服务",
    "信息技术服务",
    "信息技术服务收入",
    "差旅",
    "交通",
    "交通费",
    "办公",
    "咨询",
    "顾问费",
    "业务招待",
    "餐饮",
    "餐饮费",
    "员工福利",
    "房租物业",
    "通讯费",
    "设备采购",
}

# 内部自动核销/结算分类：由对账或工资模块专用，不放入通用记账/发票 OCR Prompt 候选
INTERNAL_SETTLEMENT_CATEGORIES: set[str] = {
    "微信支付结算",
    "支付宝结算",
    "平台提现结算",
    "股东垫付报销",
    "员工垫付报销",
}

GROUP_ORDER: tuple[str, ...] = (
    "收入",
    "员工成本",
    "费用支出",
    "采购与往来",
    "资本",
    "往来",
    "销售与财务费用",
    "营业外支出",
    "营业外收入",
)

CATEGORY_GROUPS: dict[str, str] = {
    # 收入
    "主营业务收入": "收入",
    "咨询服务收入": "收入",
    "技术服务收入": "收入",
    "其他业务收入": "收入",
    "营业外收入": "收入",
    "利息收入": "收入",
    # 营业外收入（明细）
    "政府补助": "营业外收入",
    "非流动资产处置净收益": "营业外收入",
    "捐赠收益": "营业外收入",
    "盘盈收益": "营业外收入",
    # 员工成本
    "工资发放": "员工成本",
    "社保公积金": "员工成本",
    "个税代缴": "员工成本",
    # 费用支出（日常管理与经营）
    "差旅费": "费用支出",
    "办公费": "费用支出",
    "业务招待费": "费用支出",
    "研发支出-费用化支出": "费用支出",
    "信息技术服务费": "费用支出",
    "咨询费": "费用支出",
    "租金物业": "费用支出",
    "银行及金融手续费": "费用支出",
    "水电通讯费": "费用支出",
    "推广费": "费用支出",
    "运费及物流": "费用支出",
    "员工福利费": "费用支出",
    "修理费": "费用支出",
    "开办费": "费用支出",
    "研究费用": "费用支出",
    "其他支出": "费用支出",
    # 销售与财务费用（细分）
    "广告费": "销售与财务费用",
    "展览费": "销售与财务费用",
    "商品维修费": "销售与财务费用",
    "装卸费": "销售与财务费用",
    "包装费": "销售与财务费用",
    "保险费": "销售与财务费用",
    "利息费用": "销售与财务费用",
    "汇兑损益": "销售与财务费用",
    "现金折扣": "销售与财务费用",
    # 营业外支出
    "罚款支出": "营业外支出",
    "税收滞纳金": "营业外支出",
    "捐赠支出": "营业外支出",
    "赞助支出": "营业外支出",
    "非流动资产处置净损失": "营业外支出",
    "盘亏损失": "营业外支出",
    "坏账损失": "营业外支出",
    "存货毁损报废损失": "营业外支出",
    "长期债券投资损失": "营业外支出",
    "长期股权投资损失": "营业外支出",
    "自然灾害损失": "营业外支出",
    "营业外支出": "营业外支出",
    # 采购与往来
    "原材料采购": "采购与往来",
    "商品采购": "采购与往来",
    "生产成本": "采购与往来",
    "人力成本": "采购与往来",
    "固定资产": "采购与往来",
    "无形资产": "采购与往来",
    "供应商付款": "采购与往来",
    "预付账款": "采购与往来",
    "其他应收款": "采购与往来",
    "其他应付款": "采购与往来",
    "往来款": "采购与往来",
    "往来退款": "采购与往来",
    "税费缴纳": "采购与往来",
    "所得税费用": "采购与往来",
    # 资本与股东往来
    "股东投入": "资本",
    "资本公积": "资本",
    "股东垫付": "往来",
    "股东取现": "往来",
    "股东垫付报销": "往来",
    "员工垫付报销": "往来",
    "微信支付结算": "收入",
    "支付宝结算": "收入",
    "平台提现结算": "收入",
}

_CATEGORY_DIRECTION_OVERRIDES: dict[str, str] = {
    "主营业务收入": "income",
    "咨询服务收入": "income",
    "技术服务收入": "income",
    "其他业务收入": "income",
    "营业外收入": "income",
    "非流动资产处置净收益": "income",
    "政府补助": "income",
    "捐赠收益": "income",
    "盘盈收益": "income",
    "利息收入": "income",
    "往来退款": "income",
    "股东投入": "owner_transfer",
    "资本公积": "owner_transfer",
    "股东垫付": "owner_transfer",
    "股东取现": "owner_transfer",
    "其他应收款": "owner_transfer",
    "其他应付款": "owner_transfer",
    "往来款": "owner_transfer",
    "股东垫付报销": "owner_transfer",
    "员工垫付报销": "owner_transfer",
    "微信支付结算": "owner_transfer",
    "支付宝结算": "owner_transfer",
    "平台提现结算": "owner_transfer",
}

CATEGORY_PROMPT_HINTS: dict[str, str] = {
    "工资发放": "必须走工资模块按流水核销，不得直接记费用",
    "社保公积金": "已计提时走工资模块缴纳",
    "个税代缴": "代扣代缴员工个税，走工资模块按流水核销应交个人所得税，不得记所得税费用",
    "税费缴纳": "小规模纳税人正常税款缴纳，含增值税/附加税等；不含罚款滞纳金",
    "罚款支出": "税务罚款/逾期申报罚款/行政处罚等，企业所得税不得税前扣除",
    "税收滞纳金": "逾期缴税产生的滞纳金，企业所得税不得税前扣除",
    "政府补助": "政府补贴/稳岗返还/财政奖励/个税手续费返还等",
    "广告费": "广告投放/平台投流宣传",
    "研究费用": "日常非立项研究支出；正式立项研发项目请选「研发支出-费用化支出」",
    "利息费用": "银行借款或贷款利息支出",
    "汇兑损益": "外币结算或结汇汇率差",
    "往来款": "支出记其他应收款-待收款项、收入记其他应付款",
    "往来退款": "前期暂付款的退回，冲减往来，不算收入",
}

_OCR_VAT_RATE_BY_CATEGORY: dict[str, str] = {
    "原材料采购": "13%",
    "商品采购": "13%",
    "生产成本": "13%",
    "固定资产": "13%",
    "运费及物流": "9%",
    "人力成本": "0%",
    "员工福利费": "0%",
    "税费缴纳": "0%",
    "所得税费用": "0%",
    "营业外支出": "0%",
    "罚款支出": "0%",
    "税收滞纳金": "0%",
    "捐赠支出": "0%",
    "赞助支出": "0%",
    "非流动资产处置净损失": "0%",
    "盘亏损失": "0%",
    "坏账损失": "0%",
    "存货毁损报废损失": "0%",
    "长期债券投资损失": "0%",
    "长期股权投资损失": "0%",
    "自然灾害损失": "0%",
    "利息费用": "0%",
    "汇兑损益": "0%",
    "现金折扣": "0%",
}

_OCR_EXCLUDED_CATEGORIES: set[str] = {
    "工资发放",
    "社保公积金",
    "个税代缴",
    "往来款",
    "往来退款",
    "其他应收款",
    "其他应付款",
    "供应商付款",
    "预付账款",
}


def category_direction(category: str) -> str:
    """规范分类的标准方向（income | expense | owner_transfer）。"""
    return _CATEGORY_DIRECTION_OVERRIDES.get(category, "expense")


def category_group(category: str) -> str:
    """规范分类所属语义分组，未登记时回落费用支出。"""
    return CATEGORY_GROUPS.get(category, "费用支出")


def is_alias_category(category: str) -> bool:
    """是否为仅作口语/历史兼容的别名。"""
    return category in CATEGORY_ALIASES


def render_prompt_category_table(scene: str = "bank") -> str:
    """从 CATEGORY_TO_ACCOUNT 单一真相源生成各 AI 场景使用的 Markdown 映射表。"""
    canonical = [
        (cat, info)
        for cat, info in CATEGORY_TO_ACCOUNT.items()
        if not is_alias_category(cat) and cat not in INTERNAL_SETTLEMENT_CATEGORIES
    ]
    if scene == "ocr":
        lines = [
            "| 分类 | 科目代码 | 科目名称 | VAT税率 |",
            "|---|---|---|---|",
        ]
        for cat, info in canonical:
            if category_direction(cat) != "expense" or cat in _OCR_EXCLUDED_CATEGORIES:
                continue
            vat_rate = _OCR_VAT_RATE_BY_CATEGORY.get(cat, "6%")
            hint = CATEGORY_PROMPT_HINTS.get(cat)
            name_cell = f"{info['accountName']}（{hint}）" if hint else info["accountName"]
            lines.append(f"| {cat} | {info['accountCode']} | {name_cell} | {vat_rate} |")
        return "\n".join(lines)

    if scene in ("classifier", "batch"):
        allowed = [
            (cat, info)
            for cat, info in canonical
            if cat not in {"工资发放", "社保公积金", "个税代缴", "往来退款"}
        ]
        if scene == "classifier":
            lines = [
                "| 用户分类 | 方向 | 会计科目代码 | 会计科目名称 | 科目类型 |",
                "|---|---|---|---|---|",
            ]
            for cat, info in allowed:
                direction = category_direction(cat)
                hint = CATEGORY_PROMPT_HINTS.get(cat)
                name_cell = f"{info['accountName']}（{hint}）" if hint else info["accountName"]
                lines.append(
                    f"| {cat} | {direction} | {info['accountCode']} | {name_cell} | {info['accountType']} |"
                )
            return "\n".join(lines)
        lines = [
            "| 用户分类 | 方向 | 会计科目代码 | 会计科目名称 |",
            "|---|---|---|---|",
        ]
        for cat, info in allowed:
            direction = category_direction(cat)
            lines.append(
                f"| {cat} | {direction} | {info['accountCode']} | {info['accountName']} |"
            )
        return "\n".join(lines)

    # scene == "bank"
    lines = [
        "| 用户分类 | 方向 | 会计科目代码 | 会计科目名称 |",
        "|---|---|---|---|",
    ]
    for cat, info in canonical:
        direction = "expense/income" if cat == "往来款" else category_direction(cat)
        hint = CATEGORY_PROMPT_HINTS.get(cat)
        name_cell = f"{info['accountName']}（{hint}）" if hint else info["accountName"]
        lines.append(
            f"| {cat} | {direction} | {info['accountCode']} | {name_cell} |"
        )
    return "\n".join(lines)


def lookup(category: str) -> Optional[dict]:
    """根据用户分类查找科目"""
    # 兼容口语化老数据
    if category == "老板投入":
        category = "股东投入"
    elif category == "老板垫付" or category == "老板垫付报销":
        category = "股东垫付"
    elif category == "老板取现":
        category = "股东取现"
    elif category == "税费支出":
        category = "税费缴纳"
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
    elif category == "税费支出":
        category = "税费缴纳"
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
            payable_name = "应付账款" if counterparty == "2202" else "其他应付款"
        elif direction == "income":
            counterparty = "1122"
            payable_name = "应收账款"
        else:
            counterparty = "2241"
            payable_name = "其他应付款"
    elif payer == PAYER_BOSS:
        if direction == "expense":
            counterparty = "2241"
            payable_name = "其他应付款"
        elif direction == "income":
            counterparty = "122102"
            payable_name = "其他应收款-股东"
        else:
            counterparty = "2241"
            payable_name = "其他应付款"
    elif payer == EMPLOYEE_PAYER_PREFIX or payer.startswith(f"{EMPLOYEE_PAYER_PREFIX}:"):
        # 姓名不再拼进科目名（科目名只取科目表），员工身份落在行辅助核算上
        if direction == "expense":
            counterparty = "2241"
            payable_name = "其他应付款"
        elif direction == "income":
            counterparty = "122103"
            payable_name = "其他应收款-员工往来"
        else:
            counterparty = "2241"
            payable_name = "其他应付款"
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
        payable_name = acct_name if acct_code == "2241" else "其他应付款"

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
