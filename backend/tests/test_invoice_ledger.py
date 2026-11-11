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


@pytest.mark.asyncio
async def test_red_letter_expense_imports_as_reversal():
    """红冲进项（负金额）导入为冲销凭证：借 2241 / 贷费用，金额取正。"""
    company_id = "c-inv-reversal"
    await _setup_company(company_id)

    result = await import_invoices(
        company_id,
        "owner-1",
        [_expense_row(invoice_number="INV-RED", total_amount="-106.00", tax_amount="-6.00")],
        classify=False,
    )
    assert result["imported"] == 1
    assert result["invoicesWritten"] == 1

    from sqlalchemy import select

    from db.models import JournalLine
    from db.session import session_scope

    async with session_scope() as session:
        lines = (
            await session.execute(
                select(JournalLine).where(JournalLine.company_id == company_id)
            )
        ).scalars().all()
        debit_lines = [line for line in lines if line.direction == "debit"]
        credit_lines = [line for line in lines if line.direction == "credit"]
        assert [line.account_code for line in debit_lines] == ["2241"]
        assert credit_lines and all(line.account_code != "2241" for line in credit_lines)
        assert sum(float(line.amount) for line in debit_lines) == pytest.approx(106.0)
        assert sum(float(line.amount) for line in credit_lines) == pytest.approx(106.0)
    assert (await list_invoices(company_id))["total"] == 1


# ── 先付款、后收票：发票导入自动冲抵往来暂付款（第二期） ────────────────────

async def _seed_wanglai(
    company_id: str,
    entry_id: str,
    *,
    amount: str = "500.00",
    occur: str = "2026-05-20",
    direction: str = "expense",
    confirm: bool = False,
) -> None:
    """种一张往来暂付（expense：借 122199/贷 1002）或暂收（income：借 1002/贷 2241）凭证。"""
    from services.entry_service import save_entry

    if direction == "expense":
        voucher = {
            "debit": [{"accountCode": "122199", "accountName": "其他应收款-待收款项", "amount": amount}],
            "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": amount}],
        }
    else:
        voucher = {
            "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": amount}],
            "credit": [{"accountCode": "2241", "accountName": "其他应付款-其他", "amount": amount}],
        }
    await save_entry(
        {
            "id": entry_id,
            "title": f"往来暂付-{occur}",
            "category": "往来款",
            "amount": amount,
            "direction": direction,
            "occurDate": occur,
            "source": "bank_import",
            "voucherLines": voucher,
        },
        user_id="owner-1",
        company_id=company_id,
        trusted_voucher=True,
        confirm=confirm,
    )


def _offset_expense_row(invoice_number: str, *, amount="500.00", invoice_date="2026-06-05", seller="杭州乙网络科技有限公司"):
    return _expense_row(
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        total_amount=amount,
        amount=amount,
        tax_amount="0",
        seller_name=seller,
        item_name="技术服务费",
    )


@pytest.mark.asyncio
async def test_expense_invoice_auto_offsets_prior_wanglai():
    """6 月费用票导入时命中 5 月等额往来暂付款 → 贷方直接 122199 对冲并双向标记。"""
    company_id = "c-inv-offset"
    await _setup_company(company_id)
    await _seed_wanglai(company_id, "wanglai-may", occur="2026-05-20")

    result = await import_invoices(
        company_id, "owner-1", [_offset_expense_row("INV-OFFSET-1")], classify=False
    )
    assert result["imported"] == 1
    assert len(result["offsets"]) == 1
    assert result["offsets"][0]["offsetEntryId"] == "wanglai-may"
    assert result["offsets"][0]["amount"] == "500.00"

    from sqlalchemy import select

    from db.models import JournalEntry, JournalLine
    from db.session import session_scope

    async with session_scope() as session:
        invoice_entry_id = result["entries"][0]["id"]
        inv_entry = await session.get(JournalEntry, invoice_entry_id)
        assert inv_entry.metadata_json.get("offsetFromEntryId") == "wanglai-may"
        credit_lines = (
            await session.execute(
                select(JournalLine).where(
                    JournalLine.journal_entry_id == invoice_entry_id,
                    JournalLine.direction == "credit",
                )
            )
        ).scalars().all()
        assert [line.account_code for line in credit_lines] == ["122199"]
        assert credit_lines[0].account_name == "其他应收款-待收款项"
        # 冲抵行的往来单位辅助（partner）自动挂上
        assert credit_lines[0].auxiliary.get("partner_name") == "杭州乙网络科技有限公司"
        # 暂付款被标记已冲抵
        wanglai = await session.get(JournalEntry, "wanglai-may")
        assert wanglai.metadata_json.get("linkedInvoiceEntryId") == invoice_entry_id


