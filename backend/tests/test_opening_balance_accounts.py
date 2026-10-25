# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""期初余额科目口径测试（P1-4）。

覆盖三个修复点：
1. 写侧：容器 / 停用 / 不存在科目录入期初 → OPENING_ACCOUNT_INVALID 友好错误
2. 读侧：历史期初凭证引用非末级 / 停用科目时不静默丢弃
3. 试算平衡：损益类（5602016 单位社保）不计入资产负债合计
"""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from core.errors import AccountingError
from db.base import Base
import db.models  # noqa: F401
from db.models import ChartAccount, JournalEntry, JournalLine
from services.accounting_core_service import ensure_tenant, issue_step_up_token
from services.opening_balance_service import (
    check_balanced,
    get_opening_balances,
    get_opening_details,
    save_opening_balances,
)


@pytest.fixture
async def opening_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=True)
    async with sm() as session:
        async with session.begin():
            await ensure_tenant(session, "c-ob", "owner-ob")
        yield session
    await engine.dispose()


async def _token(session):
    return await issue_step_up_token(
        session, company_id="c-ob", user_id="owner-ob", action="adjust_opening_balance",
    )


def _items(*pairs):
    return [
        {"account_code": code, "account_name": name, "debit_balance": debit, "credit_balance": credit}
        for code, name, debit, credit in pairs
    ]


@pytest.mark.asyncio
async def test_container_account_rejected_with_leaf_hint(opening_session):
    """2211 已容器化：录入期初必须被拒，并提示改用末级明细。"""
    with pytest.raises(AccountingError) as exc:
        await save_opening_balances(
            "c-ob", 2026,
            _items(
                ("1002", "银行存款", "500.00", "0.00"),
                ("2211", "应付职工薪酬", "0.00", "500.00"),
            ),
            user_id="owner-ob",
            step_up_token=await _token(opening_session),
            request_id="req-container",
            session=opening_session,
        )
    assert exc.value.code == "OPENING_ACCOUNT_INVALID"
    assert "2211" in exc.value.message
    assert "221101" in exc.value.message, "错误信息应列出可用的末级明细"


@pytest.mark.asyncio
async def test_disabled_account_rejected(opening_session):
    """用户停用的科目不能录入期初。"""
    async with opening_session.begin():
        row = (
            await opening_session.execute(
                select_account("2241")
            )
        ).scalars().one()
        row.is_active = False
    with pytest.raises(AccountingError) as exc:
        await save_opening_balances(
            "c-ob", 2026,
            _items(
                ("1002", "银行存款", "500.00", "0.00"),
                ("2241", "其他应付款", "0.00", "500.00"),
            ),
            user_id="owner-ob",
            step_up_token=await _token(opening_session),
            request_id="req-disabled",
            session=opening_session,
        )
    assert exc.value.code == "OPENING_ACCOUNT_INVALID"
    assert "已停用" in exc.value.message


def select_account(code: str):
    from sqlalchemy import select

    return select(ChartAccount).where(
        ChartAccount.company_id == "c-ob", ChartAccount.code == code
    )


async def _insert_legacy_opening_entry(session, entry_id: str, container_code: str = "2211") -> None:
    """直插一张「容器化之前」录下的期初凭证：分录行指向现已非末级的 2211。"""
    session.add(JournalEntry(
        id=entry_id,
        company_id="c-ob",
        entry_type="opening",
        status="posted",
        occurred_on=date(2026, 1, 1),
        period_start=date(2026, 1, 1),
        summary="2026 年期初余额（历史数据）",
        created_by="owner-ob",
        voucher_no=f"P-{entry_id}",
    ))
    session.add(JournalLine(
        id=f"{entry_id}-l1",
        journal_entry_id=entry_id,
        company_id="c-ob",
        line_no=1,
        account_id=f"acct-c-ob-{container_code}",
        account_code=container_code,
        account_name="应付职工薪酬",
        direction="debit",
        amount=Decimal("500.00"),
        auxiliary={},
    ))
    session.add(JournalLine(
        id=f"{entry_id}-l2",
        journal_entry_id=entry_id,
        company_id="c-ob",
        line_no=2,
        account_id="acct-c-ob-3001",
        account_code="3001",
        account_name="实收资本",
        direction="credit",
        amount=Decimal("500.00"),
        auxiliary={},
    ))
    await session.flush()


@pytest.mark.asyncio
async def test_legacy_opening_line_on_container_account_is_not_dropped(opening_session):
    """读侧兜底：期初凭证里的 2211（现已非末级）不能从余额里静默消失。"""
    async with opening_session.begin():
        await _insert_legacy_opening_entry(opening_session, "opening-c-ob-2026")

    balances = await get_opening_balances("c-ob", 2026, session=opening_session)
    by_code = {item["account_code"]: item for item in balances}
    assert "2211" in by_code, "容器化前的历史分录行不得被静默丢弃"
    assert Decimal(by_code["2211"]["debit_balance"]) == Decimal("500.00")
    assert Decimal(by_code["3001"]["credit_balance"]) == Decimal("500.00")

    details = await get_opening_details("c-ob", 2026, session=opening_session)
    detail_codes = {item["account_code"] for item in details["items"]}
    assert "2211" in detail_codes


@pytest.mark.asyncio
async def test_trial_balance_excludes_profit_loss_accounts(opening_session):
    """5602016（单位社保，损益类）不得计入期初试算平衡的资产负债合计。"""
    await save_opening_balances(
        "c-ob", 2026,
        _items(
            ("1002", "银行存款", "1000.00", "0.00"),
            ("3001", "实收资本", "0.00", "900.00"),
            ("5602016", "管理费用-单位社保", "0.00", "100.00"),
        ),
        user_id="owner-ob",
        step_up_token=await _token(opening_session),
        request_id="req-trial",
        session=opening_session,
    )
    result = await check_balanced("c-ob", 2026, session=opening_session)
    # 借贷总额相等（1000=1000），但剥离损益后 1000 ≠ 900 → 不平衡
    assert result["balanced"] is False, "损益科目不应参与期初资产负债试算平衡"
