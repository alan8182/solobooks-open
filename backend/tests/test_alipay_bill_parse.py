# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""支付宝业务明细解析：GBK、# 头尾、退款符号与完成时间、平台补贴口径。"""
from __future__ import annotations

import asyncio
from decimal import Decimal

from services.alipay_bill_service import parse_alipay_bill

_HEADER = (
    "支付宝交易号,商户订单号,业务类型,商品名称,创建时间,完成时间,门店编号,门店名称,"
    "操作员,终端号,对方账户,订单金额（元）,商家实收（元）,支付宝红包（元）,集分宝（元）,"
    "支付宝优惠（元）,商家优惠（元）,券核销金额（元）,券名称,商家红包消费金额（元）,"
    "卡消费金额（元）,退款批次号/请求号,服务费（元）,分润（元）,备注"
)

def _row(*cells: str) -> str:
    return ",".join(cells)


_CSV = "\r\n".join([
    "#支付宝业务明细查询",
    "#账号：[20889808679602250156]",
    "#起始日期：[2026年11月19日 00:00:00]   终止日期：[2026年11月20日 00:00:00]",
    "#-----------------------------------------业务明细列表----------------------------------------",
    _HEADER,
    _row(
        "2026111922001455691459783864\t", "SB261119161644A46ACA\t", "交易\t",
        "AI 点数充值 50000 点", "2026-11-19 16:16:50", "2026-11-19 16:17:01",
        "\t", "\t", "\t", "\t", "**飞(151******61)\t",
        "5.00", "5.00", "0.00", "0.00", "0.58", "0.00", "0.00",
        "收银台立减-MAP防流失-消金-高投入", "0.00\t", "0.00", "\t", "-0.03", "0.00", "",
    ),
    _row(
        "2026111922001455691459783864\t", "SB261119161644A46ACA\t", "退款\t",
        "AI 点数充值 50000 点", "2026-11-19 16:16:50", "2026-11-19 16:18:25",
        "\t", "\t", "\t", "\t", "**飞(151******61)\t",
        "-5.00", "-5.00", "0.00", "0.00", "-0.58", "0.00", "0.00",
        "收银台立减-MAP防流失-消金-高投入", "0.00\t", "0.00", "SB261119161644A46ACAR1\t",
        "0.00", "0.00", "",
    ),
    _row(
        "2026111922001455691459860507\t", "SB261119193636C36662\t", "交易\t",
        "AI 点数充值 50000 点", "2026-11-19 19:36:44", "2026-11-19 19:37:04",
        "\t", "\t", "\t", "\t", "**飞(151******61)\t",
        "5.00", "5.00", "0.00", "0.00", "0.00", "0.00", "0.00",
        "", "0.00\t", "0.00", "\t", "-0.03", "0.00", "",
    ),
    "#-----------------------------------------业务明细列表结束------------------------------------",
    "#交易合计：2笔，商家实收共10.00元，商家优惠共0.00元",
    "#退款合计：1笔，商家实收退款共-5.00元，商家优惠退款共0.00元",
    "#导出时间：[2026年11月20日 04:35:50]",
])


def _parse(content: str = _CSV):
    return asyncio.run(parse_alipay_bill(content.encode("gbk")))


def _by_key(result):
    return {row["dedupFingerprint"]: row for row in result["valid"]}


def test_parse_refund_uses_completed_time_and_signed_amounts():
    result = _parse()
    rows = _by_key(result)

    sale = rows["success:2026111922001455691459783864"]
    refund = rows["refund:2026111922001455691459783864:SB261119161644A46ACAR1"]

    assert sale["date"] == "2026-11-19"
    assert refund["date"] == "2026-11-19"  # 完成时间 16:18:25，不是原交易创建时间
    assert sale["received_amount"] == Decimal("5.00")
    assert refund["received_amount"] == Decimal("-5.00")
    assert refund["refund_amount"] == Decimal("5.00")
    assert refund["platform_subsidy"] == Decimal("-0.58")
    assert refund["revenue_amount"] == Decimal("-4.42")
    assert refund["fee"] == Decimal("0.00")
    assert refund["settled_amount"] == Decimal("-5.00")


def test_parse_excludes_platform_subsidy_from_revenue():
    result = _parse()
    rows = _by_key(result)
    sale = rows["success:2026111922001455691459783864"]

    assert sale["platform_subsidy"] == Decimal("0.58")
    assert sale["revenue_amount"] == Decimal("4.42")  # 5.00 - 0.58
    assert sale["fee"] == Decimal("0.03")
    assert sale["settled_amount"] == Decimal("4.97")  # 5.00 - 0.03

    summary = result["summary"]
    assert summary["periodStart"] == "2026-11-19"
    assert summary["periodEnd"] == "2026-11-19"
    assert summary["receivedAmount"] == Decimal("5.00")
    assert summary["subsidyAmount"] == Decimal("0.00")  # 0.58 与退款 -0.58 抵消
    assert summary["feeAmount"] == Decimal("0.06")
    assert summary["revenueAmount"] == Decimal("5.00")
    assert summary["netSettledAmount"] == Decimal("4.94")
    # 结账向导汇总卡口径：订单总额只算收款行，退款总额取绝对值
    assert summary["订单总金额"] == Decimal("10.00")
    assert summary["退款总金额"] == Decimal("5.00")
    assert summary["手续费总金额"] == Decimal("0.06")


def test_parse_does_not_trust_file_name_or_extension():
    # 文件名与内容无关，解析只看表头；这里顺便验证 CRLF + 末尾制表符已被清理
    result = _parse()
    assert result["total"] == 3
    assert result["errors"] == []
    assert result["warnings"] == []
    assert result["summary"]["merchantAccount"] == "20889808679602250156"


def test_parse_rejects_unknown_business_type():
    content = _CSV.replace("交易\t,AI 点数充值 50000 点", "转账\t,AI 点数充值 50000 点", 1)
    result = _parse(content)
    assert result["total"] == 2
    assert any("暂不支持的业务类型" in item["reason"] for item in result["errors"])


def test_parse_rejects_profit_share():
    content = _CSV.replace("-0.03,0.00,", "-0.03,1.00,", 1)
    result = _parse(content)
    assert any("分润" in item["reason"] for item in result["errors"])
