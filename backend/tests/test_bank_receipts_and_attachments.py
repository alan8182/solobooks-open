# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""银行电子回单/标准对账单与通用凭证附件全链路回归测试。"""
from __future__ import annotations

import importlib.util
import io
import zipfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fitz
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError

from db.base import Base
from db.models import Attachment, AuditEvent, Company, Invoice, JournalEntry, JournalLine
from db.session import dispose_engine, get_engine, get_session_factory, session_scope
from main import app
from services.accounting_core_service import _rebind_invoice_after_repost, create_entry, ensure_tenant
from services.attachment_storage_service import attachment_counts_by_entries, list_entry_attachments
from services.bank_receipt_service import (
    _detect_statement_pdf,
    bind_bank_receipt,
    iter_bank_zip_entries,
    list_bank_receipts,
    relink_bank_receipts,
    split_receipt_pdf_pages,
    upload_bank_receipts,
)
from services.voucher_book_pdf_service import render_voucher_book_pdf
from services.voucher_book_service import build_voucher_book


def _load_migration(filename: str):
    path = Path(__file__).resolve().parent.parent / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_png(shade: int, width: int = 420, height: int = 180) -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)
    pix.clear_with(shade)
    return pix.tobytes("png")


def _make_3receipt_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for idx, shade in enumerate((210, 225, 240)):
        top = 40 + idx * 240
        page.insert_image(fitz.Rect(40, top, 555, top + 180), stream=_make_png(shade))
    return doc.tobytes()


def _make_statement_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    page.insert_text((40, 80), "BANK STATEMENT 2026-09-05 to 2026-10-18", fontsize=12)
    return doc.tobytes()


def _make_gbk_zip(entry_name_cn: str, content: bytes) -> bytes:
    gbk_name = entry_name_cn.encode("gbk")
    placeholder = b"X" * len(gbk_name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(placeholder.decode("ascii"), content)
    return buf.getvalue().replace(placeholder, gbk_name)


@pytest.fixture
async def isolated_env(monkeypatch, tmp_path):
    db_file = tmp_path / "bank_receipts.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    get_engine.cache_clear()
    get_session_factory.cache_clear()

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS bank_statements ("
            "id TEXT PRIMARY KEY, company_id TEXT NOT NULL, transaction_date TEXT NOT NULL, "
            "description TEXT NOT NULL, amount NUMERIC(20,2) NOT NULL, "
            "balance NUMERIC(20,2) NOT NULL, statement_id TEXT NOT NULL, "
            "matched_entry_id TEXT, description_normalized INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, "
            "transaction_reference TEXT, source_row_index INTEGER)"
        )
        await conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS bank_statement_matches ("
            "statement_id TEXT, company_id TEXT, entry_id TEXT, "
            "previous_billing_status TEXT, previous_payment_date TEXT, created_at TEXT, "
            "PRIMARY KEY(statement_id, entry_id))"
        )

    async with session_scope() as session:
        await ensure_tenant(session, "c1", "test_user_001")
        company = await session.get(Company, "c1")
        assert company is not None
        company.payload = {
            "name": "杭州星辰科技有限公司",
            "taxId": "91330106MA00TEST01",
            "startDate": "2026-01-01",
            "onboardingType": "normal",
        }

    try:
        yield tmp_path
    finally:
        await dispose_engine()


def test_alembic_a3c7e9b2d4f1_upgrade_idempotent_checks_and_downgrade(monkeypatch, tmp_path):
    b8 = _load_migration("b8e2d4c6a1f3_attachments_einvoice_xml.py")
    a3 = _load_migration("a3c7e9b2d4f1_universal_voucher_attachments.py")

    db_path = tmp_path / "mig_a3.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE auth_companies (company_id VARCHAR(128) PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE invoices (id VARCHAR(128) PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE journal_entries (id VARCHAR(128) PRIMARY KEY)"))
        conn.execute(text("INSERT INTO auth_companies (company_id) VALUES ('c1')"))
        conn.execute(text("INSERT INTO journal_entries (id) VALUES ('je1')"))

        monkeypatch.setattr(b8, "op", Operations(MigrationContext.configure(conn)))
        b8.upgrade()

        conn.execute(
            text(
                "INSERT INTO attachments (id, company_id, document_type, file_name, file_ext, size_bytes, content_sha256, stored_name, status) "
                "VALUES ('old-1', 'c1', 'einvoice_xml', 'legacy.xml', 'xml', 100, 'sha-legacy', 'old-1.xml', 'active')"
            )
        )

        monkeypatch.setattr(a3, "op", Operations(MigrationContext.configure(conn)))
        a3.upgrade()

        cols = {c["name"] for c in inspect(conn).get_columns("attachments")}
        assert {"bank_statement_id", "journal_entry_id", "period", "parent_attachment_id"}.issubset(cols)
        idxs = {i["name"] for i in inspect(conn).get_indexes("attachments")}
        assert {"ix_attachments_company_statement", "ix_attachments_company_entry", "ix_attachments_company_period"}.issubset(idxs)

        # 新建各种类型均可写入
        for idx, doc_type in enumerate(
            ("bank_receipt", "bank_receipt_batch", "bank_statement_pdf", "payment_screenshot", "reimbursement_form", "payroll_sheet", "contract")
        ):
            conn.execute(
                text(
                    "INSERT INTO attachments (id, company_id, journal_entry_id, document_type, file_name, file_ext, size_bytes, content_sha256, stored_name, status) "
                    "VALUES (:id, 'c1', 'je1', :dt, 'f.png', 'png', 10, :sha, 'f.png', 'active')"
                ),
                {"id": f"new-{idx}", "dt": doc_type, "sha": f"sha-{idx}"},
            )

        with pytest.raises(IntegrityError):
            with conn.begin_nested():
                conn.execute(
                    text(
                        "INSERT INTO attachments (id, company_id, document_type, file_name, file_ext, size_bytes, content_sha256, stored_name, status) "
                        "VALUES ('bad', 'c1', 'invalid_doc_type', 'f.png', 'png', 10, 'sha-bad', 'f.png', 'active')"
                    )
                )

        # 幂等重跑
        a3.upgrade()

        # 清理新增扩展行后测试 downgrade
        conn.execute(text("DELETE FROM attachments WHERE id != 'old-1'"))
        a3.downgrade()
        cols_after = {c["name"] for c in inspect(conn).get_columns("attachments")}
        assert "bank_statement_id" not in cols_after
        assert "journal_entry_id" not in cols_after
        legacy_count = conn.execute(text("SELECT COUNT(*) FROM attachments WHERE id = 'old-1'")).scalar()
        assert legacy_count == 1


