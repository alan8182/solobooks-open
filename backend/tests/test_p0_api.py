# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""P0 新接口的接线冒烟：路由已注册、鉴权生效、响应形状与前端契约一致。

只测"接口能不能通、形状对不对"，业务口径由各 service 级测试覆盖
（test_invoice_ledger / test_auxiliary / test_legacy_import / test_backup_archive）。
"""
from __future__ import annotations


def test_partner_crud_api(client, auth_headers):
    created = client.post("/api/partners", json={"name": "接口测试客户", "type": "customer"}, headers=auth_headers)
    assert created.status_code == 200, created.text
    partner = created.json()
    assert partner["name"] == "接口测试客户"
    assert partner["type"] == "customer"

    listed = client.get("/api/partners", headers=auth_headers)
    assert listed.status_code == 200
    assert any(item["id"] == partner["id"] for item in listed.json()["items"])

    updated = client.put(
        f"/api/partners/{partner['id']}", json={"name": "接口测试客户（改名）"}, headers=auth_headers
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "接口测试客户（改名）"

    deleted = client.delete(f"/api/partners/{partner['id']}", headers=auth_headers)
    assert deleted.status_code == 200


def test_auxiliary_options_api(client, auth_headers):
    response = client.get("/api/auxiliary/options", params={"keyword": ""}, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert {"partners", "departments", "projects"} <= set(body)


def test_invoice_ledger_api(client, auth_headers):
    response = client.get("/api/invoices", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert {"items", "total", "page", "pageSize"} <= set(body)

    summary = client.get("/api/invoices/summary", headers=auth_headers)
    assert summary.status_code == 200
    assert "totals" in summary.json()


def test_legacy_import_parse_api(client, auth_headers):
    balance = client.post(
        "/api/legacy-import/balance/parse",
        json={"rows": [{"科目编码": "1002", "科目名称": "银行存款", "期初余额(借)": "100"}]},
        headers=auth_headers,
    )
    assert balance.status_code == 200, balance.text
    assert balance.json()["valid"][0]["accountCode"] == "1002"

    vouchers = client.post(
        "/api/legacy-import/vouchers/parse",
        json={
            "rows": [
                {"凭证号": "记-1", "日期": "2026-10-01", "摘要": "收款", "科目编码": "1002", "借方金额": "100"},
                {"凭证号": "记-1", "日期": "2026-10-01", "摘要": "收款", "科目编码": "3001", "贷方金额": "100"},
            ]
        },
        headers=auth_headers,
    )
    assert vouchers.status_code == 200, vouchers.text
    assert vouchers.json()["totals"]["vouchers"] == 1


def test_backup_records_api(client, auth_headers):
    response = client.get("/api/backup/records", headers=auth_headers)
    assert response.status_code == 200
    assert "items" in response.json()


def test_partner_balances_api(client, auth_headers):
    response = client.get(
        "/api/ledger/partner-balances", params={"account_code": "1122", "month": "2026-10"}, headers=auth_headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert {"items", "totalDebit", "totalCredit", "accountCode"} <= set(body)


def test_aging_group_by_partner_api(client, auth_headers):
    response = client.get("/api/report/ar-aging", params={"group_by": "partner"}, headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["group_by"] == "partner"
    assert "groups" in body
