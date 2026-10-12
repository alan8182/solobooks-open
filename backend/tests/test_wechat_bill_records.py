"""微信账单已导入记录查询接口测试。"""
from __future__ import annotations

from decimal import Decimal


def _import_rows() -> list[dict]:
    return [
        {
            "status": "SUCCESS",
            "date": "2026-07-04",
            "product_name": "积分充值",
            "order_amount": 19.00,
            "fee": 0.17,
            "order_no": "4200003125202607045579000001",
            "fee_rate": "0.90%",
        },
        {
            "status": "REFUND",
            "date": "2026-07-05",
            "product_name": "积分充值",
            "refund_amount": 10.00,
            "fee": 0.09,
            "order_no": "4200003125202607045579000002",
            "refund_order_no": "5030000125202607050001",
        },
    ]


def test_records_groups_imported_orders(client, auth_headers):
    res = client.post("/api/wechat-bill/import", json={"rows": _import_rows()}, headers=auth_headers)
    assert res.status_code == 200, res.text
    assert res.json()["imported"] == 2

    res = client.get("/api/wechat-bill/records", params={"month": "2026-07"}, headers=auth_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["month"] == "2026-07"

    items = {item["orderNo"]: item for item in body["items"]}
    assert len(items) == 2

    success = items["4200003125202607045579000001"]
    assert success["title"] == "积分充值"
    assert success["status"] == "SUCCESS"
    assert Decimal(str(success["orderAmount"])) == Decimal("19.00")
    assert Decimal(str(success["fee"])) == Decimal("0.17")
    assert Decimal(str(success["settledAmount"])) == Decimal("18.83")

    refund = items["4200003125202607045579000002"]
    assert refund["title"] == "积分充值"
    assert refund["status"] == "REFUND"
    assert Decimal(str(refund["orderAmount"])) == Decimal("-10.00")
    assert Decimal(str(refund["fee"])) == Decimal("-0.09")
    assert Decimal(str(refund["settledAmount"])) == Decimal("-9.91")

    summary = body["summary"]
    assert summary["total"] == 2
    assert Decimal(str(summary["totalOrderAmount"])) == Decimal("9.00")
    assert Decimal(str(summary["totalFee"])) == Decimal("0.08")
    assert Decimal(str(summary["totalSettled"])) == Decimal("8.92")


def test_records_rejects_bad_month(client, auth_headers):
    res = client.get("/api/wechat-bill/records", params={"month": "2026-7"}, headers=auth_headers)
    assert res.status_code == 422


def test_records_empty_month_returns_empty(client, auth_headers):
    res = client.get("/api/wechat-bill/records", params={"month": "2030-01"}, headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["items"] == []
    assert body["summary"]["total"] == 0
