# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""发票「前期已付款入账」：跨期候选检测与挂接。

场景：DeepSeek 充值即开票——5-31 公户付款已被自动入账为费用（过账、当月关账），
6-02 收到发票生成挂账草稿（借 费用 / 贷 2241）。挂接后发票主数据迁到前期凭证、
草稿删除、6 月不再重复入账；候选检测必须排除金额不符/日期晚于发票/已冲销/
发票本体/已被其他发票占用的凭证。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import db.models  # noqa: F401
from core.errors import AccountingError
from db.base import Base
from db.models import AuditEvent, Invoice, JournalEntry, LegacyEntryProjection
from services.accounting_core_service import create_entry, ensure_tenant, period_close_checks, preview_voucher_lines
from services.invoice_prior_payment_service import (
    find_duplicate_prior_payment_invoices,
    find_prior_payment_candidates,
    link_prior_payment,
)

COMPANY = "c-prior"
SELLER = "杭州深度求索人工智能基础技术研究有限公司"


@pytest.fixture
async def sessions():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.exec_driver_sql(
            "CREATE TABLE bank_statements ("
            "id TEXT PRIMARY KEY, company_id TEXT NOT NULL, transaction_date TEXT NOT NULL, "
            "description TEXT NOT NULL, amount NUMERIC(20,2) NOT NULL, "
            "balance NUMERIC(20,2) NOT NULL, statement_id TEXT NOT NULL, "
            "matched_entry_id TEXT, description_normalized INTEGER NOT NULL, created_at TEXT NOT NULL, "
            "transaction_reference TEXT, source_row_index INTEGER)"
        )
        await connection.exec_driver_sql(
            "CREATE TABLE bank_statement_matches (statement_id TEXT, company_id TEXT, entry_id TEXT, "
            "previous_billing_status TEXT, previous_payment_date TEXT, created_at TEXT, "
            "PRIMARY KEY(statement_id,entry_id))"
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def prior_payment_payload(
    entry_id: str = "prior-pay-1",
    *,
    occurred: str = "2026-05-31",
    amount: str = "500.00",
    title: str | None = None,
) -> dict:
    """付款月被系统规则入账的费用凭证：借 费用 / 贷 1002（已过账）。"""
    return {
        "id": entry_id,
        "direction": "expense",
        "amount": amount,
        "title": title or ("信息技术服务费-" + occurred),
        "category": "其他支出",
        "occurDate": occurred,
        "sourceType": "bank_import",
        "source": "bank_import",
        "voucherLines": {
            "debit": [{"accountCode": "5602999", "accountName": "管理费用-其他", "amount": amount}],
            "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": amount}],
        },
    }


def invoice_draft_payload(
    entry_id: str = "invoice-draft-1",
    *,
    occurred: str = "2026-06-02",
    amount: str = "500.00",
    invoice_number: str = "26332000004655508586",
) -> dict:
    """收票月挂账草稿：借 费用 / 贷 2241（未过账）。"""
    return {
        "id": entry_id,
        "direction": "expense",
        "amount": amount,
        "title": f"发票-{SELLER}",
        "category": "其他支出",
        "occurDate": occurred,
        "sourceType": "invoice_import",
        "source": "invoice_import",
        "invoiceNumber": invoice_number,
        "voucherLines": {
            "debit": [{"accountCode": "5602999", "accountName": "管理费用-其他", "amount": amount}],
            "credit": [{"accountCode": "2241", "accountName": "其他应付款-发票待付款", "amount": amount}],
        },
    }


async def add_invoice_row(
    session,
    entry_id: str,
    invoice_id: str = "inv-prior-1",
    *,
    seller_name: str = SELLER,
    invoice_date: date = date(2026, 6, 2),
    amount: str = "500.00",
) -> None:
    session.add(
        Invoice(
            id=invoice_id,
            company_id=COMPANY,
            entry_id=entry_id,
            direction="expense",
            invoice_number="26332000004655508586",
            invoice_date=invoice_date,
            seller_name=seller_name,
            amount_excl_tax=Decimal(amount) * Decimal("0.9434"),
            tax_amount=Decimal(amount) - Decimal(amount) * Decimal("0.9434"),
            amount_incl_tax=Decimal(amount),
            dedup_fingerprint=f"fp-{invoice_id}",
        )
    )


