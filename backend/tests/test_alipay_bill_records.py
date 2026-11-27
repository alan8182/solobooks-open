# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""平台账单统一入口：自动识别支付宝、逐笔导入与已导入记录聚合。"""
from __future__ import annotations

from decimal import Decimal

_HEADER = (
    "支付宝交易号,商户订单号,业务类型,商品名称,创建时间,完成时间,门店编号,门店名称,"
    "操作员,终端号,对方账户,订单金额（元）,商家实收（元）,支付宝红包（元）,集分宝（元）,"
    "支付宝优惠（元）,商家优惠（元）,券核销金额（元）,券名称,商家红包消费金额（元）,"
    "卡消费金额（元）,退款批次号/请求号,服务费（元）,分润（元）,备注"
)


def _row(*cells: str) -> str:
    return ",".join(cells)


def _alipay_csv(trade_no: str, refund_ref: str) -> str:
    return "\r\n".join([
        "#支付宝业务明细查询",
        "#账号：[20889808679602250156]",
        "#起始日期：[2026年11月19日 00:00:00]   终止日期：[2026年11月20日 00:00:00]",
        _HEADER,
        _row(
            trade_no, "SB261119161644A46ACA", "交易",
            "AI 点数充值 50000 点", "2026-11-19 16:16:50", "2026-11-19 16:17:01",
            "", "", "", "", "**飞(151******61)", "5.00", "5.00", "0.00", "0.00", "0.58",
            "0.00", "0.00", "活动", "0.00", "0.00", "", "-0.03", "0.00", "",
        ),
        _row(
            trade_no, "SB261119161644A46ACA", "退款",
            "AI 点数充值 50000 点", "2026-11-19 16:16:50", "2026-11-19 16:18:25",
            "", "", "", "", "**飞(151******61)", "-5.00", "-5.00", "0.00", "0.00", "-0.58",
            "0.00", "0.00", "活动", "0.00", "0.00", refund_ref, "0.00", "0.00", "",
        ),
        "#交易合计：1笔，商家实收共5.00元，商家优惠共0.00元",
        "#退款合计：1笔，商家实收退款共-5.00元，商家优惠退款共0.00元",
    ])


_ALIPAY_CSV = _alipay_csv("2026111922001455691459783864", "SB261119161644A46ACAR1")

_WECHAT_CSV = "\n".join([
    "交易时间,微信订单号,商户订单号,交易状态,商品名称,订单金额,应结订单金额,退款金额,费率,手续费",
    "`2026-09-06 10:00:00,`4200003125202609060001,`m-0001,`SUCCESS,`积分充值,`5.00,`5.00,`0.00,`0.90%,`0.05",
    "总交易单数,1,应结订单总金额,5.00,退款总金额,0.00,手续费总金额,0.05",
])


def _import_alipay(client, auth_headers, content: str = _ALIPAY_CSV):
    res = client.post(
        "/api/platform-bill/parse",
        files={"file": ("支付宝11月19日业务明细.csv", content.encode("gbk"), "text/csv")},
        headers=auth_headers,
    )
    assert res.status_code == 200, res.text
    parsed = res.json()
    assert parsed["platform"] == "alipay"
    assert len(parsed["valid"]) == 2
    return parsed


def test_parse_detects_alipay_from_gbk_content(client, auth_headers):
    parsed = _import_alipay(client, auth_headers)
    assert parsed["summary"]["periodStart"] == "2026-11-19"
    # 这份夹具只有一收一退：实收净额 0，服务费 0.03，净结算 -0.03
    assert Decimal(str(parsed["summary"]["净结算合计"])) == Decimal("-0.03")


def test_parse_detects_wechat_from_utf8_content(client, auth_headers):
    res = client.post(
        "/api/platform-bill/parse",
        files={"file": ("微信账单.csv", _WECHAT_CSV.encode("utf-8-sig"), "text/csv")},
        headers=auth_headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["platform"] == "wechat"


def test_detail_import_and_records_group_by_trade_no(client, auth_headers):
    parsed = _import_alipay(client, auth_headers)

    res = client.post("/api/platform-bill/import", json={
        "platform": "alipay",
        "importMode": "detail",
        "rows": parsed["valid"],
    }, headers=auth_headers)
    assert res.status_code == 200, res.text
    assert res.json()["imported"] == 2

    res = client.get(
        "/api/platform-bill/records",
        params={"month": "2026-11", "platform": "alipay"},
        headers=auth_headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["total"] == 1
    item = body["items"][0]
    assert item["orderNo"] == "2026111922001455691459783864"
    assert item["status"] == "REFUND"
    assert Decimal(str(item["orderAmount"])) == Decimal("0.00")
    assert Decimal(str(item["fee"])) == Decimal("0.03")
    assert Decimal(str(item["settledAmount"])) == Decimal("-0.03")

    # 不传 platform 时合并展示，并带平台标记
    merged = client.get(
        "/api/platform-bill/records",
        params={"month": "2026-11"},
        headers=auth_headers,
    ).json()
    assert merged["items"][0]["platform"] == "alipay"


def test_reimport_same_rows_is_idempotent(client, auth_headers):
    parsed = _import_alipay(
        client, auth_headers,
        _alipay_csv("2026111922001455691459900001", "SB261119161644A46AR99"),
    )
    payload = {"platform": "alipay", "importMode": "detail", "rows": parsed["valid"]}
    first = client.post("/api/platform-bill/import", json=payload, headers=auth_headers).json()
    second = client.post("/api/platform-bill/import", json=payload, headers=auth_headers).json()

    assert first["imported"] == 2
    assert second["imported"] == 0
    assert second["ignored_duplicates"] == 2


def test_unknown_platform_is_rejected(client, auth_headers):
    res = client.post("/api/platform-bill/import", json={
        "platform": "paypal",
        "importMode": "summary",
        "summary": {},
    }, headers=auth_headers)
    assert res.status_code == 422
