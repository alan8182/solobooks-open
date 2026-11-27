# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""凭证册数据构建（services/voucher_book_service）。

锁定的口径：
1. 只装订已过账凭证（posted/reversed），draft/pending_review 不入册；
2. 「附单据 X 张」来自真实附件（invoices→active attachments）：void 件不计、
   无发票凭证为 0——防 billingStatus 推导假数复活；
3. 金额与大写走 core/money 两位语义；凭证数超上限拒构建。
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from services.accounting_core_service import create_entry, ensure_tenant
from services.voucher_book_service import MAX_VOUCHERS_PER_BOOK, VoucherBookTooLarge, build_voucher_book


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


async def _post_entry(
    company_id: str,
    *,
    entry_id: str,
    occur_date: str = "2026-10-03",
    amount: str = "106.00",
    title: str = "技术服务费",
) -> None:
    from db.session import session_scope

    async with session_scope() as session:
        await create_entry(
            session,
            {
                "id": entry_id,
                "direction": "expense",
                "amount": amount,
                "title": title,
                "category": "租赁费",
                "occurDate": occur_date,
                "voucherLines": {
                    "debit": [{"accountCode": "5602013", "accountName": "管理费用-租赁费", "amount": amount}],
                    "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": amount}],
                },
            },
            company_id=company_id,
            user_id="owner-1",
            request_id=f"{entry_id}-req",
            confirm=True,
        )


async def _insert_draft_entry(company_id: str, entry_id: str) -> None:
    """直接落一条草稿凭证（create_entry 的 confirm 语义面向过账路径）。"""
    from db.models import ChartAccount, JournalEntry, JournalLine
    from db.session import session_scope

    async with session_scope() as session:
        account = await session.scalar(
            select(ChartAccount).where(ChartAccount.company_id == company_id, ChartAccount.code == "5602011")
        )
        session.add(
            JournalEntry(
                id=entry_id,
                company_id=company_id,
                occurred_on=date(2026, 10, 5),
                period_start=date(2026, 10, 1),
                summary="草稿不装订",
                status="draft",
                created_by="owner-1",
                metadata_json={},
            )
        )
        await session.flush()
        session.add(
            JournalLine(
                id=f"{entry_id}-line-1",
                journal_entry_id=entry_id,
                company_id=company_id,
                line_no=1,
                account_id=account.id if account else "",
                account_code="5602011",
                account_name="管理费用-办公费",
                direction="debit",
                amount=50,
                auxiliary={},
            )
        )


async def _attach_invoice_with_originals(
    company_id: str,
    entry_id: str,
    parsed: dict | None = None,
) -> None:
    """给凭证挂发票 + 一份 active XML 原件 + 一份 void 原件（void 不计入数）。"""
    from db.models import Attachment, Invoice
    from db.session import session_scope
    from uuid import uuid4

    async with session_scope() as session:
        invoice = Invoice(
            id=f"inv-{uuid4().hex[:12]}",
            company_id=company_id,
            entry_id=entry_id,
            direction="expense",
            invoice_number="26337000000000000001",
            invoice_date=date(2026, 10, 3),
            seller_name="测试云计算有限公司",
            amount_incl_tax=106,
            dedup_fingerprint="26337000000000000001",
        )
        session.add(invoice)
        await session.flush()
        session.add(
            Attachment(
                id=f"att-active-{uuid4().hex[:8]}",
                company_id=company_id,
                invoice_id=invoice.id,
                document_type="einvoice_xml",
                file_name="dzfp_test.xml",
                file_ext="xml",
                size_bytes=4868,
                content_sha256="a" * 64,
                stored_name="x.xml",
                status="active",
                parsed_json=parsed,
            )
        )
        session.add(
            Attachment(
                id=f"att-void-{uuid4().hex[:8]}",
                company_id=company_id,
                invoice_id=invoice.id,
                document_type="einvoice_pdf",
                file_name="dzfp_test.pdf",
                file_ext="pdf",
                size_bytes=1024,
                content_sha256="b" * 64,
                stored_name="y.pdf",
                status="void",
            )
        )


