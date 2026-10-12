"""工资/社保/个税模块回归测试（2026-09-30 修复验证）。

覆盖：
1. maker_checker 死链自愈：凭证复核过账后，工资表懒同步为 accrued，可正常发放；
2. 社保/个税缴纳凭证：清偿 2211/2221 挂账、金额正确、重复缴纳被拒；
3. 崩溃恢复：计提凭证已建但工资表状态未更新时，重试不产生重复凭证；
4. 月中新员工补入：get_or_create 幂等补齐缺失员工记录。

走真实 save_entry → 会计核心链路（ACCOUNTING_TEST_MODE + sqlite）。
"""
import asyncio
import sqlite3
from decimal import Decimal

import pytest

sqlite3.register_adapter(Decimal, lambda value: format(value, "f"))


async def _setup_company(company_id: str, posting_policy: str = "owner_self_post") -> None:
    """建表 + 建租户 + 可选复核人。"""
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine, session_scope
    from services.accounting_core_service import ensure_tenant
    from db.models import AuthUser, Company, CompanyMember
    from sqlalchemy import select

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    now = "2027-01-01T00:00:00+00:00"
    async with session_scope() as session:
        await ensure_tenant(session, company_id, "owner-1")
        if posting_policy != "owner_self_post":
            company = (
                await session.execute(select(Company).where(Company.company_id == company_id))
            ).scalar_one()
            company.posting_policy = posting_policy
        if posting_policy == "maker_checker":
            session.add(AuthUser(
                user_id="reviewer-1", openid="", phone="", phone_verified_at="",
                wechat_openid_bound_at="", current_company_id=company_id,
                nickname="复核员", avatar="", created_at=now, last_login_at=now, updated_at=now,
            ))
            session.add(CompanyMember(company_id=company_id, user_id="reviewer-1", role="accountant", status="active"))


def _salary_schema() -> None:
    from services.salary_service import _ensure_db_ready

    asyncio.get_event_loop().run_until_complete(_ensure_db_ready())


@pytest.mark.asyncio
async def test_maker_checker_dead_end_healed():
    """死链修复：复核过账后，工资表通过懒同步变为 accrued，可正常发放。"""
    await _setup_company("c-sal-mc", posting_policy="maker_checker")

    from services.accounting_core_service import approve_journal
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, accrue_salary_sheet, pay_salary_sheet,
    )
    from db.session import session_scope

    await create_employee("c-sal-mc", "李四", "技术部", 6000, 4000)
    await get_or_create_salary_sheet("c-sal-mc", "2027-04")
    await replace_salary_allocations("c-sal-mc", "owner-1", "2027-04", [])

    accrued = await accrue_salary_sheet("c-sal-mc", "owner-1", "2027-04")
    assert accrued["status"] == "pending_review"

    async with session_scope() as session:
        entry = await approve_journal(
            session, accrued["accrual_voucher_id"],
            company_id="c-sal-mc", user_id="reviewer-1", request_id="r1",
        )
    assert entry.status == "posted"

    # 重新读取工资表 → 懒同步为 accrued
    healed = await get_or_create_salary_sheet("c-sal-mc", "2027-04")
    assert healed["status"] == "accrued", "凭证已过账但工资表未自愈"

    paid = await pay_salary_sheet("c-sal-mc", "owner-1", "2027-04")
    assert paid["status"] == "paid"


@pytest.mark.asyncio
async def test_social_and_tax_payment_clears_liabilities():
    """社保/个税缴纳：金额正确、凭证生成、重复缴纳被拒。"""
    await _setup_company("c-sal-liab")

    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, accrue_salary_sheet,
        pay_payroll_liability,
    )
    from services.entry_service import get_entry

    await create_employee("c-sal-liab", "王五", "技术部", 8000, 5000)
    await get_or_create_salary_sheet("c-sal-liab", "2027-05")
    await replace_salary_allocations("c-sal-liab", "owner-1", "2027-05", [])
    await accrue_salary_sheet("c-sal-liab", "owner-1", "2027-05")

    # 个人 5000×(10.5%+8%)=925；公司 5000×(25%+8%)=1650 → 社保公积金合计 2575
    ss = await pay_payroll_liability("c-sal-liab", "owner-1", "2027-05", "social_security")
    assert ss["ss_paid_voucher_id"]
    ss_entry = await get_entry(ss["ss_paid_voucher_id"], user_id="owner-1", company_id="c-sal-liab")
    assert Decimal(str(ss_entry["amount"])) == Decimal("2575.00")

    # 个税为 0 时缴纳被拒（本月无人缴税）
    with pytest.raises(ValueError, match="金额为 0"):
        await pay_payroll_liability("c-sal-liab", "owner-1", "2027-05", "tax")

    # 重复缴纳被拒
    with pytest.raises(ValueError, match="已缴纳"):
        await pay_payroll_liability("c-sal-liab", "owner-1", "2027-05", "social_security")


