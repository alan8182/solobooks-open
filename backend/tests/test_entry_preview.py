# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""结账向导第二步：待确认单据的借贷预览。

预览与落账必须同口径（preview == post），否则「认可入账」又会变成盲选。
已过账凭证改分类只能红冲重记，不能被静默改写。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from core.errors import ENTRY_POSTED_IMMUTABLE, AccountingError
from db.base import Base
from db.models import JournalEntry, JournalLine
import db.models  # noqa: F401
from services.accounting_core_service import (
    create_entry,
    preview_voucher_lines,
    update_draft,
)


@pytest.fixture
async def preview_sessions():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def expense_payload(
    entry_id: str = "preview-expense",
    *,
    category: str = "业务招待费",
    amount: str = "481.33",
) -> dict:
    """无付款人、无显式分录：走系统建议（贷银行存款），与待确认卡片一致。"""
    return {
        "id": entry_id,
        "direction": "expense",
        "amount": amount,
        "title": "腾讯云服务器",
        "category": category,
        "occurDate": "2026-08-18",
        "sourceType": "manual",
    }


async def _lines(session, entry_id: str) -> list[JournalLine]:
    return list(
        (
            await session.execute(
                select(JournalLine)
                .where(JournalLine.journal_entry_id == entry_id)
                .order_by(JournalLine.line_no)
            )
        ).scalars().all()
    )


def _preview_pairs(preview: dict) -> list[tuple[str, str, str]]:
    return sorted(
        [(direction, line["accountCode"], line["amount"])
         for direction in ("debit", "credit")
         for line in preview[direction]]
    )


def _written_pairs(lines: list[JournalLine]) -> list[tuple[str, str, str]]:
    return sorted((line.direction, line.account_code, f"{line.amount:.2f}") for line in lines)


@pytest.mark.asyncio
async def test_preview_boss_paid_expense_shows_account_and_person_partner(preview_sessions):
    async with preview_sessions() as session:
        async with session.begin():
            await create_entry(
                session,
                expense_payload(),
                company_id="c-preview",
                user_id="owner",
                request_id="req-preview-create",
                confirm=False,
            )
            preview = await preview_voucher_lines(
                session,
                "preview-expense",
                company_id="c-preview",
                payer="boss_personal",
            )

    assert preview["direction"] == "expense"
    assert preview["category"] == "业务招待费"
    assert preview["payer"] == "boss_personal"
    assert preview["amount"] == "481.33"
    assert preview["source"] == "rule"
    assert [line["accountCode"] for line in preview["debit"]] == ["5602002"]
    assert preview["debit"][0]["accountName"] == "管理费用-业务招待费"
    assert preview["debit"][0]["amount"] == "481.33"
    assert [line["accountCode"] for line in preview["credit"]] == ["2241"]
    # 垫付人的身份靠往来单位区分，预览必须显示出来
    assert preview["credit"][0]["partnerName"] == "股东"


@pytest.mark.asyncio
async def test_preview_category_change_moves_debit_account(preview_sessions):
    async with preview_sessions() as session:
        async with session.begin():
            await create_entry(
                session,
                expense_payload(),
                company_id="c-preview-cat",
                user_id="owner",
                request_id="req-preview-cat",
                confirm=False,
            )
            preview = await preview_voucher_lines(
                session,
                "preview-expense",
                company_id="c-preview-cat",
                payer="boss_personal",
                category="信息技术服务费",
            )

    assert preview["category"] == "信息技术服务费"
    assert preview["debit"][0]["accountCode"] == "5602015"
    assert preview["debit"][0]["accountName"] == "管理费用-信息技术服务费"
    assert preview["credit"][0]["accountCode"] == "2241"


@pytest.mark.asyncio
async def test_preview_falls_back_for_unknown_category(preview_sessions):
    async with preview_sessions() as session:
        async with session.begin():
            await create_entry(
                session,
                expense_payload(),
                company_id="c-preview-fallback",
                user_id="owner",
                request_id="req-preview-fallback",
                confirm=False,
            )
            preview = await preview_voucher_lines(
                session,
                "preview-expense",
                company_id="c-preview-fallback",
                payer="boss_personal",
                category="不存在的分类",
            )

    assert preview["source"] == "fallback"
    assert preview["debit"][0]["accountCode"] == "5602999"


