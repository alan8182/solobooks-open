# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""账簿签发与留痕测试。

要点：
1. 未结账不许签发（否则盖章装订的是会变的数字）；
2. 签发后重打，内容与 hash 必须与当时一致；
3. 作废不删册，旧内容仍可取回。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from services.accounting_core_service import create_entry, ensure_tenant
from services.ledger_book_snapshot_service import (
    SnapshotError,
    _content_hash,
    get_snapshot,
    issue_snapshot,
    list_snapshots,
    void_snapshot,
)


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


async def _post_entry(company_id: str) -> None:
    from db.session import session_scope

    async with session_scope() as session:
        await create_entry(
            session,
            {
                "id": f"{company_id}-entry",
                "direction": "expense",
                "amount": "800.00",
                "title": "支付房租",
                "category": "租赁费",
                "occurDate": "2026-10-03",
                "voucherLines": {
                    "debit": [{"accountCode": "5602013", "accountName": "管理费用-租赁费", "amount": "800.00"}],
                    "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "800.00"}],
                },
            },
            company_id=company_id,
            user_id="owner-1",
            request_id=f"{company_id}-req",
            confirm=True,
        )


async def _set_period_status(company_id: str, month: str, status: str) -> None:
    from db.models import AccountingPeriod
    from db.session import session_scope

    year, month_number = (int(part) for part in month.split("-"))
    async with session_scope() as session:
        existing = await session.scalar(
            select(AccountingPeriod).where(
                AccountingPeriod.company_id == company_id,
                AccountingPeriod.period_start == date(year, month_number, 1),
            )
        )
        if existing is None:
            session.add(
                AccountingPeriod(
                    company_id=company_id,
                    period_start=date(year, month_number, 1),
                    status=status,
                )
            )
        else:
            existing.status = status


@pytest.mark.asyncio
async def test_issue_requires_closed_period():
    company_id = "c-snap-open"
    await _setup_company(company_id)
    await _post_entry(company_id)

    with pytest.raises(SnapshotError) as exc:
        await issue_snapshot(company_id, month="2026-10", user_id="owner-1")
    assert exc.value.code == "PERIOD_NOT_CLOSED"
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_issue_numbers_books_and_keeps_payload():
    company_id = "c-snap-issue"
    await _setup_company(company_id)
    await _post_entry(company_id)
    await _set_period_status(company_id, "2026-10", "closed")

    first = await issue_snapshot(company_id, month="2026-10", user_id="owner-1")
    assert first["bookNo"] == "2026-10-0001"
    assert first["status"] == "issued"
    assert len(first["contentHash"]) == 64
    payload = first["payload"]
    assert payload["meta"]["bookNo"] == "2026-10-0001"
    assert payload["meta"]["contentHash"] == first["contentHash"]
    assert payload["meta"]["pageCount"] == first["pageCount"]
    assert payload["pagination"]["totalPages"] == first["pageCount"]

    # 实时册与签发册必须同指纹，否则前端永远判"已过期"
    from services.ledger_book_service import build_ledger_book

    live = await build_ledger_book(company_id, month="2026-10")
    assert live["meta"]["contentHash"] == first["contentHash"]
    assert live["meta"]["pageCount"] == first["pageCount"]

    # 重打：内容与 hash 必须与签发时一致
    restored = await get_snapshot(company_id, first["id"])
    assert _content_hash(restored["payload"]) == first["contentHash"]
    assert restored["payload"]["balance"]["totalDebit"] == payload["balance"]["totalDebit"]

    # 再签一册：编号递增，旧册不动
    second = await issue_snapshot(company_id, month="2026-10", user_id="owner-1")
    assert second["bookNo"] == "2026-10-0002"
    old = await get_snapshot(company_id, first["id"])
    assert old["bookNo"] == "2026-10-0001"
    assert old["status"] == "issued"


@pytest.mark.asyncio
async def test_snapshot_hash_changes_when_entries_change():
    """反结账后又补记：新册 hash 与旧册不同，旧册内容不变。"""
    company_id = "c-snap-drift"
    await _setup_company(company_id)
    await _post_entry(company_id)
    await _set_period_status(company_id, "2026-10", "closed")
    first = await issue_snapshot(company_id, month="2026-10", user_id="owner-1")

    await _set_period_status(company_id, "2026-10", "open")
    # 反结账后补记一张凭证（绕过 create_entry 的补记拦截，只测签发侧）
    from db.models import ChartAccount, JournalEntry, JournalLine
    from db.session import session_scope

    async with session_scope() as session:
        chart = (
            await session.execute(
                select(ChartAccount).where(
                    ChartAccount.company_id == company_id,
                    ChartAccount.code.in_(("1002", "5602013")),
                )
            )
        ).scalars().all()
        by_code = {row.code: row for row in chart}
        entry = JournalEntry(
            company_id=company_id,
            voucher_no="记-0099",
            occurred_on=date(2026, 10, 9),
            period_start=date(2026, 10, 1),
            summary="补记办公费",
            status="posted",
            created_by="owner-1",
        )
        session.add(entry)
        await session.flush()
        session.add_all(
            [
                JournalLine(
                    journal_entry_id=entry.id,
                    company_id=company_id,
                    line_no=1,
                    account_id=by_code["5602013"].id,
                    account_code="5602013",
                    account_name="管理费用-租赁费",
                    direction="debit",
                    amount=Decimal("120.00"),
                ),
                JournalLine(
                    journal_entry_id=entry.id,
                    company_id=company_id,
                    line_no=2,
                    account_id=by_code["1002"].id,
                    account_code="1002",
                    account_name="银行存款",
                    direction="credit",
                    amount=Decimal("120.00"),
                ),
            ]
        )
    await _set_period_status(company_id, "2026-10", "closed")

    second = await issue_snapshot(company_id, month="2026-10", user_id="owner-1")
    assert second["contentHash"] != first["contentHash"]

    old = await get_snapshot(company_id, first["id"])
    assert _content_hash(old["payload"]) == first["contentHash"]
    assert Decimal(old["payload"]["balance"]["totalDebit"]) == Decimal("800.00")
    assert Decimal(second["payload"]["balance"]["totalDebit"]) == Decimal("920.00")