async def link_statement_description(session, entry_id: str, description: str, statement_id: str = "bs-1") -> None:
    """给前期付款凭证挂上流水描述（模拟银行导入后的匹配现状）。"""
    await session.execute(
        text(
            "INSERT INTO bank_statements (id, company_id, transaction_date, description, amount, "
            "balance, statement_id, matched_entry_id, description_normalized, created_at) "
            "VALUES (:id, :cid, '2026-05-31 10:22:55', :desc, -500.00, 0.00, :sid, :eid, 0, '2026-05-31')"
        ),
        {"id": statement_id, "cid": COMPANY, "desc": description, "sid": statement_id, "eid": entry_id},
    )
    await session.execute(
        text(
            "INSERT INTO bank_statement_matches (statement_id, company_id, entry_id, "
            "previous_billing_status, previous_payment_date, created_at) "
            "VALUES (:sid, :cid, :eid, NULL, NULL, '2026-05-31')"
        ),
        {"sid": statement_id, "cid": COMPANY, "eid": entry_id},
    )


@pytest.mark.asyncio
async def test_candidates_find_cross_period_payment_and_rank_by_counterparty(sessions):
    """跨期命中：5 月付款 → 6 月发票；流水对方含销方名时排强置信。"""
    async with sessions() as session:
        async with session.begin():
            await create_entry(
                session, prior_payment_payload(), company_id=COMPANY, user_id="owner",
                request_id="req-create-prior", confirm=True,
            )
            await create_entry(
                session, invoice_draft_payload(), company_id=COMPANY, user_id="owner",
                request_id="req-create-draft", confirm=False,
            )
            await add_invoice_row(session, "invoice-draft-1")
            await link_statement_description(
                session, "prior-pay-1", "汇兑 | 附言: 往来款 (对方: " + SELLER + ")"
            )
            result = await find_prior_payment_candidates(session, company_id=COMPANY, invoice_entry_id="invoice-draft-1")

    candidates = result["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["entryId"] == "prior-pay-1"
    assert candidates[0]["occurredOn"] == "2026-05-31"
    assert candidates[0]["status"] == "posted"
    assert candidates[0]["confidence"] == "counterparty"
    assert "杭州深度求索" in candidates[0]["statementDescription"]


@pytest.mark.asyncio
async def test_candidates_exclude_irrelevant_entries(sessions):
    """排除：金额不符 / 日期晚于发票 / 已冲销 / 发票本体 / 归档底稿 / 已被其他发票占用。"""
    async with sessions() as session:
        async with session.begin():
            # 合规付款（唯一应命中的）
            await create_entry(
                session, prior_payment_payload(), company_id=COMPANY, user_id="owner",
                request_id="req-1", confirm=True,
            )
            # 金额不符
            wrong_amount = prior_payment_payload("prior-wrong-amount", amount="480.00")
            await create_entry(session, wrong_amount, company_id=COMPANY, user_id="owner",
                               request_id="req-2", confirm=True)
            # 日期晚于发票日（6 月之后才付款）
            late = prior_payment_payload("prior-late", occurred="2026-06-10")
            await create_entry(session, late, company_id=COMPANY, user_id="owner",
                               request_id="req-3", confirm=True)
            # 发票本体（另一张发票的挂账凭证，同金额）
            other_draft = invoice_draft_payload("other-invoice-draft", invoice_number="11111111111111111111")
            await create_entry(session, other_draft, company_id=COMPANY, user_id="owner",
                               request_id="req-4", confirm=False)
            # 归档底稿（不参与配对）
            archive_only = prior_payment_payload("prior-archive", occurred="2026-05-30")
            archive_only["isArchiveOnly"] = True
            await create_entry(session, archive_only, company_id=COMPANY, user_id="owner",
                               request_id="req-5", confirm=True)
            # 已被其他发票占用的付款凭证（投影 payload 带发票号）
            occupied = prior_payment_payload("prior-occupied", occurred="2026-05-29")
            await create_entry(session, occupied, company_id=COMPANY, user_id="owner",
                               request_id="req-6", confirm=True)
            await create_entry(
                session, invoice_draft_payload(), company_id=COMPANY, user_id="owner",
                request_id="req-7", confirm=False,
            )
            await add_invoice_row(session, "invoice-draft-1")
            projection = await session.get(LegacyEntryProjection, "prior-occupied")
            projection.payload = '{"invoiceNumber": "11111111111111111111"}'
            result = await find_prior_payment_candidates(session, company_id=COMPANY, invoice_entry_id="invoice-draft-1")

    entry_ids = [c["entryId"] for c in result["candidates"]]
    assert "prior-pay-1" in entry_ids
    for excluded in ("prior-wrong-amount", "prior-late", "other-invoice-draft", "prior-archive", "prior-occupied"):
        assert excluded not in entry_ids, excluded


@pytest.mark.asyncio
async def test_candidates_generic_word_overlap_is_not_text_confidence(sessions):
    """回归 2026-10 误报：「报销」两字交集曾把无关的报销付款推成中置信建议。

    王翼 10-31 的发票（¥300）被挂到 6-26「报销垫付款-发票-深圳市腾讯」——
    两份摘要只有「报销」一词相同。通用词交集必须降级为 amount_only，
    由「前期已付款」列表人工判断，不当强建议推给用户。
    """
    async with sessions() as session:
        async with session.begin():
            await create_entry(
                session,
                prior_payment_payload(
                    "prior-baoxiao-626",
                    occurred="2026-06-26",
                    amount="300.00",
                    title="报销垫付款-发票-深圳市腾讯计算机系统有限公司",
                ),
                company_id=COMPANY, user_id="owner", request_id="req-1", confirm=True,
            )
            await create_entry(
                session,
                invoice_draft_payload("invoice-draft-wangyi", occurred="2026-10-31", amount="300.00"),
                company_id=COMPANY, user_id="owner", request_id="req-2", confirm=False,
            )
            await add_invoice_row(
                session, "invoice-draft-wangyi", invoice_id="inv-wangyi",
                seller_name="王翼", invoice_date=date(2026, 10, 31), amount="300.00",
            )
            await link_statement_description(
                session, "prior-baoxiao-626", "汇兑 | 附言: 报销微信开放平台认证费用 (对方: 杨云飞)",
                statement_id="bs-baoxiao",
            )
            result = await find_prior_payment_candidates(
                session, company_id=COMPANY, invoice_entry_id="invoice-draft-wangyi"
            )

    candidates = result["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["confidence"] == "amount_only"


@pytest.mark.asyncio
async def test_candidates_exclude_reversed_payment(sessions):
    """已冲销的付款凭证不能作为挂接目标（挂上去会指向无效凭证）。"""
    from services.accounting_core_service import reverse_journal

    async with sessions() as session:
        async with session.begin():
            await create_entry(
                session, prior_payment_payload(), company_id=COMPANY, user_id="owner",
                request_id="req-1", confirm=True,
            )
            await create_entry(
                session, invoice_draft_payload(), company_id=COMPANY, user_id="owner",
                request_id="req-2", confirm=False,
            )
            await add_invoice_row(session, "invoice-draft-1")
            await reverse_journal(
                session, "prior-pay-1", company_id=COMPANY, user_id="owner",
                request_id="req-3", reason="测试冲销",
            )
            result = await find_prior_payment_candidates(session, company_id=COMPANY, invoice_entry_id="invoice-draft-1")

    assert result["candidates"] == []


@pytest.mark.asyncio
async def test_link_rebinds_invoice_deletes_draft_and_writes_evidence(sessions):
    """挂接：entry_id 迁移 + 发票号写入前期凭证投影 + 草稿删除 + 审计留痕。"""
    async with sessions() as session:
        async with session.begin():
            await create_entry(
                session, prior_payment_payload(), company_id=COMPANY, user_id="owner",
                request_id="req-1", confirm=True,
            )
            await create_entry(
                session, invoice_draft_payload(), company_id=COMPANY, user_id="owner",
                request_id="req-2", confirm=False,
            )
            await add_invoice_row(session, "invoice-draft-1")
            result = await link_prior_payment(
                session,
                company_id=COMPANY,
                invoice_entry_id="invoice-draft-1",
                prior_entry_id="prior-pay-1",
                user_id="owner",
                request_id="req-link",
            )

        assert result["priorEntry"]["id"] == "prior-pay-1"
        invoice = (await session.execute(select(Invoice).where(Invoice.id == "inv-prior-1"))).scalar_one()
        assert invoice.entry_id == "prior-pay-1"  # 主数据指向活凭证（A3）
        assert await session.get(JournalEntry, "invoice-draft-1") is None  # 草稿已删
        projection = await session.get(LegacyEntryProjection, "prior-pay-1")
        assert "26332000004655508586" in str(projection.payload)  # 前期凭证带发票号证据
        audits = (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.company_id == COMPANY,
                    AuditEvent.action.in_(("rebind_invoice_entry", "delete_draft")),
                )
            )
        ).scalars().all()
        assert {audit.action for audit in audits} == {"rebind_invoice_entry", "delete_draft"}
        rebind = next(audit for audit in audits if audit.action == "rebind_invoice_entry")
        assert rebind.after["reason"] == "prior_paid_link"

        # 挂接后再查候选：草稿已不存在，返回空
        result = await find_prior_payment_candidates(session, company_id=COMPANY, invoice_entry_id="invoice-draft-1")
        assert result["candidates"] == []


