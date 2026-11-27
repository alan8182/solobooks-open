# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""凭证册签发定版（ledger_book_snapshot_service 的 voucher kind 分支）。

锁定：关账拦截、册号跨 kind 隔离（同月账簿册与凭证册各自从 0001 递增）、
签发即归档（PDF+JSON 同事务）、审计事件 issue_voucher_book、作废幂等、
下载 sha256 409 与冻结 payload 只读重渲染（voucher 渲染器分发）。
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from services.accounting_core_service import create_entry, ensure_tenant
from services.ledger_book_snapshot_service import (
    SnapshotError,
    get_snapshot,
    issue_snapshot,
    issue_voucher_book_snapshot,
    list_snapshots,
    render_snapshot_pdf,
    void_snapshot,
)

month = "2026-10"


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


async def _post_entry(company_id: str, *, occur_date: str = "2026-10-03") -> None:
    from db.session import session_scope

    async with session_scope() as session:
        await create_entry(
            session,
            {
                "id": f"{company_id}-entry-{occur_date}",
                "direction": "expense",
                "amount": "800.00",
                "title": "支付房租",
                "category": "租赁费",
                "occurDate": occur_date,
                "voucherLines": {
                    "debit": [{"accountCode": "5602013", "accountName": "管理费用-租赁费", "amount": "800.00"}],
                    "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "800.00"}],
                },
            },
            company_id=company_id,
            user_id="owner-1",
            request_id=f"{company_id}-req-{occur_date}",
            confirm=True,
        )


async def _set_period_closed(company_id: str, period: str = month) -> None:
    from db.models import AccountingPeriod
    from db.session import session_scope

    year, month_number = (int(part) for part in period.split("-"))
    async with session_scope() as session:
        existing = await session.scalar(
            select(AccountingPeriod).where(
                AccountingPeriod.company_id == company_id,
                AccountingPeriod.period_start == date(year, month_number, 1),
            )
        )
        if existing is None:
            session.add(AccountingPeriod(company_id=company_id, period_start=date(year, month_number, 1), status="closed"))
        else:
            existing.status = "closed"


async def _audit_count(company_id: str, action: str) -> int:
    from db.models import AuditEvent
    from db.session import session_scope

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.company_id == company_id,
                    AuditEvent.action == action,
                )
            )
        ).scalars().all()
    return len(rows)


@pytest.mark.asyncio
async def test_issue_voucher_book_requires_closed_period():
    company_id = "c-vbook-open"
    await _setup_company(company_id)
    await _post_entry(company_id)
    with pytest.raises(SnapshotError) as exc_info:
        await issue_voucher_book_snapshot(company_id, month=month, user_id="owner-1")
    assert exc_info.value.code == "PERIOD_NOT_CLOSED"


@pytest.mark.asyncio
async def test_issue_voucher_book_persists_snapshot_and_audit():
    company_id = "c-vbook-issue"
    await _setup_company(company_id)
    await _post_entry(company_id)
    await _set_period_closed(company_id)

    issued = await issue_voucher_book_snapshot(company_id, month=month, user_id="owner-1")
    assert issued["bookKind"] == "voucher"
    assert issued["bookNo"] == f"{month}-0001"
    assert issued["hasPdf"] is True
    assert issued["payload"]["meta"]["bookNo"] == issued["bookNo"]
    assert issued["payload"]["meta"]["voucherCount"] == 1
    assert await _audit_count(company_id, "issue_voucher_book") == 1

    # 列表按 kind 隔离：凭证册列表只含 voucher；账簿列表不含 voucher
    voucher_items = await list_snapshots(company_id, book_kind="voucher")
    assert [item["id"] for item in voucher_items] == [issued["id"]]
    ledger_items = await list_snapshots(company_id, include_kinds=("monthly", "yearly"))
    assert all(item["bookKind"] != "voucher" for item in ledger_items)

    # 详情（屏幕预览数据源）
    detail = await get_snapshot(company_id, issued["id"])
    assert detail["payload"]["vouchers"][0]["title"] == "支付房租"


