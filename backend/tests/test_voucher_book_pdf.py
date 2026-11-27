# Copyright (c) 2026 芷子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""凭证册 PDF 渲染（services/voucher_book_pdf_service）。

锁定：页数真值防线（实际页数 ≠ pagination.totalPages 拒绝出册）、invariant=1
同 payload 逐字节可复现、书签结构、横版 A4 页面尺寸。
"""
from __future__ import annotations

import pytest

from services.voucher_book_pdf_service import PAGE_W, PAGE_H, render_voucher_book_pdf
from services.voucher_book_pdf_service import PdfPageCountMismatch
from services.voucher_book_service import _paginate


def _synthetic_book(voucher_count: int = 3, original_count: int = 2) -> dict:
    def voucher(seq: int, *, att: int = 1) -> dict:
        lines = [
            {
                "summary": f"测试业务{seq}",
                "code": "5602013" if i == 0 else "1002",
                "name": "管理费用-租赁费" if i == 0 else "银行存款",
                "auxiliaryText": "往来:测试供应商" if i else "",
                "side": "debit" if i == 0 else "credit",
                "amount": "106.00",
                "quantity": "",
                "unit": "",
                "unitPrice": "",
            }
            for i in range(2)
        ]
        return {
            "entryId": f"e-{seq}",
            "voucherNo": f"记-{seq:04d}",
            "voucherSeq": str(seq),
            "date": f"2026-10-{seq:02d}",
            "period": "2026-10-01",
            "title": f"测试业务{seq}",
            "backfill": seq == 2,
            "lines": lines,
            "blankRows": 4,
            "debitTotal": "106.00",
            "creditTotal": "106.00",
            "capitalAmount": "壹佰零陆圆整",
            "attachmentCount": att,
            "invoiceNumbers": ["26337000000000000001"] if att else [],
        }

    originals = [
        {
            "attachmentId": f"att-{i}",
            "entryId": f"e-{i}",
            "voucherNo": f"记-{i:04d}",
            "invoiceNumber": f"2633700000000000000{i}",
            "fileName": f"dzfp_{i}_测试云计算有限公司.xml",
            "documentType": "einvoice_xml",
            "contentSha256": "c" * 64,
            "sizeBytes": 4928,
        }
        for i in range(1, original_count + 1)
    ]
    vouchers = [voucher(i, att=1 if i <= original_count else 0) for i in range(1, voucher_count + 1)]
    book = {
        "meta": {
            "companyName": "示例采购有限公司",
            "month": "2026-10",
            "periodStart": "2026-10-01",
            "periodEnd": "2026-10-31",
            "periodStatus": "closed",
            "voucherCount": voucher_count,
            "attachmentTotal": original_count,
            "xmlOriginalTotal": original_count,
            "debitTotal": f"{106 * voucher_count:.2f}",
            "contentHash": "a" * 64,
        },
        "vouchers": vouchers,
        "originals": originals,
        "originalsMissing": [
            {"voucherNo": v["voucherNo"], "title": v["title"], "date": v["date"]}
            for v in vouchers
            if v["attachmentCount"] == 0
        ],
    }
    book["pagination"] = _paginate(book["vouchers"], book["originals"])
    return book


def test_render_page_count_matches_pagination():
    book = _synthetic_book(voucher_count=3, original_count=2)
    result = render_voucher_book_pdf(book)
    assert result.page_count == book["pagination"]["totalPages"]
    assert result.pdf_bytes.startswith(b"%PDF-")
    # 书签：封面 + 每凭证 + 目录 + 签章
    assert result.outline_titles == ["凭证册封面", "记-0001", "记-0002", "记-0003", "原件归档目录", "装订签章"]


def test_render_is_reproducible_invariant():
    book = _synthetic_book()
    first = render_voucher_book_pdf(book)
    second = render_voucher_book_pdf(book)
    assert first.pdf_bytes == second.pdf_bytes
    assert first.pdf_sha256 == second.pdf_sha256


def test_page_count_mismatch_rejected():
    book = _synthetic_book()
    bad = {**book, "pagination": {**book["pagination"], "totalPages": 99}}
    with pytest.raises(PdfPageCountMismatch):
        render_voucher_book_pdf(bad)


def test_landscape_page_size():
    # 凭证横版：宽 > 高（A4 纵向的宽高互换）
    assert PAGE_W > PAGE_H


def test_issue_status_stamped_in_payload_meta():
    """签发时 meta 已带 bookNo/generatedAt，重渲染（归档件丢失场景）逐字节可复现。"""
    book = _synthetic_book()
    book["meta"]["bookNo"] = "2026-10-0001"
    book["meta"]["generatedBy"] = "装订人"
    book["meta"]["generatedAt"] = "2026-11-21T16:00:00+08:00"
    first = render_voucher_book_pdf(book, snapshot_status="issued", book_no="2026-10-0001")
    second = render_voucher_book_pdf(
        book,
        snapshot_status="issued",
        book_no="2026-10-0001",
        generated_by="装订人",
        generated_at="2026-11-21T16:00:00+08:00",
    )
    assert first.pdf_bytes == second.pdf_bytes


def test_void_status_stamped():
    book = _synthetic_book()
    result = render_voucher_book_pdf(
        book,
        snapshot_status="void",
        book_no="2026-10-0001",
        void_reason="重复装订",
        voided_at="2026-11-21T17:00:00+08:00",
    )
    assert result.page_count == book["pagination"]["totalPages"]


def _synthetic_attachment(seq: int, *, items_count: int = 3) -> dict:
    return {
        "attachmentId": f"att-{seq}",
        "invoiceNumber": f"2633700000000000000{seq}",
        "issueTime": f"2026-10-0{seq}",
        "documentType": "einvoice_xml",
        "contentSha256": "c" * 64,
        "fileName": f"dzfp_{seq}.xml",
        "parsed": {
            "invoiceNumber": f"2633700000000000000{seq}",
            "issueTime": f"2026-10-0{seq}",
            "generalOrSpecial": "普通发票",
            "isBlue": True,
            "hasTaxBureauSignature": True,
            "buyer": {"name": "示例采购有限公司", "taxId": "91330110MA00BUYER1"},
            "seller": {"name": "测试云计算有限公司", "taxId": "91330106MA00TEST01"},
            "amounts": {"excl": "50.94", "tax": "3.06", "incl": "54.00", "inclChinese": "伍拾肆圆整"},
            "remark": "测试备注",
            "items": [
                {
                    "itemName": f"*生产生活服务*软件服务费明细行{i}",
                    "spec": "无",
                    "unit": "套",
                    "quantity": "1",
                    "unitPrice": "8.49",
                    "amount": "8.49",
                    "taxRate": "0.06",
                    "taxAmount": "0.51",
                    "taxIncludedAmount": "9.00",
                }
                for i in range(items_count)
            ],
        },
    }


def test_render_inserts_repro_pages_after_voucher():
    """方案 A：每凭证后逐份 XML 附件渲染复现页，书签挂凭证号子级。"""
    book = _synthetic_book(voucher_count=2, original_count=2)
    book["vouchers"][0]["attachments"] = [_synthetic_attachment(1)]
    book["vouchers"][1]["attachments"] = [_synthetic_attachment(2), _synthetic_attachment(3, items_count=14)]
    book["pagination"] = _paginate(book["vouchers"], book["originals"])
    # 页数：封面1 + (1+1) + (1+2) + 目录1 + 签章1 = 8
    assert book["pagination"]["totalPages"] == 8
    result = render_voucher_book_pdf(book)
    assert result.page_count == 8
    assert result.outline_titles == [
        "凭证册封面",
        "记-0001",
        "　发票 26337000000000000001",
        "记-0002",
        "　发票 26337000000000000002",
        "　发票 26337000000000000003",
        "原件归档目录",
        "装订签章",
    ]


def test_legacy_payload_without_attachments_unchanged():
    """旧冻结 payload（无 attachments 字段）重渲染页数不变——已签发册兼容。"""
    book = _synthetic_book(voucher_count=3, original_count=2)
    result = render_voucher_book_pdf(book)
    assert result.page_count == book["pagination"]["totalPages"] == 6