@pytest.mark.asyncio
async def test_link_rejects_posted_invoice_entry(sessions):
    """发票凭证已过账：不能改挂，走冲销/重新选择。"""
    from services.accounting_core_service import get_journal

    async with sessions() as session:
        async with session.begin():
            await create_entry(
                session, prior_payment_payload(), company_id=COMPANY, user_id="owner",
                request_id="req-1", confirm=True,
            )
            await create_entry(
                session, invoice_draft_payload(), company_id=COMPANY, user_id="owner",
                request_id="req-2", confirm=True,  # 直接过账
            )
            await add_invoice_row(session, "invoice-draft-1")
            with pytest.raises(AccountingError) as exc_info:
                await link_prior_payment(
                    session, company_id=COMPANY, invoice_entry_id="invoice-draft-1",
                    prior_entry_id="prior-pay-1", user_id="owner", request_id="req-link",
                )
    assert exc_info.value.code == "ENTRY_POSTED_IMMUTABLE"


@pytest.mark.asyncio
async def test_link_rejects_amount_mismatch_and_late_date(sessions):
    """金额不一致 / 前期凭证晚于发票日，均不可挂接。"""
    async with sessions() as session:
        async with session.begin():
            await create_entry(
                session, prior_payment_payload("prior-wrong", amount="480.00"),
                company_id=COMPANY, user_id="owner", request_id="req-1", confirm=True,
            )
            await create_entry(
                session, prior_payment_payload("prior-late", occurred="2026-06-10"),
                company_id=COMPANY, user_id="owner", request_id="req-2", confirm=True,
            )
            await create_entry(
                session, invoice_draft_payload(), company_id=COMPANY, user_id="owner",
                request_id="req-3", confirm=False,
            )
            await add_invoice_row(session, "invoice-draft-1")
            with pytest.raises(AccountingError) as amount_error:
                await link_prior_payment(
                    session, company_id=COMPANY, invoice_entry_id="invoice-draft-1",
                    prior_entry_id="prior-wrong", user_id="owner", request_id="req-4",
                )
            assert amount_error.value.code == "PRIOR_PAYMENT_AMOUNT_MISMATCH"
            with pytest.raises(AccountingError) as late_error:
                await link_prior_payment(
                    session, company_id=COMPANY, invoice_entry_id="invoice-draft-1",
                    prior_entry_id="prior-late", user_id="owner", request_id="req-5",
                )
            assert late_error.value.code == "PRIOR_ENTRY_AFTER_INVOICE"


