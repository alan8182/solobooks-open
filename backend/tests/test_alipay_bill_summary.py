# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""支付宝按账单期间汇总：平台补贴不计收入、退款负数、期间幂等与重叠拦截。"""
from __future__ import annotations

from decimal import Decimal

import pytest

from services.vat_service import VAT_PAYABLE_ACCOUNT, VatConfig


def _amounts_by_code(lines: list[dict]) -> dict[str, Decimal]:
    amounts: dict[str, Decimal] = {}
    for line in lines or []:
        code = str(line.get("accountCode") or "")
        amounts[code] = amounts.get(code, Decimal("0")) + Decimal(str(line.get("amount") or 0))
    return amounts


def _patch(monkeypatch, *, existing_keys=(), existing_periods=(), has_details: bool = False):
    from services import alipay_bill_service

    async def fake_existing(company_id):
        return set(existing_keys)

    async def fake_periods(company_id):
        return list(existing_periods)

    async def fake_has_detail(company_id, start, end):
        return has_details

    async def fake_vat_config(company_id):
        return VatConfig()

    saved: list = []

    async def fake_save(entries, *, user_id, company_id, existing_entry_updates=None, **kwargs):
        saved.extend(entries)
        return entries

    monkeypatch.setattr(alipay_bill_service, "_load_existing_alipay_keys", fake_existing)
    monkeypatch.setattr(alipay_bill_service, "_load_existing_alipay_summary_periods", fake_periods)
    monkeypatch.setattr(alipay_bill_service, "_has_alipay_detail_in_period", fake_has_detail)
    monkeypatch.setattr(alipay_bill_service, "get_vat_config_by_id", fake_vat_config)
    monkeypatch.setattr(alipay_bill_service, "save_entries_bulk", fake_save)
    return saved


@pytest.mark.asyncio
async def test_summary_splits_subsidy_to_non_operating_income(monkeypatch):
    saved = _patch(monkeypatch)
    from services import alipay_bill_service

    result = await alipay_bill_service.import_alipay_bill_summary("c1", "u1", {
        "periodStart": "2026-11-18",
        "periodEnd": "2026-11-18",
        "receivedAmount": "598.00",
        "subsidyAmount": "0.52",
        "feeAmount": "3.59",
        "totalCount": 1,
    })

    assert result["imported"] == 1, result
    entry = saved[0]
    debits = _amounts_by_code(entry["voucherLines"]["debit"])
    credits = _amounts_by_code(entry["voucherLines"]["credit"])

    assert debits["1012002"] == Decimal("594.41")
    assert debits["5603002"] == Decimal("3.59")
    assert credits["5001"] == Decimal("591.56")       # 597.48 / 1.01
    assert credits[VAT_PAYABLE_ACCOUNT] == Decimal("5.92")
    assert credits["5301"] == Decimal("0.52")         # 平台补贴不进主营业务收入
    assert sum(debits.values()) == sum(credits.values())

    assert entry["source"] == "alipay_bill"
    assert entry["platform"] == "alipay"
    assert entry["dedupFingerprint"] == "monthly_summary:2026-11-18:2026-11-18"
    assert entry["platformSubsidy"] == Decimal("0.52")
    assert entry["receivedAmount"] == Decimal("598.00")
    assert entry["feeAmount"] == Decimal("3.59")
    assert entry["occurDate"] == "2026-11-18"


@pytest.mark.asyncio
async def test_summary_nets_refund_and_fee(monkeypatch):
    saved = _patch(monkeypatch)
    from services import alipay_bill_service

    result = await alipay_bill_service.import_alipay_bill_summary("c1", "u1", {
        "periodStart": "2026-11-19",
        "periodEnd": "2026-11-19",
        "receivedAmount": "5.00",
        "subsidyAmount": "0.00",
        "feeAmount": "0.06",
        "totalCount": 3,
    })

    assert result["imported"] == 1, result
    entry = saved[0]
    debits = _amounts_by_code(entry["voucherLines"]["debit"])
    credits = _amounts_by_code(entry["voucherLines"]["credit"])

    assert debits["1012002"] == Decimal("4.94")
    assert debits["5603002"] == Decimal("0.06")
    assert credits["5001"] == Decimal("4.95")
    assert credits[VAT_PAYABLE_ACCOUNT] == Decimal("0.05")
    assert "5301" not in credits  # 退款把补贴冲平
    assert sum(debits.values()) == sum(credits.values())
    assert entry["amount"] == Decimal("5.00")


@pytest.mark.asyncio
async def test_summary_same_period_is_idempotent(monkeypatch):
    _patch(monkeypatch, existing_keys={"monthly_summary:2026-11-18:2026-11-18"})
    from services import alipay_bill_service

    result = await alipay_bill_service.import_alipay_bill_summary("c1", "u1", {
        "periodStart": "2026-11-18",
        "periodEnd": "2026-11-18",
        "receivedAmount": "598.00",
        "subsidyAmount": "0.52",
        "feeAmount": "3.59",
        "totalCount": 1,
    })

    assert result["imported"] == 0
    assert result["ignored_duplicates"] == 1
    assert result["results"][0]["status"] == "duplicate"