@pytest.mark.asyncio
async def test_wanglai_offset_not_reused_for_second_invoice():
    """同一笔暂付款不被二次冲抵：第二张同额发票回落 2241 挂账。"""
    company_id = "c-inv-offset-once"
    await _setup_company(company_id)
    await _seed_wanglai(company_id, "wanglai-once", occur="2026-05-20")

    first = await import_invoices(
        company_id, "owner-1", [_offset_expense_row("INV-OFFSET-A")], classify=False
    )
    second = await import_invoices(
        company_id, "owner-1", [_offset_expense_row("INV-OFFSET-B")], classify=False
    )
    assert first["imported"] == 1 and second["imported"] == 1
    assert len(first["offsets"]) == 1
    assert second["offsets"] == []

    from sqlalchemy import select

    from db.models import JournalLine
    from db.session import session_scope

    async with session_scope() as session:
        second_credit = (
            await session.execute(
                select(JournalLine).where(
                    JournalLine.journal_entry_id == second["entries"][0]["id"],
                    JournalLine.direction == "credit",
                )
            )
        ).scalars().all()
        assert [line.account_code for line in second_credit] == ["2241"]


@pytest.mark.asyncio
async def test_posted_wanglai_marked_via_projection():
    """已过账暂付款的冲抵标记走投影白名单（linkedInvoiceEntryId）。"""
    company_id = "c-inv-offset-posted"
    await _setup_company(company_id)
    await _seed_wanglai(company_id, "wanglai-posted", occur="2026-05-20", confirm=True)

    result = await import_invoices(
        company_id, "owner-1", [_offset_expense_row("INV-OFFSET-P")], classify=False
    )
    assert result["imported"] == 1 and len(result["offsets"]) == 1

    from sqlalchemy import select

    from db.models import LegacyEntryProjection
    from db.session import session_scope

    async with session_scope() as session:
        projection = (
            await session.execute(
                select(LegacyEntryProjection).where(LegacyEntryProjection.id == "wanglai-posted")
            )
        ).scalar_one()
        payload = json.loads(projection.payload) if isinstance(projection.payload, str) else projection.payload
        assert payload.get("linkedInvoiceEntryId") == result["entries"][0]["id"]


@pytest.mark.asyncio
async def test_income_invoice_offsets_prior_temp_receipt():
    """收入票对称：5 月往来暂收款（贷 2241）+ 6 月销售票 → 借方冲 2241。"""
    company_id = "c-inv-offset-income"
    await _setup_company(company_id)
    await _seed_wanglai(company_id, "wanglai-receipt", occur="2026-05-20", direction="income")

    row = {
        "invoice_number": "INV-OFFSET-I",
        "invoice_date": "2026-06-05",
        "total_amount": "500.00",
        "amount": "500.00",
        "tax_amount": "0",
        "buyer_name": "杭州丙贸易有限公司",
        "seller_name": "默认账本",
        "direction": "income",
        "item_name": "技术服务费",
        "category": "主营业务收入",
    }
    result = await import_invoices(company_id, "owner-1", [row], classify=False)
    assert result["imported"] == 1 and len(result["offsets"]) == 1

    from sqlalchemy import select

    from db.models import JournalLine
    from db.session import session_scope

    async with session_scope() as session:
        debit_lines = (
            await session.execute(
                select(JournalLine).where(
                    JournalLine.journal_entry_id == result["entries"][0]["id"],
                    JournalLine.direction == "debit",
                )
            )
        ).scalars().all()
        assert [line.account_code for line in debit_lines] == ["2241"]