@pytest.mark.asyncio
async def test_voucher_book_no_isolated_from_ledger_book_no():
    """同月账簿册与凭证册册号各自递增（book_kind 是册号唯一键的一部分）。"""
    company_id = "c-vbook-number"
    await _setup_company(company_id)
    await _post_entry(company_id)
    await _set_period_closed(company_id)

    ledger_book = await issue_snapshot(company_id, month=month, user_id="owner-1")
    voucher_book = await issue_voucher_book_snapshot(company_id, month=month, user_id="owner-1")
    assert ledger_book["bookNo"] == f"{month}-0001"
    assert voucher_book["bookNo"] == f"{month}-0001"
    # 重签发：凭证册号 +1，账簿册不受影响
    again = await issue_voucher_book_snapshot(company_id, month=month, user_id="owner-1")
    assert again["bookNo"] == f"{month}-0002"
    assert again["contentHash"] == voucher_book["contentHash"]


@pytest.mark.asyncio
async def test_download_prefers_archive_and_409_on_corruption(monkeypatch):
    company_id = "c-vbook-download"
    await _setup_company(company_id)
    await _post_entry(company_id)
    await _set_period_closed(company_id)
    issued = await issue_voucher_book_snapshot(company_id, month=month, user_id="owner-1")

    from pathlib import Path

    from db.models import LedgerBookSnapshot
    from db.session import session_scope

    async with session_scope() as session:
        row = await session.scalar(
            select(LedgerBookSnapshot).where(LedgerBookSnapshot.id == issued["id"])
        )
    archive_path = Path(row.pdf_path)
    assert archive_path.exists()

    rendered = await render_snapshot_pdf(company_id, issued["id"])
    assert rendered["archivedPdf"] is True
    assert rendered["pdfBytes"] == archive_path.read_bytes()
    assert rendered["fileName"].endswith(".pdf")

    # 篡改归档件 → sha256 409（不把损坏原件交给用户）
    archive_path.write_bytes(b"%PDF-tampered")
    with pytest.raises(SnapshotError) as exc_info:
        await render_snapshot_pdf(company_id, issued["id"])
    assert exc_info.value.status_code == 409

    # 归档件丢失 → 冻结 payload 只读现渲染（voucher 渲染器分发），不回写库
    archive_path.unlink()
    fallback = await render_snapshot_pdf(company_id, issued["id"])
    assert fallback["archivedPdf"] is False
    assert fallback["pageCount"] == issued["pageCount"]


@pytest.mark.asyncio
async def test_void_voucher_book_is_idempotent_with_audit():
    company_id = "c-vbook-void"
    await _setup_company(company_id)
    await _post_entry(company_id)
    await _set_period_closed(company_id)
    issued = await issue_voucher_book_snapshot(company_id, month=month, user_id="owner-1")

    voided = await void_snapshot(company_id, issued["id"], reason="重复装订", user_id="owner-1")
    assert voided["status"] == "void"
    assert voided["voidReason"] == "重复装订"
    again = await void_snapshot(company_id, issued["id"], reason="重复装订", user_id="owner-1")
    assert again["status"] == "void"
    # 作废后 PDF 仍可下载（已作废水印）
    rendered = await render_snapshot_pdf(company_id, issued["id"])
    assert rendered["status"] == "void"
    assert await _audit_count(company_id, "issue_voucher_book") == 1


async def test_preview_endpoint_does_not_persist_snapshot(client, auth_headers):
    """预览接口只读：返回分页 payload，且不创建任何快照行（阅读器预览件不受影响）。"""
    from sqlalchemy import func, select

    from db.models import LedgerBookSnapshot
    from db.session import session_scope

    period = "2026-07"

    async def _count() -> int:
        async with session_scope() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(LedgerBookSnapshot)
                    .where(
                        LedgerBookSnapshot.company_id == "c1",
                        LedgerBookSnapshot.book_kind == "voucher",
                        LedgerBookSnapshot.period == period,
                    )
                )
                or 0
            )

    before = await _count()
    resp = client.get("/api/voucher-book/preview", params={"month": period}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["signed"] is False
    assert body["payload"]["pagination"]["totalPages"] >= 3
    assert body["payload"]["meta"]["month"] == period
    assert await _count() == before
