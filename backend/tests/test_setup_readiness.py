# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""账套就绪度（上手引导 M1）判定测试。

这几条用例锁住的是「完成状态必须能被数据证明」这条原则：
空账套不能自证完成、派生期初不能冒充用户录入、冲销后的期初不能算完成。
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import db.models  # noqa: F401
from core.errors import AccountingError
from db.base import Base
from db.models import ChartAccount, Company, JournalEntry, JournalLine, TaxRegistration
from services.accounting_core_service import ensure_tenant
from services.setup_readiness_service import compute_readiness, resolve_fiscal_year

CID = "c-setup"
UID = "owner-setup"
TODAY = date(2026, 6, 15)


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


async def _fill_profile(session, **overrides):
    company = await session.get(Company, CID)
    payload = {
        "name": "测试主体",
        "startDate": "2025-01-01",
        "taxId": "91330100MA2TEST01X",
        "type": "有限责任公司",
        "taxpayerType": "small_scale",
    }
    payload.update(overrides)
    company.payload = payload
    await session.commit()


async def _accounts(session):
    rows = (
        await session.execute(
            select(ChartAccount).where(
                ChartAccount.company_id == CID,
                ChartAccount.is_leaf.is_(True),
                ChartAccount.is_active.is_(True),
            )
        )
    ).scalars().all()
    debit = next(item for item in rows if item.code.startswith("1"))
    credit = next(item for item in rows if item.code.startswith("2"))
    return debit, credit


async def _add_entry(
    session,
    *,
    entry_id: str | None = None,
    entry_type: str = "standard",
    status: str = "posted",
    occurred_on: date = date(2026, 3, 10),
    amount: Decimal = Decimal("1000.00"),
):
    debit, credit = await _accounts(session)
    entry = JournalEntry(
        id=entry_id or f"je-{uuid4().hex[:10]}",
        company_id=CID,
        occurred_on=occurred_on,
        period_start=occurred_on.replace(day=1),
        summary="测试凭证",
        status=status,
        entry_type=entry_type,
        currency="CNY",
        created_by=UID,
    )
    session.add(entry)
    await session.flush()
    session.add_all(
        [
            JournalLine(
                journal_entry_id=entry.id,
                company_id=CID,
                line_no=1,
                account_id=debit.id,
                account_code=debit.code,
                account_name=debit.name,
                direction="debit",
                amount=amount,
            ),
            JournalLine(
                journal_entry_id=entry.id,
                company_id=CID,
                line_no=2,
                account_id=credit.id,
                account_code=credit.code,
                account_name=credit.name,
                direction="credit",
                amount=amount,
            ),
        ]
    )
    await session.commit()
    return entry


def by_key(result, key):
    return next(item for item in result["items"] if item["key"] == key)


async def test_empty_book_has_no_self_proving_completion(session):
    """空账套：只有 profile 可做，其余因缺启用日期而锁定，不能自证完成。"""
    result = await compute_readiness(session, CID, user_id=UID, today=TODAY)

    assert result["view"] == "boss"
    assert result["ready"] is False
    assert result["completed"] == 0
    assert result["total"] == 5
    assert [item["key"] for item in result["items"]] == [
        "company_profile",
        "tax_registration",
        "opening_balance",
        "first_voucher",
        "report_view",
    ]
    assert by_key(result, "company_profile")["status"] == "todo"
    opening = by_key(result, "opening_balance")
    assert opening["status"] == "locked"
    assert opening["blockedBy"] == "company_profile"
    assert by_key(result, "report_view")["blockedBy"] == "first_voucher"
    # 深链必须是真实路由
    assert by_key(result, "company_profile")["deepLink"] == "/mine/companies"


async def test_zero_start_book_waives_opening_balance(session):
    """今年起账的账套没有历史余额，期初是「不适用」而不是「未完成」。"""
    await _fill_profile(session, startDate="2026-02-01")

    result = await compute_readiness(session, CID, user_id=UID, today=TODAY)
    opening = by_key(result, "opening_balance")

    assert opening["status"] == "waived"
    assert opening["waivedReason"] == "zero_start"
    # waived 不进分母
    assert result["total"] == 4


async def test_tax_registration_counts_as_done(session):
    await _fill_profile(session)
    session.add(
        TaxRegistration(
            id="tr-1",
            company_id=CID,
            levy_item="增值税",
            filing_cycle="quarter",
            status="active",
            return_group="vat",
        )
    )
    await session.commit()

    result = await compute_readiness(session, CID, user_id=UID, today=TODAY)
    tax = by_key(result, "tax_registration")

    assert tax["status"] == "done"
    assert "1" in (tax["detail"] or "")


async def test_first_voucher_unlocks_report(session):
    await _fill_profile(session)
    entry = await _add_entry(session)

    result = await compute_readiness(session, CID, user_id=UID, today=TODAY)

    assert by_key(result, "first_voucher")["status"] == "done"
    assert by_key(result, "report_view")["status"] == "done"
    assert entry.status == "posted"


