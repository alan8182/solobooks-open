# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""发票电子原件（附件）服务与路由。

锁定的口径：
1. 上传即归档：XML 解析后按发票号自动挂台账（matched）；查不到 → parked
   （invoice_id 为空 + import_rows），绝不自动入账；
2. 原件不可变（A2）：作废只置状态 + 审计留痕，文件保留；下载重算 sha256，
   不符 409、缺失 410、跨账套 404；
3. 防重复：同文件（sha256）重复上传 duplicate；relink 按发票号幂等回挂；
4. 台账列表聚合 attachmentCount/hasOriginalXml 与 verify 状态同源输出（A3）。
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from services import attachment_storage_service as att_svc
from services.accounting_core_service import ensure_tenant
from services.attachment_storage_service import (
    AttachmentError,
    list_attachments,
    parked_count,
    read_attachment_file,
    relink_parked_attachments,
    set_invoice_verify,
    upload_attachments,
    void_attachment,
)
from services.invoice_ledger_service import list_invoices, list_invoices_with_attachments
from services.invoice_service import import_invoices

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "einvoice"
XML = (FIXTURES / "enterprise_blue.xml").read_bytes()
XML_EXEMPT = (FIXTURES / "exempt_person_agent.xml").read_bytes()
INVOICE_NUMBER = "26337000000000000001"


async def _setup_company(company_id: str) -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine, session_scope

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_scope() as session:
        await ensure_tenant(session, company_id, "owner-1")


async def _import_blue_invoice(company_id: str, *, total_amount: str = "54.00") -> str:
    result = await import_invoices(
        company_id,
        "owner-1",
        [
            {
                "invoice_number": INVOICE_NUMBER,
                "invoice_date": "2026-11-21",
                "total_amount": total_amount,
                "tax_amount": "3.06",
                "seller_name": "测试云计算有限公司",
                "buyer_name": "示例采购有限公司",
                "direction": "expense",
                "item_name": "*生产生活服务*软件服务费",
            }
        ],
        classify=False,
    )
    assert result["imported"] == 1
    listing = await list_invoices(company_id)
    return listing["items"][0]["id"]


# ---------- 服务层 ----------


async def test_upload_parks_unmatched_xml_with_import_rows():
    """台账没有这张票 → parked（不自动入账），import_rows 供一键生成草稿。"""
    company_id = "c-att-park"
    await _setup_company(company_id)
    results = await upload_attachments(company_id, "owner-1", [{"file_name": "dzfp_test.xml", "payload": XML}])
    assert len(results) == 1
    item = results[0]
    assert item["outcome"] == "parked"
    assert item["invoiceId"] is None
    assert item["invoiceNumber"] == INVOICE_NUMBER
    assert item["importRows"] and item["importRows"][0]["total_amount"] == "54.00"
    assert await parked_count(company_id) == 1
    # 文件落盘
    attachment_id = item["attachmentId"]
    assert (att_svc.ATTACHMENTS_DIR / company_id[:12] / f"{attachment_id}.xml").exists()


async def test_upload_matches_ledger_invoice_and_aggregates():
    """台账已有同号票 → matched + 一致性无告警；聚合字段 hasOriginalXml=True。"""
    company_id = "c-att-match"
    await _setup_company(company_id)
    invoice_id = await _import_blue_invoice(company_id)
    results = await upload_attachments(company_id, "owner-1", [{"file_name": "dzfp_test.xml", "payload": XML}])
    item = results[0]
    assert item["outcome"] == "matched"
    assert item["invoiceId"] == invoice_id
    assert item["warnings"] == []

    listing = await list_invoices_with_attachments(company_id)
    row = listing["items"][0]
    assert row["attachmentCount"] == 1
    assert row["hasOriginalXml"] is True
    assert row["verifyStatus"] == "unverified"


async def test_upload_flags_amount_mismatch_but_still_attaches():
    """金额与台账不一致：挂上但给警告（状态可解释），不静默、不拒收。"""
    company_id = "c-att-mismatch"
    await _setup_company(company_id)
    await _import_blue_invoice(company_id, total_amount="56.00")
    results = await upload_attachments(company_id, "owner-1", [{"file_name": "dzfp_test.xml", "payload": XML}])
    item = results[0]
    assert item["outcome"] == "matched"
    assert any("价税合计与台账不一致" in warning for warning in item["warnings"])