@pytest.mark.asyncio
async def test_link_rejects_invoice_source_prior_entry(sessions):
    """前期凭证若是别的发票凭证/发票付款，不能当作付款挂接。"""
    async with sessions() as session:
        async with session.begin():
            await create_entry(
                session, invoice_draft_payload("other-invoice-draft", occurred="2026-05-31", invoice_number="11111111111111111111"),
                company_id=COMPANY, user_id="owner", request_id="req-1", confirm=True,
            )
            await create_entry(
                session, invoice_draft_payload(), company_id=COMPANY, user_id="owner",
                request_id="req-2", confirm=False,
            )
            await add_invoice_row(session, "invoice-draft-1", invoice_id="inv-prior-2")
            with pytest.raises(AccountingError) as exc_info:
                await link_prior_payment(
                    session, company_id=COMPANY, invoice_entry_id="invoice-draft-1",
                    prior_entry_id="other-invoice-draft", user_id="owner", request_id="req-3",
                )
    assert exc_info.value.code == "PRIOR_ENTRY_NOT_PAYMENT"


@pytest.mark.asyncio
async def test_preview_prepaid_balance_insufficient_warning(sessions):
    """预存扣款守门：1123 余额不足以冲减时，预览必须给出警告与数字。"""
    def prepaid_payload(entry_id: str, amount: str) -> dict:
        return {
            "id": entry_id,
            "direction": "expense",
            "amount": amount,
            "title": "供应商预付款",
            "category": "预付账款",
            "occurDate": "2026-05-20",
            "sourceType": "manual",
            "voucherLines": {
                "debit": [{"accountCode": "1123", "accountName": "预付账款", "amount": amount}],
                "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": amount}],
            },
        }

    def big_expense_payload(entry_id: str, amount: str = "500.00") -> dict:
        return {
            "id": entry_id,
            "direction": "expense",
            "amount": amount,
            "title": "发票-杭州深度求索人工智能基础技术研究有限公司",
            "category": "其他支出",
            "occurDate": "2026-06-02",
            "sourceType": "invoice_import",
            "source": "invoice_import",
            "voucherLines": {
                "debit": [{"accountCode": "5602999", "accountName": "管理费用-其他", "amount": amount}],
                "credit": [{"accountCode": "2241", "accountName": "其他应付款-发票待付款", "amount": amount}],
            },
        }

    async with sessions() as session:
        async with session.begin():
            # 余额不足：预付 100 < 冲减 500
            await create_entry(session, prepaid_payload("prepay-small", "100.00"),
                               company_id=COMPANY, user_id="owner", request_id="req-1", confirm=True)
            await create_entry(session, big_expense_payload("invoice-draft-1"),
                               company_id=COMPANY, user_id="owner", request_id="req-2", confirm=False)
            preview = await preview_voucher_lines(
                session, "invoice-draft-1", company_id=COMPANY, payer="prepaid_deduct",
            )
            codes = [warning["code"] for warning in preview["warnings"]]
            assert "PREPAID_BALANCE_INSUFFICIENT" in codes
            warning = next(w for w in preview["warnings"] if w["code"] == "PREPAID_BALANCE_INSUFFICIENT")
            assert warning["currentBalance"] == "100.00"
            assert warning["deductAmount"] == "500.00"

            # 余额充足：预付 600 ≥ 500，不告警
            await create_entry(session, prepaid_payload("prepay-large", "600.00"),
                               company_id=COMPANY, user_id="owner", request_id="req-3", confirm=True)
            preview_ok = await preview_voucher_lines(
                session, "invoice-draft-1", company_id=COMPANY, payer="prepaid_deduct",
            )
            assert "PREPAID_BALANCE_INSUFFICIENT" not in [w["code"] for w in preview_ok["warnings"]]
            assert [line["accountCode"] for line in preview_ok["credit"]] == ["1123"]