@pytest.mark.asyncio
async def test_summary_overlapping_period_is_blocked(monkeypatch):
    _patch(monkeypatch, existing_periods=[("2026-11-01", "2026-11-30")])
    from services import alipay_bill_service

    result = await alipay_bill_service.import_alipay_bill_summary("c1", "u1", {
        "periodStart": "2026-11-18",
        "periodEnd": "2026-11-20",
        "receivedAmount": "5.00",
        "subsidyAmount": "0.00",
        "feeAmount": "0.03",
        "totalCount": 1,
    })

    assert result["imported"] == 0
    assert result["failed"] == 1
    assert "重叠" in result["results"][0]["error"]


@pytest.mark.asyncio
async def test_summary_is_blocked_when_detail_already_imported(monkeypatch):
    """同一期间已逐笔入账时，再按汇总导入会重复计收入，必须拦住。"""
    saved = _patch(monkeypatch, has_details=True)
    from services import alipay_bill_service

    result = await alipay_bill_service.import_alipay_bill_summary("c1", "u1", {
        "periodStart": "2026-11-19",
        "periodEnd": "2026-11-19",
        "receivedAmount": "5.00",
        "subsidyAmount": "0.00",
        "feeAmount": "0.03",
        "totalCount": 1,
    })

    assert result["imported"] == 0
    assert result["failed"] == 1
    assert "逐笔明细" in result["results"][0]["error"]
    assert saved == []


@pytest.mark.asyncio
async def test_detail_is_blocked_when_summary_already_imported(monkeypatch):
    """反向同理：汇总凭证已存在时，明细导入不能把同一批交易再记一遍。"""
    saved = _patch(monkeypatch, existing_periods=[("2026-11-19", "2026-11-19")])
    from services import alipay_bill_service

    async def no_history(company_id, order_nos):
        return {}

    monkeypatch.setattr(alipay_bill_service, "_load_existing_alipay_order_history", no_history)

    result = await alipay_bill_service.import_alipay_bill("c1", "u1", [{
        "occurred_on": "2026-11-19",
        "dedupFingerprint": "success:2026111922001455691459783864",
        "platform_order_no": "2026111922001455691459783864",
    }])

    assert result["imported"] == 0
    assert result["failed"] == 1
    assert "已按汇总入账" in result["results"][0]["error"]
    assert saved == []


@pytest.mark.asyncio
async def test_summary_daily_files_can_coexist(monkeypatch):
    """按账单期间汇总：11-18 与 11-19 两份日账单都能入账，不互相判重。"""
    saved = _patch(monkeypatch, existing_periods=[("2026-11-18", "2026-11-18")])
    from services import alipay_bill_service

    result = await alipay_bill_service.import_alipay_bill_summary("c1", "u1", {
        "periodStart": "2026-11-19",
        "periodEnd": "2026-11-19",
        "receivedAmount": "5.00",
        "subsidyAmount": "0.00",
        "feeAmount": "0.06",
        "totalCount": 3,
    })

    assert result["imported"] == 1, result
    assert saved[0]["dedupFingerprint"] == "monthly_summary:2026-11-19:2026-11-19"


@pytest.mark.asyncio
async def test_summary_period_loader_and_detail_guard_use_real_sql():
    """守卫谓词的真实 SQL 行为（不用 mock）：汇总行进期间清单、不算逐笔；逐笔行反之。

    2026-11-20 的 PG 事故里，谓词是 COALESCE+整数（500）与 = 1（永不命中），
    SQLite 上都「看起来正常」。这里与 test_postgres_compat 的方言断言配合：
    即使有人改回整数字面量比较，SQLite 行为测试也会因为语义变化而暴露。
    """
    import json as _json

    from services import entry_service
    from services.alipay_bill_service import (
        _has_alipay_detail_in_period,
        _load_existing_alipay_summary_periods,
    )
    from services.database import get_conn

    company_id = "test_ali_guard_sql"
    await entry_service._ensure_schema()

    async def _insert(entry_id: str, payload: dict) -> None:
        conn = await get_conn()
        try:
            await conn.execute(
                "INSERT INTO entries (id, user_id, company_id, payload, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (entry_id, "u-ali-guard", company_id, _json.dumps(payload, ensure_ascii=False),
                 "2026-11-20T00:00:00+00:00", "2026-11-20T00:00:00+00:00"),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def _cleanup() -> None:
        conn = await get_conn()
        try:
            await conn.execute("DELETE FROM entries WHERE company_id = ?", (company_id,))
            await conn.commit()
        finally:
            await conn.close()

    await _cleanup()
    try:
        await _insert(f"{company_id}-summary", {
            "source": "alipay_bill", "is_monthly_summary": True,
            "billPeriodStart": "2026-11-18", "billPeriodEnd": "2026-11-18",
            "occurDate": "2026-11-18",
        })
        await _insert(f"{company_id}-detail", {
            "source": "alipay_bill", "occurDate": "2026-11-19", "amount": "5.00",
        })

        periods = await _load_existing_alipay_summary_periods(company_id)
        assert ("2026-11-18", "2026-11-18") in periods, periods

        # 汇总行不能被当成逐笔明细（否则「先汇总后明细」会被误拦）
        assert await _has_alipay_detail_in_period(company_id, "2026-11-18", "2026-11-18") is False
        # 逐笔行要能被看到（「先明细后汇总」仍要拦）
        assert await _has_alipay_detail_in_period(company_id, "2026-11-19", "2026-11-19") is True
    finally:
        await _cleanup()