def test_gbk_zip_splitting_and_statement_detection():
    receipt_pdf = _make_3receipt_pdf()
    stmt_pdf = _make_statement_pdf()

    slices = split_receipt_pdf_pages(receipt_pdf)
    assert len(slices) == 3
    assert [s["sliceIndex"] for s in slices] == [1, 2, 3]
    assert all(s["png"].startswith(b"\x89PNG") for s in slices)

    is_stmt, periods = _detect_statement_pdf("杭州银行_客户交易标准对账单.pdf", stmt_pdf)
    assert is_stmt is True
    assert periods == ["2026-09", "2026-10"]

    zip_bytes = _make_gbk_zip("电子回单_202610.pdf", receipt_pdf)
    entries = iter_bank_zip_entries(zip_bytes)
    assert len(entries) == 1
    assert entries[0]["file_name"] == "电子回单_202610.pdf"
    assert entries[0]["ext"] == "pdf"
    assert entries[0]["error"] == ""


@pytest.mark.asyncio
async def test_receipt_upload_dedup_relink_and_bind(isolated_env, monkeypatch):
    # 按次序给切出的 3 张图 / 二次重下（不同 PNG 像素，但同 receiptNo）提供稳定提取结果
    seq_metas = [
        {"receiptNo": "REC-1001", "txDate": "2026-10-15", "amount": "1200.00", "direction": "expense", "payeeName": "阿里云计算有限公司", "payerName": "杭州星辰科技有限公司", "summary": "云服务费"},
        {"receiptNo": "REC-1002", "txDate": "2026-10-20", "amount": "500.00", "direction": "income", "payerName": "杭州大客户", "payeeName": "杭州星辰科技有限公司", "summary": "货款"},
        {"receiptNo": "REC-1003", "txDate": "2026-10-22", "amount": "80.00", "direction": "expense", "payeeName": "杭州供电局", "payerName": "杭州星辰科技有限公司", "summary": "电费"},
        # 同一回单 REC-1001 换个打印时间重下（图片字节不同但回单号相同 -> 必须去重）
        {"receiptNo": "REC-1001", "txDate": "2026-10-15", "amount": "1200.00", "direction": "expense", "payeeName": "阿里云计算有限公司", "payerName": "杭州星辰科技有限公司", "summary": "云服务费"},
    ]
    call_idx = {"i": 0}

    async def fake_extract(*_args, **_kwargs):
        meta = seq_metas[call_idx["i"]]
        call_idx["i"] += 1
        return dict(meta)

    monkeypatch.setattr("services.bank_receipt_service.extract_receipt_metadata", fake_extract)

    res = await upload_bank_receipts(
        "c1",
        "test_user_001",
        [
            {"file_name": "batch_oct.pdf", "payload": _make_3receipt_pdf()},
            {"file_name": "杭州银行_客户交易标准对账单.pdf", "payload": _make_statement_pdf()},
            {"file_name": "re_downloaded_single.png", "payload": _make_png(123)},
        ],
    )
    assert res["summary"]["batchCount"] == 1
    assert res["summary"]["receiptTotal"] == 3
    assert res["summary"]["parkedCount"] == 3
    assert res["summary"]["statementPdfCount"] == 1
    assert res["summary"]["duplicateCount"] == 1  # re_downloaded_single 同 receiptNo REC-1001 被拦

    # 后补银行流水与凭证，执行 relink 自动回挂
    async with session_scope() as session:
        e1 = await create_entry(
            session,
            {
                "id": "je-ali",
                "direction": "expense",
                "amount": "1200.00",
                "title": "云服务费",
                "category": "信息技术服务费",
                "occurDate": "2026-10-15",
                "sourceType": "manual",
                "voucherLines": {
                    "debit": [{"accountCode": "5602", "accountName": "管理费用", "amount": "1200.00"}],
                    "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "1200.00"}],
                },
            },
            company_id="c1",
            user_id="test_user_001",
            request_id="req-je-ali",
            confirm=True,
        )
        await session.execute(
            text(
                "INSERT INTO bank_statements (id, company_id, transaction_date, description, amount, balance, statement_id, matched_entry_id, description_normalized, created_at) "
                "VALUES ('stmt-ali', 'c1', '2026-10-15', '汇兑 阿里云计算有限公司 云服务费', -1200.00, 8800.00, 'b-1', :eid, 0, '2026-10-15T10:00:00')"
            ),
            {"eid": e1.id},
        )
        await session.execute(
            text(
                "INSERT INTO bank_statements (id, company_id, transaction_date, description, amount, balance, statement_id, matched_entry_id, description_normalized, created_at) "
                "VALUES ('stmt-power', 'c1', '2026-10-22', '供电局电费', -80.00, 8720.00, 'b-1', NULL, 0, '2026-10-22T10:00:00')"
            )
        )

    relink_res = await relink_bank_receipts("c1", "test_user_001")
    assert relink_res["relinked"] >= 1

    ali_receipt = next(r for r in (await list_bank_receipts("c1", statement_id="stmt-ali"))["items"])
    assert ali_receipt["parsedMeta"]["receiptNo"] == "REC-1001"

    # parked 剩下的 REC-1002 可手动 bind 到流水
    parked = (await list_bank_receipts("c1", parked=True))["items"]
    rec_1002 = next(r for r in parked if r["parsedMeta"]["receiptNo"] == "REC-1002")
    bound = await bind_bank_receipt("c1", "test_user_001", rec_1002["id"], "stmt-power")
    assert bound["bankStatementId"] == "stmt-power"

    # 三路聚合计数（银行回单 + 直连 + 发票）去重计数验证
    counts = await attachment_counts_by_entries("c1", [e1.id])
    assert counts[e1.id] == 1