@pytest.mark.asyncio
async def test_candidates_match_truncated_and_core_company_name(sessions):
    """户名匹配增强：银行截断户名及去后缀核心字号（≥4字）命中强置信，2字短名不误报。"""
    async with sessions() as session:
        async with session.begin():
            # 1. 银行流水对方被截断：「杭州深度求索人工智能基础技术」vs 发票全称「杭州深度求索人工智能基础技术研究有限公司」
            await create_entry(
                session,
                prior_payment_payload("prior-trunc", occurred="2026-05-31", amount="500.00"),
                company_id=COMPANY, user_id="owner", request_id="req-t1", confirm=True,
            )
            await link_statement_description(
                session,
                "prior-trunc",
                "汇兑 | 附言: 往来款 (对方: 杭州深度求索人工智能基础技术)",
                statement_id="bs-trunc",
            )
            # 2. 附言含核心字号「深度求索」
            await create_entry(
                session,
                prior_payment_payload("prior-core", occurred="2026-05-30", amount="500.00"),
                company_id=COMPANY, user_id="owner", request_id="req-t2", confirm=True,
            )
            await link_statement_description(
                session,
                "prior-core",
                "汇兑 | 附言: 深度求索充值",
                statement_id="bs-core",
            )
            await create_entry(
                session,
                invoice_draft_payload("inv-ds", occurred="2026-06-02", amount="500.00"),
                company_id=COMPANY, user_id="owner", request_id="req-t3", confirm=False,
            )
            await add_invoice_row(session, "inv-ds", invoice_id="inv-ds-row", seller_name=SELLER, amount="500.00")
            res = await find_prior_payment_candidates(session, company_id=COMPANY, invoice_entry_id="inv-ds")

            by_id = {c["entryId"]: c for c in res["candidates"]}
            assert by_id["prior-trunc"]["confidence"] == "counterparty"
            assert by_id["prior-core"]["confidence"] == "counterparty"

            # 3. 短个人姓名（2字）不应误判为强置信 counterparty
            await create_entry(
                session,
                prior_payment_payload("prior-short", occurred="2026-05-28", amount="300.00"),
                company_id=COMPANY, user_id="owner", request_id="req-t4", confirm=True,
            )
            await link_statement_description(
                session, "prior-short", "汇兑 | 附言: 测试费 (对方: 李四)", statement_id="bs-short"
            )
            await create_entry(
                session,
                invoice_draft_payload("inv-short", occurred="2026-06-02", amount="300.00", invoice_number="99999999"),
                company_id=COMPANY, user_id="owner", request_id="req-t5", confirm=False,
            )
            await add_invoice_row(
                session, "inv-short", invoice_id="inv-short-row", seller_name="王翼", amount="300.00"
            )
            res_short = await find_prior_payment_candidates(session, company_id=COMPANY, invoice_entry_id="inv-short")
            assert all(c["confidence"] != "counterparty" for c in res_short["candidates"])