def test_validate_invoice_voucher_offset_whitelist():
    """offset 白名单：冲抵模式放行 122199（费用贷）/2241（收入借），默认模式不变。"""
    from services.invoice_service import _validate_invoice_voucher

    expense_voucher = {
        "debit": [{"accountCode": "5602999", "accountName": "管理费用-其他", "amount": "500"}],
        "credit": [{"accountCode": "122199", "accountName": "其他应收款-待收款项", "amount": "500"}],
    }
    assert _validate_invoice_voucher("expense", expense_voucher, offset=True) == expense_voucher
    with pytest.raises(ValueError):
        _validate_invoice_voucher("expense", expense_voucher)

    income_voucher = {
        "debit": [{"accountCode": "2241", "accountName": "其他应付款-其他", "amount": "500"}],
        "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": "500"}],
    }
    assert _validate_invoice_voucher("income", income_voucher, offset=True) == income_voucher
    with pytest.raises(ValueError):
        _validate_invoice_voucher("income", income_voucher)


# ── 导入时确认付款方：老板/员工垫付 → 2241 挂个人往来 ──────────────────────


async def _entry_and_payable_lines(entry_id: str):
    from sqlalchemy import select

    from db.models import JournalEntry, JournalLine
    from db.session import session_scope

    async with session_scope() as session:
        entry = await session.get(JournalEntry, entry_id)
        lines = (
            await session.execute(
                select(JournalLine).where(
                    JournalLine.journal_entry_id == entry_id,
                    JournalLine.account_code == "2241",
                )
            )
        ).scalars().all()
        return (
            dict(entry.metadata_json or {}),
            [dict(line.auxiliary or {}) for line in lines],
        )


@pytest.mark.asyncio
async def test_boss_paid_expense_invoice_links_shareholder_partner():
    """老板垫付导入：2241 的往来单位是股东（P-BOSS），不是票面供应商。"""
    from sqlalchemy import select

    from db.models import Partner
    from db.session import session_scope

    company_id = "c-inv-payer-boss"
    await _setup_company(company_id)

    result = await import_invoices(
        company_id,
        "owner-1",
        [_expense_row(invoice_number="INV-BOSS-1", payer="boss_personal")],
        classify=False,
    )
    assert result["imported"] == 1

    metadata, payables = await _entry_and_payable_lines(result["entries"][0]["id"])
    assert metadata["payer"] == "boss_personal"
    assert metadata["paymentStatus"] == "paid"
    assert len(payables) == 1
    assert payables[0]["partner_name"] == "股东"

    async with session_scope() as session:
        partner = await session.get(Partner, payables[0]["partner_id"])
        assert partner.code == "P-BOSS"
        assert partner.type == "person"


@pytest.mark.asyncio
async def test_employee_paid_expense_invoice_links_employee_partner():
    """员工垫付导入：2241 挂到该员工（带姓名的 employee_personal）。"""
    from sqlalchemy import select

    from db.models import Partner
    from db.session import session_scope

    company_id = "c-inv-payer-employee"
    await _setup_company(company_id)

    result = await import_invoices(
        company_id,
        "owner-1",
        [_expense_row(invoice_number="INV-EMP-1", payer="employee_personal:张三")],
        classify=False,
    )
    assert result["imported"] == 1

    metadata, payables = await _entry_and_payable_lines(result["entries"][0]["id"])
    assert metadata["payer"] == "employee_personal:张三"
    assert metadata["paymentStatus"] == "paid"
    assert payables[0]["partner_name"] == "张三"

    async with session_scope() as session:
        partner = await session.get(Partner, payables[0]["partner_id"])
        assert partner.type == "person"
        assert partner.code != "P-BOSS"


