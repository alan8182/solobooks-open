# Copyright (c) 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""错误 context 的可定位性测试（上手引导 M3 · L3 错误救援）。

前端救援弹窗的出路是「定位到错在哪一行、改成哪个科目」，所以这些 context
不是锦上添花：**没有它们，规则表里的按钮全是死的**。

`UNBALANCED_ENTRY` 有两个抛出点（创建与过账），这里各写一个用例——
只改一处是这类任务最典型的漏改。
"""

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import db.models  # noqa: F401
from core.errors import AccountingError
from db.base import Base
from db.models import Company
from services.accounting_core_service import (
    create_entry,
    ensure_tenant,
    issue_step_up_token,
    post_journal,
    update_draft,
)
from services.opening_balance_service import save_opening_balances

CID = "c-rescue"
UID = "owner-rescue"


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=True)
    async with factory() as db:
        async with db.begin():
            await ensure_tenant(db, CID, UID)
        yield db
    await engine.dispose()


def balanced_payload(entry_id: str, amount: str = "1000.00") -> dict:
    return {
        "id": entry_id,
        "direction": "expense",
        "amount": amount,
        "title": "测试支出",
        "category": "办公费",
        "occurDate": "2026-07-31",
        "sourceType": "manual",
        "voucherLines": {
            "debit": [{"accountCode": "5602", "accountName": "管理费用", "amount": amount}],
            "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": amount}],
        },
    }


async def test_unbalanced_entry_context_points_at_the_lines(session: AsyncSession):
    """创建路径不平：context 必须带差额与行定位。"""
    payload = balanced_payload("je-unbalanced-create")
    payload["voucherLines"]["credit"][0]["amount"] = "900.00"

    with pytest.raises(AccountingError) as excinfo:
        await create_entry(session, payload, company_id=CID, user_id=UID, request_id="req-1", confirm=False)

    error = excinfo.value
    assert error.code == "UNBALANCED_ENTRY"
    context = error.context or {}
    assert context["difference"] == "100.00"
    assert context["debit"] == "1000.00"
    assert context["credit"] == "900.00"
    lines = context["lines"]
    assert len(lines) == 2
    assert [line["lineNo"] for line in lines] == [1, 2]
    assert lines[0]["direction"] == "debit"
    # 隐私红线：定位行里绝不能出现辅助核算（内含客户名）
    assert all("auxiliary" not in line for line in lines)


async def test_unbalanced_entry_context_on_posting(session: AsyncSession):
    """过账路径不平：与创建路径是两个独立抛出点，必须各自带全 context。"""
    async with session.begin():
        entry = await create_entry(
            session,
            balanced_payload("je-unbalanced-post"),
            company_id=CID,
            user_id=UID,
            request_id="req-2",
            confirm=False,
        )

    # 制造历史脏数据：创建时是平的，之后被改成不平（老数据迁移、期间结转都可能）
    async with session.begin():
        await session.execute(
            text(
                "UPDATE journal_lines SET amount = amount + 250 "
                "WHERE journal_entry_id = :id AND direction = 'debit'"
            ),
            {"id": entry.id},
        )

    with pytest.raises(AccountingError) as excinfo:
        await post_journal(session, entry.id, company_id=CID, user_id=UID, request_id="req-3")

    context = excinfo.value.context or {}
    assert excinfo.value.code == "UNBALANCED_ENTRY"
    assert context["difference"] == "250.00"
    assert context["lines"], "过账路径也要带行定位，否则救援按钮指不到具体行"
    assert {line["direction"] for line in context["lines"]} == {"debit", "credit"}


async def test_invalid_account_context_offers_usable_children(session: AsyncSession):
    """容器科目被拒：必须告诉用户「换成哪个明细」，出路才成立。"""
    payload = {
        "id": "je-container",
        "direction": "expense",
        "amount": "5000.00",
        "title": "容器科目不该能用",
        "category": "人力成本",
        "occurDate": "2026-07-31",
        "sourceType": "manual",
        "voucherLines": {
            "debit": [{"accountCode": "5602001", "accountName": "管理人员职工薪酬", "amount": "5000.00"}],
            "credit": [{"accountCode": "2211", "accountName": "应付职工薪酬", "amount": "5000.00"}],
        },
    }

    with pytest.raises(AccountingError) as excinfo:
        await create_entry(session, payload, company_id=CID, user_id=UID, request_id="req-4", confirm=False)

    context = excinfo.value.context or {}
    assert excinfo.value.code == "INVALID_ACCOUNT"
    lines = context["lines"]
    assert lines, "必须有逐科目说明"
    assert all(item["reason"] in {"not_found", "inactive", "not_leaf"} for item in lines)
    for item in lines:
        if item["reason"] == "not_leaf":
            assert item["suggestedChildren"], "非末级科目必须给出可替换的明细候选"


async def test_opening_side_conflict_context_names_the_subject(session: AsyncSession):
    """同一科目既填借方又填贷方：context 必须点名是哪个科目、差多少。"""
    items = [
        {"account_code": "1002", "account_name": "银行存款", "debit_balance": "1000.00", "credit_balance": "0"},
        {"account_code": "2202", "account_name": "其他应付款", "debit_balance": "0", "credit_balance": "1000.00"},
        {"account_code": "1001", "account_name": "库存现金", "debit_balance": "50.00", "credit_balance": "50.00"},
    ]
    # 期初写入有二次验证门禁（STEP_UP_REQUIRED），这里先取 token 再触发目标校验
    async with session.begin():
        token = await issue_step_up_token(
            session, company_id=CID, user_id=UID, action="adjust_opening_balance"
        )

    with pytest.raises(AccountingError) as excinfo:
        await save_opening_balances(
            CID, 2026, items, user_id=UID, step_up_token=token, request_id="req-5", session=session
        )

    context = excinfo.value.context or {}
    assert excinfo.value.code == "OPENING_SIDE_CONFLICT"
    assert context["accountCode"] == "1001"
    assert context["accountName"] == "库存现金"
    assert context["debit"] == "50.00"
    assert context["credit"] == "50.00"


async def test_duplicate_voucher_no_lists_used_numbers(session: AsyncSession):
    """凭证号重复：给出同月已占用号，用户才有得挑。"""
    original = {
        "id": "legacy-1",
        "direction": "income",
        "amount": "1000.00",
        "title": "技术服务费",
        "category": "主营业务收入",
        "occurDate": "2026-05-20",
        "sourceType": "legacy_import",
        "voucherNo": "记-0007",
        "confirm": True,
    }
    async with session.begin():
        await create_entry(session, original, company_id=CID, user_id=UID, request_id="req-6", confirm=True)

    duplicate = dict(original, id="legacy-2", request_id="req-7")
    with pytest.raises(AccountingError) as excinfo:
        await create_entry(session, duplicate, company_id=CID, user_id=UID, request_id="req-7", confirm=True)

    context = excinfo.value.context or {}
    assert excinfo.value.code == "DUPLICATE_VOUCHER_NO"
    assert context["voucherNo"] == "记-0007"
    assert context["occurredOn"] == "2026-05-20"
    assert "记-0007" in context["usedVoucherNos"]


async def test_posted_immutable_context_locates_the_voucher(session: AsyncSession):
    """已过账不可变：出路是「去冲销」，必须指得到是哪一张。"""
    async with session.begin():
        entry = await create_entry(
            session,
            balanced_payload("je-locked"),
            company_id=CID,
            user_id=UID,
            request_id="req-8",
            confirm=True,
        )
        entry_id = entry.id

    async with session.begin():
        await post_journal(session, entry_id, company_id=CID, user_id=UID, request_id="req-9")

    with pytest.raises(AccountingError) as excinfo:
        async with session.begin():
            await update_draft(
                session,
                entry_id,
                {"amount": "2000.00"},
                company_id=CID,
                user_id=UID,
                request_id="req-10",
            )

    context = excinfo.value.context or {}
    assert excinfo.value.code == "ENTRY_POSTED_IMMUTABLE"
    assert context["entryId"] == entry_id
    assert context["status"] == "posted"
    assert context["occurredOn"] == "2026-07-31"
    assert context["voucherRef"]


async def test_review_required_context_names_the_maker(session: AsyncSession):
    """需他人复核：出路是「提交复核」，context 要说明是谁制的单。"""
    async with session.begin():
        company = await session.get(Company, CID)
        company.posting_policy = "maker_checker"
        entry = await create_entry(
            session,
            balanced_payload("je-review"),
            company_id=CID,
            user_id=UID,
            request_id="req-11",
            confirm=False,
        )
        entry_id = entry.id

    with pytest.raises(AccountingError) as excinfo:
        async with session.begin():
            await post_journal(session, entry_id, company_id=CID, user_id=UID, request_id="req-12")

    context = excinfo.value.context or {}
    assert excinfo.value.code == "REVIEW_REQUIRED"
    assert context["entryId"] == entry_id
    assert context["createdBy"] == UID
    assert context["status"] in {"pending_review", "draft"}