@pytest.mark.asyncio
async def test_preview_voucher_lines_warns_on_prior_payment_duplicate_risk(sessions):
    """事前防呆：进项票预览选老板垫付/员工垫付/还没付款且命中近90天公户付款时，返回重复风险告警。"""
    async with sessions() as session:
        async with session.begin():
            await ensure_tenant(session, COMPANY, "owner")
            await create_entry(
                session,
                prior_payment_payload("prior-pay-1", occurred="2026-05-31", amount="500.00"),
                company_id=COMPANY, user_id="owner", request_id="req-p1", confirm=True,
            )
            await link_statement_description(
                session,
                "prior-pay-1",
                f"汇兑 | 附言: 往来款 (对方: {SELLER})",
                statement_id="bs-p1",
            )
            await create_entry(
                session,
                invoice_draft_payload("invoice-draft-1", occurred="2026-06-02", amount="500.00"),
                company_id=COMPANY, user_id="owner", request_id="req-p2", confirm=False,
            )
            await add_invoice_row(session, "invoice-draft-1")

            prev_boss = await preview_voucher_lines(
                session, "invoice-draft-1", company_id=COMPANY, payer="boss_personal"
            )
            boss_warn = next((w for w in prev_boss["warnings"] if w["code"] == "PRIOR_PAYMENT_DUPLICATE_RISK"), None)
            assert boss_warn is not None
            assert boss_warn["priorEntryId"] == "prior-pay-1"
            assert boss_warn["priorAmount"] == "500.00"

            prev_unpaid = await preview_voucher_lines(
                session, "invoice-draft-1", company_id=COMPANY, payment_status="unpaid"
            )
            assert any(w["code"] == "PRIOR_PAYMENT_DUPLICATE_RISK" for w in prev_unpaid["warnings"])

            prev_company = await preview_voucher_lines(
                session, "invoice-draft-1", company_id=COMPANY, payer="company_account"
            )
            assert not any(w["code"] == "PRIOR_PAYMENT_DUPLICATE_RISK" for w in prev_company["warnings"])