@pytest.mark.asyncio
async def test_router_voucher_attachments_repost_rebind_and_voucher_book(isolated_env, monkeypatch):
    # 造 1 张已过账凭证 + 关联发票 + 关联银行流水回单 + 覆盖当月对账单 PDF
    async with session_scope() as session:
        await create_entry(
            session,
            {
                "id": "je-book-1",
                "direction": "expense",
                "amount": "300.00",
                "title": "采购报销及水电费",
                "category": "办公费",
                "occurDate": "2026-10-12",
                "sourceType": "manual",
                "voucherLines": {
                    "debit": [{"accountCode": "5602", "accountName": "管理费用", "amount": "300.00"}],
                    "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "300.00"}],
                },
            },
            company_id="c1",
            user_id="test_user_001",
            request_id="req-je-book-1",
            confirm=True,
        )
        inv = Invoice(
            id="inv-book-1",
            company_id="c1",
            entry_id="je-book-1",
            direction="expense",
            invoice_number="26330000111122223333",
            dedup_fingerprint="26330000111122223333",
            amount_excl_tax=Decimal("283.02"),
            tax_amount=Decimal("16.98"),
            amount_incl_tax=Decimal("300.00"),
        )
        session.add(inv)
        await session.execute(
            text(
                "INSERT INTO bank_statements (id, company_id, transaction_date, description, amount, balance, statement_id, matched_entry_id, description_normalized, created_at) "
                "VALUES ('stmt-book-1', 'c1', '2026-10-12', '杭州某办公用品公司', -300.00, 9700.00, 'b-2', 'je-book-1', 0, '2026-10-12T09:00:00')"
            )
        )

    headers = {"Authorization": "Bearer test-token-unit-test"}
    with TestClient(app) as tc:
        # 1) 为凭证直挂支付截图 + 报销单 + 工资表
        pay_png = _make_png(160)
        reimb_png = _make_png(170)
        payroll_png = _make_png(180)
        for doc_type, fname, content in (
            ("payment_screenshot", "wechat_pay.png", pay_png),
            ("reimbursement_form", "baoxiao.png", reimb_png),
            ("payroll_sheet", "salary_oct.png", payroll_png),
        ):
            resp = tc.post(
                f"/api/vouchers/je-book-1/attachments?document_type={doc_type}",
                headers=headers,
                files=[("files", (fname, content, "image/png"))],
            )
            assert resp.status_code == 200
            assert resp.json()["storedCount"] == 1

        # 2) 上传银行回单 + 对账单 PDF（带 stub OCR 使 REC-9001 命中 stmt-book-1）
        async def fake_receipt_extract(*_args, **_kwargs):
            return {
                "receiptNo": "REC-9001",
                "txDate": "2026-10-12",
                "amount": "300.00",
                "direction": "expense",
                "payeeName": "杭州某办公用品公司",
                "payerName": "杭州星辰科技有限公司",
                "summary": "办公用品",
            }

        monkeypatch.setattr("services.bank_receipt_service.extract_receipt_metadata", fake_receipt_extract)
        up_bank = tc.post(
            "/api/bank/receipts/upload?use_vision=false",
            headers=headers,
            files=[
                ("files", ("receipt_300.png", _make_png(190), "image/png")),
                ("files", ("杭州银行_客户交易标准对账单.pdf", _make_statement_pdf(), "application/pdf")),
            ],
        )
        assert up_bank.status_code == 200, up_bank.text
        up_json = up_bank.json()
        assert up_json["summary"]["matchedCount"] == 1, up_json
        assert up_bank.json()["summary"]["statementPdfCount"] == 1

        # 3) 查询凭证附件总览：3 直挂 + 1 银行回单 = 4 active
        listed = tc.get("/api/vouchers/je-book-1/attachments", headers=headers)
        assert listed.status_code == 200
        data = listed.json()
        assert data["activeCount"] == 4
        relations = {item["relation"] for item in data["items"]}
        assert relations == {"direct", "bank_statement"}

        # 4) 预览（inline）、下载（attachment）、作废（soft void）
        pay_att = next(i for i in data["items"] if i["documentType"] == "payment_screenshot")
        pv = tc.get(f"/api/attachments/{pay_att['id']}/preview", headers=headers)
        assert pv.status_code == 200
        assert pv.headers["content-disposition"].startswith("inline;")
        assert pv.content == pay_png

        dl = tc.get(f"/api/attachments/{pay_att['id']}/download", headers=headers)
        assert dl.status_code == 200
        assert dl.headers["content-disposition"].startswith("attachment;")

        vd = tc.post(f"/api/attachments/{pay_att['id']}/void", headers=headers, json={"reason": "重复截图作废"})
        assert vd.status_code == 200
        assert vd.json()["status"] == "void"

        # 作废后活跃计数变为 3
        after_void = tc.get("/api/vouchers/je-book-1/attachments?include_void=false", headers=headers).json()
        assert after_void["activeCount"] == 3

    # 5) 凭证册构建与 PDF 渲染：含剩余 3 张图片原件页 + 原件目录含覆盖当月的对账单 PDF
    book = await build_voucher_book("c1", month="2026-10")
    v = book["vouchers"][0]
    assert v["attachmentCount"] == 3
    assert len(v["attachments"]) == 3
    assert any(o["documentType"] == "bank_statement_pdf" and o["voucherNo"] == "期间对账单" for o in book["originals"])

    rendered = render_voucher_book_pdf(book)
    assert rendered.page_count == book["pagination"]["totalPages"]
    assert rendered.pdf_bytes[:4] == b"%PDF"

    # 6) 红冲重记迁移动作 _rebind_invoice_after_repost：即使无发票（invoice=None）也同步迁移 direct attachments
    async with session_scope() as session:
        old_entry = await session.get(JournalEntry, "je-book-1")
        assert old_entry is not None
        new_entry_row = JournalEntry(
            id="je-book-1-repost",
            company_id="c1",
            voucher_no="记-0099",
            occurred_on=old_entry.occurred_on,
            period_start=old_entry.period_start,
            summary="重记凭证",
            status="posted",
            entry_type="standard",
            currency="CNY",
            created_by="test_user_001",
        )
        session.add(new_entry_row)
        await session.flush()
        await _rebind_invoice_after_repost(
            session,
            company_id="c1",
            old_entry_id="je-book-1",
            new_entry=new_entry_row,
            user_id="test_user_001",
            request_id="req-repost-1",
        )

    migrated = await list_entry_attachments("c1", "je-book-1-repost", include_void=False)
    migrated_direct = [i for i in migrated["items"] if i["relation"] == "direct"]
    assert len(migrated_direct) == 2  # reimbursement_form + payroll_sheet 跟随新凭证
    old_left = await list_entry_attachments("c1", "je-book-1", include_void=False)
    assert all(i["relation"] != "direct" for i in old_left["items"])


