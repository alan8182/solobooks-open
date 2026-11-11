# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""微信支付月度汇总导入的增值税价税分离测试。

月度汇总凭证此前按含税全额贷记 5001、从未计提增值税（与手动记账口径不一致），
回归口径：贷 5001 不含税 + 贷 2221026 应交增值税（小规模纳税人专用）。
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from services.vat_service import VAT_PAYABLE_ACCOUNT, VatConfig


def _summary(order="850.00", refund="0.00", fee="8.10", count="10") -> dict:
    return {
        "总交易单数": count,
        "订单总金额": order,
        "退款总金额": refund,
        "手续费总金额": fee,
    }


def _amounts_by_code(lines: list[dict]) -> dict:
    amounts: dict[str, Decimal] = {}
    for line in lines or []:
        code = str(line.get("accountCode") or "")
        amounts[code] = amounts.get(code, Decimal("0")) + Decimal(str(line.get("amount") or 0))
    return amounts


def _patch_fakes(monkeypatch) -> list:
    """隔离外部依赖：去重查询返回空、落库只捕获、VAT 配置用缺省小规模 1%。"""
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

    saved: list = []

    async def fake_save(entries, *, user_id, company_id, **kwargs):
        saved.extend(entries)
        return [{**entry, "id": f"entry-{idx}"} for idx, entry in enumerate(entries)]

    async def fake_vat_config(company_id):
        return VatConfig()

    monkeypatch.setattr(wechat_bill_service, "get_conn", fake_get_conn)
    monkeypatch.setattr(wechat_bill_service, "save_entries_bulk", fake_save)
    monkeypatch.setattr(wechat_bill_service, "get_vat_config_by_id", fake_vat_config)
    return saved


@pytest.mark.asyncio
async def test_summary_import_splits_vat(monkeypatch):
    saved = _patch_fakes(monkeypatch)
    from services import wechat_bill_service

    result = await wechat_bill_service.import_wechat_bill_summary(
        "c1", "u1", _summary(), occur_date="2026-09-27"
    )

    assert result["imported"] == 1, result
    entry = saved[0]
    debits = _amounts_by_code(entry["voucherLines"]["debit"])
    credits = _amounts_by_code(entry["voucherLines"]["credit"])
    # 借：净结算 841.90 + 手续费 8.10 = 850
    assert debits["1012001"] == Decimal("841.90")
    assert debits["5603002"] == Decimal("8.10")
    # 贷：不含税收入 841.58 + 增值税 8.42 = 850（850 / 1.01 = 841.58）
    assert credits["5001"] == Decimal("841.58")
    assert credits[VAT_PAYABLE_ACCOUNT] == Decimal("8.42")
    assert sum(debits.values()) == sum(credits.values())
    # payload 金额保持含税口径（/records 聚合与前端汇总都读它），税额字段随凭证分离
    assert entry["amount"] == Decimal("850.00")
    assert entry["vatSeparated"] is True
    assert entry["vatAmount"] == Decimal("8.42")
    assert entry["amountExcludingTax"] == Decimal("841.58")


@pytest.mark.asyncio
async def test_summary_import_refund_heavier_than_orders_books_red_vat(monkeypatch):
    """退款大于订单：含税净收入为负，收入与税额同为红字记借方，借贷仍平衡。"""
    saved = _patch_fakes(monkeypatch)
    from services import wechat_bill_service

    result = await wechat_bill_service.import_wechat_bill_summary(
        "c1", "u1", _summary(order="100.00", refund="150.00", fee="1.00"), occur_date="2026-09-27"
    )

    assert result["imported"] == 1, result
    entry = saved[0]
    debits = _amounts_by_code(entry["voucherLines"]["debit"])
    credits = _amounts_by_code(entry["voucherLines"]["credit"])
    assert debits["5001"] == Decimal("49.50")
    assert debits[VAT_PAYABLE_ACCOUNT] == Decimal("0.50")
    assert debits["5603002"] == Decimal("1.00")
    assert credits["1012001"] == Decimal("51.00")
    assert sum(debits.values()) == sum(credits.values())
    assert entry["amount"] == Decimal("-50.00")
    assert entry["vatAmount"] == Decimal("-0.50")
    assert entry["amountExcludingTax"] == Decimal("-49.50")


@pytest.mark.asyncio
async def test_summary_import_keeps_gross_for_non_taxable_revenue(monkeypatch):
    """收入科目非增值税应税销售（营业外收入）时按含税全额入账、不计提。"""
    saved = _patch_fakes(monkeypatch)
    from services import wechat_bill_service

    result = await wechat_bill_service.import_wechat_bill_summary(
        "c1", "u1", _summary(), occur_date="2026-09-27",
        revenue_code="5301", revenue_name="营业外收入",
    )

    assert result["imported"] == 1, result
    entry = saved[0]
    credits = _amounts_by_code(entry["voucherLines"]["credit"])
    assert credits["5301"] == Decimal("850.00")
    assert VAT_PAYABLE_ACCOUNT not in credits
    assert "vatSeparated" not in entry


@pytest.mark.asyncio
async def test_summary_import_duplicate_month_ignored(monkeypatch):
    from services import wechat_bill_service

    _patch_fakes(monkeypatch)

    async def fake_existing_keys(company_id):
        return {"monthly_summary:2026-09"}

    monkeypatch.setattr(
        wechat_bill_service, "_load_existing_wechat_transaction_keys", fake_existing_keys
    )

    result = await wechat_bill_service.import_wechat_bill_summary(
        "c1", "u1", _summary(), occur_date="2026-09-27"
    )

    assert result["imported"] == 0
    assert result["ignored_duplicates"] == 1
    assert result["results"][0]["status"] == "duplicate"


def test_summary_import_http_persists_vat_separation(client, auth_headers):
    """端到端：HTTP 导入月度汇总，落库凭证含 2221026 行且 vatSeparated 持久化。"""
    res = client.post("/api/wechat-bill/import", json={
        "importMode": "summary",
        "summary": _summary(order="85.00", refund="0.00", fee="0.81", count="1"),
        "occurDate": "2031-03-15",
    }, headers=auth_headers)
    assert res.status_code == 200, res.text
    assert res.json()["imported"] == 1

    body = client.get("/api/record/entries", headers=auth_headers).json()
    summary_entry = next(e for e in body["entries"] if e.get("is_monthly_summary"))
    debits = _amounts_by_code(summary_entry["voucherLines"]["debit"])
    credits = _amounts_by_code(summary_entry["voucherLines"]["credit"])
    assert debits["1012001"] == Decimal("84.19")
    assert debits["5603002"] == Decimal("0.81")
    assert credits["5001"] == Decimal("84.16")  # 85 / 1.01
    assert credits[VAT_PAYABLE_ACCOUNT] == Decimal("0.84")
    assert sum(debits.values()) == sum(credits.values())
    assert summary_entry["vatSeparated"] is True
