# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""电子账簿 PDF 归档：签发即落盘、可校验、可重下。

锁定四条口径：
1. 签发成功必须同时有 PDF 归档件（路径 + sha256 + 大小）；
2. 下载优先读归档件，返回字节与签发时逐字节一致；
3. 归档件丢失时只读现渲染，不借下载动作回写数据库（A2）；
4. PDF 页数必须等于后端分页真值，否则拒绝归档（A3）。
"""
from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from services.accounting_core_service import create_entry, ensure_tenant
from services.ledger_book_pdf_service import PdfPageCountMismatch, render_ledger_book_pdf
from services.ledger_book_service import _paginate
from services.backup_archive_service import (
    build_ledger_pdf_archive,
    get_record,
    list_records,
)
from services.ledger_book_snapshot_service import (
    find_snapshot_pdf_sha_by_path,
    issue_snapshot,
    render_snapshot_pdf,
    void_snapshot,
)


async def _ensure_tables() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _setup_company(company_id: str, user_id: str = "owner-1") -> None:
    from db.session import session_scope

    await _ensure_tables()
    async with session_scope() as session:
        await ensure_tenant(session, company_id, user_id)


async def _post_entry(
    company_id: str,
    *,
    occur_date: str = "2026-10-03",
    entry_id: str | None = None,
    user_id: str = "owner-1",
) -> None:
    from db.session import session_scope

    async with session_scope() as session:
        await create_entry(
            session,
            {
                "id": entry_id or f"{company_id}-entry",
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
            user_id=user_id,
            request_id=f"{company_id}-req",
            confirm=True,
        )


async def _set_period_closed(company_id: str, month: str) -> None:
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
                    status="closed",
                )
            )
        else:
            existing.status = "closed"


async def _issued(company_id: str, month: str = "2026-10") -> dict:
    await _setup_company(company_id)
    await _post_entry(company_id)
    await _set_period_closed(company_id, month)
    return await issue_snapshot(company_id, month=month, user_id="owner-1")


async def _row(company_id: str, snapshot_id: str):
    from db.models import LedgerBookSnapshot
    from db.session import session_scope

    async with session_scope() as session:
        return await session.scalar(
            select(LedgerBookSnapshot).where(
                LedgerBookSnapshot.company_id == company_id,
                LedgerBookSnapshot.id == snapshot_id,
            )
        )


@pytest.mark.asyncio
async def test_issue_persists_pdf_archive_and_exposes_hash():
    company_id = "c-pdf-issue"
    issued = await _issued(company_id)

    assert issued["hasPdf"] is True
    assert len(issued["pdfSha256"]) == 64
    assert issued["pdfSizeBytes"] > 0

    row = await _row(company_id, issued["id"])
    path = Path(row.pdf_path)
    assert path.exists()
    data = path.read_bytes()
    assert data.startswith(b"%PDF-")
    assert hashlib.sha256(data).hexdigest() == issued["pdfSha256"]
    assert len(data) == issued["pdfSizeBytes"]
    assert row.pdf_generated_at is not None


@pytest.mark.asyncio
async def test_snapshot_pdf_reads_archived_bytes_first():
    company_id = "c-pdf-download"
    issued = await _issued(company_id)
    row = await _row(company_id, issued["id"])

    rendered = await render_snapshot_pdf(company_id, issued["id"])
    assert rendered["archivedPdf"] is True
    assert rendered["pdfSha256"] == row.pdf_sha256
    assert rendered["pdfBytes"] == Path(row.pdf_path).read_bytes()


@pytest.mark.asyncio
async def test_missing_archive_falls_back_readonly():
    company_id = "c-pdf-fallback"
    issued = await _issued(company_id)
    row = await _row(company_id, issued["id"])
    Path(row.pdf_path).unlink()

    rendered = await render_snapshot_pdf(company_id, issued["id"])
    assert rendered["archivedPdf"] is False
    assert rendered["pdfBytes"].startswith(b"%PDF-")

    # 下载是只读动作：不得顺手把 pdf_path 写回数据库
    again = await _row(company_id, issued["id"])
    assert again.pdf_path == row.pdf_path
    assert again.pdf_sha256 == row.pdf_sha256


@pytest.mark.asyncio
async def test_void_keeps_pdf_downloadable():
    company_id = "c-pdf-void"
    issued = await _issued(company_id)
    row = await _row(company_id, issued["id"])
    archive = Path(row.pdf_path)

    await void_snapshot(company_id, issued["id"], reason="更正签发", user_id="owner-1")
    assert archive.exists()

    rendered = await render_snapshot_pdf(company_id, issued["id"])
    assert rendered["status"] == "void"
    assert rendered["archivedPdf"] is True
    assert hashlib.sha256(rendered["pdfBytes"]).hexdigest() == row.pdf_sha256


@pytest.mark.asyncio
async def test_backup_pdf_archive_reuses_issued_file_and_records_it():
    company_id = "c-pdf-backup"
    issued = await _issued(company_id)
    row = await _row(company_id, issued["id"])

    result = await build_ledger_pdf_archive(
        company_id,
        "owner-1",
        year=2026,
        month="2026-10",
        scope="month",
        snapshot_id=issued["id"],
    )
    assert result["pdfSha256"] == row.pdf_sha256
    # 签发件已不可变，归档记录直接指向同一份文件，不再复制
    assert Path(result["filePath"]) == Path(row.pdf_path)
    assert Path(result["filePath"]).exists()

    record = next(item for item in await list_records(company_id) if item["id"] == result["id"])
    assert record["fileFormat"] == "pdf"
    assert record["exists"] is True
    stored = await get_record(company_id, result["id"])
    assert stored is not None
    assert Path(stored["filePath"]) == Path(row.pdf_path)


def _synthetic_book(detail_count: int = 45) -> dict:
    """构造一本「目录条目超过一页」的账簿，校验 PDF 页数跟随分页真值。"""
    details = []
    for index in range(detail_count):
        code = f"6001{index:02d}"
        details.append(
            {
                "code": code,
                "name": f"测试科目{index}",
                "opening": "0.00",
                "openingDirection": "debit",
                "lineCount": 1,
                "lines": [
                    {
                        "date": "2026-10-03",
                        "voucherRef": "记-1",
                        "summary": "测试发生额",
                        "counterparties": [{"code": "1002", "name": "银行存款"}],
                        "debit": "1.00",
                        "credit": "0.00",
                        "balanceDirection": "debit",
                        "balance": "1.00",
                    }
                ],
                "periodTotals": {"debit": "1.00", "credit": "0.00"},
                "yearTotals": {"debit": "1.00", "credit": "0.00"},
                "closingDirection": "debit",
                "closing": "1.00",
            }
        )
    book = {
        "meta": {
            "companyName": "测试账套",
            "scope": "month",
            "month": "2026-10",
            "year": 2026,
            "entryCount": 1,
            "accountingStandard": "小企业会计准则",
            "periodStatus": "closed",
        },
        "balance": {
            "items": [
                {
                    "accountCode": "1002",
                    "accountName": "银行存款",
                    "openingDirection": "debit",
                    "openingBalance": "0.00",
                    "debitTotal": "1.00",
                    "creditTotal": "0.00",
                    "closingDirection": "debit",
                    "closingBalance": "1.00",
                }
            ],
            "totalDebit": "1.00",
            "totalCredit": "1.00",
            "isBalanced": True,
            "isOpeningBalanced": True,
            "isClosingBalanced": True,
        },
        "general": None,
        "details": details,
        "journals": [],
        "chronological": {
            "voucherCount": 1,
            "totalDebit": "1.00",
            "totalCredit": "1.00",
            "items": [
                {
                    "date": "2026-10-03",
                    "voucherRef": "记-1",
                    "summary": "测试发生额",
                    "accountCode": "1002",
                    "accountName": "银行存款",
                    "debit": "1.00",
                    "credit": "0.00",
                },
                {
                    "date": "2026-10-03",
                    "voucherRef": "记-1",
                    "summary": "测试发生额",
                    "accountCode": "600101",
                    "accountName": "测试科目0",
                    "debit": "0.00",
                    "credit": "1.00",
                },
            ],
        },
    }
    book["pagination"] = _paginate(book)
    return book


def test_pdf_pages_follow_pagination_truth():
    book = _synthetic_book()
    # 目录条目（余额表 + 45 本明细账 + 序时账 + 2 条另册）超过旧版单页上限，
    # 但分页真值仍按后端 sections 为准，PDF 不得自行顺延页码。
    assert len(book["details"]) == 45
    rendered = render_ledger_book_pdf(
        book,
        snapshot_status="issued",
        book_no="2026-10-0001",
        content_hash="a" * 64,
        generated_by="测试",
        generated_at="2026-11-19T10:00:00+08:00",
    )
    assert rendered.page_count == book["pagination"]["totalPages"]
    assert rendered.pdf_bytes.startswith(b"%PDF-")
    assert "封面" in rendered.outline_titles
    assert "目录" in rendered.outline_titles

    again = render_ledger_book_pdf(
        book,
        snapshot_status="issued",
        book_no="2026-10-0001",
        content_hash="a" * 64,
        generated_by="测试",
        generated_at="2026-11-19T10:00:00+08:00",
    )
    # 同一 payload 必须逐字节可复现，否则「重打与原版一致」不成立
    assert again.pdf_bytes == rendered.pdf_bytes


def test_pdf_uses_embedded_cjk_font_when_available():
    from services import ledger_book_pdf_service as pdf_service

    available = [
        regular
        for regular, _ in pdf_service._FONT_CANDIDATES
        if regular and os.path.exists(regular)
    ]
    if not available:
        pytest.skip("本机没有可嵌入的中文 TrueType 字体，跳过嵌入校验")
    # STSong-Light 是 CID 回落字体，不嵌入 PDF，换机器打开可能乱码。
    assert pdf_service.resolve_fonts()[0] != "STSong-Light"


@pytest.mark.asyncio
async def test_download_response_rejects_sha_mismatch(tmp_path):
    from routers.backup import _download_response, _sha256_file

    path = tmp_path / "ledger.pdf"
    path.write_bytes(b"%PDF-1.4 archive")
    digest = _sha256_file(str(path))

    response = await _download_response(str(path), "ledger.pdf", expected_sha256=digest)
    assert response.headers["X-Backup-Sha256"] == digest

    with pytest.raises(HTTPException) as exc:
        await _download_response(str(path), "ledger.pdf", expected_sha256="0" * 64)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_snapshot_pdf_http_chain(client, auth_headers):
    """接口级链路：签发 → 快照下载 → 生效册下载 → 归档 → 归档重下。"""
    company_id = "c1"
    month = "2027-04"
    await _setup_company(company_id, user_id="test_user_001")
    await _post_entry(
        company_id,
        occur_date=f"{month}-03",
        entry_id="c1-pdf-chain",
        user_id="test_user_001",
    )
    await _set_period_closed(company_id, month)

    issued = client.post(
        "/api/ledger/book/snapshots",
        json={"month": month, "scope": "month"},
        headers=auth_headers,
    )
    assert issued.status_code == 200, issued.text
    data = issued.json()
    assert data["hasPdf"] is True
    sha = data["pdfSha256"]

    snapshot_pdf = client.get(
        f"/api/ledger/book/snapshots/{data['id']}/pdf", headers=auth_headers
    )
    assert snapshot_pdf.status_code == 200
    assert snapshot_pdf.content.startswith(b"%PDF-")
    assert snapshot_pdf.headers["x-book-pdf-sha256"] == sha
    assert snapshot_pdf.headers["x-book-pdf-archived"] == "1"

    live_pdf = client.get(
        "/api/ledger/book/pdf",
        params={"month": month, "scope": "month", "prefer_issued": 1},
        headers=auth_headers,
    )
    assert live_pdf.status_code == 200
    assert live_pdf.headers["x-book-pdf-archived"] == "1"
    assert live_pdf.headers["x-book-pdf-sha256"] == sha

    archive = client.post(
        "/api/backup/ledger-pdf",
        params={"year": 2027, "month": month, "scope": "month"},
        headers=auth_headers,
    )
    assert archive.status_code == 200, archive.text
    assert archive.headers["x-book-pdf-sha256"] == sha
    assert archive.headers["x-backup-sha256"] == sha

    record_id = archive.headers["x-backup-id"]
    download = client.get(
        f"/api/backup/records/{record_id}/download", headers=auth_headers
    )
    assert download.status_code == 200
    assert download.headers["x-backup-sha256"] == hashlib.sha256(download.content).hexdigest()

    # 签发件可按路径反查到登记哈希，供归档下载做完整性校验
    row = await _row(company_id, data["id"])
    assert await find_snapshot_pdf_sha_by_path(company_id, row.pdf_path) == row.pdf_sha256


def test_pdf_rejects_page_count_mismatch():
    book = _synthetic_book(detail_count=2)
    book["pagination"]["totalPages"] += 1
    with pytest.raises(PdfPageCountMismatch):
        render_ledger_book_pdf(
            book,
            snapshot_status="issued",
            book_no="2026-10-0002",
            content_hash="b" * 64,
            generated_by="测试",
            generated_at="2026-11-19T10:00:00+08:00",
        )