@pytest.mark.asyncio
async def test_preview_unpaid_employee_and_prepaid_counterparties(preview_sessions):
    async with preview_sessions() as session:
        async with session.begin():
            await create_entry(
                session,
                expense_payload(),
                company_id="c-preview-payers",
                user_id="owner",
                request_id="req-preview-payers",
                confirm=False,
            )
            unpaid = await preview_voucher_lines(
                session,
                "preview-expense",
                company_id="c-preview-payers",
                payment_status="unpaid",
            )
            employee = await preview_voucher_lines(
                session,
                "preview-expense",
                company_id="c-preview-payers",
                payer="employee_personal:张三",
            )
            anonymous_employee = await preview_voucher_lines(
                session,
                "preview-expense",
                company_id="c-preview-payers",
                payer="employee_personal",
            )
            prepaid = await preview_voucher_lines(
                session,
                "preview-expense",
                company_id="c-preview-payers",
                payer="prepaid_deduct",
            )

    # 未付款：挂应付，往来对手是供应商，不显示个人
    assert unpaid["credit"][0]["accountCode"] == "2241"
    assert "partnerName" not in unpaid["credit"][0]
    # 具名员工：挂同名往来单位；裸 employee_personal 不知道是谁，不挂
    assert employee["credit"][0]["accountCode"] == "2241"
    assert employee["credit"][0]["partnerName"] == "张三"
    assert anonymous_employee["credit"][0]["accountCode"] == "2241"
    assert "partnerName" not in anonymous_employee["credit"][0]
    # 预存扣款：贷预付账款
    assert prepaid["credit"][0]["accountCode"] == "1123"


@pytest.mark.asyncio
async def test_preview_matches_draft_lines_written_by_update_draft(preview_sessions):
    async with preview_sessions() as session:
        async with session.begin():
            await create_entry(
                session,
                expense_payload(),
                company_id="c-preview-parity",
                user_id="owner",
                request_id="req-preview-parity",
                confirm=False,
            )
            preview = await preview_voucher_lines(
                session,
                "preview-expense",
                company_id="c-preview-parity",
                payer="boss_personal",
                category="信息技术服务费",
            )
            await update_draft(
                session,
                "preview-expense",
                {"payer": "boss_personal", "category": "信息技术服务费"},
                company_id="c-preview-parity",
                user_id="owner",
                request_id="req-preview-parity-update",
            )
            written = await _lines(session, "preview-expense")

    assert _preview_pairs(preview) == _written_pairs(written)
    person_line = next(line for line in written if line.account_code == "2241")
    assert (person_line.auxiliary or {}).get("partner_name") == "股东"


@pytest.mark.asyncio
async def test_preview_matches_reposted_posted_entry(preview_sessions):
    async with preview_sessions() as session:
        async with session.begin():
            await create_entry(
                session,
                expense_payload(),
                company_id="c-preview-posted",
                user_id="owner",
                request_id="req-preview-posted",
                confirm=True,
            )
            preview = await preview_voucher_lines(
                session,
                "preview-expense",
                company_id="c-preview-posted",
                payer="boss_personal",
                category="信息技术服务费",
            )
            reposted = await update_draft(
                session,
                "preview-expense",
                {
                    "payer": "boss_personal",
                    "category": "信息技术服务费",
                    "autoReverseRepost": True,
                },
                company_id="c-preview-posted",
                user_id="owner",
                request_id="req-preview-posted-update",
            )
            written = await _lines(session, reposted.id)
            original = await session.get(JournalEntry, "preview-expense")

    assert reposted.id != "preview-expense"
    assert reposted.status == "posted"
    assert original.status == "reversed"
    assert _preview_pairs(preview) == _written_pairs(written)
    person_line = next(line for line in written if line.account_code == "2241")
    assert (person_line.auxiliary or {}).get("partner_name") == "股东"


@pytest.mark.asyncio
async def test_posted_category_change_requires_auto_reverse_repost(preview_sessions):
    async with preview_sessions() as session:
        async with session.begin():
            await create_entry(
                session,
                expense_payload(),
                company_id="c-preview-locked",
                user_id="owner",
                request_id="req-preview-locked",
                confirm=True,
            )
            with pytest.raises(AccountingError) as excinfo:
                await update_draft(
                    session,
                    "preview-expense",
                    {"category": "信息技术服务费"},
                    company_id="c-preview-locked",
                    user_id="owner",
                    request_id="req-preview-locked-update",
                )
            assert excinfo.value.code == ENTRY_POSTED_IMMUTABLE
            original = await session.get(JournalEntry, "preview-expense")

    # 没有 autoReverseRepost：已过账凭证的借贷一个字都不能动
    assert original.status == "posted"


@pytest.mark.asyncio
async def test_posted_category_unchanged_only_updates_tracking_fields(preview_sessions):
    async with preview_sessions() as session:
        async with session.begin():
            await create_entry(
                session,
                expense_payload(),
                company_id="c-preview-same-cat",
                user_id="owner",
                request_id="req-preview-same-cat",
                confirm=True,
            )
            unchanged = await update_draft(
                session,
                "preview-expense",
                {"category": "业务招待费", "billingStatus": "reconciled"},
                company_id="c-preview-same-cat",
                user_id="owner",
                request_id="req-preview-same-cat-update",
            )
            lines = await _lines(session, "preview-expense")

    assert unchanged.id == "preview-expense"
    assert [line.account_code for line in lines] == ["5602002", "1002"]
