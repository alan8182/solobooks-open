# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Test mid-year opening balance migration, YTD profit/loss accumulation, and reset flow."""
import pytest
from datetime import date
from decimal import Decimal
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from db.base import Base
import db.models  # noqa: F401
from db.models import Company, CompanyMember, ChartAccount, JournalEntry, JournalLine
from services.accounting_core_service import ensure_tenant, issue_step_up_token, posted_entries_for_reports
from services.opening_balance_service import (
    get_opening_details,
    get_opening_partner_balances,
    get_year_start_balances,
    reset_opening_balances,
    save_opening_balances,
    check_balanced,
)


@pytest.fixture
async def midyear_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 结账检查（period_close_checks）会读两张原生 bank 表，与 test_accounting_core 同构
        await conn.exec_driver_sql(
            "CREATE TABLE bank_statements ("
            "id TEXT PRIMARY KEY, company_id TEXT NOT NULL, transaction_date TEXT NOT NULL, "
            "description TEXT NOT NULL, amount NUMERIC(20,2) NOT NULL, "
            "balance NUMERIC(20,2) NOT NULL, statement_id TEXT NOT NULL, "
            "matched_entry_id TEXT, description_normalized INTEGER NOT NULL, created_at TEXT NOT NULL, "
            "transaction_reference TEXT, source_row_index INTEGER)"
        )
        await conn.exec_driver_sql(
            "CREATE TABLE bank_statement_matches (statement_id TEXT, company_id TEXT, entry_id TEXT, "
            "previous_billing_status TEXT, previous_payment_date TEXT, created_at TEXT, "
            "PRIMARY KEY(statement_id,entry_id))"
        )
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=True)
    async with sm() as session:
        async with session.begin():
            await ensure_tenant(session, "c_test", "owner_u1")
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_midyear_opening_balance_and_ytd_accumulation(midyear_session):
    session = midyear_session
    # 模拟企业于 2026 年 6 月迁入：
    # 1. 资产负债期初（2026-06-01）：银行存款 100,000，应付账款 20,000，实收资本 80,000
    # 2. 1-5 月损益本年累计发生额：主营业务收入 500,000，管理费用 200,000（累计净利 300,000）
    # 3. 税务备忘：以前年度亏损 50,000，已预缴所得税 15,000
    token = await issue_step_up_token(
        session,
        company_id="c_test",
        user_id="owner_u1",
        action="adjust_opening_balance",
    )
    
    asset_items = [
        {"account_code": "1002", "account_name": "银行存款", "debit_balance": "100000.00", "credit_balance": "0.00"},
        {"account_code": "2202", "account_name": "应付账款", "debit_balance": "0.00", "credit_balance": "20000.00", "partner_name": "供应商甲"},
        {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0.00", "credit_balance": "80000.00"},
    ]
    ytd_items = [
        {"account_code": "5001", "account_name": "主营业务收入", "debit_balance": "0.00", "credit_balance": "500000.00"},
        {"account_code": "5602999", "account_name": "管理费用-其他", "debit_balance": "200000.00", "credit_balance": "0.00"},
    ]
    tax_memory = {
        "prior_year_loss": 50000,
        "prepaid_cit_tax": 15000,
        "migration_note": "从金蝶云会计迁入",
    }

    # 还没保存过：start_period 只是 "{year}-01" 兜底值，不能被当成用户选的建账时间，
    # 否则前端会把 10 月启用的账套显示成「1 月启用」
    before = await get_opening_details("c_test", 2026, session=session)
    assert before["start_period"] == "2026-01"
    assert before["start_period_saved"] is False

    res = await save_opening_balances(
        "c_test",
        2026,
        asset_items,
        user_id="owner_u1",
        step_up_token=token,
        request_id="req-midyear-01",
        start_period="2026-06",
        ytd_items=ytd_items,
        tax_memory=tax_memory,
        session=session,
    )
    assert res["voucherStatus"] == "posted"
    assert "ytdEntry" in res
    assert res["ytdEntry"]["voucherStatus"] == "posted"

    # 验证资产负债凭证发生日期为 2026-06-01
    assert res["occurDate"] == "2026-06-01"
    # 验证 YTD 凭证发生日期为 2026-05-31（5月末）
    assert res["ytdEntry"]["occurDate"] == "2026-05-31"

    # 验证 get_opening_details 读取结果
    details = await get_opening_details("c_test", 2026, session=session)
    assert details["is_locked"] is True
    assert details["start_period"] == "2026-06"
    assert details["start_period_saved"] is True
    assert details["tax_memory"]["priorYearLoss"] == 50000
    assert details["tax_memory"]["prepaidCitTax"] == 15000
    assert details["tax_memory"]["migrationNote"] == "从金蝶云会计迁入"

    # 验证 YTD 凭证自动由 3103 本年利润配平
    ytd_debits = res["ytdEntry"]["voucherLines"]["debit"]
    profit_line = next(line for line in ytd_debits if line["accountCode"] == "3103")
    assert Decimal(str(profit_line["amount"])) == Decimal("300000.00")

    # 验证总账报表查询：所有凭证均为 posted 状态且属于当前账套
    authoritative = await posted_entries_for_reports(session, "c_test")
    entry_ids = [e["id"] for e in authoritative]
    assert "opening-c_test-2026" in entry_ids
    assert "opening-c_test-2026-ytd" in entry_ids

    # 测试冲销重置
    reset_token = await issue_step_up_token(
        session,
        company_id="c_test",
        user_id="owner_u1",
        action="adjust_opening_balance",
    )
    reset_res = await reset_opening_balances(
        "c_test",
        2026,
        user_id="owner_u1",
        step_up_token=reset_token,
        request_id="req-midyear-reset",
        session=session,
    )
    assert reset_res["ok"] is True
    assert reset_res["reversal"]["voucherStatus"] == "posted"


@pytest.mark.asyncio
async def test_year_start_snapshot_round_trip_with_midyear_opening(midyear_session):
    """年初快照随期初凭证落库：年中建账时报表「年初余额」列用它，不能拿期初顶替。

    2026-10 启用：期初是 9 月 30 日余额（12,315.09），1 月 1 日货币资金是
    18,377.71。这两个数差着 1-9 月的全部流水，混用会让报送表的年初列整体错位。
    """
    session = midyear_session
    token = await issue_step_up_token(
        session, company_id="c_test", user_id="owner_u1", action="adjust_opening_balance",
    )
    await save_opening_balances(
        "c_test",
        2026,
        [
            {"account_code": "1002", "account_name": "银行存款", "debit_balance": "12315.09", "credit_balance": "0"},
            {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0", "credit_balance": "12315.09"},
        ],
        user_id="owner_u1",
        step_up_token=token,
        request_id="req-year-start",
        start_period="2026-10",
        year_start_items=[
            {"account_code": "1002", "account_name": "银行存款", "debit_balance": "18377.71", "credit_balance": "0"},
            {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0", "credit_balance": "18377.71"},
        ],
        session=session,
    )

    items, known = await get_year_start_balances("c_test", 2026, session=session)
    assert known is True
    cash = next(i for i in items if i["account_code"] == "1002")
    assert Decimal(str(cash["debit_balance"])) == Decimal("18377.71")

    details = await get_opening_details("c_test", 2026, session=session)
    assert len(details["year_start_items"]) == 2
    # 期初本身仍是建账时点的 9 月 30 日数，两者互不覆盖
    opening_cash = next(i for i in details["items"] if i["account_code"] == "1002")
    assert Decimal(str(opening_cash["debit_balance"])) == Decimal("12315.09")


@pytest.mark.asyncio
async def test_opening_and_year_start_keep_partner_dimension(midyear_session):
    """期初与年初快照都要保留客商：报表按「科目 + 客商」方向重分类要靠它。

    2202 甲供应商贷方 20,000、乙供应商借方 5,000：科目净额是贷方 15,000，
    但乙的 5,000 是预付性质，报表必须拆出去，否则资产、负债同时少列。
    """
    session = midyear_session
    token = await issue_step_up_token(
        session, company_id="c_test", user_id="owner_u1", action="adjust_opening_balance",
    )
    await save_opening_balances(
        "c_test",
        2026,
        [
            {"account_code": "1002", "account_name": "银行存款", "debit_balance": "15000", "credit_balance": "0"},
            {"account_code": "2202", "account_name": "应付账款", "debit_balance": "0", "credit_balance": "20000", "partner_name": "甲供应商"},
            {"account_code": "2202", "account_name": "应付账款", "debit_balance": "5000", "credit_balance": "0", "partner_name": "乙供应商"},
        ],
        user_id="owner_u1",
        step_up_token=token,
        request_id="req-partner-opening",
        start_period="2026-10",
        year_start_items=[
            {"account_code": "1002", "account_name": "银行存款", "debit_balance": "13000", "credit_balance": "0"},
            {"account_code": "2202", "account_name": "应付账款", "debit_balance": "0", "credit_balance": "15000", "partner_name": "甲供应商"},
            {"account_code": "2202", "account_name": "应付账款", "debit_balance": "2000", "credit_balance": "0", "partner_name": "乙供应商"},
        ],
        session=session,
    )

    partners = await get_opening_partner_balances("c_test", 2026, session=session)
    payable = {(p["account_code"], p["partner_name"]): p for p in partners}
    assert Decimal(str(payable[("2202", "甲供应商")]["credit_balance"])) == Decimal("20000")
    assert Decimal(str(payable[("2202", "乙供应商")]["debit_balance"])) == Decimal("5000")
    assert Decimal(str(payable[("1002", "")]["debit_balance"])) == Decimal("15000")

    year_start, known = await get_year_start_balances("c_test", 2026, session=session)
    assert known is True
    year_partners = {(i["account_code"], i["partner_name"]) for i in year_start}
    assert ("2202", "甲供应商") in year_partners
    assert ("2202", "乙供应商") in year_partners

    details = await get_opening_details("c_test", 2026, session=session)
    assert {i.get("partner_name", "") for i in details["year_start_items"]} == {"甲供应商", "乙供应商", ""}


@pytest.mark.asyncio
async def test_year_start_unknown_when_midyear_opening_has_no_snapshot(midyear_session):
    """年中建账又没导年初数 → known=False，报表不得把期初当成年初填进报送表。"""
    session = midyear_session
    token = await issue_step_up_token(
        session, company_id="c_test", user_id="owner_u1", action="adjust_opening_balance",
    )
    await save_opening_balances(
        "c_test",
        2026,
        [
            {"account_code": "1002", "account_name": "银行存款", "debit_balance": "100", "credit_balance": "0"},
            {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0", "credit_balance": "100"},
        ],
        user_id="owner_u1",
        step_up_token=token,
        request_id="req-no-year-start",
        start_period="2026-06",
        session=session,
    )
    items, known = await get_year_start_balances("c_test", 2026, session=session)
    assert items == []
    assert known is False


@pytest.mark.asyncio
async def test_year_start_falls_back_to_opening_for_january_start(midyear_session):
    """1 月建账时期初就是年初，没有快照也算 known。"""
    session = midyear_session
    token = await issue_step_up_token(
        session, company_id="c_test", user_id="owner_u1", action="adjust_opening_balance",
    )
    await save_opening_balances(
        "c_test",
        2026,
        [
            {"account_code": "1002", "account_name": "银行存款", "debit_balance": "100", "credit_balance": "0"},
            {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0", "credit_balance": "100"},
        ],
        user_id="owner_u1",
        step_up_token=token,
        request_id="req-jan-start",
        start_period="2026-01",
        session=session,
    )
    items, known = await get_year_start_balances("c_test", 2026, session=session)
    assert items == []
    assert known is True


@pytest.mark.asyncio
async def test_opening_partner_name_is_resolved_to_id_and_groups_aging(midyear_session):
    """期初只给往来单位名，保存后必须落 partner_id。

    回归的是「老板看不出欠谁钱」的根因：期初分录豁免辅助核算归一化
    （accounting_core_service 里 opening 不在 strict_auxiliary 内），
    partner_id 只能由服务层按名字解析。缺了它，账龄按 partner_id 分组时
    期初的往来余额会全部塌进「未指定往来单位」。
    """
    from sqlalchemy import select

    session = midyear_session
    token = await issue_step_up_token(
        session,
        company_id="c_test",
        user_id="owner_u1",
        action="adjust_opening_balance",
    )
    items = [
        {"account_code": "1002", "account_name": "银行存款", "debit_balance": "4842.58", "credit_balance": "0.00"},
        {"account_code": "1122", "account_name": "应收账款", "debit_balance": "543.30", "credit_balance": "0.00", "partner_name": "梧桐树保险经纪有限公司"},
        {"account_code": "1122", "account_name": "应收账款", "debit_balance": "1614.12", "credit_balance": "0.00", "partner_name": "东方大地（武汉）保险经纪有限公司"},
        {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0.00", "credit_balance": "7000.00"},
    ]

    res = await save_opening_balances(
        "c_test",
        2026,
        items,
        user_id="owner_u1",
        step_up_token=token,
        request_id="req-partner-01",
        start_period="2026-01",
        session=session,
    )
    assert res["voucherStatus"] == "posted"

    # 每个往来单位都建了档案，并各自落到期初凭证的不同行上
    from db.models import Partner

    partners = (
        await session.execute(select(Partner).where(Partner.company_id == "c_test"))
    ).scalars().all()
    names = {p.name for p in partners}
    assert "梧桐树保险经纪有限公司" in names
    assert "东方大地（武汉）保险经纪有限公司" in names

    lines = (
        await session.execute(
            select(JournalLine).where(JournalLine.account_code == "1122")
        )
    ).scalars().all()
    aux_ids = {str((line.auxiliary or {}).get("partner_id") or "") for line in lines}
    assert "" not in aux_ids, f"期初往来行缺 partner_id：{[l.auxiliary for l in lines]}"
    assert len(aux_ids) == 2

    # 账龄按往来单位分组的落点：分组的 key 就是 partner_id（见 aging_service
    # query_aging 的 `key = partner_id or "__unassigned__"`），所以上面断言
    # auxiliary 里每条都带 id、且两个单位各一个 id，就等于账龄能拆开而不是全塌进
    # 「未指定往来单位」。这里不直接调 query_aging——它走独立 session_scope，
    # 读不到本用例的内存库。


@pytest.mark.asyncio
async def test_reimport_after_reset_must_be_allowed(midyear_session):
    """冲销重置后必须能重新导入期初。

    前端弹窗承诺「解除期初锁定以便重新编辑」，reset_opening_balances 的
    docstring 也写着「允许用户重新导入建账」。但 reverse_journal 只把原凭证置
    reversed、不删行（A1 要求留审计历史），期初凭证 id 又是确定性的，
    save_opening_balances 的 _find_entry 按同一个 id 查就会命中墓碑，于是重导
    撞 ENTRY_POSTED_IMMUTABLE 409；is_locked 又因 status == "reversed" 恒为
    True。结果：冲销一次即永久锁死，只能改库。

    修法：is_locked 只认 posted；期初凭证 id 分代（重导用 -g2），旧代留作审计
    历史；reset 把来源行置 void，否则重导撞 DUPLICATE_SOURCE。
    """
    session = midyear_session
    items = [
        {"account_code": "1002", "account_name": "银行存款", "debit_balance": "1000.00", "credit_balance": "0.00"},
        {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0.00", "credit_balance": "1000.00"},
    ]
    first_token = await issue_step_up_token(
        session, company_id="c_test", user_id="owner_u1", action="adjust_opening_balance",
    )
    first = await save_opening_balances(
        "c_test", 2026, items, user_id="owner_u1", step_up_token=first_token,
        request_id="req-first", start_period="2026-01", session=session,
    )
    assert first["voucherStatus"] == "posted"

    reset_token = await issue_step_up_token(
        session, company_id="c_test", user_id="owner_u1", action="adjust_opening_balance",
    )
    await reset_opening_balances(
        "c_test", 2026, user_id="owner_u1", step_up_token=reset_token,
        request_id="req-reset", session=session,
    )

    details = await get_opening_details("c_test", 2026, session=session)
    assert details["is_locked"] is False, "冲销后仍锁定，用户无法重新编辑期初"
    # 重设后建账时间回到「未选择」，前端才能按公司启用日期重新给默认值
    assert details["start_period_saved"] is False, "重设后仍钉在旧建账月份"

    retry_token = await issue_step_up_token(
        session, company_id="c_test", user_id="owner_u1", action="adjust_opening_balance",
    )
    second = await save_opening_balances(
        "c_test", 2026, items, user_id="owner_u1", step_up_token=retry_token,
        request_id="req-second", start_period="2026-01", session=session,
    )
    assert second["voucherStatus"] == "posted"


@pytest.mark.asyncio
async def test_reimport_uses_new_generation_and_keeps_old_as_audit_history(midyear_session):
    """重导换一代 id，旧期初留作审计历史，且不产生重复活凭证。

    A1「唯一活凭证」：同一时点只能有一条活期初。重导若复用旧 id 会撞 PK；
    若删旧行又违反「已过账不可变」。所以旧代置 reversed 留痕，新代接过活凭证
    的角色，两代互不干扰。
    """
    from sqlalchemy import select

    session = midyear_session
    items = [
        {"account_code": "1002", "account_name": "银行存款", "debit_balance": "1000.00", "credit_balance": "0.00"},
        {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0.00", "credit_balance": "1000.00"},
    ]
    t1 = await issue_step_up_token(session, company_id="c_test", user_id="owner_u1", action="adjust_opening_balance")
    await save_opening_balances("c_test", 2026, items, user_id="owner_u1", step_up_token=t1,
                                request_id="gen-1", start_period="2026-01", session=session)
    t2 = await issue_step_up_token(session, company_id="c_test", user_id="owner_u1", action="adjust_opening_balance")
    await reset_opening_balances("c_test", 2026, user_id="owner_u1", step_up_token=t2,
                                 request_id="gen-reset", session=session)
    t3 = await issue_step_up_token(session, company_id="c_test", user_id="owner_u1", action="adjust_opening_balance")
    await save_opening_balances("c_test", 2026, items, user_id="owner_u1", step_up_token=t3,
                                request_id="gen-2", start_period="2026-01", session=session)

    mains = (
        await session.execute(
            select(JournalEntry).where(
                JournalEntry.company_id == "c_test",
                JournalEntry.entry_type == "opening",
            )
        )
    ).scalars().all()
    by_id = {e.id: e.status for e in mains}
    # 第 1 代（裸 id）留作审计历史，第 2 代（-g2）是活凭证
    assert by_id.get("opening-c_test-2026") == "reversed"
    assert by_id.get("opening-c_test-2026-g2") == "posted"
    live_posted = [i for i, st in by_id.items() if st == "posted" and not i.endswith("-ytd")]
    assert len(live_posted) == 1, f"应只有一条活期初，实际 {live_posted}"

    # 来源行：第 1 代被 reset 置 void（墓碑），第 2 代是新建的活来源。
    # 没有这一步 void，重导会撞 DUPLICATE_SOURCE —— 第二次保存能成功即是证明。
    from db.models import SourceTransaction

    srcs = (
        await session.execute(
            select(SourceTransaction).where(SourceTransaction.company_id == "c_test")
        )
    ).scalars().all()
    opening_srcs = {
        s0.external_key: s0.status for s0 in srcs if s0.source_type == "opening_balance"
    }
    assert opening_srcs.get("opening:c_test:2026:1") == "void"
    assert opening_srcs.get("opening:c_test:2026:2") == "confirmed"


async def _insert_activity(
    session, entry_id: str, occurred_on: date, *, status: str = "posted", entry_type: str = "standard",
) -> None:
    session.add(JournalEntry(
        id=entry_id,
        company_id="c_test",
        entry_type=entry_type,
        status=status,
        occurred_on=occurred_on,
        period_start=occurred_on.replace(day=1),
        summary="历史业务",
        created_by="owner_u1",
        voucher_no=f"V-{entry_id}",
    ))
    await session.flush()


_OPENING_ITEMS = [
    {"account_code": "1002", "account_name": "银行存款", "debit_balance": "1000.00", "credit_balance": "0.00"},
    {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0.00", "credit_balance": "1000.00"},
]


@pytest.mark.asyncio
async def test_opening_rejected_when_prior_activity_in_same_year(midyear_session):
    """建账月之前本年已有业务分录 → 409 OPENING_PRIOR_ACTIVITY。

    老板先录了 3 月的业务，再导「1-5 月」余额表：这些业务的结果已经在余额表里，
    再生成 6 月 1 日的期初凭证会把 3 月算两遍。
    """
    from core.errors import AccountingError
    from sqlalchemy import select

    session = midyear_session
    await _insert_activity(session, "je-2026-03", date(2026, 3, 15))
    token = await issue_step_up_token(
        session, company_id="c_test", user_id="owner_u1", action="adjust_opening_balance",
    )

    with pytest.raises(AccountingError) as info:
        await save_opening_balances(
            "c_test", 2026, _OPENING_ITEMS, user_id="owner_u1", step_up_token=token,
            request_id="req-prior-activity", start_period="2026-06", session=session,
        )
    assert info.value.code == "OPENING_PRIOR_ACTIVITY"
    assert info.value.status_code == 409
    assert "1-5 月" in info.value.message
    assert info.value.context["count"] == 1
    assert info.value.context["earliestOccurredOn"] == "2026-03-15"

    # 失败不留期初凭证
    opening = (
        await session.execute(
            select(JournalEntry).where(
                JournalEntry.company_id == "c_test",
                JournalEntry.entry_type == "opening",
            )
        )
    ).scalars().all()
    assert opening == []


@pytest.mark.asyncio
async def test_opening_allows_activity_in_start_month(midyear_session):
    """建账月当月的分录不算「之前」，不能被拦。"""
    session = midyear_session
    await _insert_activity(session, "je-2026-06", date(2026, 6, 10))
    token = await issue_step_up_token(
        session, company_id="c_test", user_id="owner_u1", action="adjust_opening_balance",
    )
    result = await save_opening_balances(
        "c_test", 2026, _OPENING_ITEMS, user_id="owner_u1", step_up_token=token,
        request_id="req-same-month", start_period="2026-06", session=session,
    )
    assert result["voucherStatus"] == "posted"


@pytest.mark.asyncio
async def test_january_opening_ignores_same_year_activity(midyear_session):
    """1 月建账不生成 YTD 过渡凭证，本年分录不该被双计校验拦下。"""
    session = midyear_session
    await _insert_activity(session, "je-2026-03-b", date(2026, 3, 15))
    token = await issue_step_up_token(
        session, company_id="c_test", user_id="owner_u1", action="adjust_opening_balance",
    )
    result = await save_opening_balances(
        "c_test", 2026, _OPENING_ITEMS, user_id="owner_u1", step_up_token=token,
        request_id="req-january", start_period="2026-01", session=session,
    )
    assert result["voucherStatus"] == "posted"


@pytest.mark.asyncio
async def test_opening_ignores_reversed_and_reversal_activity(midyear_session):
    """已冲销的原分录与冲销分录净额为零，不能拦住「冲销重置后重导」。"""
    session = midyear_session
    await _insert_activity(session, "je-reversed", date(2026, 2, 10), status="reversed")
    await _insert_activity(
        session, "je-reversal", date(2026, 2, 20), entry_type="reversal",
    )
    token = await issue_step_up_token(
        session, company_id="c_test", user_id="owner_u1", action="adjust_opening_balance",
    )
    result = await save_opening_balances(
        "c_test", 2026, _OPENING_ITEMS, user_id="owner_u1", step_up_token=token,
        request_id="req-reversed", start_period="2026-06", session=session,
    )
    assert result["voucherStatus"] == "posted"


@pytest.mark.asyncio
async def test_reset_reimport_keeps_report_cash_and_ledger_to_report_passes(midyear_session):
    """期初「导入 → 核对重设 → 重导」后报表不被冲销凭证清零，结账核对通过。

    根因回归（2026-11 生产实测）：报表发生额排除期初凭证却不排除「冲销期初的
    冲销凭证」，重设后期初被「期初映射 − 冲销」抵消成 0——报表货币资金 0.00、
    资产负债表拆成镜像对、LEDGER_TO_REPORT 阻断。同批锁定两个口径：
    利润表本年累计对 YTD 按方向毛额读取（月结型老账损益科目借贷对称，净额恒 0）；
    账账核对剔除同月冲销对（发生额 = 重导 g2 单张，而非 3 倍）。
    """
    from services.tax_filing_export_service import (
        build_statement_data_from_entries,
        parse_period,
    )
    from services.accounting_core_service import period_close_checks
    from services.opening_balance_service import get_opening_balances

    session = midyear_session
    asset_items = [
        {"account_code": "1002", "account_name": "银行存款", "debit_balance": "1000.00", "credit_balance": "0.00"},
        {"account_code": "3103", "account_name": "本年利润", "debit_balance": "300.00", "credit_balance": "0.00"},
        {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0.00", "credit_balance": "1300.00"},
    ]
    # 月结型老账：损益科目本年累计借贷双方等额（净额 0，利润已结转到 3103）
    ytd_items = [
        {"account_code": "5001", "account_name": "主营业务收入", "debit_balance": "500.00", "credit_balance": "500.00"},
        {"account_code": "5602004", "account_name": "管理费用-办公费", "debit_balance": "300.00", "credit_balance": "300.00"},
    ]

    first_token = await issue_step_up_token(
        session, company_id="c_test", user_id="owner_u1", action="adjust_opening_balance",
    )
    first = await save_opening_balances(
        "c_test", 2026, asset_items, user_id="owner_u1", step_up_token=first_token,
        request_id="req-first", start_period="2026-10", ytd_items=ytd_items, session=session,
    )
    assert first["voucherStatus"] == "posted"

    reset_token = await issue_step_up_token(
        session, company_id="c_test", user_id="owner_u1", action="adjust_opening_balance",
    )
    reset_res = await reset_opening_balances(
        "c_test", 2026, user_id="owner_u1", step_up_token=reset_token,
        request_id="req-reset", session=session,
    )
    assert reset_res["ok"] is True

    second_token = await issue_step_up_token(
        session, company_id="c_test", user_id="owner_u1", action="adjust_opening_balance",
    )
    second = await save_opening_balances(
        "c_test", 2026, asset_items, user_id="owner_u1", step_up_token=second_token,
        request_id="req-second", start_period="2026-10", ytd_items=ytd_items, session=session,
    )
    assert second["voucherStatus"] == "posted"
    assert second["id"] != first["id"], "重导必须换代（-g2），旧代留作审计历史"

    entries = await posted_entries_for_reports(session, "c_test")
    opening_items = await get_opening_balances("c_test", 2026, session=session)
    data = build_statement_data_from_entries(
        entries, opening_items, parse_period("2026-10", "month"), {"name": "重设回归", "taxId": "T1"},
    )

    # 期初现金不被冲销凭证抵消
    assert data.balance_current["cash"] == Decimal("1000.00")
    # 利润表本年累计：方向毛额还原原账本 1-9 月（收入 500 / 费用 300）
    assert data.profit_ytd["revenue"] == Decimal("500.00")
    assert data.profit_ytd["management_expense"] == Decimal("300.00")
    assert data.profit_ytd["net_profit"] == Decimal("200.00")
    # 资产负债表权益用净额口径：对称 YTD 净额 0，3103 借 300 原样挂账
    assert data.profit_ytd_net["net_profit"] == Decimal("0.00")
    assert data.balance_current["retained_earnings"] == Decimal("-300.00")
    assert data.balance_current["asset_total"] == data.balance_current["liability_equity_total"]

    checks = await period_close_checks(session, "c_test", "2026-10")
    by_code = {c["code"]: c for c in checks["checks"]}
    ledger_to_report = by_code["LEDGER_TO_REPORT"]
    assert ledger_to_report["status"] == "passed", ledger_to_report.get("issues")
    assert Decimal(ledger_to_report["reportCash"]) == Decimal("1000.00")
    assert Decimal(ledger_to_report["ledgerCash"]) == Decimal("1000.00")
    # 账账核对：同月冲销对（v1 + 冲销）剔除，发生额只剩 g2 一张
    ledger_to_ledger = by_code["LEDGER_TO_LEDGER"]
    assert Decimal(ledger_to_ledger["debit"]) == Decimal("1300.00")
    assert Decimal(ledger_to_ledger["credit"]) == Decimal("1300.00")
    assert ledger_to_ledger["status"] == "passed"