async def test_opening_entry_that_is_not_balanced_is_still_in_progress(session):
    """期初录了但借贷不平 → 进行中，不能算完成。"""
    await _fill_profile(session)
    debit, credit = await _accounts(session)
    session.add(
        JournalEntry(
            id=f"opening-{CID}-2026",
            company_id=CID,
            occurred_on=date(2025, 12, 31),
            period_start=date(2026, 1, 1),
            summary="期初余额",
            status="posted",
            entry_type="opening",
            currency="CNY",
            created_by=UID,
        )
    )
    await session.flush()
    session.add(
        JournalLine(
            journal_entry_id=f"opening-{CID}-2026",
            company_id=CID,
            line_no=1,
            account_id=debit.id,
            account_code=debit.code,
            account_name=debit.name,
            direction="debit",
            amount=Decimal("500.00"),
        )
    )
    session.add(
        JournalLine(
            journal_entry_id=f"opening-{CID}-2026",
            company_id=CID,
            line_no=2,
            account_id=credit.id,
            account_code=credit.code,
            account_name=credit.name,
            direction="credit",
            amount=Decimal("400.00"),
        )
    )
    await session.commit()

    result = await compute_readiness(session, CID, user_id=UID, today=TODAY)
    opening = by_key(result, "opening_balance")

    assert opening["status"] == "doing"
    assert "不平" in (opening["detail"] or "")


async def test_balanced_opening_entry_is_done(session):
    await _fill_profile(session)
    await _add_entry(session, entry_id=f"opening-{CID}-2026", entry_type="opening")

    result = await compute_readiness(session, CID, user_id=UID, today=TODAY)
    opening = by_key(result, "opening_balance")

    assert opening["status"] == "done"


async def test_reversed_opening_entry_is_not_done(session):
    """冲销后的期初凭证仍留在表里，但状态是 reversed，不能被判完成。"""
    await _fill_profile(session)
    await _add_entry(
        session,
        entry_id=f"opening-{CID}-2026",
        entry_type="opening",
        status="reversed",
    )

    result = await compute_readiness(session, CID, user_id=UID, today=TODAY)
    opening = by_key(result, "opening_balance")

    assert opening["status"] == "todo"
    assert "冲销" in (opening["detail"] or "")


async def test_current_year_activity_does_not_fake_a_derived_opening(session):
    """本年有凭证但没录期初 → 期初必须是未完成（派生只看上年）。"""
    await _fill_profile(session)
    await _add_entry(session, occurred_on=date(2026, 3, 10))

    result = await compute_readiness(session, CID, user_id=UID, today=TODAY)
    opening = by_key(result, "opening_balance")

    assert opening["status"] == "todo"


async def test_prior_year_activity_counts_as_carried_forward(session):
    await _fill_profile(session)
    await _add_entry(session, occurred_on=date(2025, 8, 10))

    result = await compute_readiness(session, CID, user_id=UID, today=TODAY)
    opening = by_key(result, "opening_balance")

    assert opening["status"] == "done"
    assert "结转" in (opening["detail"] or "")


async def test_view_switch_keeps_the_same_truth(session):
    """两套文案、一套状态：同一账套在两个视图下的状态必须完全一致。"""
    await _fill_profile(session)
    await _add_entry(session)

    boss = await compute_readiness(session, CID, user_id=UID, view="boss", today=TODAY)
    accounting = await compute_readiness(
        session, CID, user_id=UID, view="accounting", today=TODAY
    )

    boss_status = {item["key"]: item["status"] for item in boss["items"]}
    accounting_status = {item["key"]: item["status"] for item in accounting["items"]}
    shared = set(boss_status) & set(accounting_status)
    assert shared
    for key in shared:
        assert boss_status[key] == accounting_status[key]

    # 文案与顺序必须不同
    assert boss_status.keys() != accounting_status.keys()
    assert by_key(boss, "first_voucher")["title"] == "记第一笔账"
    assert by_key(accounting, "first_voucher")["title"] == "首张凭证过账"
    # 科目表是信息项，不进分母
    chart = by_key(accounting, "chart_accounts")
    assert chart["informational"] is True
    assert accounting["total"] == 5


async def test_waived_legacy_import_leaves_denominator(session):
    # 老账套且未录期初 → 旧账导入是待办项，才能观测到「主动跳过」的效果
    await _fill_profile(session, startDate="2025-01-01")

    baseline = await compute_readiness(
        session, CID, user_id=UID, view="accounting", today=TODAY
    )
    waived = await compute_readiness(
        session,
        CID,
        user_id=UID,
        view="accounting",
        waived=["legacy_import"],
        today=TODAY,
    )

    assert by_key(baseline, "legacy_import")["status"] == "todo"
    assert by_key(waived, "legacy_import")["status"] == "waived"
    assert waived["total"] == baseline["total"] - 1


async def test_missing_company_raises_404(session):
    with pytest.raises(AccountingError) as excinfo:
        await compute_readiness(session, "c-missing", user_id=UID, today=TODAY)

    assert excinfo.value.code == "NOT_FOUND"
    assert excinfo.value.status_code == 404


async def test_fiscal_year_prefers_start_date_when_it_is_in_the_future(session):
    """启用日期在下一年时，会计年度要取启用年，否则期初录了也判不到。"""
    await _fill_profile(session, startDate="2027-01-01")
    company = await session.get(Company, CID)

    assert resolve_fiscal_year(company, date(2026, 6, 15)) == 2027
    result = await compute_readiness(session, CID, user_id=UID, today=TODAY)
    assert result["fiscalYear"] == 2027