@pytest.mark.asyncio
async def test_void_keeps_content_and_requires_reason():
    company_id = "c-snap-void"
    await _setup_company(company_id)
    await _post_entry(company_id)
    await _set_period_status(company_id, "2026-10", "closed")
    issued = await issue_snapshot(company_id, month="2026-10", user_id="owner-1")

    with pytest.raises(SnapshotError):
        await void_snapshot(company_id, issued["id"], reason="   ", user_id="owner-1")

    voided = await void_snapshot(company_id, issued["id"], reason="科目用错，重新签发", user_id="owner-1")
    assert voided["status"] == "void"
    assert voided["voidReason"] == "科目用错，重新签发"

    still_there = await get_snapshot(company_id, issued["id"])
    assert still_there["payload"]["meta"]["bookNo"] == "2026-10-0001"


@pytest.mark.asyncio
async def test_list_snapshots_filters_by_period():
    company_id = "c-snap-list"
    await _setup_company(company_id)
    await _post_entry(company_id)
    await _set_period_status(company_id, "2026-10", "closed")
    await _set_period_status(company_id, "2026-09", "closed")
    await issue_snapshot(company_id, month="2026-10", user_id="owner-1")
    await issue_snapshot(company_id, month="2026-09", user_id="owner-1")

    all_items = await list_snapshots(company_id)
    assert len(all_items) == 2
    october = await list_snapshots(company_id, period="2026-10")
    assert [item["bookNo"] for item in october] == ["2026-10-0001"]
    assert "payload" not in october[0], "列表不应带 payload（体积大）"


@pytest.mark.asyncio
async def test_diff_two_books_reports_account_and_voucher_changes():
    """补记后重签：差异要指出哪些科目变了、变了多少、多出哪几张凭证。"""
    from services.ledger_book_snapshot_service import SnapshotError, diff_snapshots

    company_id = "c-snap-diff"
    await _setup_company(company_id)
    await _post_entry(company_id)
    await _set_period_status(company_id, "2026-10", "closed")
    first = await issue_snapshot(company_id, month="2026-10", user_id="owner-1")

    same = await diff_snapshots(company_id, first["id"], first["id"])
    assert same["hashEqual"] is True
    assert same["balance"] == []
    assert same["vouchersAdded"] == [] and same["vouchersRemoved"] == []

    # 反结账补记一张 120 元办公费，再结账重签
    await _set_period_status(company_id, "2026-10", "open")
    from db.models import ChartAccount, JournalEntry, JournalLine
    from db.session import session_scope

    async with session_scope() as session:
        chart = (
            await session.execute(
                select(ChartAccount).where(
                    ChartAccount.company_id == company_id,
                    ChartAccount.code.in_(("1002", "5602013")),
                )
            )
        ).scalars().all()
        by_code = {row.code: row for row in chart}
        entry = JournalEntry(
            company_id=company_id,
            voucher_no="记-0099",
            occurred_on=date(2026, 10, 9),
            period_start=date(2026, 10, 1),
            summary="补记办公费",
            status="posted",
            created_by="owner-1",
        )
        session.add(entry)
        await session.flush()
        session.add_all(
            [
                JournalLine(
                    journal_entry_id=entry.id,
                    company_id=company_id,
                    line_no=1,
                    account_id=by_code["5602013"].id,
                    account_code="5602013",
                    account_name="管理费用-租赁费",
                    direction="debit",
                    amount=Decimal("120.00"),
                ),
                JournalLine(
                    journal_entry_id=entry.id,
                    company_id=company_id,
                    line_no=2,
                    account_id=by_code["1002"].id,
                    account_code="1002",
                    account_name="银行存款",
                    direction="credit",
                    amount=Decimal("120.00"),
                ),
            ]
        )
    await _set_period_status(company_id, "2026-10", "closed")
    second = await issue_snapshot(company_id, month="2026-10", user_id="owner-1")

    diff = await diff_snapshots(company_id, second["id"], first["id"])
    assert diff["hashEqual"] is False
    assert diff["base"]["bookNo"] == first["bookNo"]
    assert diff["target"]["bookNo"] == second["bookNo"]
    assert diff["vouchersAdded"] == ["记-0099"]
    assert diff["vouchersRemoved"] == []

    changed = {row["accountCode"]: row for row in diff["balance"]}
    assert set(changed) == {"1002", "5602013"}
    assert Decimal(changed["5602013"]["debitDelta"]) == Decimal("120.00")
    assert Decimal(changed["5602013"]["closingDelta"]) == Decimal("120.00")
    assert Decimal(changed["1002"]["creditDelta"]) == Decimal("120.00")
    assert Decimal(changed["1002"]["closingDelta"]) == Decimal("-120.00")

    with pytest.raises(SnapshotError) as exc:
        await diff_snapshots(company_id, second["id"], "does-not-exist")
    assert exc.value.code == "SNAPSHOT_NOT_FOUND"
