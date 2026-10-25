# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""人工调整分录（会计纠错）的契约。

系统按「方向 + 分类 + 付款方式」自动生成分录，规则偶有记错科目。这里锁定
三条底线：借贷平衡、借方合计仍等于交易金额、科目必须是启用中的末级科目；
以及两条留痕要求：调整后打标记、改分类不得静默冲掉手改结果。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from core.errors import AccountingError, ENTRY_POSTED_IMMUTABLE, UNBALANCED_ENTRY
from db.base import Base
from db.models import AuditEvent, JournalLine
import db.models  # noqa: F401
from services.accounting_core_service import (
    adjust_voucher_lines,
    create_entry,
    get_journal,
    serialize_journal,
    update_draft,
)


@pytest.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _income_payload(entry_id: str, amount: str = "101.00") -> dict:
    return {
        "id": entry_id,
        "direction": "income",
        "amount": amount,
        "title": "咨询服务收入",
        "category": "主营业务收入",
        "occurDate": "2026-07-01",
        "sourceType": "manual",
        "voucherLines": {
            "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": amount}],
            "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": amount}],
        },
    }


async def _create(factory, entry_id: str, *, confirm: bool = False, amount: str = "101.00"):
    async with factory() as session:
        async with session.begin():
            await create_entry(
                session,
                _income_payload(entry_id, amount),
                company_id="c-adjust",
                user_id="owner-1",
                request_id=f"create-{entry_id}",
                confirm=confirm,
            )


async def _adjust(factory, entry_id: str, lines=None, **kwargs):
    async with factory() as session:
        async with session.begin():
            return await adjust_voucher_lines(
                session,
                entry_id,
                lines=lines,
                company_id="c-adjust",
                user_id="owner-1",
                request_id=f"adjust-{entry_id}",
                **kwargs,
            )


def _lines(*rows):
    return [
        {"direction": direction, "accountCode": code, "amount": amount}
        for direction, code, amount in rows
    ]


@pytest.mark.asyncio
async def test_adjust_rewrites_lines_and_marks_manual(sessions):
    """会计把「银行存款」改成「应收账款」，并按含税收入拆出应交税费。"""
    await _create(sessions, "adj-1")

    entry = await _adjust(
        sessions,
        "adj-1",
        _lines(
            ("debit", "1122", "101.00"),
            ("credit", "5001", "100.00"),
            ("credit", "2221026", "1.00"),
        ),
        reason="客户未付款，应为应收",
    )

    async with sessions() as session:
        payload = await serialize_journal(session, entry)
        stored = (
            await session.execute(
                select(JournalLine).where(JournalLine.journal_entry_id == "adj-1").order_by(JournalLine.line_no)
            )
        ).scalars().all()
        audit = (
            await session.execute(select(AuditEvent).where(AuditEvent.action == "adjust_voucher_lines"))
        ).scalar_one()

    assert [line.account_code for line in stored] == ["1122", "5001", "2221026"]
    assert [line.direction for line in stored] == ["debit", "credit", "credit"]
    assert payload["voucherLines"]["debit"][0]["amount"] == "101.00"
    assert payload["voucherManuallyAdjusted"] is True
    assert payload["voucherAdjustment"]["reason"] == "客户未付款，应为应收"
    assert payload["voucherAdjustment"]["previousLines"][0]["accountCode"] == "1002"
    assert audit.entity_id == "adj-1"
    # 名称由服务端按科目表回填，不接受客户端文案
    assert payload["voucherLines"]["debit"][0]["accountName"] == "应收账款"


@pytest.mark.asyncio
async def test_adjust_rejects_unbalanced_lines(sessions):
    await _create(sessions, "adj-unbalanced")

    with pytest.raises(AccountingError) as exc:
        await _adjust(
            sessions,
            "adj-unbalanced",
            _lines(("debit", "1122", "101.00"), ("credit", "5001", "100.00")),
        )

    assert exc.value.code == UNBALANCED_ENTRY