@pytest.mark.asyncio
async def test_company_account_invoice_keeps_supplier_partner():
    """公司账户付款（默认）：既有行为不变，2241 仍挂票面供应商，不建个人往来。"""
    from sqlalchemy import select

    from db.models import Partner
    from db.session import session_scope

    company_id = "c-inv-payer-company"
    await _setup_company(company_id)

    result = await import_invoices(
        company_id, "owner-1", [_expense_row(invoice_number="INV-CO-1")], classify=False
    )
    assert result["imported"] == 1

    metadata, payables = await _entry_and_payable_lines(result["entries"][0]["id"])
    assert metadata["payer"] == "company_account"
    assert "paymentStatus" not in metadata
    assert payables[0]["partner_name"] == "杭州甲科技有限公司"

    async with session_scope() as session:
        people = (
            await session.execute(
                select(Partner).where(
                    Partner.company_id == company_id, Partner.type == "person"
                )
            )
        ).scalars().all()
        assert people == []


@pytest.mark.asyncio
async def test_unpaid_invoice_keeps_supplier_partner_even_with_boss_payer():
    """未付款票：即使选了老板垫付，对手方仍是供应商，个人往来不抢位。"""
    from sqlalchemy import select

    from db.models import Partner
    from db.session import session_scope

    company_id = "c-inv-payer-unpaid"
    await _setup_company(company_id)

    result = await import_invoices(
        company_id,
        "owner-1",
        [_expense_row(
            invoice_number="INV-UNPAID-1",
            payer="boss_personal",
            paymentStatus="unpaid",
        )],
        classify=False,
    )
    assert result["imported"] == 1

    metadata, payables = await _entry_and_payable_lines(result["entries"][0]["id"])
    assert metadata["payer"] == "boss_personal"
    assert metadata["paymentStatus"] == "unpaid"
    assert payables[0]["partner_name"] == "杭州甲科技有限公司"

    async with session_scope() as session:
        people = (
            await session.execute(
                select(Partner).where(
                    Partner.company_id == company_id, Partner.type == "person"
                )
            )
        ).scalars().all()
        assert people == []


@pytest.mark.asyncio
async def test_tech_service_invoice_not_auto_archived():
    """普通供应商的技术服务费发票不再被"平台手续费"启发式误归档（DeepSeek 实测坑）；
    支付平台（财付通等）卖方的仍按底稿归档。"""
    company_id = "c-inv-techfee"
    await _setup_company(company_id)
    # 顺序导入（同批并发行有既有的 partner 建档竞态，与本用例无关）
    first = await import_invoices(
        company_id, "owner-1", [_expense_row(
            invoice_number="INV-TECH-1",
            seller_name="杭州深度求索人工智能基础技术研究有限公司",
            item_name="*生产生活服务*技术服务费",
        )], classify=False,
    )
    second = await import_invoices(
        company_id, "owner-1", [_expense_row(
            invoice_number="INV-TECH-2",
            seller_name="财付通支付科技有限公司",
            item_name="*信息技术服务*技术服务费",
        )], classify=False,
    )
    assert first["imported"] == 1 and second["imported"] == 1

    from sqlalchemy import select

    from db.models import JournalEntry
    from db.session import session_scope

    async with session_scope() as session:
        metas = {}
        for row in (await session.execute(select(JournalEntry).where(
            JournalEntry.company_id == company_id,
        ))).scalars().all():
            metas[row.metadata_json.get("invoiceNumber")] = row.metadata_json
    # 普通供应商技术服务票：不归档（进待办撮合池、可参与正常记账）
    assert metas["INV-TECH-1"].get("isArchiveOnly") in (None, False, "")
    # 支付平台卖方：仍自动归档为底稿
    assert metas["INV-TECH-2"].get("isArchiveOnly") is True


