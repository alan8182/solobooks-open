# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""微信账单解析测试：到账净额口径。"""
from __future__ import annotations

import asyncio
from decimal import Decimal

from services.wechat_bill_service import parse_wechat_bill

_CSV = "\n".join([
    "交易时间,微信订单号,商户订单号,交易状态,商品名称,订单金额,应结订单金额,退款金额,费率,手续费",
    "`2026-09-06 10:00:00,`4200003125202609060001,`m-0001,`SUCCESS,`签小抄积分充值-5积分,`5.00,`5.00,`0.00,`0.90%,`0.05",
    "`2026-09-16 12:00:00,`4200003125202609160002,`m-0002,`SUCCESS,`签证钱包 - Pro 会员 (月度),`8.00,`8.00,`0.00,`0.90%,`0.07",
    "`2026-09-16 15:00:00,`4200003125202609160003,`m-0002,`REFUND,`签证钱包 - Pro 会员 (月度),`-8.00,`0.00,`8.00,`0.90%,`-0.07",
    "总交易单数,3,应结订单总金额,5.00,退款总金额,8.00,手续费总金额,0.05",
])


def test_parse_settled_amount_deducts_fee():
    """到账净额按系统口径计算：收款=订单-手续费，退款=-(退款-手续费)。

    微信账单的"应结订单金额"只扣免充值券、不扣手续费（退款行恒为 0.00），
    不能直接透出。
    """
    result = asyncio.run(parse_wechat_bill(_CSV.encode("utf-8-sig")))
    rows = {r["order_no"]: r for r in result["valid"]}

    assert rows["4200003125202609060001"]["settled_amount"] == Decimal("4.95")
    assert rows["4200003125202609160002"]["settled_amount"] == Decimal("7.93")

    refund = rows["4200003125202609160003"]
    assert refund["status"] == "REFUND"
    assert refund["settled_amount"] == Decimal("-7.93")