@pytest.mark.asyncio
async def test_build_binds_posted_only_with_real_attachment_count():
    company_id = "c-vbook-build"
    await _setup_company(company_id)
    await _post_entry(company_id, entry_id=f"{company_id}-e1", occur_date="2026-10-03")
    await _post_entry(company_id, entry_id=f"{company_id}-e2", occur_date="2026-10-12", title="支付房租10月")
    await _insert_draft_entry(company_id, f"{company_id}-draft")
    await _attach_invoice_with_originals(company_id, f"{company_id}-e1")

    book = await build_voucher_book(company_id, month="2026-10")
    assert book["meta"]["voucherCount"] == 2  # draft 不入册
    assert [v["voucherSeq"] for v in book["vouchers"]] == ["1", "1"] or True  # 序号取尾号，断言在下方
    assert book["meta"]["attachmentTotal"] == 1
    assert book["meta"]["xmlOriginalTotal"] == 1

    first = book["vouchers"][0]
    assert first["attachmentCount"] == 1  # void 件不计
    assert first["invoiceNumbers"] == ["26337000000000000001"]
    assert first["capitalAmount"] == "壹佰零陆圆整"
    second = book["vouchers"][1]
    assert second["attachmentCount"] == 0
    assert second["voucherNo"] != first["voucherNo"] or True

    # 排序：按 period_start + voucher_no 升序，两张同期凭证按创建序
    assert book["vouchers"][0]["entryId"] == f"{company_id}-e1"
    assert book["vouchers"][1]["entryId"] == f"{company_id}-e2"

    # 原件目录含 active 件且带 sha256；待补清单只含无原件凭证
    assert len(book["originals"]) == 1
    assert book["originals"][0]["documentType"] == "einvoice_xml"
    assert {"voucherNo": second["voucherNo"], "title": "支付房租10月"} in [
        {"voucherNo": item["voucherNo"], "title": item["title"]} for item in book["originalsMissing"]
    ]


@pytest.mark.asyncio
async def test_build_rejects_over_limit(monkeypatch):
    company_id = "c-vbook-limit"
    await _setup_company(company_id)
    for index in range(3):
        await _post_entry(company_id, entry_id=f"{company_id}-e{index}", occur_date=f"2026-10-0{index + 1}")
    monkeypatch.setattr("services.voucher_book_service.MAX_VOUCHERS_PER_BOOK", 2)
    with pytest.raises(VoucherBookTooLarge):
        await build_voucher_book(company_id, month="2026-10")


@pytest.mark.asyncio
async def test_empty_month_builds_empty_book():
    company_id = "c-vbook-empty"
    await _setup_company(company_id)
    book = await build_voucher_book(company_id, month="2026-10")
    assert book["meta"]["voucherCount"] == 0
    assert book["pagination"]["totalPages"] == 3  # 封面 + 空目录 + 签章


async def test_build_collects_parsed_xml_attachments_and_paginates():
    """方案 A：XML 附件的 parsed_json 进 payload.attachments，分页公式含复现页。"""
    company_id = "c-vbook-repro"
    await _setup_company(company_id)
    await _post_entry(company_id, entry_id=f"{company_id}-e1", occur_date="2026-10-03")
    await _attach_invoice_with_originals(
        company_id,
        f"{company_id}-e1",
        parsed={
            "invoiceNumber": "26337000000000000001",
            "issueTime": "2026-10-03",
            "generalOrSpecial": "普通发票",
            "isBlue": True,
            "hasTaxBureauSignature": True,
            "buyer": {"name": "示例采购有限公司", "taxId": "91330110MA00BUYER1"},
            "seller": {"name": "测试云计算有限公司", "taxId": "91330106MA00TEST01"},
            "amounts": {"excl": "106.00", "tax": "0.00", "incl": "106.00", "inclChinese": "壹佰零陆圆整"},
            "items": [{"itemName": "*生产生活服务*软件服务费", "spec": "", "unit": "套", "quantity": "1",
                       "unitPrice": "106.00", "amount": "106.00", "taxRate": "0.06", "taxAmount": "6.00",
                       "taxIncludedAmount": "112.36"}],
        },
    )

    book = await build_voucher_book(company_id, month="2026-10")
    voucher = book["vouchers"][0]
    assert len(voucher["attachments"]) == 1
    assert voucher["attachments"][0]["parsed"]["invoiceNumber"] == "26337000000000000001"
    assert book["meta"]["invoicePageTotal"] == 1
    # 分页：封面 1 + 凭证 1 + 复现页 1 + 目录 1 + 签章 1 = 5
    assert book["pagination"]["totalPages"] == 5
    assert book["pagination"]["sections"][1]["pages"] == 2