@pytest.mark.asyncio
async def test_adjust_keeps_debit_total_equal_to_trade_amount(sessions):
    """只允许在同一笔金额内重分配：借方合计变了就是改交易，不是改科目。"""
    await _create(sessions, "adj-amount")

    with pytest.raises(AccountingError) as exc:
        await _adjust(
            sessions,
            "adj-amount",
            _lines(("debit", "1122", "100.00"), ("credit", "5001", "100.00")),
        )

    assert exc.value.code == "VOUCHER_AMOUNT_MISMATCH"
    assert exc.value.context["expected"] == "101.00"
    assert exc.value.context["actual"] == "100.00"


@pytest.mark.asyncio
async def test_adjust_rejects_container_parent_account(sessions):
    """1221 是容器科目，记账必须落到子级，手工调整也不能破例。"""
    await _create(sessions, "adj-container")

    with pytest.raises(AccountingError) as exc:
        await _adjust(
            sessions,
            "adj-container",
            _lines(("debit", "1221", "101.00"), ("credit", "5001", "101.00")),
        )

    assert exc.value.code == "INVALID_ACCOUNT"
    assert exc.value.context["accountCodes"] == ["1221"]


@pytest.mark.asyncio
async def test_adjust_rejects_posted_entry(sessions):
    await _create(sessions, "adj-posted", confirm=True)

    with pytest.raises(AccountingError) as exc:
        await _adjust(
            sessions,
            "adj-posted",
            _lines(("debit", "1122", "101.00"), ("credit", "5001", "101.00")),
        )

    assert exc.value.code == ENTRY_POSTED_IMMUTABLE


@pytest.mark.asyncio
async def test_reset_to_system_restores_generated_lines(sessions):
    await _create(sessions, "adj-reset")
    await _adjust(
        sessions,
        "adj-reset",
        _lines(("debit", "1122", "101.00"), ("credit", "5001", "101.00")),
    )

    entry = await _adjust(sessions, "adj-reset", reset_to_system=True)

    async with sessions() as session:
        payload = await serialize_journal(session, entry)
    assert payload["voucherLines"]["debit"][0]["accountCode"] == "1002"
    assert "voucherManuallyAdjusted" not in payload
    assert "voucherAdjustment" not in payload


@pytest.mark.asyncio
async def test_category_change_requires_confirmation_after_manual_adjust(sessions):
    """改分类会按规则重算分录 —— 手改过的凭证必须先确认，否则静默丢失。"""
    await _create(sessions, "adj-guard")
    await _adjust(
        sessions,
        "adj-guard",
        _lines(("debit", "1122", "101.00"), ("credit", "5001", "101.00")),
    )

    async with sessions() as session:
        async with session.begin():
            with pytest.raises(AccountingError) as exc:
                await update_draft(
                    session,
                    "adj-guard",
                    {"category": "其他业务收入"},
                    company_id="c-adjust",
                    user_id="owner-1",
                    request_id="edit-1",
                )
    assert exc.value.code == "VOUCHER_ADJUSTMENT_WILL_BE_LOST"
    assert exc.value.context["adjustedFields"] == ["category"]

    async with sessions() as session:
        async with session.begin():
            updated = await update_draft(
                session,
                "adj-guard",
                {"category": "其他业务收入", "confirmRegenerateVoucher": True},
                company_id="c-adjust",
                user_id="owner-1",
                request_id="edit-2",
            )
        payload = await serialize_journal(session, updated)

    # 确认覆盖后按新分类重算，手工标记清理干净
    assert payload["voucherLines"]["credit"][0]["accountCode"] == "5051"
    assert "voucherManuallyAdjusted" not in payload
    assert "confirmRegenerateVoucher" not in payload


@pytest.mark.asyncio
async def test_non_financial_edit_keeps_manual_lines(sessions):
    """改摘要不动分录，也不该被覆盖保护拦住。"""
    await _create(sessions, "adj-title")
    await _adjust(
        sessions,
        "adj-title",
        _lines(("debit", "1122", "101.00"), ("credit", "5001", "101.00")),
    )

    async with sessions() as session:
        async with session.begin():
            updated = await update_draft(
                session,
                "adj-title",
                {"title": "改成新的摘要"},
                company_id="c-adjust",
                user_id="owner-1",
                request_id="edit-title",
            )
        payload = await serialize_journal(session, updated)

    assert payload["title"] == "改成新的摘要"
    assert payload["voucherLines"]["debit"][0]["accountCode"] == "1122"
    assert payload["voucherManuallyAdjusted"] is True
