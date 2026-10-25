# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""发票主数据（P0 WP1）：导入双写、去重换表、台账查询、存量回填。

锁定三条口径：
1. 发票导入成功后 invoices 表有行且 entry_id 关联凭证；
2. 同一张发票重导被拦（发票号路径 + 无号指纹路径各一条）；
3. 存量 payload 回填幂等，重跑不重复建行。
"""
from __future__ import annotations

import json

import pytest

from services.accounting_core_service import ensure_tenant
from services.invoice_ledger_service import (
    backfill_invoices,
    invoice_summary,
    list_invoices,
)
from services.invoice_service import import_invoices


async def _ensure_tables() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _setup_company(company_id: str) -> None:
    from db.session import session_scope

    await _ensure_tables()
    async with session_scope() as session:
        await ensure_tenant(session, company_id, "owner-1")


def _expense_row(**overrides):
    row = {
        "invoice_number": "INV-0001",
        "invoice_date": "2026-10-08",
        "total_amount": "106.00",
        "tax_amount": "6.00",
        "seller_name": "杭州甲科技有限公司",
        "buyer_name": "默认账本",
        "direction": "expense",
        "item_name": "办公用品",
        "tax_rate": "6%",
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_import_writes_invoice_ledger_and_partner():
    company_id = "c-inv-write"
    await _setup_company(company_id)

    result = await import_invoices(company_id, "owner-1", [_expense_row()], classify=False)
    assert result["imported"] == 1
    assert result["invoicesWritten"] == 1
    assert result["partnersLinked"] == 1

    listing = await list_invoices(company_id)
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["invoiceNumber"] == "INV-0001"
    assert item["direction"] == "expense"
    assert item["entryId"]
    assert item["partnerId"]
    # 导入的发票凭证是 draft 待审，尚未分配凭证号；台账要能跳转到该凭证
    assert item["voucherRef"] == ""

    # 往来单位自动建档，且挂账分录（2241）带上 partner_id
    from sqlalchemy import select

    from db.models import JournalLine, Partner
    from db.session import session_scope

    async with session_scope() as session:
        partner = (
            await session.execute(select(Partner).where(Partner.company_id == company_id))
        ).scalar_one()
        assert partner.name == "杭州甲科技有限公司"
        lines = (
            await session.execute(
                select(JournalLine).where(
                    JournalLine.company_id == company_id,
                    JournalLine.account_code == "2241",
                )
            )
        ).scalars().all()
        assert lines and lines[0].auxiliary.get("partner_id") == partner.id
        assert lines[0].auxiliary.get("partner_name") == partner.name


@pytest.mark.asyncio
async def test_reimport_is_blocked_by_invoice_number():
    company_id = "c-inv-dedup"
    await _setup_company(company_id)

    first = await import_invoices(company_id, "owner-1", [_expense_row()], classify=False)
    assert first["imported"] == 1

    second = await import_invoices(company_id, "owner-1", [_expense_row()], classify=False)
    assert second["imported"] == 0
    assert second["ignored_duplicates"] == 1
    assert (await list_invoices(company_id))["total"] == 1


@pytest.mark.asyncio
async def test_reimport_without_invoice_number_uses_fingerprint():
    company_id = "c-inv-fingerprint"
    await _setup_company(company_id)
    row = _expense_row(invoice_number="", total_amount="88.00", tax_amount="0")

    first = await import_invoices(company_id, "owner-1", [row], classify=False)
    assert first["imported"] == 1
    second = await import_invoices(company_id, "owner-1", [row], classify=False)
    assert second["ignored_duplicates"] == 1
    assert (await list_invoices(company_id))["total"] == 1


@pytest.mark.asyncio
async def test_list_filters_and_summary():
    company_id = "c-inv-query"
    await _setup_company(company_id)
    await import_invoices(
        company_id,
        "owner-1",
        [
            _expense_row(invoice_number="INV-A", invoice_date="2026-10-08", seller_name="甲公司"),
            _expense_row(
                invoice_number="INV-B",
                invoice_date="2026-09-20",
                seller_name="乙公司",
                total_amount="212.00",
                tax_amount="12.00",
            ),
        ],
        classify=False,
    )

    october = await list_invoices(company_id, month_from="2026-10", month_to="2026-10")
    assert october["total"] == 1
    assert october["items"][0]["invoiceNumber"] == "INV-A"

    keyword = await list_invoices(company_id, keyword="乙公司")
    assert keyword["total"] == 1

    summary = await invoice_summary(company_id, month_from="2026-09", month_to="2026-10")
    months = {item["month"]: item for item in summary["months"]}
    assert set(months) == {"2026-09", "2026-10"}
    assert months["2026-10"]["expenseCount"] == 1
    assert float(summary["totals"]["expenseAmount"]) == pytest.approx(318.0)


@pytest.mark.asyncio
async def test_backfill_is_idempotent():
    """存量 payload 回填：首次建行，重跑不重复。"""
    from db.models import LegacyEntryProjection
    from db.session import session_scope

    company_id = "c-inv-backfill"
    await _setup_company(company_id)
    payload = {
        "id": "legacy-entry-1",
        "title": "旧发票",
        "amount": "53.00",
        "direction": "expense",
        "source": "invoice_import",
        "invoiceNumber": "OLD-777",
        "occurDate": "2026-03-05",
        "seller_name": "老供应商",
        "tax_amount": "3.00",
    }
    async with session_scope() as session:
        session.add(
            LegacyEntryProjection(
                id="legacy-entry-1",
                user_id="owner-1",
                company_id=company_id,
                payload=json.dumps(payload, ensure_ascii=False),
                created_at="2026-03-05T00:00:00+00:00",
                updated_at="2026-03-05T00:00:00+00:00",
            )
        )
        await session.flush()

    first = await backfill_invoices(company_id)
    assert first["created"] == 1
    second = await backfill_invoices(company_id)
    assert second["created"] == 0
    assert (await list_invoices(company_id))["total"] == 1