@pytest.mark.asyncio
async def test_period_close_checks_emits_duplicate_prior_payment_notice(sessions):
    """事后兜底：结账体检检测到本月老板垫付进项票与前期公户付款重复时，输出 DUPLICATE_PRIOR_PAYMENT_INVOICE 提示。"""
    async with sessions() as session:
        async with session.begin():
            await ensure_tenant(session, COMPANY, "owner")
            await create_entry(
                session,
                prior_payment_payload("prior-pay-1", occurred="2026-05-31", amount="500.00"),
                company_id=COMPANY, user_id="owner", request_id="req-c1", confirm=True,
            )
            await link_statement_description(
                session,
                "prior-pay-1",
                f"汇兑 | 附言: 往来款 (对方: {SELLER})",
                statement_id="bs-c1",
            )
            dup_inv = invoice_draft_payload("invoice-dup-602", occurred="2026-06-02", amount="500.00")
            dup_inv["payer"] = "boss_personal"
            dup_inv["voucherLines"] = {
                "debit": [{"accountCode": "5602015", "accountName": "管理费用-信息技术服务费", "amount": "500.00"}],
                "credit": [{"accountCode": "2241", "accountName": "其他应付款", "amount": "500.00"}],
            }
            await create_entry(
                session, dup_inv, company_id=COMPANY, user_id="owner", request_id="req-c2", confirm=True,
            )
            await add_invoice_row(session, "invoice-dup-602")

            scan = await find_duplicate_prior_payment_invoices(
                session, company_id=COMPANY, month_start=date(2026, 6, 1)
            )
            assert scan["count"] == 1
            assert scan["amount"] == "500.00"
            assert scan["records"][0]["priorEntryId"] == "prior-pay-1"

            checks = await period_close_checks(session, COMPANY, "2026-06")
            dup_notice = next(
                (n for n in checks["notices"] if n["code"] == "DUPLICATE_PRIOR_PAYMENT_INVOICE"), None
            )
            assert dup_notice is not None
            assert dup_notice["count"] == 1
            assert dup_notice["amount"] == "500.00"
            assert dup_notice["records"][0]["entryId"] == "invoice-dup-602"
            assert dup_notice["records"][0]["priorEntryId"] == "prior-pay-1"
