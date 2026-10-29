# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""API integration tests using FastAPI TestClient.

Uses the test-token bypass mechanism (no JWT required).
Pure computational endpoints only — no LLM-dependent endpoints tested.
"""
import json
import sqlite3
import uuid
from decimal import Decimal
from pathlib import Path

import pytest


def _update_company_payload(company_id: str, updates: dict, *, remove: tuple[str, ...] = ()) -> None:
    """同步测试内直接改公司 payload（CIT 完整度只看 restrictedIndustry）。"""
    import asyncio

    async def _run() -> None:
        from db.models import Company
        from db.session import session_scope

        async with session_scope() as session:
            row = await session.get(Company, company_id)
            assert row is not None, f"company {company_id} not found"
            payload = dict(row.payload or {})
            for key in remove:
                payload.pop(key, None)
            payload.update(updates)
            row.payload = payload

    asyncio.run(_run())


class TestHealth:
    def test_health_returns_ok(self, client):
        """Health endpoint requires no auth."""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "SoloBooks"

    def test_health_no_auth_required(self, client):
        """Health should work without Authorization header."""
        resp = client.get("/api/health", headers={})
        assert resp.status_code == 200


class TestAuthoritativeAccountingApi:
    month = "2031-06"
    next_month = "2031-07"

    @staticmethod
    def _row_value(data, key, column):
        row = next(item for item in data["rows"] if item["key"] == key)
        return row["values"][column]

    def test_protected_endpoint_without_auth(self, client):
        assert client.post("/api/report/balance-sheet", json={"month": self.month}).status_code == 401

    def test_reports_reject_client_entries(self, client, auth_headers):
        response = client.post(
            "/api/report/balance-sheet",
            json={"month": self.month, "entries": []},
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_empty_period_uses_money_strings(self, client, auth_headers):
        response = client.post(
            "/api/report/balance-sheet",
            json={"month": "2099-12"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["totalAssets"] == "0.00"

    def test_posted_journals_are_the_only_report_source(self, client, auth_headers):
        entries = [
            ("core-api-income-2031", "income", "10000.00", "主营业务收入", "服务收入", "2031-06-01"),
            ("core-api-office-2031", "expense", "3000.00", "办公费", "办公用品", "2031-06-05"),
            ("core-api-meal-2031", "expense", "500.00", "业务招待费", "客户餐饮", "2031-06-10"),
        ]
        created_ids = []
        for entry_id, direction, amount, category, title, occurred_on in entries:
            payload = {
                "id": entry_id,
                "direction": direction,
                "amount": amount,
                "category": category,
                "title": title,
                "occurDate": occurred_on,
                "sourceType": "integration_test",
                "dedupFingerprint": entry_id,
            }
            response = client.post(
                "/api/record/confirm",
                json=payload,
                headers={**auth_headers, "Idempotency-Key": f"confirm:{entry_id}"},
            )
            assert response.status_code == 200, response.text
            assert response.json()["voucherStatus"] == "posted"
            assert response.json()["entry"]["amount"] == amount
            created_ids.append(response.json()["id"])

        # Replaying an idempotent request returns the original result and creates no duplicate.
        replay = client.post(
            "/api/record/confirm",
            json={
                "id": entries[0][0], "direction": entries[0][1], "amount": entries[0][2],
                "category": entries[0][3], "title": entries[0][4], "occurDate": entries[0][5],
                "sourceType": "integration_test", "dedupFingerprint": entries[0][0],
            },
            headers={**auth_headers, "Idempotency-Key": f"confirm:{entries[0][0]}"},
        )
        assert replay.status_code == 200
        assert replay.json()["id"] == created_ids[0]
        reused = client.post(
            "/api/record/confirm",
            json={
                "id": "different-request", "direction": "income", "amount": "1.00",
                "category": "主营业务收入", "title": "不同请求", "occurDate": "2031-06-01",
            },
            headers={**auth_headers, "Idempotency-Key": f"confirm:{entries[0][0]}"},
        )
        assert reused.status_code == 409
        assert reused.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"

        income = client.post(
            "/api/report/income-statement",
            json={"month": self.month, "viewMode": "summary"},
            headers=auth_headers,
        ).json()
        # 10000 含税收入按 1% 价税分离：收入 9900.99、应交增值税 99.01（未关账前仍为负债）
        assert self._row_value(income, "operating_revenue", "current") == "9900.99"
        assert self._row_value(income, "admin_expenses", "current") == "3500.00"
        assert self._row_value(income, "net_profit", "current") == "6400.99"

        balance = client.post(
            "/api/report/balance-sheet", json={"month": self.month}, headers=auth_headers
        ).json()
        assert balance["isBalanced"] is True
        assert balance["totalAssets"] == "6500.00"
        assert balance["totalLiabilities"] == "99.01"
        assert balance["totalEquity"] == "6400.99"

        cash = client.post(
            "/api/report/cash-flow", json={"month": self.month}, headers=auth_headers
        ).json()
        assert cash["operatingNet"] == "6500.00"
        assert cash["openingCash"] == "0.00"
        assert cash["endingCash"] == "6500.00"
        assert cash["classificationBalanced"] is True
        assert cash["cashRollForwardBalanced"] is True
        assert cash["balanceSheetCashBalanced"] is True

        # 从业人数改为名册派生：清空历史字段，仅保留"限制行业未确认"的初始态
        _update_company_payload("c1", {}, remove=("restrictedIndustry", "employeeCount"))
        cit = client.post(
            "/api/report/cit-assist",
            json={"period": "2031-Q2", "periodType": "quarter"},
            headers=auth_headers,
        )
        assert cit.status_code == 200
        cit_data = cit.json()
        assert cit_data["period"] == "2031-Q2"
        # 办公费 3000 + 招待费 500 均无票据信息，按无合规扣除凭证全额调增：
        # 实际利润额 = 6400.99 + 3500 = 9900.99
        assert cit_data["autoAdjustmentTotal"] == "3500.00"
        assert cit_data["cumulativeRevenue"] == "9900.99"
        assert cit_data["cumulativeProfit"] == "6400.99"
        assert cit_data["smallMicroEligibility"] == "undetermined"
        # 公司制名册为空按 1 人兜底（法定代表人口径）
        assert cit_data["employeeAverage"] == "1.00"
        assert cit_data["companyTaxProfileComplete"] is False
        assert cit_data["standardTax"] == "2475.25"
        assert cit_data["estimatedTax"] == "2475.25"
        assert cit_data["readyToFile"] is False
        assert cit_data["assetTotalStart"] == "0.00"
        assert cit_data["assetTotalEnd"] == "0.65"
        assert cit_data["operatingCostConfirmed"] is False
        assert cit_data["totalProfitConfirmed"] is False
        assert cit_data["assetLoss"] is None
        assert cit_data["beginnerReady"] is False

        # 确认非限制行业后即可判定；请求体里的人数不再覆盖名册派生值
        _update_company_payload("c1", {"restrictedIndustry": False})
        eligible_cit = client.post(
            "/api/report/cit-assist",
            json={
                "period": "2031-Q2", "periodType": "quarter",
                "employeeCountStart": 999, "employeeCountEnd": 999,
                "assetTotalStart": "1.00", "assetTotalEnd": "1.00",
                "restrictedIndustry": True, "manualAdjustmentsConfirmed": True,
            },
            headers=auth_headers,
        ).json()
        assert eligible_cit["employeeAverage"] == "1.00"
        assert eligible_cit["smallMicroEligibility"] == "eligible"
        assert eligible_cit["taxReduction"] == "1980.20"
        assert eligible_cit["estimatedTax"] == "495.05"
        assert eligible_cit["readyToFile"] is True

        trial = client.post(
            "/api/ledger/trial-balance", json={"month": self.month}, headers=auth_headers
        ).json()
        assert trial["isBalanced"] is True
        assert len(trial["items"]) > 0

        general = client.get(
            "/api/ledger/general",
            params={"account_code": "1002", "month": self.month},
            headers=auth_headers,
        ).json()
        assert general["items"][-1]["balance"] == "6500.00"
        summary = client.get(
            "/api/ledger/balance-summary",
            params={"month": self.month},
            headers=auth_headers,
        ).json()
        assert summary["isBalanced"] is True
        assert summary["totalDebit"] == summary["totalCredit"]
        bank_row = next(item for item in summary["items"] if item["accountCode"] == "1002")
        # 首月无期初，期末 = 期初 + 本期净额；1002 本期净额 6500.00
        assert bank_row["openingBalance"] == "0.00"
        assert bank_row["closingBalance"] == "6500.00"
        # 次月查询：期初结转上月期末
        next_month = client.get(
            "/api/ledger/balance-summary",
            params={"month": self.next_month},
            headers=auth_headers,
        ).json()
        next_bank = next(item for item in next_month["items"] if item["accountCode"] == "1002")
        assert next_bank["openingBalance"] == "6500.00"
        assert next_bank["closingBalance"] == next_bank["openingBalance"]

        vouchers = client.post(
            "/api/ledger/voucher", json={"entryIds": created_ids}, headers=auth_headers
        )
        assert vouchers.status_code == 200
        assert len(vouchers.json()["vouchers"]) == 3
        assert vouchers.json()["vouchers"][0]["debitEntries"][0]["amount"] == "10000.00"

        immutable = client.patch(
            f"/api/record/entries/{created_ids[0]}", json={"title": "篡改"}, headers=auth_headers
        )
        assert immutable.status_code == 409
        assert immutable.json()["detail"]["code"] == "ENTRY_POSTED_IMMUTABLE"

    def test_empty_tax_and_trial_reports(self, client, auth_headers):
        vat = client.post(
            "/api/report/vat-assist",
            json={"month": "2099-12", "taxpayerType": "small_scale"},
            headers=auth_headers,
        ).json()
        assert vat["totalVatPayable"] == "0.00"
        trial = client.post(
            "/api/ledger/trial-balance", json={"month": "2099-12"}, headers=auth_headers
        ).json()
        # 试算平衡与科目余额表统一实现后，空月列出全部启用科目但全为 0 行
        assert trial["items"], "统一口径后应返回完整科目表 0 行"
        assert all(row["debitTotal"] == "0.00" and row["creditTotal"] == "0.00" for row in trial["items"])
        assert trial["isBalanced"] is True

    def test_bank_import_is_idempotent_and_blocks_close_until_reconciled(self, client, auth_headers):
        payload = {
            "statement_id": "api-bank-source-2032",
            "rows": [{
                "transaction_date": "2032-07-08",
                "description": "待核销银行流水",
                "amount": "12.34",
                "balance": "100.00",
            }],
        }
        headers = {**auth_headers, "Idempotency-Key": "bank-import:api-bank-source-2032"}
        imported = client.post("/api/bank/import-json", json=payload, headers=headers)
        assert imported.status_code == 200, imported.text
        assert imported.json()["imported"] == 1
        replay = client.post("/api/bank/import-json", json=payload, headers=headers)
        assert replay.status_code == 200
        assert replay.json() == imported.json()

        checks = client.get("/api/periods/2032-07/close-checks", headers=auth_headers)
        assert checks.status_code == 200, checks.text
        actual = next(item for item in checks.json()["checks"] if item["code"] == "BOOK_TO_ACTUAL")
        assert actual["status"] == "blocking"
        assert actual["unreconciledBankCount"] >= 1


    def test_wechat_settlement_http_confirm_updates_original_entries_without_new_income(self, client, auth_headers):
        imported = client.post('/api/wechat-bill/import', json={"rows": [{
            "status": "SUCCESS", "date": "2034-07-14", "order_no": "wx-http-settlement",
            "product_name": "微信核销测试", "order_amount": 5, "fee": 0.05,
        }]}, headers=auth_headers)
        assert imported.status_code == 200, imported.text
        assert imported.json()["imported"] == 1  # 导入结果按订单计数，订单包含收入和手续费两笔账目。
        entries_before = client.get('/api/record/entries', headers=auth_headers).json()["entries"]
        originals = [entry for entry in entries_before if entry.get("wechat_order_no") == "wx-http-settlement"]
        ids = {entry["id"] for entry in originals}
        assert len(ids) == 2
        bank = client.post('/api/bank/import-json', json={"statement_id": "wx-http-batch", "rows": [{
            "transaction_date": "2034-07-15", "description": "财付通支付科技有限公司", "amount": 4.95,
        }]}, headers=auth_headers)
        assert bank.status_code == 200, bank.text
        statement = client.get('/api/bank/statements?statement_id=wx-http-batch', headers=auth_headers).json()["items"][0]
        blocked = client.post('/api/bank/confirm-suggestion', json={
            "statement_id": statement["id"], "type": "new", "new_entry": {
                "title": "银行重复收入", "amount": 4.95, "direction": "income",
                "category": "主营业务收入", "occurDate": "2034-07-15",
            },
        }, headers=auth_headers)
        assert blocked.status_code == 409, blocked.text
        matched = client.post('/api/bank/confirm-suggestion', json={
            "statement_id": statement["id"], "type": "match", "matched_entry_ids": sorted(ids),
        }, headers=auth_headers)
        assert matched.status_code == 200, matched.text
        entries_after = client.get('/api/record/entries', headers=auth_headers).json()["entries"]
        assert len(entries_after) == len(entries_before) + 1
        settlement_id = matched.json()["settlementEntryId"]
        settlement = next(entry for entry in entries_after if entry["id"] == settlement_id)
        assert settlement["voucherStatus"] == "draft"
        assert settlement["direction"] == "owner_transfer"
        assert set(settlement["originalEntryIds"]) == ids
        assert settlement["occurDate"] == "2034-07-15"
        for entry in entries_after:
            if entry["id"] in ids:
                assert entry["billingStatus"] == "reconciled"
                assert entry["paymentDate"] == "2034-07-15"
        statement_after = client.get('/api/bank/statements?statement_id=wx-http-batch', headers=auth_headers).json()["items"][0]
        assert set(statement_after["matched_entry_ids"]) == ids | {settlement_id}
        repeat = client.post('/api/bank/confirm-suggestion', json={
            "statement_id": statement["id"], "type": "match", "matched_entry_ids": sorted(ids),
        }, headers=auth_headers)
        assert repeat.status_code == 200 and not repeat.json()["settlementCreated"]
        difference = client.get('/api/bank/difference?month=2034-07', headers=auth_headers).json()
        assert not difference["balance_reconciliation"]["fully_reconciled"]
        assert any(issue["type"] == "unposted_match" for issue in difference["matched_effect_issues"])


class TestInvoice:
    def test_invoice_parse_direction_detection(self, client, auth_headers):
        rows = [
            {
                "发票号码": "1111",
                "开票日期": "2026-05-15",
                "销售方": "杭州惠保信网络科技有限公司",
                "购买方": "上海豹云网络信息服务有限公司",
                "价税合计": "100.0",
            },
            {
                "发票号码": "2222",
                "开票日期": "2026-05-15",
                "销售方": "成都彩虹电器（集团）股份有限公司",
                "购买方": "杭州惠保信网络科技有限公司",
                "价税合计": "200.0",
            },
            {
                "发票号码": "3333",
                "开票日期": "2026-05-15",
                "销售方": "杭州市拱墅区同泡大餐饮店",
                "购买方": "杭州惠保信网络科技有限公司",
                "价税合计": "300.0",
            },
        ]
        resp = client.post(
            "/api/invoice/parse",
            json={"rows": rows},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid_count"] == 3
        valid = data["valid"]
        
        # Row 0: Seller is 杭州惠保信 -> income
        assert valid[0]["direction"] == "income"
        assert valid[0]["seller_name"] == "杭州惠保信网络科技有限公司"
        
        # Row 1: Buyer is 杭州惠保信 -> expense
        assert valid[1]["direction"] == "expense"
        assert valid[1]["seller_name"] == "成都彩虹电器（集团）股份有限公司"
        
        # Row 2: Buyer is 杭州惠保信 -> expense
        assert valid[2]["direction"] == "expense"
        assert valid[2]["seller_name"] == "杭州市拱墅区同泡大餐饮店"

    @pytest.mark.asyncio
    async def test_invoice_import_sales_row_without_category_normalizes_to_income(self, monkeypatch):
        """销项票缺类别（结账向导兜底前的原始形态）不应导致导入 500。

        历史缺陷：向导以 classify=False 导入时，无类别行被兜底成支出类别"其他支出"，
        收入方向配出贷方支出科目 5602999，被收支校验拒绝后整批 500。
        """
        from services import invoice_service

        saved_entries = []

        async def fake_get_company_name(company_id, rows):
            return ""

        async def fake_save_entry(entry, *, user_id, company_id, **kwargs):
            saved_entries.append(entry)
            return {**entry, "id": "entry-1"}

        monkeypatch.setattr(invoice_service, "get_real_company_name", fake_get_company_name)
        monkeypatch.setattr(invoice_service, "save_entry", fake_save_entry)

        result = await invoice_service.import_invoices(
            "c1",
            "u1",
            [{
                "invoice_number": "sales-no-category-1",
                "invoice_date": "2026-09-15",
                "seller_name": "杭州南山科技有限公司",
                "buyer_name": "测试客户有限公司",
                "amount": 11300,
                "direction": "income",
                "category": "其他支出",
            }],
            classify=False,
        )

        assert result["imported"] == 1
        entry = saved_entries[0]
        assert entry["direction"] == "income"
        assert entry["category"] == "主营业务收入"
        credit_codes = [line["accountCode"] for line in entry["voucherLines"]["credit"]]
        assert "5001" in credit_codes  # 贷方必须落在收入科目

    @pytest.mark.asyncio
    async def test_invoice_import_without_classify_preserves_preview_values(self, monkeypatch):
        from services import invoice_service

        saved_entries = []

        async def fake_get_company_name(company_id, rows):
            return ""

        async def fake_save_entry(entry, *, user_id, company_id, **kwargs):
            saved_entries.append(entry)
            return {**entry, "id": "entry-1"}

        async def fail_classify(*args, **kwargs):
            raise AssertionError("classify_invoice should not be called when classify=False")

        monkeypatch.setattr(invoice_service, "get_real_company_name", fake_get_company_name)
        monkeypatch.setattr(invoice_service, "save_entry", fake_save_entry)
        monkeypatch.setattr(invoice_service, "classify_invoice", fail_classify)

        result = await invoice_service.import_invoices(
            "c1",
            "u1",
            [{
                "invoice_number": "manual-direction-1",
                "invoice_date": "2026-05-15",
                "seller_name": "测试客户有限公司",
                "buyer_name": "本公司",
                "amount": 100,
                "direction": "income",
                "category": "主营业务收入",
            }],
            classify=False,
        )

        assert result["imported"] == 1
        assert saved_entries[0]["direction"] == "income"
        assert saved_entries[0]["category"] == "主营业务收入"

    @pytest.mark.asyncio
    async def test_invoice_import_income_creates_receivable_not_cash(self, monkeypatch):
        """收入发票是凭证不是收款：借方必须是 1122 应收，而不是 1002 银行存款。

        历史缺陷：发票服务构造的 1122/2241 被 entry_service 的确定性重算静默覆盖，
        导致发票导入时直接减少银行存款，与后续流水配对后重复支出。
        """
        from services import invoice_service

        saved_entries = []

        async def fake_get_company_name(company_id, rows):
            return ""

        async def fake_save_entry(entry, *, user_id, company_id, **kwargs):
            saved_entries.append((entry, kwargs))
            return {**entry, "id": "entry-1"}

        monkeypatch.setattr(invoice_service, "get_real_company_name", fake_get_company_name)
        monkeypatch.setattr(invoice_service, "save_entry", fake_save_entry)

        result = await invoice_service.import_invoices(
            "c1",
            "u1",
            [{
                "invoice_number": "income-invoice-1",
                "invoice_date": "2026-05-15",
                "seller_name": "本公司",
                "buyer_name": "测试客户有限公司",
                "amount": 1010,
                "direction": "income",
                "category": "主营业务收入",
            }],
            classify=False,
        )

        assert result["imported"] == 1
        entry, kwargs = saved_entries[0]
        voucher = entry["voucherLines"]
        assert voucher["debit"][0]["accountCode"] == "1122"
        credits = {line["accountCode"]: line["amount"] for line in voucher["credit"]}
        assert credits["5001"] == 1000
        assert credits["2221026"] == 10
        assert kwargs.get("trusted_voucher") is True

    @pytest.mark.asyncio
    async def test_invoice_import_expense_creates_payable_not_cash(self, monkeypatch):
        from services import invoice_service

        saved_entries = []

        async def fake_get_company_name(company_id, rows):
            return ""

        async def fake_save_entry(entry, *, user_id, company_id, **kwargs):
            saved_entries.append(entry)
            return {**entry, "id": "entry-1"}

        monkeypatch.setattr(invoice_service, "get_real_company_name", fake_get_company_name)
        monkeypatch.setattr(invoice_service, "save_entry", fake_save_entry)

        result = await invoice_service.import_invoices(
            "c1",
            "u1",
            [{
                "invoice_number": "expense-invoice-1",
                "invoice_date": "2026-05-15",
                "seller_name": "成都彩虹电器（集团）股份有限公司",
                "buyer_name": "本公司",
                "amount": 3000,
                "direction": "expense",
                "category": "办公费",
            }],
            classify=False,
        )

        assert result["imported"] == 1
        voucher = saved_entries[0]["voucherLines"]
        assert voucher["debit"][0]["accountCode"] == "5602004"
        assert voucher["credit"][0]["accountCode"] == "2241"

    @pytest.mark.asyncio
    async def test_classify_entry_invoice_endpoint(self, client, auth_headers, monkeypatch):
        from services import entry_service, invoice_service
        
        fake_entry = {
            "id": "test-entry-for-ai",
            "direction": "expense",
            "amount": 200,
            "category": "其他支出",
            "title": "腾讯云服务器年付",
            "occurDate": "2026-06-10",
            "buyer_name": "本公司",
            "seller_name": "腾讯云计算（北京）有限责任公司",
            "item_name": "信息技术服务*云服务器",
            "tax_amount": 12,
            "tax_rate": "6%",
        }
        
        async def fake_get_entry(entry_id, user_id, company_id):
            if entry_id == "test-entry-for-ai":
                class MockEntry:
                    def __init__(self, d):
                        self.d = d
                    def get(self, k, default=None):
                        return self.d.get(k, default)
                    def __getattr__(self, name):
                        return self.d.get(name)
                return MockEntry(fake_entry)
            return None
            
        async def fake_classify_invoice(row, company_name, *, user_id="", company_id=""):
            return {
                "direction": "expense",
                "category": "信息技术服务费",
                "reason": "发票名目包含云服务器，属于信息技术服务范畴",
                "confidence": 0.95
            }
            
        async def fake_get_company_name(company_id, rows):
            return "本公司"
            
        monkeypatch.setattr(entry_service, "get_entry", fake_get_entry)
        monkeypatch.setattr(invoice_service, "classify_invoice", fake_classify_invoice)
        monkeypatch.setattr(invoice_service, "get_real_company_name", fake_get_company_name)
        
        response = client.post("/api/invoice/classify-entry/test-entry-for-ai", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["category"] == "信息技术服务费"
        assert "云服务器" in data["reason"]

    @pytest.mark.asyncio
    async def test_classify_entry_invoice_endpoint_with_context(self, client, auth_headers, monkeypatch):
        from services import entry_service, invoice_service
        
        fake_entry = {
            "id": "test-entry-for-ai-ctx",
            "direction": "expense",
            "amount": 200,
            "category": "其他支出",
            "title": "购买服务器",
            "occurDate": "2026-06-10",
        }
        
        captured_row = []

        async def fake_get_entry(entry_id, user_id, company_id):
            class MockEntry:
                def __init__(self, d):
                    self.d = d
                def get(self, k, default=None):
                    return self.d.get(k, default)
                def __getattr__(self, name):
                    return self.d.get(name)
            return MockEntry(fake_entry)
            
        async def fake_classify_invoice(row, company_name, *, user_id="", company_id=""):
            captured_row.append(row)
            return {
                "direction": "expense",
                "category": "研发支出-费用化支出",
                "reason": f"用户补充说明为研发: {row.get('business_context')}",
                "confidence": 0.90
            }
            
        async def fake_get_company_name(company_id, rows):
            return "本公司"
            
        monkeypatch.setattr(entry_service, "get_entry", fake_get_entry)
        monkeypatch.setattr(invoice_service, "classify_invoice", fake_classify_invoice)
        monkeypatch.setattr(invoice_service, "get_real_company_name", fake_get_company_name)
        
        response = client.post(
            "/api/invoice/classify-entry/test-entry-for-ai-ctx",
            json={"business_context": "用于AI项目研发开发服务"},
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["category"] == "研发支出-费用化支出"
        assert "用于AI项目研发" in data["reason"]
        assert captured_row[0]["business_context"] == "用于AI项目研发开发服务"


class TestParseVatRate:
    def test_parse_vat_rate(self):
        from services.invoice_service import parse_vat_rate
        assert parse_vat_rate("3%") == Decimal("0.030000")
        assert parse_vat_rate("1.0%") == Decimal("0.010000")
        assert parse_vat_rate("13%普通发票") == Decimal("0.130000")
        assert parse_vat_rate("0.03") == Decimal("0.030000")
        assert parse_vat_rate(0.01) == Decimal("0.010000")
        assert parse_vat_rate(3) == Decimal("0.030000")
        assert parse_vat_rate("免税") == Decimal("0.000000")
        assert parse_vat_rate(None) == Decimal("0.000000")



class TestBankReconciliation:
    @pytest.fixture
    async def temp_bank_db(self, monkeypatch):
        import aiosqlite
        from services import bank_service, database, entry_service

        await entry_service._ensure_schema()

        # _ensure_schema 的进程级 once 标记指向的是旧库；每个测试是全新内存库，必须重置
        bank_service._schema_ready = False

        uri = f"file:bank-test-{uuid.uuid4().hex}?mode=memory&cache=shared"
        keeper = await aiosqlite.connect(uri, uri=True)
        keeper.row_factory = aiosqlite.Row

        async def get_test_conn():
            conn = await aiosqlite.connect(uri, uri=True)
            conn.row_factory = aiosqlite.Row
            return conn

        monkeypatch.setattr(database, "get_conn", get_test_conn)
        monkeypatch.setattr(bank_service, "get_conn", get_test_conn)
        await keeper.execute(
            "CREATE TABLE entries ("
            "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, company_id TEXT NOT NULL, "
            "payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        await keeper.commit()
        yield uri
        await keeper.close()

    async def _insert_entry(self, company_id, payload):
        from services import database
        conn = await database.get_conn()
        try:
            await conn.execute(
                "INSERT INTO entries (id, user_id, company_id, payload, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    payload["id"],
                    "u-bank-test",
                    company_id,
                    json.dumps(payload, ensure_ascii=False),
                    "2026-06-01T00:00:00+00:00",
                    "2026-06-01T00:00:00+00:00",
                ),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def _statement_row(self, statement_id):
        from services import database

        conn = await database.get_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM bank_statements WHERE statement_id = ? LIMIT 1",
                (statement_id,),
            )
            return await cursor.fetchone()
        finally:
            await conn.close()

    async def _entry_payload(self, entry_id):
        from services import database

        conn = await database.get_conn()
        try:
            cursor = await conn.execute("SELECT payload FROM entries WHERE id = ?", (entry_id,))
            row = await cursor.fetchone()
            if row is not None:
                return json.loads(row["payload"])
        finally:
            await conn.close()

        # New records are authoritative journals; the legacy in-memory table is
        # only a fixture for pre-migration rows.
        from db.models import JournalEntry
        from db.session import session_scope
        from services.accounting_core_service import serialize_journal

        async with session_scope() as session:
            journal = await session.get(JournalEntry, entry_id)
            assert journal is not None
            return await serialize_journal(session, journal)

    @pytest.mark.asyncio
    async def test_match_suggestions_do_not_reconcile_until_confirmed(self, temp_bank_db):
        from deps.auth import AuthContext
        from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
        from services.bank_service import auto_match, import_statements_json

        company_id = "c-bank-test"
        entry_id = "entry-bank-match-1"
        await self._insert_entry(company_id, {
            "id": entry_id,
            "direction": "expense",
            "amount": 500,
            "category": "信息技术服务费",
            "title": "技术服务费",
            "occurDate": "2026-06-10",
            "payer": "company_account",
            "billingStatus": "has_invoice",
        })
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-11",
            "description": "技术服务费付款",
            "amount": -500,
            "balance": 9500,
        }], "bank-batch-1")

        stmt = await self._statement_row("bank-batch-1")
        assert stmt["matched_entry_id"] is None

        result = await auto_match(company_id)
        assert result["matched"] == 0
        assert result["suggested"] == 1
        assert result["suggestions"][0]["matchedEntryId"] == entry_id

        stmt_after_suggest = await self._statement_row("bank-batch-1")
        assert stmt_after_suggest["matched_entry_id"] is None
        assert (await self._entry_payload(entry_id))["billingStatus"] == "has_invoice"

        await confirm_suggestion(
            ConfirmSuggestionRequest(
                statement_id=stmt_after_suggest["id"],
                type="match",
                matched_entry_id=entry_id,
            ),
            AuthContext(user_id="u-bank-test", company_id=company_id),
        )

        stmt_after_confirm = await self._statement_row("bank-batch-1")
        assert stmt_after_confirm["matched_entry_id"] == entry_id
        confirmed_payload = await self._entry_payload(entry_id)
        assert confirmed_payload["billingStatus"] == "reconciled"
        assert confirmed_payload["occurDate"] == "2026-06-10"
        assert confirmed_payload["paymentDate"] == "2026-06-11"

    @pytest.mark.asyncio
    async def test_cross_month_match_uses_bank_date_for_bank_balance(self, temp_bank_db):
        from deps.auth import AuthContext
        from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
        from services.bank_service import get_difference_report, import_statements_json

        company_id = "c-bank-cross-month"
        entry_id = "entry-cross-month"
        await self._insert_entry(company_id, {
            "id": entry_id,
            "direction": "expense",
            "amount": 500,
            "category": "研发支出-费用化支出",
            "title": "5月付款、6月开票",
            "occurDate": "2026-06-02",
            "payer": "company_account",
            "billingStatus": "has_invoice",
        })
        await import_statements_json(company_id, [{
            "transaction_date": "2026-05-31",
            "description": "研发服务付款",
            "amount": -500,
            "balance": 9500,
        }], "bank-batch-cross-month")
        stmt = await self._statement_row("bank-batch-cross-month")

        await confirm_suggestion(
            ConfirmSuggestionRequest(
                statement_id=stmt["id"],
                type="match",
                matched_entry_id=entry_id,
            ),
            AuthContext(user_id="u-bank-test", company_id=company_id),
        )

        report = await get_difference_report(company_id, "2026-05")
        assert report["balance_reconciliation"]["book_bank_balance"] == -500
        payload = await self._entry_payload(entry_id)
        assert payload["occurDate"] == "2026-06-02"
        assert payload["paymentDate"] == "2026-05-31"

    @pytest.mark.asyncio
    async def test_business_context_can_override_technical_service_to_r_and_d(self, temp_bank_db):
        from services.bank_service import classify_single_statement, import_statements_json

        company_id = "c-bank-context"
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-12",
            "description": "技术服务费",
            "amount": -1200,
            "balance": 8300,
        }], "bank-batch-rd")
        stmt = await self._statement_row("bank-batch-rd")

        result = await classify_single_statement(company_id, stmt["id"], "这笔技术服务费用于研发项目")

        assert result["category"] == "研发支出-费用化支出"
        assert result["accountCode"] == "4301"
        assert "研发" in result["reason"]

    @pytest.mark.asyncio
    async def test_ai_suggest_corrects_invalid_fallback_direction_for_income_statement(self, temp_bank_db, monkeypatch):
        from services import llm
        from services.bank_service import ai_suggest_matches, import_statements_json

        company_id = "c-bank-ai-fallback"
        await self._insert_entry(company_id, {
            "id": "entry-unrelated",
            "direction": "income",
            "amount": 999,
            "category": "??????",
            "occurDate": "2026-06-01",
            "title": "unrelated income",
            "billingStatus": "has_invoice",
        })
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-04",
            # 描述不能含「微信/财付通」等关键词，否则会被 wechat_withdrawal
            # 快速规则拦截，走不到下面要测的 AI 方向校正路径
            "description": "客户注册服务退款",
            "amount": 0.74,
            "balance": 100.74,
        }], "bank-batch-positive-fallback")

        def fake_safe_chat_text_json(*args, **kwargs):
            return {
                "direction": "expense",
                "category": "其他支出",
                "accountCode": "5602999",
                "accountName": "管理费用-其他",
                "title": "wrong expense suggestion",
                "confidence": 0.95,
                "reason": "wrong direction",
            }

        monkeypatch.setattr(llm, "safe_chat_text_json", fake_safe_chat_text_json)

        suggestions = await ai_suggest_matches(company_id)

        assert suggestions[0]["type"] == "new"
        assert suggestions[0]["suggestedEntry"]["direction"] == "income"
        assert suggestions[0]["suggestedEntry"]["accountCode"] == "5051"

    @pytest.mark.asyncio
    async def test_classify_single_statement_infers_missing_direction_and_keeps_category(
        self, temp_bank_db, monkeypatch
    ):
        from services import llm
        from services.bank_service import classify_single_statement, import_statements_json

        company_id = "c-bank-classify-direction"
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-12",
            "description": "转入-技术服务预付款",
            "amount": 2400,
            "balance": 2400,
        }], "bank-batch-classify-direction")
        stmt = await self._statement_row("bank-batch-classify-direction")

        # 模型只给了分类、没有 direction，不应该整条退化成兜底草稿
        monkeypatch.setattr(llm, "safe_chat_text_json", lambda *args, **kwargs: {
            "category": "其他业务收入",
            "accountCode": "5051",
            "accountName": "其他业务收入",
            "title": "技术服务预付款",
            "confidence": "0.9",
            "reason": "预收服务款",
        })

        result = await classify_single_statement(company_id, stmt["id"], "")

        assert result["direction"] == "income"
        assert result["category"] == "其他业务收入"
        assert result["confidence"] == 0.8
        assert "推断" in result["reason"]

    @pytest.mark.asyncio
    async def test_ai_suggest_runs_llm_match_for_decimal_amounts(self, temp_bank_db, monkeypatch):
        """金额阈值用 Decimal 计算：混入 float 会 TypeError，整条建议静默退化成兜底。"""
        from services import llm
        from services.bank_service import ai_suggest_matches, import_statements_json

        company_id = "c-bank-decimal-gap"
        await self._insert_entry(company_id, {
            "id": "entry-decimal",
            "direction": "expense",
            "amount": 2380,
            "category": "信息技术服务费",
            "occurDate": "2026-05-01",
            "title": "云服务费",
            "billingStatus": "has_invoice",
        })
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-12",
            "description": "技术服务预付款",
            "amount": -2400,
            "balance": 100,
        }], "bank-batch-decimal")

        monkeypatch.setattr(llm, "safe_chat_text_json", lambda *args, **kwargs: {
            "matchedEntryId": "entry-decimal", "confidence": 0.9, "reason": "金额接近",
        })

        suggestions = await ai_suggest_matches(company_id)

        assert suggestions[0]["type"] == "difference"
        assert suggestions[0]["matchedEntryId"] == "entry-decimal"
        assert suggestions[0]["reason"] == "金额接近"

    @pytest.mark.asyncio
    async def test_confirm_new_owner_transfer_income_is_idempotent(self, temp_bank_db):
        from deps.auth import AuthContext
        from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
        from services import database
        from services.bank_service import import_statements_json

        company_id = "c-bank-owner-transfer"
        await import_statements_json(company_id, [{
            "transaction_date": "2026-05-22",
            "description": "股东实缴出资款",
            "amount": 20000,
            "balance": 20000,
        }], "bank-batch-owner-transfer")
        stmt = await self._statement_row("bank-batch-owner-transfer")

        request = ConfirmSuggestionRequest(
            statement_id=stmt["id"],
            type="new",
            new_entry={
                "title": "股东实缴出资款",
                "amount": 20000,
                "direction": "owner_transfer",
                "category": "股东投入",
                "accountCode": "3001",
                "accountName": "实收资本",
                "occurDate": "2026-05-22",
                "remark": "何静谷 实缴注册资本",
            },
        )
        ctx = AuthContext(user_id="u-bank-test", company_id=company_id)

        result = await confirm_suggestion(request, ctx)
        assert result["status"] == "created"
        entry_id = result["entryId"]

        stmt_after_confirm = await self._statement_row("bank-batch-owner-transfer")
        assert stmt_after_confirm["matched_entry_id"] == entry_id
        payload = await self._entry_payload(entry_id)
        assert payload["direction"] == "owner_transfer"
        assert payload["billingStatus"] == "reconciled"

        retry_result = await confirm_suggestion(request, ctx)
        assert retry_result == {"status": "already_matched", "entryId": entry_id}

        from services.entry_service import list_entries
        assert len(await list_entries("u-bank-test", company_id)) == 1



    @pytest.mark.asyncio
    async def test_confirm_new_interest_income_ignores_stale_frontend_voucher(self, temp_bank_db):
        from deps.auth import AuthContext
        from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
        from services.bank_service import import_statements_json

        company_id = "c-bank-interest-income"
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-21",
            "description": "银行结息",
            "amount": 4.15,
            "balance": 1004.15,
        }], "bank-batch-interest-income")
        stmt = await self._statement_row("bank-batch-interest-income")

        result = await confirm_suggestion(
            ConfirmSuggestionRequest(
                statement_id=stmt["id"],
                type="new",
                new_entry={
                    "title": "银行结息",
                    "amount": 4.15,
                    "direction": "income",
                    "category": "利息收入",
                    "accountCode": "5603005",
                    "accountName": "财务费用-利息收入",
                    "occurDate": "2026-06-21",
                    "voucherLines": {
                        "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": 4.15}],
                        "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": 4.15}],
                    },
                },
            ),
            AuthContext(user_id="u-bank-test", company_id=company_id),
        )

        assert result["status"] == "created"
        payload = await self._entry_payload(result["entryId"])
        assert payload["category"] == "利息收入"
        assert payload["voucherLines"]["debit"][0]["accountCode"] == "1002"
        assert payload["voucherLines"]["credit"][0]["accountCode"] == "5603005"
        assert payload["voucherLines"]["credit"][0]["accountName"] == "财务费用-利息收入"

    @pytest.mark.asyncio
    async def test_confirm_match_rejects_amount_mismatch_without_writing(self, temp_bank_db):
        from deps.auth import AuthContext
        from fastapi import HTTPException
        from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
        from services.bank_service import import_statements_json

        company_id = "c-bank-amount-mismatch"
        entry_id = "entry-bank-mismatch"
        await self._insert_entry(company_id, {
            "id": entry_id,
            "direction": "expense",
            "amount": 480,
            "category": "信息技术服务费",
            "title": "技术服务费",
            "occurDate": "2026-06-10",
            "payer": "company_account",
            "billingStatus": "has_invoice",
        })
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-11",
            "description": "技术服务费付款",
            "amount": -500,
            "balance": 9500,
        }], "bank-batch-mismatch")
        stmt = await self._statement_row("bank-batch-mismatch")

        with pytest.raises(HTTPException) as exc_info:
            await confirm_suggestion(
                ConfirmSuggestionRequest(
                    statement_id=stmt["id"],
                    type="match",
                    matched_entry_id=entry_id,
                ),
                AuthContext(user_id="u-bank-test", company_id=company_id),
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["statementAmount"] == -500
        assert exc_info.value.detail["entryBankEffect"] == -480
        assert exc_info.value.detail["difference"] == -20
        stmt_after = await self._statement_row("bank-batch-mismatch")
        assert stmt_after["matched_entry_id"] is None
        assert (await self._entry_payload(entry_id))["billingStatus"] == "has_invoice"

    @pytest.mark.asyncio
    async def test_confirm_match_uses_voucher_bank_effect_before_payload_amount(self, temp_bank_db):
        from deps.auth import AuthContext
        from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
        from services.bank_service import import_statements_json

        company_id = "c-bank-voucher-effect"
        entry_id = "entry-voucher-effect"
        await self._insert_entry(company_id, {
            "id": entry_id,
            "direction": "expense",
            "amount": 999,
            "category": "信息技术服务费",
            "title": "技术服务费",
            "occurDate": "2026-06-10",
            "payer": "company_account",
            "billingStatus": "has_invoice",
            "voucherLines": {
                "debit": [{"accountCode": "5602004", "accountName": "管理费用-信息技术服务费", "amount": 500}],
                "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": 500}],
            },
        })
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-11",
            "description": "技术服务费付款",
            "amount": -500,
            "balance": 9500,
        }], "bank-batch-voucher-effect")
        stmt = await self._statement_row("bank-batch-voucher-effect")

        result = await confirm_suggestion(
            ConfirmSuggestionRequest(
                statement_id=stmt["id"],
                type="match",
                matched_entry_id=entry_id,
            ),
            AuthContext(user_id="u-bank-test", company_id=company_id),
        )

        assert result == {"status": "matched", "entryId": entry_id}
        stmt_after = await self._statement_row("bank-batch-voucher-effect")
        assert stmt_after["matched_entry_id"] == entry_id
        assert (await self._entry_payload(entry_id))["billingStatus"] == "reconciled"

    @pytest.mark.asyncio
    async def test_confirm_match_rejects_duplicate_entry_match(self, temp_bank_db):
        from deps.auth import AuthContext
        from fastapi import HTTPException
        from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
        from services.bank_service import import_statements_json

        company_id = "c-bank-duplicate-match"
        entry_id = "entry-duplicate-match"
        await self._insert_entry(company_id, {
            "id": entry_id,
            "direction": "expense",
            "amount": 300,
            "category": "办公费",
            "title": "办公用品",
            "occurDate": "2026-06-10",
            "payer": "company_account",
            "billingStatus": "has_invoice",
        })
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-11",
            "description": "办公用品付款",
            "amount": -300,
            "balance": 9500,
        }], "bank-batch-dup-1")
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-12",
            "description": "办公用品付款二",
            "amount": -300,
            "balance": 9200,
        }], "bank-batch-dup-2")
        first_stmt = await self._statement_row("bank-batch-dup-1")
        second_stmt = await self._statement_row("bank-batch-dup-2")
        ctx = AuthContext(user_id="u-bank-test", company_id=company_id)

        await confirm_suggestion(
            ConfirmSuggestionRequest(statement_id=first_stmt["id"], type="match", matched_entry_id=entry_id),
            ctx,
        )
        with pytest.raises(HTTPException) as exc_info:
            await confirm_suggestion(
                ConfirmSuggestionRequest(statement_id=second_stmt["id"], type="match", matched_entry_id=entry_id),
                ctx,
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_confirm_group_match_and_undo_restores_previous_statuses(self, temp_bank_db):
        from deps.auth import AuthContext
        from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
        from services.bank_service import get_reconciliation_health, get_statements, import_statements_json

        company_id = "c-bank-group-match"
        income_id = "entry-group-income"
        fee_id = "entry-group-fee"
        await self._insert_entry(company_id, {
            "id": income_id,
            "direction": "income",
            "amount": 59,
            "category": "其他业务收入",
            "title": "签小抄-人工审核-基础版",
            "occurDate": "2026-06-17",
            "billingStatus": "has_invoice",
            "voucherLines": {
                "debit": [{"accountCode": "1012001", "accountName": "微信支付待结算", "amount": 59}],
                "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": 59}],
            },
        })
        await self._insert_entry(company_id, {
            "id": fee_id,
            "direction": "expense",
            "amount": 0.53,
            "category": "手续费",
            "title": "微信手续费-签小抄-人工审核-基础版",
            "occurDate": "2026-06-17",
            "billingStatus": "no_document",
            "voucherLines": {
                "debit": [{"accountCode": "5603002", "accountName": "财务费用-手续费", "amount": 0.53}],
                "credit": [{"accountCode": "1012001", "accountName": "微信支付待结算", "amount": 0.53}],
            },
        })
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-18",
            "description": "财付通支付科技有限公司",
            "amount": 58.47,
            "balance": 1058.47,
        }], "bank-batch-group")
        stmt = await self._statement_row("bank-batch-group")
        ctx = AuthContext(user_id="u-bank-test", company_id=company_id)

        result = await confirm_suggestion(
            ConfirmSuggestionRequest(
                statement_id=stmt["id"],
                type="match",
                matched_entry_ids=[income_id, fee_id],
            ),
            ctx,
        )

        assert result == {
            "status": "matched",
            "entryId": income_id,
            "entryIds": [income_id, fee_id],
        }
        statements = await get_statements(company_id)
        assert statements[0]["matched_entry_id"] == income_id
        assert statements[0]["matched_entry_ids"] == [income_id, fee_id]
        assert (await self._entry_payload(income_id))["billingStatus"] == "reconciled"
        assert (await self._entry_payload(fee_id))["billingStatus"] == "reconciled"
        assert (await self._entry_payload(income_id))["paymentDate"] == "2026-06-18"
        assert (await self._entry_payload(fee_id))["paymentDate"] == "2026-06-18"
        health = await get_reconciliation_health(company_id, "2026-06")
        assert health["summary"] == {"highRiskCount": 0, "warningCount": 0}

        undo_result = await confirm_suggestion(
            ConfirmSuggestionRequest(statement_id=stmt["id"], type="match"),
            ctx,
        )

        assert undo_result["status"] == "unmatched"
        # 排序 tiebreaker 用 entry_id（PG 兼容），顺序不保证插入序
        assert sorted(undo_result["entryIds"]) == sorted([income_id, fee_id])
        statements = await get_statements(company_id)
        assert statements[0]["matched_entry_id"] is None
        assert statements[0]["matched_entry_ids"] == []
        assert (await self._entry_payload(income_id))["billingStatus"] == "has_invoice"
        assert (await self._entry_payload(fee_id))["billingStatus"] == "no_document"
        assert "paymentDate" not in await self._entry_payload(income_id)
        assert "paymentDate" not in await self._entry_payload(fee_id)

    @pytest.mark.asyncio
    async def test_repair_missing_settlement_voucher_is_idempotent(self, temp_bank_db):
        from deps.auth import AuthContext
        from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
        from services.bank_service import (
            get_difference_report,
            import_statements_json,
            repair_matched_statement_effect,
        )

        company_id = "c-bank-settlement-repair"
        income_id = "entry-repair-income"
        fee_id = "entry-repair-fee"
        await self._insert_entry(company_id, {
            "id": income_id,
            "direction": "income",
            "amount": 59,
            "title": "销售收入",
            "occurDate": "2026-06-17",
            "voucherLines": {
                "debit": [{"accountCode": "1012001", "accountName": "微信支付待结算", "amount": 59}],
                "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": 59}],
            },
        })
        await self._insert_entry(company_id, {
            "id": fee_id,
            "direction": "expense",
            "amount": 0.53,
            "title": "微信手续费",
            "occurDate": "2026-06-17",
            "voucherLines": {
                "debit": [{"accountCode": "5603002", "accountName": "手续费", "amount": 0.53}],
                "credit": [{"accountCode": "1012001", "accountName": "微信支付待结算", "amount": 0.53}],
            },
        })
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-18",
            "description": "微信结算到账",
            "amount": 58.47,
            "balance": 1058.47,
        }], "bank-batch-repair")
        stmt = await self._statement_row("bank-batch-repair")
        ctx = AuthContext(user_id="u-bank-test", company_id=company_id)
        await confirm_suggestion(
            ConfirmSuggestionRequest(
                statement_id=stmt["id"],
                type="match",
                matched_entry_ids=[income_id, fee_id],
            ),
            ctx,
        )

        before = await get_difference_report(company_id, "2026-06")
        assert before["matched_effect_issue_count"] == 1
        assert before["matched_effect_issues"][0]["type"] == "missing_settlement_voucher"

        repaired = await repair_matched_statement_effect(company_id, ctx.user_id, stmt["id"])
        assert repaired["status"] == "repaired"
        repair_payload = await self._entry_payload(repaired["entryId"])
        assert repair_payload["voucherLines"] == {
            "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "58.47"}],
            "credit": [{"accountCode": "1012001", "accountName": "微信支付待结算", "amount": "58.47"}],
        }

        after = await get_difference_report(company_id, "2026-06")
        assert after["matched_effect_issue_count"] == 0
        repeated = await repair_matched_statement_effect(company_id, ctx.user_id, stmt["id"])
        assert repeated["status"] == "already_repaired"

    @pytest.mark.asyncio
    async def test_repair_reversed_imported_bank_voucher_direction(self, temp_bank_db):
        from services import database
        from services.bank_service import (
            get_difference_report,
            import_statements_json,
            repair_matched_statement_effect,
        )

        company_id = "c-bank-direction-repair"
        entry_id = "bank-entry-direction-repair"
        await self._insert_entry(company_id, {
            "id": entry_id,
            "source": "bank_import",
            "direction": "income",
            "amount": 0.74,
            "title": "小程序注册验证",
            "occurDate": "2026-06-04",
            "paymentDate": "2026-06-04",
            "billingStatus": "reconciled",
            "voucherLines": {
                "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": 0.74}],
                "credit": [{"accountCode": "1221", "accountName": "其他应收款-其他", "amount": 0.74}],
            },
        })
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-04",
            "description": "小程序注册验证",
            "amount": -0.74,
            "balance": 999.26,
        }], "bank-batch-direction-repair")
        stmt = await self._statement_row("bank-batch-direction-repair")
        conn = await database.get_conn()
        try:
            await conn.execute(
                "UPDATE bank_statements SET matched_entry_id = ? WHERE id = ?",
                (entry_id, stmt["id"]),
            )
            await conn.execute(
                "INSERT INTO bank_statement_matches "
                "(statement_id, company_id, entry_id, previous_billing_status, previous_payment_date, created_at) "
                "VALUES (?, ?, ?, NULL, NULL, '2026-06-04T00:00:00+00:00')",
                (stmt["id"], company_id, entry_id),
            )
            await conn.commit()
        finally:
            await conn.close()

        before = await get_difference_report(company_id, "2026-06")
        assert before["matched_effect_issues"][0]["type"] == "reversed_bank_direction"

        result = await repair_matched_statement_effect(
            company_id,
            "u-bank-test",
            stmt["id"],
        )
        assert result["status"] == "repaired"
        payload = await self._entry_payload(entry_id)
        assert payload["direction"] == "expense"
        assert payload["voucherLines"]["credit"][0]["accountCode"] == "1002"
        after = await get_difference_report(company_id, "2026-06")
        assert after["matched_effect_issue_count"] == 0

    @pytest.mark.asyncio
    async def test_confirm_group_match_rejects_net_amount_mismatch_without_writing(self, temp_bank_db):
        from deps.auth import AuthContext
        from fastapi import HTTPException
        from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
        from services.bank_service import get_statements, import_statements_json

        company_id = "c-bank-group-mismatch"
        entry_ids = ["entry-group-mismatch-income", "entry-group-mismatch-fee"]
        await self._insert_entry(company_id, {
            "id": entry_ids[0], "direction": "income", "amount": 59,
            "category": "其他业务收入", "title": "收入", "occurDate": "2026-06-17",
        })
        await self._insert_entry(company_id, {
            "id": entry_ids[1], "direction": "expense", "amount": 0.50,
            "category": "手续费", "title": "手续费", "occurDate": "2026-06-17",
        })
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-18", "description": "净额收款",
            "amount": 58.47, "balance": 1058.47,
        }], "bank-batch-group-mismatch")
        stmt = await self._statement_row("bank-batch-group-mismatch")

        with pytest.raises(HTTPException) as exc_info:
            await confirm_suggestion(
                ConfirmSuggestionRequest(
                    statement_id=stmt["id"], type="match", matched_entry_ids=entry_ids,
                ),
                AuthContext(user_id="u-bank-test", company_id=company_id),
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["statementAmount"] == Decimal("58.47")
        assert exc_info.value.detail["entryBankEffect"] == Decimal("58.50")
        assert exc_info.value.detail["difference"] == Decimal("-0.03")
        statements = await get_statements(company_id)
        assert statements[0]["matched_entry_ids"] == []

    @pytest.mark.asyncio
    async def test_group_secondary_entry_cannot_match_another_statement(self, temp_bank_db):
        from deps.auth import AuthContext
        from fastapi import HTTPException
        from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
        from services.bank_service import import_statements_json

        company_id = "c-bank-group-duplicate"
        income_id = "entry-group-duplicate-income"
        fee_id = "entry-group-duplicate-fee"
        await self._insert_entry(company_id, {
            "id": income_id, "direction": "income", "amount": 59,
            "category": "其他业务收入", "title": "收入", "occurDate": "2026-06-17",
        })
        await self._insert_entry(company_id, {
            "id": fee_id, "direction": "expense", "amount": 0.53,
            "category": "手续费", "title": "手续费", "occurDate": "2026-06-17",
        })
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-18", "description": "净额收款",
            "amount": 58.47, "balance": 1058.47,
        }], "bank-batch-group-primary")
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-19", "description": "手续费付款",
            "amount": -0.53, "balance": 1057.94,
        }], "bank-batch-group-secondary")
        primary = await self._statement_row("bank-batch-group-primary")
        secondary = await self._statement_row("bank-batch-group-secondary")
        ctx = AuthContext(user_id="u-bank-test", company_id=company_id)

        await confirm_suggestion(
            ConfirmSuggestionRequest(
                statement_id=primary["id"], type="match", matched_entry_ids=[income_id, fee_id],
            ),
            ctx,
        )
        with pytest.raises(HTTPException) as exc_info:
            await confirm_suggestion(
                ConfirmSuggestionRequest(
                    statement_id=secondary["id"], type="match", matched_entry_id=fee_id,
                ),
                ctx,
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_confirm_match_advancement_boss(self, temp_bank_db):
        from deps.auth import AuthContext
        from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
        from services.bank_service import import_statements_json

        company_id = "c-adv-boss-match"
        entry_id = "entry-adv-boss"
        await self._insert_entry(company_id, {
            "id": entry_id,
            "direction": "expense",
            "amount": 250,
            "category": "办公费",
            "title": "办公用品(老板垫付)",
            "occurDate": "2026-06-10",
            "payer": "boss_personal",
            "billingStatus": "has_invoice",
        })
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-12",
            "description": "老板办公费报销打款",
            "amount": -250,
            "balance": 9000,
        }], "bank-batch-adv-boss")
        stmt = await self._statement_row("bank-batch-adv-boss")
        ctx = AuthContext(user_id="u-bank-test", company_id=company_id)

        result = await confirm_suggestion(
            ConfirmSuggestionRequest(statement_id=stmt["id"], type="match", matched_entry_id=entry_id),
            ctx,
        )

        assert result["status"] == "matched"
        new_entry_id = result["entryId"]
        assert new_entry_id.startswith("reim-")

        # Verify new reimbursement entry was created correctly
        reim_payload = await self._entry_payload(new_entry_id)
        assert reim_payload["amount"] == "250.00"
        assert reim_payload["direction"] == "owner_transfer"
        assert reim_payload["category"] == "股东垫付报销"
        assert reim_payload["payer"] == "boss_personal"
        assert reim_payload["billingStatus"] == "reconciled"

        # Verify original entry is reconciled
        orig_payload = await self._entry_payload(entry_id)
        assert orig_payload["billingStatus"] == "reconciled"

        # Verify statement is linked to the reimbursement entry
        stmt_after = await self._statement_row("bank-batch-adv-boss")
        assert stmt_after["matched_entry_id"] == new_entry_id

    @pytest.mark.asyncio
    async def test_confirm_match_advancement_employee(self, temp_bank_db):
        from deps.auth import AuthContext
        from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
        from services.bank_service import import_statements_json

        company_id = "c-adv-emp-match"
        entry_id = "entry-adv-emp"
        await self._insert_entry(company_id, {
            "id": entry_id,
            "direction": "expense",
            "amount": 350.5,
            "category": "差旅费",
            "title": "出差机票(李四垫付)",
            "occurDate": "2026-06-10",
            "payer": "employee_personal:李四",
            "billingStatus": "has_invoice",
        })
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-15",
            "description": "李四出差报销",
            "amount": -350.5,
            "balance": 8500,
        }], "bank-batch-adv-emp")
        stmt = await self._statement_row("bank-batch-adv-emp")
        ctx = AuthContext(user_id="u-bank-test", company_id=company_id)

        result = await confirm_suggestion(
            ConfirmSuggestionRequest(statement_id=stmt["id"], type="match", matched_entry_id=entry_id),
            ctx,
        )

        assert result["status"] == "matched"
        new_entry_id = result["entryId"]
        assert new_entry_id.startswith("reim-")

        # Verify new reimbursement entry was created correctly
        reim_payload = await self._entry_payload(new_entry_id)
        assert reim_payload["amount"] == "350.50"
        assert reim_payload["direction"] == "owner_transfer"
        assert reim_payload["category"] == "员工垫付报销"
        assert reim_payload["payer"] == "employee_personal:李四"
        assert reim_payload["billingStatus"] == "reconciled"

        # Verify original entry is reconciled
        orig_payload = await self._entry_payload(entry_id)
        assert orig_payload["billingStatus"] == "reconciled"

    @pytest.mark.asyncio
    async def test_existing_reimbursement_payment_matches_without_creating_nested_entry(self, temp_bank_db):
        from deps.auth import AuthContext
        from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
        from services.bank_service import import_statements_json

        company_id = "c-existing-reimbursement-payment"
        entry_id = "reim-existing-payment"
        await self._insert_entry(company_id, {
            "id": entry_id,
            "direction": "owner_transfer",
            "amount": 300,
            "category": "员工垫付报销",
            "title": "支付员工报销款",
            "occurDate": "2026-06-26",
            "payer": "employee_personal:杨云飞",
            "voucherLines": {
                "debit": [{"accountCode": "2241", "accountName": "其他应付款-员工往来(杨云飞)", "amount": 300}],
                "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": 300}],
            },
        })
        await import_statements_json(company_id, [{
            "transaction_date": "2026-06-26",
            "description": "支付杨云飞报销款",
            "amount": -300,
            "balance": 9000,
        }], "bank-batch-existing-reimbursement")
        stmt = await self._statement_row("bank-batch-existing-reimbursement")

        result = await confirm_suggestion(
            ConfirmSuggestionRequest(statement_id=stmt["id"], type="match", matched_entry_id=entry_id),
            AuthContext(user_id="u-bank-test", company_id=company_id),
        )

        assert result == {"status": "matched", "entryId": entry_id}
        assert (await self._statement_row("bank-batch-existing-reimbursement"))["matched_entry_id"] == entry_id
        from services import database
        conn = await database.get_conn()
        try:
            cursor = await conn.execute(
                "SELECT COUNT(*) AS cnt FROM entries WHERE id = ?",
                (f"reim-{stmt['id']}-{entry_id}",),
            )
            assert (await cursor.fetchone())["cnt"] == 0
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_reconciliation_health_reports_integrity_issues(self, temp_bank_db):
        from services import database
        from services.bank_service import get_reconciliation_health, import_statements_json

        company_id = "c-bank-health"
        await self._insert_entry(company_id, {
            "id": "entry-amount-mismatch",
            "direction": "expense",
            "amount": 100,
            "category": "办公费",
            "title": "金额不一致条目",
            "occurDate": "2026-06-01",
            "billingStatus": "reconciled",
        })
        await self._insert_entry(company_id, {
            "id": "entry-direction-mismatch",
            "direction": "expense",
            "amount": 80,
            "category": "办公费",
            "title": "方向不一致条目",
            "occurDate": "2026-06-02",
            "billingStatus": "reconciled",
        })
        await self._insert_entry(company_id, {
            "id": "entry-duplicate-health",
            "direction": "expense",
            "amount": 30,
            "category": "办公费",
            "title": "重复引用条目",
            "occurDate": "2026-06-03",
            "billingStatus": "reconciled",
        })
        await self._insert_entry(company_id, {
            "id": "entry-pending-health",
            "direction": "expense",
            "amount": 40,
            "category": "办公费",
            "title": "待确认条目",
            "occurDate": "2026-06-04",
            "billingStatus": "has_invoice",
        })
        rows = [
            {"transaction_date": "2026-06-01", "description": "断链流水", "amount": -60, "balance": 1000},
            {"transaction_date": "2026-06-02", "description": "金额不一致", "amount": -120, "balance": 880},
            {"transaction_date": "2026-06-03", "description": "方向不一致", "amount": 80, "balance": 960},
            {"transaction_date": "2026-06-04", "description": "重复一", "amount": -30, "balance": 930},
            {"transaction_date": "2026-06-05", "description": "重复二", "amount": -30, "balance": 900},
            {"transaction_date": "2026-06-06", "description": "待确认", "amount": -40, "balance": 860},
        ]
        await import_statements_json(company_id, rows, "bank-batch-health")

        conn = await database.get_conn()
        try:
            links = [
                ("missing-entry", "断链流水"),
                ("entry-amount-mismatch", "金额不一致"),
                ("entry-direction-mismatch", "方向不一致"),
                ("entry-duplicate-health", "重复一"),
                ("entry-duplicate-health", "重复二"),
                ("entry-pending-health", "待确认"),
            ]
            for entry_id, desc in links:
                await conn.execute(
                    "UPDATE bank_statements SET matched_entry_id = ? WHERE company_id = ? AND description = ?",
                    (entry_id, company_id, desc),
                )
            await conn.commit()
        finally:
            await conn.close()

        health = await get_reconciliation_health(company_id, "2026-06")

        assert len(health["broken_links"]) == 1
        assert len(health["amount_mismatches"]) == 1
        assert len(health["direction_mismatches"]) == 1
        assert len(health["duplicate_entry_matches"]) == 1
        assert len(health["pending_confirmations"]) == 1
        assert health["summary"] == {"highRiskCount": 4, "warningCount": 1}

    async def _insert_wechat_order(self, company_id, order_no, amount=5, fee=0.05, occur_date="2026-07-14"):
        for suffix, direction, value, debit, credit in [
            ("income", "income", amount, "1012001", "5001"),
            ("fee", "expense", fee, "5603002", "1012001"),
        ]:
            await self._insert_entry(company_id, {
                "id": f"{order_no}-{suffix}", "source": "wechat_bill", "wechat_order_no": order_no,
                "title": suffix, "category": "主营业务收入" if direction == "income" else "银行及金融手续费",
                "direction": direction, "amount": value, "occurDate": occur_date,
                "voucherLines": {"debit": [{"accountCode": debit, "amount": value}],
                                 "credit": [{"accountCode": credit, "amount": value}]},
            })

    @pytest.mark.asyncio
    async def test_wechat_settlement_suggests_group_without_llm_or_writes(self, temp_bank_db, monkeypatch):
        from services.bank_service import ai_suggest_matches, import_statements_json
        from services import llm

        company_id = "c-wx-group-suggestion"
        await self._insert_wechat_order(company_id, "wx-order")
        # WeChat candidates must not depend on the most recent 100 journals.
        for index in range(101):
            await self._insert_entry(company_id, {"id": f"unrelated-{index}", "amount": 100,
                "direction": "income", "occurDate": "2026-10-01"})
        await import_statements_json(company_id, [{"transaction_date": "2026-07-15",
            "description": "入账 | 财付通支付科技有限公司", "amount": 4.95}], "wx-suggest")

        def unexpected_llm(*args, **kwargs):
            raise AssertionError("A known settlement must not be classified as new revenue")
        monkeypatch.setattr(llm, "safe_chat_text_json", unexpected_llm)
        suggestions = await ai_suggest_matches(company_id)
        assert len(suggestions) == 1
        assert suggestions[0]["type"] == "match"
        assert set(suggestions[0]["matchedEntryIds"]) == {"wx-order-income", "wx-order-fee"}
        assert len(suggestions[0]["matchedEntries"]) == 2
        assert (await self._statement_row("wx-suggest"))["matched_entry_id"] is None
        assert "billingStatus" not in await self._entry_payload("wx-order-income")

    @pytest.mark.asyncio
    async def test_wechat_settlement_rejects_duplicate_new_entry_then_matches_originals(self, temp_bank_db):
        from fastapi import HTTPException
        from deps.auth import AuthContext
        from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
        from services.bank_service import import_statements_json
        from services import database

        company_id = "c-wx-duplicate-guard"
        await self._insert_wechat_order(company_id, "wx-original")
        await import_statements_json(company_id, [{"transaction_date": "2026-07-15",
            "description": "财付通支付科技有限公司", "amount": 4.95}], "wx-guard")
        stmt = await self._statement_row("wx-guard")
        ctx = AuthContext(user_id="u-bank-test", company_id=company_id)
        with pytest.raises(HTTPException) as exc:
            await confirm_suggestion(ConfirmSuggestionRequest(statement_id=stmt["id"], type="new",
                new_entry={"title": "银行入账", "amount": 4.95, "direction": "income",
                           "category": "主营业务收入", "occurDate": "2026-07-15"}), ctx)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "EXISTING_WECHAT_ENTRIES"
        ids = exc.value.detail["candidateEntryGroups"][0]
        conn = await database.get_conn()
        try:
            assert (await (await conn.execute("SELECT COUNT(*) FROM entries WHERE company_id = ?",
                                             (company_id,))).fetchone())[0] == 2
        finally:
            await conn.close()
        assert (await self._statement_row("wx-guard"))["matched_entry_id"] is None
        await confirm_suggestion(ConfirmSuggestionRequest(statement_id=stmt["id"], type="match",
                                                        matched_entry_ids=ids), ctx)
        assert (await self._entry_payload("wx-original-income"))["billingStatus"] == "reconciled"
        assert (await self._entry_payload("wx-original-fee"))["billingStatus"] == "reconciled"

    @pytest.mark.asyncio
    async def test_wechat_settlement_daily_batch_and_ambiguous_orders(self, temp_bank_db):
        from services.bank_service import get_wechat_settlement_candidates, ai_suggest_matches, import_statements_json
        company_id = "c-wx-daily-candidates"
        await self._insert_wechat_order(company_id, "wx-a", amount=45, fee=0.41)
        await self._insert_wechat_order(company_id, "wx-b", amount=19, fee=0.17)
        statement = {"transaction_date": "2026-07-15", "description": "财付通", "amount": 63.42}
        groups = await get_wechat_settlement_candidates(company_id, statement)
        assert len(groups) == 1
        assert {entry["id"] for entry in groups[0]} == {"wx-a-income", "wx-a-fee", "wx-b-income", "wx-b-fee"}
        assert await get_wechat_settlement_candidates("other-company", statement) == []
        assert await get_wechat_settlement_candidates(company_id, {**statement, "description": "其他客户收款"}) == []
        assert await get_wechat_settlement_candidates(company_id, {**statement, "transaction_date": "2026-09-15"}) == []

        await self._insert_wechat_order(company_id, "wx-same-1")
        await self._insert_wechat_order(company_id, "wx-same-2")
        await import_statements_json(company_id, [{**statement, "amount": 4.95}], "wx-ambiguous")
        suggestion = (await ai_suggest_matches(company_id))[0]
        # 智能认领制下模糊金额订单不做自动归组（避免错配），流水退回
        # 汇总记账：直接建议微信结算资金划转草稿
        assert suggestion["type"] == "new"
        assert suggestion["rule"] == "wechat_withdrawal"
        assert suggestion["suggestedEntry"]["category"] == "微信支付结算"
        assert float(suggestion["suggestedEntry"]["amount"]) == pytest.approx(4.95)

    @pytest.mark.asyncio
    async def test_wechat_settlement_excludes_confirmed_partial_and_refunded_orders(self, temp_bank_db):
        from services.bank_service import get_wechat_settlement_candidates
        from services import database
        company_id = "c-wx-ineligible"
        await self._insert_wechat_order(company_id, "wx-done")
        conn = await database.get_conn()
        try:
            await conn.execute("UPDATE entries SET payload = json_set(payload, '$.billingStatus', 'reconciled') WHERE id = ?",
                               ("wx-done-income",))
            await conn.commit()
        finally:
            await conn.close()
        stmt = {"transaction_date": "2026-07-15", "description": "微信结算", "amount": 4.95}
        assert await get_wechat_settlement_candidates(company_id, stmt) == []
        await self._insert_wechat_order(company_id, "wx-refunded")
        for suffix, amount, debit, credit in [("refund", 5, "5001", "1012001"), ("fee-refund", 0.05, "1012001", "5603002")]:
            await self._insert_entry(company_id, {"id": suffix, "source": "wechat_bill", "wechat_order_no": "wx-refunded",
                "occurDate": "2026-07-14", "voucherLines": {
                    "debit": [{"accountCode": debit, "amount": amount}],
                    "credit": [{"accountCode": credit, "amount": amount}]}})
        assert await get_wechat_settlement_candidates(company_id, stmt) == []

    @pytest.mark.asyncio
    async def test_wechat_large_settlement_is_not_classified_as_new_income(self, temp_bank_db):
        from services.bank_service import ai_suggest_matches, import_statements_json
        from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
        from deps.auth import AuthContext
        from fastapi import HTTPException
        company_id = "c-wx-large-settlement"
        for index in range(11):
            await self._insert_wechat_order(company_id, f"wx-large-{index}")
        await import_statements_json(company_id, [{"transaction_date": "2026-07-15",
            "description": "财付通结算", "amount": 54.45}], "wx-large-batch")
        suggestion = (await ai_suggest_matches(company_id))[0]
        assert suggestion["type"] == "match"
        assert len(suggestion["matchedEntryIds"]) == 22
        statement = await self._statement_row("wx-large-batch")
        with pytest.raises(HTTPException) as exc:
            await confirm_suggestion(ConfirmSuggestionRequest(statement_id=statement["id"], type="new",
                new_entry={"amount": 54.45, "direction": "income", "category": "主营业务收入"}),
                AuthContext(user_id="u-bank-test", company_id=company_id))
        assert exc.value.status_code == 409


class TestWechatBill:
    @pytest.mark.asyncio
    async def test_cross_batch_full_refund_closes_unsettled_historical_order(self, monkeypatch):
        from services import wechat_bill_service

        async def fake_existing_keys(company_id):
            return {"success:wx-cross-batch"}

        async def fake_order_history(company_id, order_nos):
            assert order_nos == {"wx-cross-batch"}
            return {
                "wx-cross-batch": [
                    {
                        "id": "old-income",
                        "bank_matched": False,
                        "payload": {
                            "id": "old-income", "direction": "income", "amount": 5,
                            "wechat_order_no": "wx-cross-batch",
                            "wechat_transaction_key": "success:wx-cross-batch",
                        },
                    },
                    {
                        "id": "old-fee",
                        "bank_matched": False,
                        "payload": {
                            "id": "old-fee", "direction": "expense", "amount": 0.05,
                            "wechat_order_no": "wx-cross-batch",
                            "wechat_transaction_key": "success:wx-cross-batch",
                        },
                    },
                ]
            }

        saved = {}

        async def fake_save(entries, *, user_id, company_id, existing_entry_updates=None, **kwargs):
            saved["entries"] = entries
            saved["updates"] = existing_entry_updates or []
            return entries

        monkeypatch.setattr(wechat_bill_service, "_load_existing_wechat_transaction_keys", fake_existing_keys)
        monkeypatch.setattr(wechat_bill_service, "_load_existing_wechat_order_history", fake_order_history, raising=False)
        monkeypatch.setattr(wechat_bill_service, "save_entries_bulk", fake_save)

        result = await wechat_bill_service.import_wechat_bill("c1", "u1", [
            {
                "status": "SUCCESS", "date": "2026-06-01", "product_name": "重复历史收款",
                "order_amount": 5, "fee": 0.05, "order_no": "wx-cross-batch",
            },
            {
                "status": "REFUND", "date": "2026-07-02", "product_name": "跨月退款",
                "refund_amount": 5, "fee": 0.05, "order_no": "wx-cross-batch",
                "refund_order_no": "refund-cross-batch",
            },
        ])

        assert result["imported"] == 1
        assert result["ignored_duplicates"] == 1
        assert all(entry["billingStatus"] == "reconciled" for entry in saved["entries"])
        assert all(entry["reconciliationType"] == "internal_refund" for entry in saved["entries"])
        assert {item["id"] for item in saved["updates"]} == {"old-income", "old-fee"}
        assert all(item["billingStatus"] == "reconciled" for item in saved["updates"])

    @pytest.mark.asyncio
    async def test_fee_entry_uses_distinct_source_key_end_to_end(self):
        """回归：一笔交易生成的收入+手续费（及退款+手续费退还）条目在同一批次入库时，
        不得因共享 wechat_transaction_key 触发会计核心来源唯一性校验自碰撞
        （历史 bug：整批报"该来源业务已入账"）。走真实 create_entry 链路。"""
        from db.base import Base
        import db.models  # noqa: F401
        from db.session import get_engine
        from services import wechat_bill_service
        from services.entry_service import list_entries

        engine = get_engine()
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        company_id = "c-wechat-fee-key"
        user_id = "owner-1"
        rows = [
            {
                "status": "SUCCESS", "date": "2026-08-07", "product_name": "签小抄积分充值-6积分",
                "order_amount": 6, "fee": 0.05, "order_no": "wx-fee-key-1",
                "wechat_transaction_key": "success:wx-fee-key-1",
            },
            {
                "status": "REFUND", "date": "2026-08-20", "product_name": "签小抄积分充值-6积分",
                "refund_amount": 6, "fee": 0.05, "order_no": "wx-fee-key-2",
                "refund_order_no": "refund-fee-key-1",
                "wechat_transaction_key": "refund:wx-fee-key-2:refund-fee-key-1",
            },
        ]

        result = await wechat_bill_service.import_wechat_bill(company_id, user_id, rows)
        assert result["imported"] == 2, result
        assert result["failed"] == 0, result

        entries = await list_entries(user_id, company_id)
        keys = sorted(
            e["wechat_transaction_key"] for e in entries if e.get("wechat_transaction_key")
        )
        assert keys == [
            "refund:wx-fee-key-2:refund-fee-key-1",
            "refund:wx-fee-key-2:refund-fee-key-1:fee",
            "success:wx-fee-key-1",
            "success:wx-fee-key-1:fee",
        ]

        # 重复导入同一账单：行级去重仍然生效，不会重复入账
        result2 = await wechat_bill_service.import_wechat_bill(company_id, user_id, rows)
        assert result2["imported"] == 0
        assert result2["ignored_duplicates"] == 2

    @pytest.mark.asyncio
    async def test_cross_batch_refund_stays_open_after_original_order_reached_bank(self, monkeypatch):
        from services import wechat_bill_service

        async def fake_existing_keys(company_id):
            return {"success:wx-bank-settled"}

        async def fake_order_history(company_id, order_nos):
            return {
                "wx-bank-settled": [{
                    "id": "old-income", "bank_matched": True,
                    "payload": {
                        "id": "old-income", "direction": "income", "amount": 5,
                        "wechat_order_no": "wx-bank-settled",
                        "wechat_transaction_key": "success:wx-bank-settled",
                    },
                }]
            }

        saved = {}

        async def fake_save(entries, *, user_id, company_id, existing_entry_updates=None, **kwargs):
            saved["entries"] = entries
            saved["updates"] = existing_entry_updates or []
            return entries

        monkeypatch.setattr(wechat_bill_service, "_load_existing_wechat_transaction_keys", fake_existing_keys)
        monkeypatch.setattr(wechat_bill_service, "_load_existing_wechat_order_history", fake_order_history, raising=False)
        monkeypatch.setattr(wechat_bill_service, "save_entries_bulk", fake_save)

        result = await wechat_bill_service.import_wechat_bill("c1", "u1", [{
            "status": "REFUND", "date": "2026-07-02", "product_name": "已结算退款",
            "refund_amount": 5, "fee": 0.05, "order_no": "wx-bank-settled",
            "refund_order_no": "refund-after-bank",
        }])

        assert result["imported"] == 1
        assert saved["updates"] == []
        assert all("billingStatus" not in entry for entry in saved["entries"])

    @pytest.mark.asyncio
    async def test_cross_batch_partial_refund_does_not_close_historical_order(self, monkeypatch):
        from services import wechat_bill_service

        async def fake_existing_keys(company_id):
            return {"success:wx-partial"}

        async def fake_order_history(company_id, order_nos):
            return {
                "wx-partial": [{
                    "id": "old-income", "bank_matched": False,
                    "payload": {
                        "id": "old-income", "direction": "income", "amount": 10,
                        "wechat_order_no": "wx-partial",
                        "wechat_transaction_key": "success:wx-partial",
                    },
                }]
            }

        saved = {}

        async def fake_save(entries, *, user_id, company_id, existing_entry_updates=None, **kwargs):
            saved["entries"] = entries
            saved["updates"] = existing_entry_updates or []
            return entries

        monkeypatch.setattr(wechat_bill_service, "_load_existing_wechat_transaction_keys", fake_existing_keys)
        monkeypatch.setattr(wechat_bill_service, "_load_existing_wechat_order_history", fake_order_history, raising=False)
        monkeypatch.setattr(wechat_bill_service, "save_entries_bulk", fake_save)

        result = await wechat_bill_service.import_wechat_bill("c1", "u1", [{
            "status": "REFUND", "date": "2026-07-02", "product_name": "部分退款",
            "refund_amount": 4, "fee": 0.02, "order_no": "wx-partial",
            "refund_order_no": "refund-partial",
        }])

        assert result["imported"] == 1
        assert saved["updates"] == []
        assert all("billingStatus" not in entry for entry in saved["entries"])

    @pytest.mark.asyncio
    async def test_wechat_bill_import_uses_one_bulk_save_and_filters_file_duplicates(self, monkeypatch):
        from services import wechat_bill_service

        class FakeCursor:
            async def fetchall(self):
                return []

        class FakeConn:
            async def execute(self, *args, **kwargs):
                return FakeCursor()

            async def close(self):
                pass

        async def fake_get_conn():
            return FakeConn()

        saved_batches = []

        async def fake_save_entries_bulk(entries, *, user_id, company_id, **kwargs):
            saved_batches.append(entries)
            return [{**entry, "id": f"entry-{idx}"} for idx, entry in enumerate(entries)]

        monkeypatch.setattr(wechat_bill_service, "get_conn", fake_get_conn)
        monkeypatch.setattr(wechat_bill_service, "save_entries_bulk", fake_save_entries_bulk)

        result = await wechat_bill_service.import_wechat_bill(
            "c1",
            "u1",
            [
                {
                    "status": "SUCCESS",
                    "date": "2026-06-01",
                    "product_name": "课程收入",
                    "order_amount": 100,
                    "fee": 0.6,
                    "refund_amount": 0,
                    "order_no": "wx-1",
                    "fee_rate": "0.6%",
                },
                {
                    "status": "SUCCESS",
                    "date": "2026-06-01",
                    "product_name": "重复订单",
                    "order_amount": 100,
                    "fee": 0.6,
                    "refund_amount": 0,
                    "order_no": "wx-1",
                    "fee_rate": "0.6%",
                },
                {
                    "status": "REFUND",
                    "date": "2026-06-02",
                    "product_name": "退款课程",
                    "order_amount": 0,
                    "fee": 0.2,
                    "refund_amount": 30,
                    "order_no": "wx-2",
                    "fee_rate": "0.6%",
                },
            ],
        )

        assert result["imported"] == 2
        assert result["ignored_duplicates"] == 1
        assert result["failed"] == 0
        assert len(saved_batches) == 1
        assert len(saved_batches[0]) == 4
        assert all(entry["source"] == "wechat_bill" for entry in saved_batches[0])

        refund_entry = next(e for e in saved_batches[0] if e.get("remark") == "微信退款冲销")
        assert refund_entry["amount"] == -30
        assert refund_entry["direction"] == "income"

        revenue_entry = saved_batches[0][0]
        assert revenue_entry["voucherLines"]["debit"][0]["accountCode"] == "1012001"
        assert revenue_entry["voucherLines"]["credit"][0]["accountCode"] == "5001"

        fee_entry = saved_batches[0][1]
        assert fee_entry["voucherLines"]["debit"][0]["accountCode"] == "5603002"
        assert fee_entry["voucherLines"]["credit"][0]["accountCode"] == "1012001"

        fee_refund = next(e for e in saved_batches[0] if e.get("remark") == "退款手续费退还")
        assert fee_refund["amount"] == Decimal("-0.20")
        assert fee_refund["direction"] == "expense"
        assert fee_refund["voucherLines"]["debit"][0]["accountCode"] == "1012001"
        assert fee_refund["voucherLines"]["credit"][0]["accountCode"] == "5603002"

    @pytest.mark.asyncio
    async def test_wechat_bill_import_keeps_refund_with_same_order_no(self, monkeypatch):
        from services import wechat_bill_service

        class FakeCursor:
            async def fetchall(self):
                return []

        class FakeConn:
            async def execute(self, *args, **kwargs):
                return FakeCursor()

            async def close(self):
                pass

        async def fake_get_conn():
            return FakeConn()

        saved_batches = []

        async def fake_save_entries_bulk(entries, *, user_id, company_id, **kwargs):
            saved_batches.append(entries)
            return [{**entry, "id": f"entry-{idx}"} for idx, entry in enumerate(entries)]

        monkeypatch.setattr(wechat_bill_service, "get_conn", fake_get_conn)
        monkeypatch.setattr(wechat_bill_service, "save_entries_bulk", fake_save_entries_bulk)

        result = await wechat_bill_service.import_wechat_bill(
            "c1",
            "u1",
            [
                {
                    "status": "SUCCESS",
                    "date": "2026-06-01",
                    "transaction_time": "2026-06-01 10:00:00",
                    "product_name": "课程收入",
                    "order_amount": 5,
                    "settled_amount": 5,
                    "fee": 0.05,
                    "refund_amount": 0,
                    "order_no": "wx-same-order",
                    "fee_rate": "0.9%",
                },
                {
                    "status": "REFUND",
                    "date": "2026-06-01",
                    "transaction_time": "2026-06-01 11:00:00",
                    "product_name": "课程收入",
                    "order_amount": 0,
                    "fee": 0.05,
                    "refund_amount": 5,
                    "order_no": "wx-same-order",
                    "refund_order_no": "refund-1",
                    "fee_rate": "0.9%",
                },
                {
                    "status": "REFUND",
                    "date": "2026-06-01",
                    "transaction_time": "2026-06-01 11:00:00",
                    "product_name": "重复退款",
                    "order_amount": 0,
                    "fee": 0.05,
                    "refund_amount": 5,
                    "order_no": "wx-same-order",
                    "refund_order_no": "refund-1",
                    "fee_rate": "0.9%",
                },
            ],
        )

        assert result["imported"] == 2
        assert result["ignored_duplicates"] == 1
        assert result["failed"] == 0
        assert len(saved_batches) == 1
        assert len(saved_batches[0]) == 4
        assert all(entry["source"] == "wechat_bill" for entry in saved_batches[0])
        assert all(entry["billingStatus"] == "reconciled" for entry in saved_batches[0])

        fee_refund = next(entry for entry in saved_batches[0] if entry.get("remark") == "退款手续费退还")
        assert fee_refund["amount"] == Decimal("-0.05")
        assert fee_refund["direction"] == "expense"
        assert fee_refund["voucherLines"]["debit"][0]["accountCode"] == "1012001"
        assert fee_refund["voucherLines"]["credit"][0]["accountCode"] == "5603002"

        transaction_keys = {entry["wechat_transaction_key"] for entry in saved_batches[0]}
        # 手续费/手续费退还条目使用 ":fee" 后缀 key，与同笔交易的收入/退款条目区分，
        # 避免同一批次入库时触发会计核心来源唯一性校验自碰撞
        assert transaction_keys == {
            "success:wx-same-order",
            "success:wx-same-order:fee",
            "refund:wx-same-order:refund-1",
            "refund:wx-same-order:refund-1:fee",
        }

    def test_wechat_bill_entries_do_not_have_bank_effect_until_settlement(self):
        from services.bank_service import _bank_effect_from_voucher, build_group_reconciliation_check

        revenue_voucher = {
            "debit": [{"accountCode": "1012001", "accountName": "微信支付待结算", "amount": 100}],
            "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": 100}],
        }
        fee_voucher = {
            "debit": [{"accountCode": "5603002", "accountName": "财务费用-手续费", "amount": 0.6}],
            "credit": [{"accountCode": "1012001", "accountName": "微信支付待结算", "amount": 0.6}],
        }
        settlement_voucher = {
            "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": 99.4}],
            "credit": [{"accountCode": "1012001", "accountName": "微信支付待结算", "amount": 99.4}],
        }
        refund_voucher = {
            "debit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": 5}],
            "credit": [{"accountCode": "1012001", "accountName": "微信支付待结算", "amount": 5}],
        }
        refunded_fee_voucher = {
            "debit": [{"accountCode": "1012001", "accountName": "微信支付待结算", "amount": 0.05}],
            "credit": [{"accountCode": "5603002", "accountName": "财务费用-手续费", "amount": 0.05}],
        }

        assert _bank_effect_from_voucher(revenue_voucher) == 0
        assert _bank_effect_from_voucher(fee_voucher) == 0
        assert _bank_effect_from_voucher(settlement_voucher) == Decimal("99.40")
        refund_group = build_group_reconciliation_check(-4.95, [
            {"id": "refund", "voucherLines": refund_voucher},
            {"id": "fee-refund", "voucherLines": refunded_fee_voucher},
        ])
        assert refund_group["entryBankEffect"] == Decimal("-4.95")
        assert refund_group["amountMatches"] is True
        assert refund_group["directionMatches"] is True


class TestBankBatchDeleteApi:
    @staticmethod
    def _test_db():
        return Path(__file__).resolve().parent.parent / "data" / ".pytest_accounting.db"

    def test_batch_delete_removes_statements_matches_and_sources(self, client, auth_headers):
        rows = [
            {"transaction_date": "2035-03-01", "description": "批量删除流水一", "amount": 100, "balance": "900"},
            {"transaction_date": "2035-03-02", "description": "批量删除流水二", "amount": -40, "balance": "860"},
            {"transaction_date": "2035-03-03", "description": "批量删除流水三", "amount": -15, "balance": "845"},
        ]
        imported = client.post("/api/bank/import-json", json={
            "statement_id": "batch-del-source", "rows": rows,
        }, headers=auth_headers)
        assert imported.status_code == 200, imported.text
        assert imported.json()["imported"] == 3

        items = client.get(
            "/api/bank/statements?statement_id=batch-del-source", headers=auth_headers
        ).json()["items"]
        assert len(items) == 3
        id_one, id_two, id_three = [item["id"] for item in items]

        db_path = self._test_db()
        conn = sqlite3.connect(db_path)
        try:
            company_id = conn.execute(
                "SELECT company_id FROM bank_statements WHERE id = ?", (id_one,)
            ).fetchone()[0]
            for statement_id, entry_id in ((id_one, "entry-del-A"), (id_three, "entry-del-B")):
                conn.execute(
                    "INSERT INTO bank_statement_matches (statement_id, company_id, entry_id, "
                    "previous_billing_status, previous_payment_date, created_at) VALUES (?, ?, ?, NULL, NULL, ?)",
                    (statement_id, company_id, entry_id, "2035-03-01T00:00:00"),
                )
            conn.commit()
        finally:
            conn.close()

        deleted = client.post(
            "/api/bank/statements/batch-delete",
            json={"ids": [id_one, id_two, "bs-not-exists"]},
            headers=auth_headers,
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["deleted"] == 2

        remaining = client.get(
            "/api/bank/statements?statement_id=batch-del-source", headers=auth_headers
        ).json()["items"]
        assert [item["id"] for item in remaining] == [id_three]

        conn = sqlite3.connect(db_path)
        try:
            placeholders = ",".join("?" for _ in (id_one, id_two, id_three))
            match_ids = {row[0] for row in conn.execute(
                f"SELECT statement_id FROM bank_statement_matches WHERE statement_id IN ({placeholders})",
                (id_one, id_two, id_three)).fetchall()}
            source_keys = {row[0] for row in conn.execute(
                f"SELECT external_key FROM source_transactions WHERE source_type = 'bank' "
                f"AND external_key IN ({placeholders})",
                (id_one, id_two, id_three)).fetchall()}
        finally:
            conn.close()
        assert match_ids == {id_three}
        assert id_three in source_keys
        assert not {id_one, id_two} & (match_ids | source_keys)

        reimported = client.post("/api/bank/import-json", json={
            "statement_id": "batch-del-source", "rows": rows,
        }, headers=auth_headers)
        assert reimported.status_code == 200, reimported.text
        assert reimported.json()["imported"] == 2
        assert reimported.json()["skipped"] == 1

    def test_batch_delete_validates_ids(self, client, auth_headers):
        response = client.post("/api/bank/statements/batch-delete", json={"ids": []}, headers=auth_headers)
        assert response.status_code == 422
