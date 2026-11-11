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


def test_aging_excludes_reversal_pairs_api(client, auth_headers):
    """冲销对不进账龄：红字冲销凭证与被冲销原凭证相互抵消。

    回归的是「期初冲销重导后未收金额翻倍」：aging_service 原先按
    status.in_(("posted","reversed")) 取行，把已冲销原凭证和红字冲销凭证各按
    正数算了一次，未收总额凭空 ×2。口径同 closing_validation_service 的
    reversal_ids 处理：成对排除，不参与账龄。
    """
    from db.session import session_scope
    from services.accounting_core_service import create_entry, ensure_tenant, reverse_journal
    import asyncio

    company_id = "c-aging-reversal"

    async def _seed():
        async with session_scope() as session:
            await ensure_tenant(session, company_id, "owner-1")

    async def _post_and_reverse():
        async with session_scope() as session:
            entry = await create_entry(
                session,
                {
                    "id": "aging-rev-1",
                    "direction": "income",
                    "amount": "900.00",
                    "title": "应收测试",
                    "category": "主营业务收入",
                    "occurDate": "2026-10-01",
                    "source": "manual",
                    "voucherLines": {
                        "debit": [{"accountCode": "1122", "accountName": "应收账款", "amount": "900.00"}],
                        "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": "900.00"}],
                    },
                },
                company_id=company_id,
                user_id="owner-1",
                request_id="aging-rev-post",
                confirm=True,
            )
            await reverse_journal(
                session, entry.id, company_id=company_id, user_id="owner-1",
                request_id="aging-rev-reverse", reason="回归测试：冲销对不应进账龄",
            )

    asyncio.run(_seed())
    asyncio.run(_post_and_reverse())

    headers = dict(auth_headers)
    headers["X-Company-Id"] = company_id
    response = client.get("/api/report/ar-aging", params={"group_by": "partner", "as_of": "2026-12-31"}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == "0.00", f"冲销对应相互抵消，实际 {body['total']}"
    assert body["count"] == 0