@pytest.mark.asyncio
async def test_relink_reextracts_parked_receipt_missing_txdate(isolated_env, monkeypatch):
    # 先用仅含金额/缺日期的旧提取结果入库为待挂回单（模拟 qwen-vl-ocr 空日期场景）
    async def empty_date_extract(*_args, **_kwargs):
        return {
            "receiptNo": "",
            "txNo": "",
            "txDate": "",
            "amount": "88.50",
            "direction": "expense",
            "payerName": "杭州星辰科技有限公司",
            "payeeName": "电信营业厅",
            "summary": "宽带费",
            "receiptType": "账务回单-付款",
        }

    monkeypatch.setattr("services.bank_receipt_service.extract_receipt_metadata", empty_date_extract)
    up = await upload_bank_receipts(
        "c1",
        "test_user_001",
        [{"file_name": "telecom_88.png", "payload": _make_png(77)}],
    )
    assert up["summary"]["parkedCount"] == 1
    att_id = up["receipts"][0]["id"]
    assert up["receipts"][0]["period"] is None

    # 写入对应的银行流水，并切换成能识别出日期与回单号的提取器
    async with session_scope() as session:
        await session.execute(
            text(
                "INSERT INTO bank_statements (id, company_id, transaction_date, description, amount, balance, statement_id, matched_entry_id, description_normalized, created_at) "
                "VALUES ('stmt-tel', 'c1', '2026-10-25', '汇兑 电信营业厅 宽带费', -88.50, 8600.00, 'b-3', NULL, 0, '2026-10-25T10:00:00')"
            )
        )

    async def full_extract(*_args, **_kwargs):
        return {
            "receiptNo": "REC-TEL-8850",
            "txNo": "TX-8850",
            "txDate": "2026-10-25",
            "amount": "88.50",
            "direction": "expense",
            "payerName": "杭州星辰科技有限公司",
            "payeeName": "电信营业厅",
            "summary": "宽带费",
            "receiptType": "账务回单-付款",
        }

    monkeypatch.setattr("services.bank_receipt_service.extract_receipt_metadata", full_extract)
    res = await relink_bank_receipts("c1", "test_user_001")
    assert res["reextracted"] == 1
    assert res["relinked"] == 1

    matched = (await list_bank_receipts("c1", statement_id="stmt-tel"))["items"]
    assert len(matched) == 1
    assert matched[0]["id"] == att_id
    assert matched[0]["period"] == "2026-10"
    assert matched[0]["parsedMeta"]["receiptNo"] == "REC-TEL-8850"