@pytest.mark.asyncio
async def test_accrual_crash_recovery_no_duplicate_voucher():
    """崩溃恢复：凭证已建但状态未更新时，重新计提不产生重复凭证。"""
    await _setup_company("c-sal-crash")

    from sqlalchemy import select
    from db.models import JournalEntry, SourceTransaction
    from db.session import session_scope
    from services.database import get_conn
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, accrue_salary_sheet,
    )

    await create_employee("c-sal-crash", "赵六", "技术部", 5000, 4000)
    await get_or_create_salary_sheet("c-sal-crash", "2027-06")
    await replace_salary_allocations("c-sal-crash", "owner-1", "2027-06", [])
    first = await accrue_salary_sheet("c-sal-crash", "owner-1", "2027-06")
    first_voucher_id = first["accrual_voucher_id"]

    # 模拟崩溃：把工资表回滚到"凭证已建但状态未更新"的中间态
    conn = await get_conn()
    try:
        await conn.execute(
            "UPDATE salary_sheets SET status = 'draft', accrual_voucher_id = NULL "
            "WHERE company_id = ? AND month = ?",
            ("c-sal-crash", "2027-06"),
        )
        await conn.commit()
    finally:
        await conn.close()

    # 重新计提 → 通过指纹找回已建凭证，不重复入账
    retry = await accrue_salary_sheet("c-sal-crash", "owner-1", "2027-06")
    assert retry["accrual_voucher_id"] == first_voucher_id, "崩溃恢复后产生了重复凭证"
    assert retry["status"] == "accrued"

    # 全库只有一张计提凭证
    async with session_scope() as session:
        source_id = (
            await session.execute(
                select(SourceTransaction.id).where(
                    SourceTransaction.company_id == "c-sal-crash",
                    SourceTransaction.source_type == "salary_accrual",
                )
            )
        ).scalars().all()
        entries = (
            await session.execute(
                select(JournalEntry.id).where(JournalEntry.source_transaction_id.in_(source_id))
            )
        ).scalars().all()
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_mid_month_hire_seeded_into_draft_sheet():
    """月中入职：已有草稿表时 get_or_create 幂等补入新员工记录。"""
    await _setup_company("c-sal-hire")

    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, accrue_salary_sheet,
    )

    await create_employee("c-sal-hire", "甲", "技术部", 6000, 4000)
    sheet = await get_or_create_salary_sheet("c-sal-hire", "2027-07")
    await replace_salary_allocations("c-sal-hire", "owner-1", "2027-07", [])
    assert len(sheet["records"]) == 1

    # 月中入职
    await create_employee("c-sal-hire", "乙", "技术部", 5000, 4000)
    sheet2 = await get_or_create_salary_sheet("c-sal-hire", "2027-07")
    assert len(sheet2["records"]) == 2, "新员工未被补入草稿工资表"
    names = {r["employee_name"] for r in sheet2["records"]}
    assert names == {"甲", "乙"}

    # 已计提的表不再补人（保护已入账数据）
    await accrue_salary_sheet("c-sal-hire", "owner-1", "2027-07")
    await create_employee("c-sal-hire", "丙", "技术部", 4000, 4000)
    sheet3 = await get_or_create_salary_sheet("c-sal-hire", "2027-07")
    assert len(sheet3["records"]) == 2, "计提后的工资表不应补入新员工"