async def test_duplicate_sha_upload_blocked():
    company_id = "c-att-dup"
    await _setup_company(company_id)
    first = (await upload_attachments(company_id, "owner-1", [{"file_name": "a.xml", "payload": XML}]))[0]
    second = (await upload_attachments(company_id, "owner-1", [{"file_name": "b.xml", "payload": XML}]))[0]
    assert first["outcome"] == "parked"
    assert second["outcome"] == "duplicate"
    assert second["attachmentId"] == first["attachmentId"]
    # 批内重复同样拦截
    batch = await upload_attachments(
        company_id, "owner-1", [{"file_name": "c.xml", "payload": XML}, {"file_name": "d.xml", "payload": XML}]
    )
    assert [item["outcome"] for item in batch] == ["duplicate", "duplicate"]


async def test_zip_upload_expands_and_skips_xlsx():
    company_id = "c-att-zip"
    await _setup_company(company_id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("dzfp_1.xml", XML)
        archive.writestr("dzfp_2.xml", XML_EXEMPT)
        archive.writestr("发票统计.xlsx", b"xlsx")
    results = await upload_attachments(company_id, "owner-1", [{"file_name": "批量下载.zip", "payload": buffer.getvalue()}])
    outcomes = [item["outcome"] for item in results]
    assert outcomes == ["parked", "parked", "rejected"]
    assert await parked_count(company_id) == 2


async def test_relink_after_import_is_idempotent():
    """先传原件（parked）→ 走导入管线入账 → relink 回挂；重跑不重复。"""
    company_id = "c-att-relink"
    await _setup_company(company_id)
    parked = (await upload_attachments(company_id, "owner-1", [{"file_name": "dzfp_test.xml", "payload": XML}]))[0]
    assert parked["outcome"] == "parked"

    rows = parked["importRows"]
    await import_invoices(company_id, "owner-1", rows, classify=False)

    first = await relink_parked_attachments(company_id)
    assert first == {"relinked": 1, "scanned": 1}
    listing = await list_attachments(company_id, invoice_id=None, parked=True)
    assert listing["total"] == 0
    stored = await list_attachments(company_id, parked=False)
    attached = [item for item in stored["items"] if item["id"] == parked["attachmentId"]]
    assert attached and attached[0]["invoiceId"]
    # 重跑幂等
    again = await relink_parked_attachments(company_id)
    assert again["relinked"] == 0


async def test_void_keeps_file_and_writes_audit():
    """作废：状态置 void、文件保留、审计留痕（A2 原件不可变、目录只增不减）。"""
    company_id = "c-att-void"
    await _setup_company(company_id)
    item = (await upload_attachments(company_id, "owner-1", [{"file_name": "dzfp_test.xml", "payload": XML}]))[0]
    attachment_id = item["attachmentId"]
    path = att_svc.ATTACHMENTS_DIR / company_id[:12] / f"{attachment_id}.xml"

    voided = await void_attachment(company_id, "owner-1", attachment_id, "重复上传误挂")
    assert voided["status"] == "void"
    assert voided["voidReason"] == "重复上传误挂"
    assert path.exists()
    # 重复作废幂等
    again = await void_attachment(company_id, "owner-1", attachment_id, "")
    assert again["status"] == "void"

    from services.database import get_conn

    conn = await get_conn()
    try:
        cursor = await conn.execute(
            "SELECT action, entity_type, entity_id FROM audit_log WHERE entity_id = ?", (attachment_id,)
        )
        row = await cursor.fetchone()
    finally:
        await conn.close()
    assert row and row["action"] == "void" and row["entity_type"] == "attachment"


async def test_download_sha256_and_missing_file():
    company_id = "c-att-read"
    await _setup_company(company_id)
    item = (await upload_attachments(company_id, "owner-1", [{"file_name": "dzfp_test.xml", "payload": XML}]))[0]
    result = await read_attachment_file(company_id, item["attachmentId"])
    assert result["fileName"] == "dzfp_test.xml"
    assert result["data"] == XML

    # 篡改落盘文件 → sha256 校验失败 409（runbook 下载校验约定）
    path = att_svc.ATTACHMENTS_DIR / company_id[:12] / f"{item['attachmentId']}.xml"
    path.write_bytes(XML + b"tampered")
    with pytest.raises(AttachmentError) as exc_info:
        await read_attachment_file(company_id, item["attachmentId"])
    assert exc_info.value.status_code == 409

    # 文件缺失 → 410
    path.unlink()
    with pytest.raises(AttachmentError) as exc_info:
        await read_attachment_file(company_id, item["attachmentId"])
    assert exc_info.value.status_code == 410


async def test_verify_status_mark_and_unmark():
    company_id = "c-att-verify"
    await _setup_company(company_id)
    invoice_id = await _import_blue_invoice(company_id)
    marked = await set_invoice_verify(company_id, "owner-1", invoice_id, verified=True)
    assert marked["verifyStatus"] == "verified"
    assert marked["verifiedAt"]
    listing = await list_invoices(company_id)
    assert listing["items"][0]["verifyStatus"] == "verified"
    unmarked = await set_invoice_verify(company_id, "owner-1", invoice_id, verified=False)
    assert unmarked["verifyStatus"] == "unverified"
    assert unmarked["verifiedAt"] == ""


# ---------- 路由层（HTTP） ----------


def test_router_upload_list_download_void(client, auth_headers):
    from services.database import get_conn

    company_id = "c1"
    resp = client.post(
        "/api/invoices/attachments",
        headers=auth_headers,
        files={"files": ("dzfp_test.xml", XML, "application/xml")},
    )
    assert resp.status_code == 200
    item = resp.json()[0]
    assert item["outcome"] == "parked"
    attachment_id = item["attachmentId"]

    # 列表（parked）
    resp = client.get("/api/invoices/attachments?parked=true", headers=auth_headers)
    assert resp.status_code == 200
    assert any(entry["id"] == attachment_id for entry in resp.json()["items"])
    # parked 列表带 import_rows（一键生成草稿的输入）
    entry = next(entry for entry in resp.json()["items"] if entry["id"] == attachment_id)
    assert entry["importRows"] and entry["importRows"][0]["invoice_number"] == INVOICE_NUMBER

    # parked-count
    resp = client.get("/api/invoices/attachments/parked-count", headers=auth_headers)
    assert resp.json()["count"] >= 1

    # 下载：sha256 头 + 内容一致
    resp = client.get(f"/api/invoices/attachments/{attachment_id}/download", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["X-Attachment-Sha256"]
    assert resp.content == XML

    # 篡改后 409
    stored_path = att_svc.ATTACHMENTS_DIR / company_id[:12] / f"{attachment_id}.xml"
    stored_path.write_bytes(XML + b"x")
    resp = client.get(f"/api/invoices/attachments/{attachment_id}/download", headers=auth_headers)
    assert resp.status_code == 409

    # 作废 + 文件仍在
    resp = client.post(
        f"/api/invoices/attachments/{attachment_id}/void",
        headers=auth_headers,
        json={"reason": "测试作废"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "void"
    assert stored_path.exists()


async def test_router_rejects_foreign_attachment(client, auth_headers):
    """跨账套访问原件应 404（A3 数据隔离）。"""
    await _setup_company("other_att_company")
    results = await upload_attachments(
        "other_att_company", "owner-1", [{"file_name": "dzfp_test.xml", "payload": XML}]
    )
    attachment_id = results[0]["attachmentId"]
    resp = client.get(f"/api/invoices/attachments/{attachment_id}/download", headers=auth_headers)
    assert resp.status_code == 404


async def test_router_verify_endpoint(client, auth_headers):
    """c1 由 auth 兜底身份共用（mailbox 等测试也会建它），直接建发票行，不动租户归属。"""
    from sqlalchemy import select

    from db.base import Base
    import db.models  # noqa: F401
    from db.models import Invoice
    from db.session import get_engine, session_scope

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_scope() as session:
        existing = (
            await session.execute(
                select(Invoice).where(Invoice.company_id == "c1", Invoice.invoice_number == INVOICE_NUMBER)
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                Invoice(id="inv-verify-e2e", company_id="c1", direction="expense", invoice_number=INVOICE_NUMBER)
            )
    resp = client.patch("/api/invoices/inv-verify-e2e/verify", headers=auth_headers, json={"verified": True})
    assert resp.status_code == 200
    assert resp.json()["verifyStatus"] == "verified"


def test_router_rejects_unsupported_and_bad_xml(client, auth_headers):
    resp = client.post(
        "/api/invoices/attachments",
        headers=auth_headers,
        files={"files": ("photo.jpg", b"jpeg-bytes", "image/jpeg")},
    )
    assert resp.status_code == 200
    assert resp.json()[0]["outcome"] == "rejected"

    bad = (FIXTURES / "bad_reconcile.xml").read_bytes()
    resp = client.post(
        "/api/invoices/attachments",
        headers=auth_headers,
        files={"files": ("bad.xml", bad, "application/xml")},
    )
    assert resp.status_code == 200
    assert resp.json()[0]["outcome"] == "rejected"
    assert "勾稽不平" in resp.json()[0]["error"]