@pytest.mark.asyncio
async def test_link_invoice_to_expense_rebooks_payable_hang():
    """付款凭证借应付款（跨期"发票先到"形态）：关联核销不得红冲唯一费用记账，
    而是把发票挂账行 2241→2202 重订到付款对手科目，双方对冲平账。"""
    company_id = "c-inv-link-payable"
    await _setup_company(company_id)

    from services.entry_service import save_entry
    from services.todo_service import link_invoice_to_expense

    await save_entry(
        {
            "id": "inv-posted",
            "title": "发票-杭州深度求索人工智能基础技术研究有限公司",
            "category": "其他支出",
            "amount": "500.00",
            "direction": "expense",
            "occurDate": "2026-06-02",
            "source": "invoice_import",
            "invoiceNumber": "INV-LINK-1",
            "merchant": "杭州深度求索人工智能基础技术研究有限公司",
            "billingStatus": "has_invoice",
            "isArchiveOnly": True,
            "voucherLines": {
                "debit": [{"accountCode": "5602999", "accountName": "管理费用-其他", "amount": "500.00"}],
                "credit": [{
                    "accountCode": "2241", "accountName": "其他应付款", "amount": "500.00",
                    "auxiliary": {"partner_name": "杭州深度求索人工智能基础技术研究有限公司"},
                }],
            },
        },
        user_id="owner-1", company_id=company_id, trusted_voucher=True, confirm=True,
    )
    await save_entry(
        {
            "id": "pay-entry",
            "title": "支付杭州深度求索人工智能基础技术研究有限公司往来款",
            "category": "供应商付款",
            "amount": "500.00",
            "direction": "expense",
            "occurDate": "2026-05-31",
            "source": "bank_import",
            "billingStatus": "reconciled",
            "voucherLines": {
                "debit": [{"accountCode": "2202", "accountName": "应付账款", "amount": "500.00"}],
                "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "500.00"}],
            },
        },
        user_id="owner-1", company_id=company_id, trusted_voucher=True, confirm=True,
    )

    result = await link_invoice_to_expense(
        "inv-posted", "pay-entry", user_id="owner-1", company_id=company_id,
    )
    assert result["status"] == "linked"
    new_voucher_id = result["linkedInvoiceVoucherId"]
    assert new_voucher_id and new_voucher_id != "inv-posted"

    from sqlalchemy import select

    from db.models import JournalEntry, JournalLine, LegacyEntryProjection
    from db.session import session_scope

    async with session_scope() as session:
        # 新发票凭证：费用借方行原样保留，贷方挂账重订为付款对手科目 2202，往来单位辅助沿用
        debit_lines = (await session.execute(select(JournalLine).where(
            JournalLine.journal_entry_id == new_voucher_id, JournalLine.direction == "debit",
        ))).scalars().all()
        credit_lines = (await session.execute(select(JournalLine).where(
            JournalLine.journal_entry_id == new_voucher_id, JournalLine.direction == "credit",
        ))).scalars().all()
        assert [(line.account_code, str(line.amount)) for line in debit_lines] == [("5602999", "500.00")]
        assert [(line.account_code, str(line.amount)) for line in credit_lines] == [("2202", "500.00")]
        assert credit_lines[0].auxiliary.get("partner_name") == "杭州深度求索人工智能基础技术研究有限公司"
        new_entry = await session.get(JournalEntry, new_voucher_id)
        assert new_entry.status == "posted"
        assert new_entry.metadata_json.get("invoiceNumber") == "INV-LINK-1"
        assert not new_entry.metadata_json.get("isArchiveOnly")

        # 原发票凭证已红冲并标归档
        old_entry = await session.get(JournalEntry, "inv-posted")
        assert old_entry.status == "reversed"

        # 付款凭证（银行关联不动）拿到发票元数据，指向重订后的活凭证
        pay_projection = await session.get(LegacyEntryProjection, "pay-entry")
        pay_payload = json.loads(pay_projection.payload) if isinstance(pay_projection.payload, str) else pay_projection.payload
        assert pay_payload.get("billingStatus") == "has_invoice"
        assert pay_payload.get("linkedInvoiceEntryId") == new_voucher_id
        assert pay_payload.get("invoiceNumber") == "INV-LINK-1"