@pytest.mark.asyncio
async def test_partial_rd_allocation_goes_to_admin():
    """P3-8：研发部门员工允许部分分摊（≤100%），未分摊余额计入管理费用。"""
    await _setup_company("c-sal-partial")

    from db.session import session_scope
    from services.rd_service import create_project
    from services.entry_service import get_entry
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, accrue_salary_sheet, list_employees,
    )

    async with session_scope() as session:
        project = await create_project(
            session, "c-sal-partial", "owner-1",
            {"code": "RD-PARTIAL", "name": "部分分摊项目", "type": "self_started",
             "status": "active",
             "startDate": __import__("datetime").date(2027, 1, 1),
             "endDate": __import__("datetime").date(2028, 12, 31)},
            request_id="t-partial",
        )

    await create_employee("c-sal-partial", "钱七", "研发部", 10000, 10000)
    await get_or_create_salary_sheet("c-sal-partial", "2027-08")
    employees = await list_employees("c-sal-partial")
    employee_id = employees[-1]["id"]

    # 60% 进研发、40% 留管理 —— 旧规则强制 100% 会拒绝
    await replace_salary_allocations("c-sal-partial", "owner-1", "2027-08", [
        {"employeeId": employee_id,
         "projectId": project["id"],
         "allocationRate": 0.6,
         "allocationBasis": "60% 工时投入研发"},
    ])
    accrued = await accrue_salary_sheet("c-sal-partial", "owner-1", "2027-08")
    entry = await get_entry(accrued["accrual_voucher_id"], user_id="owner-1", company_id="c-sal-partial")
    lines = entry["voucherLines"]
    rd_total = sum(Decimal(str(l["amount"])) for l in lines["debit"] if l["accountCode"] == "530101")
    admin_total = sum(Decimal(str(l["amount"])) for l in lines["debit"] if l["accountCode"] == "5609")
    gross = Decimal("10000.00")
    company_ss = (gross * Decimal("0.33")).quantize(Decimal("0.01"))  # 25% 社保 + 8% 公积金
    r_cost = gross + company_ss
    assert rd_total == (r_cost * Decimal("0.6")).quantize(Decimal("0.01"))
    assert admin_total == (r_cost - rd_total).quantize(Decimal("0.01"))

    # 分摊比例合计超 100% 被分配服务拒绝（计提层的合计校验为纵深防御）
    from core.errors import AccountingError

    with pytest.raises(AccountingError, match="合计不能超过100%"):
        await replace_salary_allocations("c-sal-partial", "owner-1", "2027-08", [
            {"employeeId": employee_id,
             "projectId": project["id"], "allocationRate": 0.8, "allocationBasis": "项目A 工时"},
            {"employeeId": employee_id,
             "projectId": project["id"], "allocationRate": 0.8, "allocationBasis": "项目B 工时"},
        ])


@pytest.mark.asyncio
async def test_social_base_clamped_by_config(monkeypatch):
    """P3-6：社保基数按配置上下限限幅。"""
    import config as config_module
    from services import salary_service

    monkeypatch.setattr(config_module, "SOCIAL_SECURITY_BASE_MIN", 4462.0, raising=False)
    monkeypatch.setattr(config_module, "SOCIAL_SECURITY_BASE_MAX", 22311.0, raising=False)

    # 低于下限 → 按下限计算
    personal, company = salary_service.calculate_initial_social_security(1000)
    assert personal == (Decimal("4462.00") * Decimal("0.185")).quantize(Decimal("0.01"))
    assert company == (Decimal("4462.00") * Decimal("0.33")).quantize(Decimal("0.01"))

    # 高于上限 → 按上限计算
    personal, company = salary_service.calculate_initial_social_security(999999)
    assert personal == (Decimal("22311.00") * Decimal("0.185")).quantize(Decimal("0.01"))

    # 未配置限幅（0）→ 原样
    monkeypatch.setattr(config_module, "SOCIAL_SECURITY_BASE_MIN", 0, raising=False)
    monkeypatch.setattr(config_module, "SOCIAL_SECURITY_BASE_MAX", 0, raising=False)
    personal, company = salary_service.calculate_initial_social_security(1000)
    assert personal == (Decimal("1000.00") * Decimal("0.185")).quantize(Decimal("0.01"))


def test_housing_fund_included_in_draft():
    """P3-5：默认口径含公积金（个人 10.5%+8%，单位 25%+8%），与旧 28% 单边口径区分。"""
    from services.salary_service import calculate_initial_social_security

    personal, company = calculate_initial_social_security(10000)
    # 个人 = 10000×18.5% = 1850；单位 = 10000×33% = 3300
    assert personal == Decimal("1850.00")
    assert company == Decimal("3300.00")


@pytest.mark.asyncio
async def test_payment_voucher_uses_provided_pay_date():
    """P3-9：发放凭证日期使用传入的实际发薪日，而非工资月末。"""
    await _setup_company("c-sal-paydate")

    from services.entry_service import get_entry
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, accrue_salary_sheet, pay_salary_sheet,
    )

    await create_employee("c-sal-paydate", "孙八", "技术部", 6000, 0)
    await get_or_create_salary_sheet("c-sal-paydate", "2027-09")
    await replace_salary_allocations("c-sal-paydate", "owner-1", "2027-09", [])
    accrued = await accrue_salary_sheet("c-sal-paydate", "owner-1", "2027-09")
    paid = await pay_salary_sheet("c-sal-paydate", "owner-1", "2027-09", pay_date="2027-10-15")
    entry = await get_entry(paid["payment_voucher_id"], user_id="owner-1", company_id="c-sal-paydate")
    assert entry.get("occurDate") == "2027-10-15"
    assert accrued["status"] == "accrued"

    # 非法日期被拒
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        await pay_salary_sheet("c-sal-paydate", "owner-1", "2027-09", pay_date="not-a-date")
