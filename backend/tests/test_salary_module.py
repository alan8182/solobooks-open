# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

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
        company = (
            await session.execute(select(Company).where(Company.company_id == company_id))
        ).scalar_one()
        # 申报作业包要求统一社会信用代码：给测试账套一个合法占位
        company.payload = {**(company.payload or {}), "taxId": "91330100MA2TEST001"}
        if posting_policy != "owner_self_post":
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

    paid = await pay_salary_sheet("c-sal-mc", "owner-1", "2027-04", pay_date="2027-05-10")
    assert paid["status"] == "accrued", "复核模式下发放凭证未过账前不能算已发放"
    assert paid["paymentStatus"] == "pending_review"

    async with session_scope() as session:
        await approve_journal(
            session, paid["payment_voucher_id"],
            company_id="c-sal-mc", user_id="reviewer-1", request_id="r2",
        )
    healed_paid = await get_or_create_salary_sheet("c-sal-mc", "2027-04")
    assert healed_paid["status"] == "paid"
    assert healed_paid["paymentStatus"] == "posted"


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

    # 社保：个人 5000×10.5%=525；公司 5000×25%=1250 → 1775；公积金另计 800
    ss = await pay_payroll_liability("c-sal-liab", "owner-1", "2027-05", "social_security", pay_date="2027-06-10")
    assert ss["ss_paid_voucher_id"]
    ss_entry = await get_entry(ss["ss_paid_voucher_id"], user_id="owner-1", company_id="c-sal-liab")
    assert Decimal(str(ss_entry["amount"])) == Decimal("1775.00")
    hf = await pay_payroll_liability("c-sal-liab", "owner-1", "2027-05", "housing_fund", pay_date="2027-06-10")
    assert hf["hf_paid_voucher_id"]
    hf_entry = await get_entry(hf["hf_paid_voucher_id"], user_id="owner-1", company_id="c-sal-liab")
    assert Decimal(str(hf_entry["amount"])) == Decimal("800.00")

    # 个税为 0 时不再是错误，直接标记无需缴纳
    tax = await pay_payroll_liability("c-sal-liab", "owner-1", "2027-05", "tax")
    assert tax["taxPaymentStatus"] == "not_required"
    assert not tax["tax_paid_voucher_id"]

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
async def test_hire_date_limits_salary_sheet_months():
    """入职日期：工资表只从入职当月起计入，入职前月份不生成记录。"""
    await _setup_company("c-sal-hiredate")

    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, update_employee,
    )

    # 入职前先打开过一次月份 → 留下空草稿表
    empty_sheet = await get_or_create_salary_sheet("c-sal-hiredate", "2027-05")
    assert empty_sheet["records"] == []

    emp = await create_employee(
        "c-sal-hiredate", "新同事", "技术部", 6000, 0, hire_date="2027-07-15",
    )
    assert emp["hire_date"] == "2027-07-15"

    # 入职前月份不补人、不计提
    before = await get_or_create_salary_sheet("c-sal-hiredate", "2027-05")
    assert before["records"] == [], "入职前月份不应补入工资记录"
    assert (await get_or_create_salary_sheet("c-sal-hiredate", "2027-06"))["records"] == []

    # 入职当月及以后正常计提
    hire_month = await get_or_create_salary_sheet("c-sal-hiredate", "2027-07")
    assert [r["employee_name"] for r in hire_month["records"]] == ["新同事"]
    assert len((await get_or_create_salary_sheet("c-sal-hiredate", "2027-08"))["records"]) == 1

    # 编辑入职日期后立即生效
    updated = await update_employee("c-sal-hiredate", emp["id"], {"hire_date": "2027-09-01"})
    assert updated["hire_date"] == "2027-09-01"


@pytest.mark.asyncio
async def test_hire_date_invalid_format_rejected():
    """入职日期格式错误直接拒绝，避免脏数据写入。"""
    await _setup_company("c-sal-hiredate-bad")

    from services.salary_service import create_employee

    with pytest.raises(ValueError):
        await create_employee(
            "c-sal-hiredate-bad", "格式错", "技术部", 5000, 0, hire_date="2027/07/15",
        )


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
    rd_total = sum(Decimal(str(l["amount"])) for l in lines["debit"] if l["accountCode"] == "430100101")
    admin_salary = sum(Decimal(str(l["amount"])) for l in lines["debit"] if l["accountCode"] == "5602001")
    admin_social = sum(Decimal(str(l["amount"])) for l in lines["debit"] if l["accountCode"] == "5602016")
    admin_housing = sum(Decimal(str(l["amount"])) for l in lines["debit"] if l["accountCode"] == "5602017")
    gross = Decimal("10000.00")
    company_ss = (gross * Decimal("0.33")).quantize(Decimal("0.01"))  # 25% 社保 + 8% 公积金
    company_housing = (gross * Decimal("0.08")).quantize(Decimal("0.01"))  # 8% 单位公积金
    company_social = company_ss - company_housing  # 25% 单位社保
    r_cost = gross + company_ss
    assert rd_total == (r_cost * Decimal("0.6")).quantize(Decimal("0.01"))
    # 40% 余额拆成三条管理费用明细：工资/社保/公积金三池各自按工时分摊
    assert admin_salary == (gross * Decimal("0.4")).quantize(Decimal("0.01"))
    assert admin_social == (company_social * Decimal("0.4")).quantize(Decimal("0.01"))
    assert admin_housing == (company_housing * Decimal("0.4")).quantize(Decimal("0.01"))
    assert admin_salary + admin_social + admin_housing == (r_cost - rd_total).quantize(Decimal("0.01"))
    # 贷方明细科目齐备（221102 只挂社保、221105 挂公积金），且贷方合计 == 借方合计
    personal_ss = (gross * Decimal("0.185")).quantize(Decimal("0.01"))
    net = gross - personal_ss
    assert sum(Decimal(str(l["amount"])) for l in lines["credit"] if l["accountCode"] == "221101") == net
    assert sum(Decimal(str(l["amount"])) for l in lines["credit"] if l["accountCode"] == "221102") == company_social
    assert sum(Decimal(str(l["amount"])) for l in lines["credit"] if l["accountCode"] == "221105") == company_housing
    assert sum(Decimal(str(l["amount"])) for l in lines["credit"] if l["accountCode"] == "221104") == personal_ss
    assert sum(Decimal(str(l["amount"])) for l in lines["credit"] if l["accountCode"] == "2221012") == 0
    debit_sum = sum(Decimal(str(l["amount"])) for l in lines["debit"])
    credit_sum = sum(Decimal(str(l["amount"])) for l in lines["credit"])
    assert debit_sum == credit_sum == r_cost

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


def _sum_by_code(lines: list[dict], code: str) -> Decimal:
    return sum((Decimal(str(line["amount"])) for line in lines if line["accountCode"] == code), Decimal("0"))


async def _account_net_balance(company_id: str, code: str) -> Decimal:
    """按科目汇总已过账凭证的净额（借正贷负）。"""
    from sqlalchemy import select
    from db.models import JournalEntry, JournalLine
    from db.session import session_scope

    async with session_scope() as session:
        stmt = (
            select(JournalLine.direction, JournalLine.amount)
            .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
            .where(
                JournalLine.company_id == company_id,
                JournalLine.account_code == code,
                # JOIN 后必须同时过滤凭证侧公司，防止跨公司同 id 碰撞串数
                JournalEntry.company_id == company_id,
                # 与报表口径一致：posted + reversed 都计入，冲销凭证原地对冲；
                # 只算 posted 会让「冲销后净额归零」变成「冲销后净额翻正」
                JournalEntry.status.in_(("posted", "reversed")),
            )
        )
        rows = (await session.execute(stmt)).all()
    return sum(
        (amount if direction == "debit" else -amount for direction, amount in rows),
        Decimal("0"),
    )


@pytest.mark.asyncio
async def test_four_vouchers_close_the_loop():
    """五笔分录闭环：221101/221102/221105/221104/2221012 期末余额全部归零。"""
    await _setup_company("c-sal-loop")

    from services.entry_service import get_entry
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, update_salary_records,
        accrue_salary_sheet, pay_salary_sheet, pay_payroll_liability,
    )

    await create_employee("c-sal-loop", "周九", "技术部", 20000, 20000)
    sheet = await get_or_create_salary_sheet("c-sal-loop", "2027-10")
    await replace_salary_allocations("c-sal-loop", "owner-1", "2027-10", [])
    # 构造个税 > 0 场景（既有测试全为 0，从未覆盖个税路径）
    await update_salary_records("c-sal-loop", "2027-10", [
        {**sheet["records"][0], "personal_tax": "1500.00"},
    ])

    accrued = await accrue_salary_sheet("c-sal-loop", "owner-1", "2027-10")
    # 可控日期：发薪/缴纳都在工资所属期次月，避免凭证时序倒挂于计提
    paid = await pay_salary_sheet("c-sal-loop", "owner-1", "2027-10", pay_date="2027-11-05")
    ss = await pay_payroll_liability("c-sal-loop", "owner-1", "2027-10", "social_security", pay_date="2027-11-10")
    hf = await pay_payroll_liability("c-sal-loop", "owner-1", "2027-10", "housing_fund", pay_date="2027-11-10")
    tax = await pay_payroll_liability("c-sal-loop", "owner-1", "2027-10", "tax", pay_date="2027-11-15")

    gross = Decimal("20000.00")
    personal_ss = (gross * Decimal("0.185")).quantize(Decimal("0.01"))   # 3700.00
    personal_hf = (gross * Decimal("0.08")).quantize(Decimal("0.01"))    # 1600.00
    company_ss = (gross * Decimal("0.33")).quantize(Decimal("0.01"))     # 6600.00
    company_hf = (gross * Decimal("0.08")).quantize(Decimal("0.01"))     # 1600.00
    p_tax = Decimal("1500.00")
    net = money_of(gross - personal_ss - p_tax)                         # 14800.00

    # V1 计提
    e1 = await get_entry(accrued["accrual_voucher_id"], user_id="owner-1", company_id="c-sal-loop")
    assert _sum_by_code(e1["voucherLines"]["debit"], "5602001") == gross
    assert _sum_by_code(e1["voucherLines"]["debit"], "5602016") == company_ss - company_hf
    assert _sum_by_code(e1["voucherLines"]["debit"], "5602017") == company_hf
    assert _sum_by_code(e1["voucherLines"]["credit"], "221101") == net
    assert _sum_by_code(e1["voucherLines"]["credit"], "221102") == company_ss - company_hf
    assert _sum_by_code(e1["voucherLines"]["credit"], "221105") == company_hf
    assert _sum_by_code(e1["voucherLines"]["credit"], "221104") == personal_ss
    assert _sum_by_code(e1["voucherLines"]["credit"], "2221012") == p_tax
    assert Decimal(str(e1["amount"])) == gross + company_ss

    # V2 发放：只结清应付职工薪酬-工资
    e2 = await get_entry(paid["payment_voucher_id"], user_id="owner-1", company_id="c-sal-loop")
    assert len(e2["voucherLines"]["debit"]) == 1
    assert len(e2["voucherLines"]["credit"]) == 1
    assert _sum_by_code(e2["voucherLines"]["debit"], "221101") == net
    assert _sum_by_code(e2["voucherLines"]["credit"], "1002") == net

    # V3 缴社保：单位社保 / 代扣个人社保（不含公积金）
    e3 = await get_entry(ss["ss_paid_voucher_id"], user_id="owner-1", company_id="c-sal-loop")
    assert _sum_by_code(e3["voucherLines"]["debit"], "221102") == company_ss - company_hf
    assert _sum_by_code(e3["voucherLines"]["debit"], "221104") == personal_ss - personal_hf
    assert _sum_by_code(e3["voucherLines"]["credit"], "1002") == company_ss + personal_ss - company_hf - personal_hf

    # V4 缴公积金：单独一笔，冲 221105（单位公积金）/221104（个人合并代扣）
    e4 = await get_entry(hf["hf_paid_voucher_id"], user_id="owner-1", company_id="c-sal-loop")
    assert _sum_by_code(e4["voucherLines"]["debit"], "221105") == company_hf
    assert _sum_by_code(e4["voucherLines"]["debit"], "221104") == personal_hf
    assert _sum_by_code(e4["voucherLines"]["credit"], "1002") == company_hf + personal_hf

    # V5 缴个税
    e5 = await get_entry(tax["tax_paid_voucher_id"], user_id="owner-1", company_id="c-sal-loop")
    assert _sum_by_code(e5["voucherLines"]["debit"], "2221012") == p_tax
    assert _sum_by_code(e5["voucherLines"]["credit"], "1002") == p_tax

    # 闭环断言：五个中间科目余额归零，银行净流出 = 总人工成本
    for code in ("221101", "221102", "221104", "221105", "2221012"):
        assert await _account_net_balance("c-sal-loop", code) == 0, f"{code} 未结平"
    assert await _account_net_balance("c-sal-loop", "1002") == -(gross + company_ss)

    # 时序：发放/缴纳凭证日期不得早于计提（pay_date 可控后天然成立）
    assert e2["occurDate"] >= e1["occurDate"]
    assert e3["occurDate"] >= e1["occurDate"]
    assert e4["occurDate"] >= e1["occurDate"]
    assert e5["occurDate"] >= e1["occurDate"]


def money_of(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


@pytest.mark.asyncio
async def test_iit_uses_leaf_account_2221012():
    """回归：个税必须挂末级科目 2221012，而不是非末级的 2221（会 INVALID_ACCOUNT）。"""
    await _setup_company("c-sal-iit")

    from services.entry_service import get_entry
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, update_salary_records,
        accrue_salary_sheet, pay_payroll_liability,
    )

    await create_employee("c-sal-iit", "吴十", "技术部", 15000, 0)
    sheet = await get_or_create_salary_sheet("c-sal-iit", "2027-11")
    await replace_salary_allocations("c-sal-iit", "owner-1", "2027-11", [])
    await update_salary_records("c-sal-iit", "2027-11", [
        {**sheet["records"][0], "personal_tax": "800.00"},
    ])

    accrued = await accrue_salary_sheet("c-sal-iit", "owner-1", "2027-11")
    entry = await get_entry(accrued["accrual_voucher_id"], user_id="owner-1", company_id="c-sal-iit")
    codes = {line["accountCode"] for line in entry["voucherLines"]["credit"]}
    assert "2221012" in codes
    assert "2221" not in codes, "个税不得挂非末级科目 2221"

    tax = await pay_payroll_liability("c-sal-iit", "owner-1", "2027-11", "tax", pay_date="2027-12-10")
    tax_entry = await get_entry(tax["tax_paid_voucher_id"], user_id="owner-1", company_id="c-sal-iit")
    assert _sum_by_code(tax_entry["voucherLines"]["debit"], "2221012") == Decimal("800.00")


@pytest.mark.asyncio
async def test_payroll_payable_in_balance_sheet_after_refactor():
    """2211/1221 变为非末级后，资产负债表仍要能前缀汇总 221101/122101。"""
    await _setup_company("c-sal-bs")

    from sqlalchemy import select
    from db.models import ChartAccount
    from db.session import session_scope
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, accrue_salary_sheet,
    )
    from services.tax_filing_export_service import _sum_prefix

    async with session_scope() as session:
        accounts = (
            await session.execute(select(ChartAccount).where(ChartAccount.company_id == "c-sal-bs"))
        ).scalars().all()
        by_code = {a.code: a for a in accounts}
    # 父级已翻转非末级、子科目齐备
    assert by_code["2211"].is_leaf is False
    assert by_code["1221"].is_leaf is False
    for code in ("221101", "221102", "221103", "221104", "122101", "122102", "122103", "5602016"):
        assert by_code[code].is_leaf is True, f"{code} 应为末级科目"

    await create_employee("c-sal-bs", "郑十一", "技术部", 9000, 0)
    await get_or_create_salary_sheet("c-sal-bs", "2027-12")
    await replace_salary_allocations("c-sal-bs", "owner-1", "2027-12", [])
    await accrue_salary_sheet("c-sal-bs", "owner-1", "2027-12")

    balances = {"221101": Decimal("100.00"), "221102": Decimal("50.00")}
    assert _sum_prefix(balances, "2211") == Decimal("150.00")
    assert _sum_prefix({"122101": Decimal("30.00")}, "1221") == Decimal("30.00")


@pytest.mark.asyncio
async def test_rd_split_and_ss_mixed_allocation():
    """双池分摊各自兜底舍入残差：研发 100% 员工 + 非研发员工，总额无 1 分误差。"""
    await _setup_company("c-sal-pool")

    from services.entry_service import get_entry
    from services.rd_allocation_service import replace_salary_allocations
    from services.rd_service import create_project
    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, list_employees, accrue_salary_sheet,
    )
    from db.session import session_scope

    async with session_scope() as session:
        project = await create_project(
            session, "c-sal-pool", "owner-1",
            {"code": "RD-POOL", "name": "双池项目", "type": "self_started",
             "status": "active",
             "startDate": __import__("datetime").date(2027, 1, 1),
             "endDate": __import__("datetime").date(2028, 12, 31)},
            request_id="t-pool",
        )
    await create_employee("c-sal-pool", "研发甲", "研发部", 3333, 3333)
    await create_employee("c-sal-pool", "研发乙", "研发部", 7777, 5555)
    await create_employee("c-sal-pool", "行政丙", "行政部", 4444, 2222)
    sheet = await get_or_create_salary_sheet("c-sal-pool", "2028-01")
    employees = {e["name"]: e for e in (await list_employees("c-sal-pool"))}
    await replace_salary_allocations("c-sal-pool", "owner-1", "2028-01", [
        {"employeeId": employees["研发甲"]["id"], "projectId": project["id"],
         "allocationRate": 1, "allocationBasis": "全年投入"},
        {"employeeId": employees["研发乙"]["id"], "projectId": project["id"],
         "allocationRate": 0.7, "allocationBasis": "70% 工时"},
    ])
    accrued = await accrue_salary_sheet("c-sal-pool", "owner-1", "2028-01")
    entry = await get_entry(accrued["accrual_voucher_id"], user_id="owner-1", company_id="c-sal-pool")
    lines = entry["voucherLines"]

    def calc(base: str, ss_base: str) -> tuple[Decimal, Decimal]:
        # 单位社保按 social_security_base 计算，不是按应发工资
        return Decimal(base), (Decimal(ss_base) * Decimal("0.33")).quantize(Decimal("0.01"))

    total_gross = Decimal("0")
    total_ss = Decimal("0")
    for base, ss_base in (("3333", "3333"), ("7777", "5555"), ("4444", "2222")):
        g, c = calc(base, ss_base)
        total_gross += g
        total_ss += c

    admin_salary = _sum_by_code(lines["debit"], "5602001")
    admin_social = _sum_by_code(lines["debit"], "5602016")
    admin_housing = _sum_by_code(lines["debit"], "5602017")
    rd_total = _sum_by_code(lines["debit"], "430100101")
    assert admin_salary + admin_social + admin_housing + rd_total == total_gross + total_ss
    debit_sum = sum((Decimal(str(l["amount"])) for l in lines["debit"]), Decimal("0"))
    credit_sum = sum((Decimal(str(l["amount"])) for l in lines["credit"]), Decimal("0"))
    assert debit_sum == credit_sum, "双池分摊出现舍入残差导致凭证不平"
    # 100% 进研发的管理费用归零
    assert _sum_by_code(lines["debit"], "5602016") < total_ss


def test_split_pool_never_overflows():
    """最大余数法分摊：Σ分摊 ≡ 池，杜绝逐项四舍五入溢出。

    旧口径逐项 money(5000×0.333333)=1666.67，三项合计 5000.01，
    多出的 1 分把管理费用摊销压成 -0.01 被丢弃，凭证借贷不平、整月无法计提。
    """
    from services.salary_service import _split_pool

    shares = _split_pool(Decimal("5000.00"), [Decimal("0.333333")] * 3)
    assert sum(shares, Decimal("0")) == Decimal("5000.00")
    assert sorted(shares, reverse=True) == [Decimal("1666.67"), Decimal("1666.67"), Decimal("1666.66")]
    # 费率合计不足 100%：RD 池按比例收缩，残差归管理费用
    shares = _split_pool(Decimal("4999.50"), [Decimal("0.3333")] * 3)
    assert sum(shares, Decimal("0")) == Decimal("4999.50")
    # 边界：零池 / 空权重 / 零权重 / 单权重
    assert _split_pool(Decimal("0"), [Decimal("1")]) == [Decimal("0.00")]
    assert _split_pool(Decimal("100.00"), []) == []
    assert _split_pool(Decimal("100.00"), [Decimal("0"), Decimal("0")]) == [Decimal("0.00")] * 2
    assert _split_pool(Decimal("123.45"), [Decimal("1")]) == [Decimal("123.45")]


@pytest.mark.asyncio
async def test_repeating_decimal_rates_keep_voucher_balanced():
    """三位员工各按 33.3333% 拆三个项目：分摊闭合、凭证平衡、残差归管理费用。

    复现历史缺陷：5000 × 33.3333% × 3 = 5000.01 → admin = -0.01 被
    ``> ZERO`` 丢弃 → 借贷不平 → 整月无法计提。
    """
    await _setup_company("c-sal-round")

    from services.entry_service import get_entry
    from services.rd_allocation_service import replace_salary_allocations
    from services.rd_service import create_project
    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, list_employees, accrue_salary_sheet,
    )
    from db.session import session_scope

    async with session_scope() as session:
        project_ids = []
        for code in ("RD-R1", "RD-R2", "RD-R3"):
            project = await create_project(
                session, "c-sal-round", "owner-1",
                {"code": code, "name": f"舍入项目{code}", "type": "self_started",
                 "status": "active",
                 "startDate": __import__("datetime").date(2027, 1, 1),
                 "endDate": __import__("datetime").date(2028, 12, 31)},
                request_id=f"t-{code}",
            )
            project_ids.append(project["id"])
    # 研发丁：3 × 33.3333%（合计 99.9999%，RD 池四舍五入后吃满 5000）
    await create_employee("c-sal-round", "研发丁", "研发部", 5000, 5000)
    # 研发戊：3 × 33.33%（合计 99.99%，残差 0.30 归管理费用）
    await create_employee("c-sal-round", "研发戊", "研发部", 3000, 3000)
    sheet = await get_or_create_salary_sheet("c-sal-round", "2028-02")
    employees = {e["name"]: e for e in (await list_employees("c-sal-round"))}
    await replace_salary_allocations("c-sal-round", "owner-1", "2028-02", [
        {"employeeId": employees["研发丁"]["id"], "projectId": project_ids[0],
         "allocationRate": 0.333333, "allocationBasis": "三分之一"},
        {"employeeId": employees["研发丁"]["id"], "projectId": project_ids[1],
         "allocationRate": 0.333333, "allocationBasis": "三分之一"},
        {"employeeId": employees["研发丁"]["id"], "projectId": project_ids[2],
         "allocationRate": 0.333333, "allocationBasis": "三分之一"},
        {"employeeId": employees["研发戊"]["id"], "projectId": project_ids[0],
         "allocationRate": 0.3333, "allocationBasis": "三分之一"},
        {"employeeId": employees["研发戊"]["id"], "projectId": project_ids[1],
         "allocationRate": 0.3333, "allocationBasis": "三分之一"},
        {"employeeId": employees["研发戊"]["id"], "projectId": project_ids[2],
         "allocationRate": 0.3333, "allocationBasis": "三分之一"},
    ])

    accrued = await accrue_salary_sheet("c-sal-round", "owner-1", "2028-02")
    entry = await get_entry(accrued["accrual_voucher_id"], user_id="owner-1", company_id="c-sal-round")
    lines = entry["voucherLines"]

    debit_sum = sum((Decimal(str(l["amount"])) for l in lines["debit"]), Decimal("0"))
    credit_sum = sum((Decimal(str(l["amount"])) for l in lines["credit"]), Decimal("0"))
    assert debit_sum == credit_sum, "循环小数费率导致分摊溢出、凭证不平"

    # 研发丁：gross 5000 / c_ss 1650（社保 1250 + 公积金 400），RD 三池 rate 0.999999
    # 全部吃满 → 研发行 5000.00+1250.00+400.00
    # 研发戊：gross 3000 / c_ss 990（社保 750 + 公积金 240），三池残差
    # 0.30（工资）+ 0.07（社保）+ 0.02（公积金）归管理费用
    assert _sum_by_code(lines["debit"], "430100101") == Decimal("10639.61")
    assert _sum_by_code(lines["debit"], "5602001") == Decimal("0.30")
    assert _sum_by_code(lines["debit"], "5602016") == Decimal("0.07")
    assert _sum_by_code(lines["debit"], "5602017") == Decimal("0.02")
    # 贷方：净工资 4075+2445、单位社保 1250+750、单位公积金 400+240、个人社保 925+555
    assert _sum_by_code(lines["credit"], "221101") == Decimal("6520.00")
    assert _sum_by_code(lines["credit"], "221102") == Decimal("2000.00")
    assert _sum_by_code(lines["credit"], "221105") == Decimal("640.00")
    assert _sum_by_code(lines["credit"], "221104") == Decimal("1480.00")
    # 每个员工的三条项目明细合计精确等于其 RD 三池之和（最大余数法闭合）
    rd_lines = [l for l in lines["debit"] if l["accountCode"] == "430100101"]
    ding = [l for l in rd_lines if "研发丁" in l["rdDetail"]["notes"]]
    wu = [l for l in rd_lines if "研发戊" in l["rdDetail"]["notes"]]
    assert sum((Decimal(str(l["amount"])) for l in ding), Decimal("0")) == Decimal("6650.00")
    assert sum((Decimal(str(l["amount"])) for l in wu), Decimal("0")) == Decimal("3989.61")


@pytest.mark.asyncio
async def test_reverse_historical_2211_entry_still_works():
    """历史凭证含已被非末级化的父级科目（2211）时，冲销仍须成功。

    2211 新增了 221101/221102 子级后 is_leaf 翻转为 false，
    reverse_journal 重放原凭证行时不能再做末级校验，否则历史工资凭证被永久锁死。
    """
    import datetime as _dt

    await _setup_company("c-sal-reverse")

    from sqlalchemy import select
    from db.models import ChartAccount, JournalEntry, JournalLine
    from db.session import session_scope
    from services.accounting_core_service import reverse_journal

    entry_id = "je-sal-legacy"
    async with session_scope() as session:
        legacy_account = (
            await session.execute(select(ChartAccount).where(
                ChartAccount.company_id == "c-sal-reverse", ChartAccount.code == "2211",
            ))
        ).scalars().one()
        debit_account = (
            await session.execute(select(ChartAccount).where(
                ChartAccount.company_id == "c-sal-reverse", ChartAccount.code == "5602001",
            ))
        ).scalars().one()
        # 构造一张含旧父级科目 2211 的已过账凭证，模拟 COA 拆分前的历史数据
        session.add(JournalEntry(
            id=entry_id, company_id="c-sal-reverse", voucher_no="JE-LEGACY-1",
            occurred_on=_dt.date(2027, 1, 31), period_start=_dt.date(2027, 1, 1),
            summary="历史工资凭证", status="posted", entry_type="standard",
            created_by="owner-1", posted_by="owner-1", version=1,
        ))
        await session.flush()
        session.add(JournalLine(
            journal_entry_id=entry_id, company_id="c-sal-reverse", line_no=1,
            account_id=debit_account.id, account_code="5602001", account_name=debit_account.name,
            direction="debit", amount=Decimal("5000.00"), auxiliary={},
        ))
        session.add(JournalLine(
            journal_entry_id=entry_id, company_id="c-sal-reverse", line_no=2,
            account_id=legacy_account.id, account_code="2211", account_name="应付职工薪酬",
            direction="credit", amount=Decimal("5000.00"), auxiliary={},
        ))
        await session.flush()

    assert legacy_account.is_leaf is False, "2211 应已非末级化"

    async with session_scope() as session:
        reversal = await reverse_journal(
            session, entry_id, company_id="c-sal-reverse", user_id="owner-1",
            request_id="t-reverse-salary", reason="测试冲销历史凭证",
        )
        assert reversal.status == "posted"
        assert reversal.entry_type == "reversal"
        assert reversal.reversal_of_id == entry_id
        codes = set((await session.execute(
            select(JournalLine.account_code).where(
                JournalLine.journal_entry_id == reversal.id
            )
        )).scalars().all())
    assert "2211" in codes, "冲销凭证应忠实镜像原凭证的父级科目"



# ===== P2 测试盲区补齐（B1 / B3 / B4）=====

@pytest.mark.asyncio
async def test_salary_payments_rejected_after_period_closed():
    """B1：期间检查必须在工资链路生效——关账后不能补发工资/补缴社保个税。

    期间检查位于 _post_existing（create_entry 内），此前工资链路从未触发过
    该路径；本测试锁定：计提后关账，发放与缴纳都必须 PERIOD_CLOSED 拒绝。
    """
    await _setup_company("c-sal-period")
    # 异步测试内不能走 _salary_schema()（run_until_complete 会撞已运行的事件循环）
    from services.salary_service import _ensure_db_ready
    await _ensure_db_ready()
    # close_period 的关账检查会扫 bank_statements（原始 SQL 表，不在 Base.metadata）
    from services.bank_service import _ensure_schema as _ensure_bank_schema
    await _ensure_bank_schema()

    from core.errors import AccountingError
    from db.session import session_scope
    from services.accounting_core_service import close_period
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, update_salary_records,
        accrue_salary_sheet, pay_salary_sheet, pay_payroll_liability,
    )

    await create_employee("c-sal-period", "钱十二", "技术部", 10000, 5000)
    sheet = await get_or_create_salary_sheet("c-sal-period", "2027-10")
    await replace_salary_allocations("c-sal-period", "owner-1", "2027-10", [])
    await update_salary_records("c-sal-period", "2027-10", [
        {**sheet["records"][0], "personal_tax": "300.00"},
    ])
    await accrue_salary_sheet("c-sal-period", "owner-1", "2027-10")

    async with session_scope() as session:
        await close_period(session, "c-sal-period", "2027-10", user_id="owner-1", request_id="req-close-b1")

    # 计提凭证挂在 2027-10-31，该期间已关账 → 发放/缴纳一律拒绝
    with pytest.raises(AccountingError) as pay_exc:
        await pay_salary_sheet("c-sal-period", "owner-1", "2027-10", pay_date="2027-10-31")
    assert pay_exc.value.code == "PERIOD_CLOSED"

    with pytest.raises(AccountingError) as ss_exc:
        await pay_payroll_liability("c-sal-period", "owner-1", "2027-10", "social_security", pay_date="2027-10-31")
    assert ss_exc.value.code == "PERIOD_CLOSED"

    # 反证：拦截的是「关账期间」而非动作本身——次月（未关账）缴纳应放行
    paid = await pay_salary_sheet("c-sal-period", "owner-1", "2027-10", pay_date="2027-11-10")
    assert paid["status"] == "paid"


@pytest.mark.asyncio
async def test_accrual_reversal_restores_leaf_account_net_balances():
    """B4：计提凭证冲销后，221101/221102/221104/2221012 净额必须归零。

    冲销走 reversal 放宽路径（enforce_leaf=False）——若该放宽被误删，
    冲销凭证会在创建时就被 INVALID_ACCOUNT 拒绝。
    """
    await _setup_company("c-sal-reverse")

    from db.session import session_scope
    from services.accounting_core_service import reverse_journal
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, update_salary_records,
        accrue_salary_sheet,
    )

    await create_employee("c-sal-reverse", "孙十三", "技术部", 12000, 6000)
    sheet = await get_or_create_salary_sheet("c-sal-reverse", "2027-10")
    await replace_salary_allocations("c-sal-reverse", "owner-1", "2027-10", [])
    await update_salary_records("c-sal-reverse", "2027-10", [
        {**sheet["records"][0], "personal_tax": "1200.00"},
    ])
    accrued = await accrue_salary_sheet("c-sal-reverse", "owner-1", "2027-10")

    # 计提后四科目均有非零挂账
    for code in ("221101", "221102", "221104", "2221012"):
        assert await _account_net_balance("c-sal-reverse", code) != 0, f"{code} 计提后应有余额"

    async with session_scope() as session:
        await reverse_journal(
            session, accrued["accrual_voucher_id"],
            company_id="c-sal-reverse", user_id="owner-1",
            request_id="req-reverse-b4", reason="测试冲销计提凭证",
        )

    # 冲销后四科目归零，银行无流出
    for code in ("221101", "221102", "221104", "2221012", "1002"):
        assert await _account_net_balance("c-sal-reverse", code) == 0, f"{code} 冲销后未归零"


@pytest.mark.asyncio
async def test_balance_sheet_aggregates_container_children_from_real_entries():
    """B3 真实断言：报表父级行 == 真实凭证子级求和（独立 oracle：计提金额）。

    防 _sum_prefix 回退成 _sum_codes（旧行为会让 2211/1221 行静默归零仍全绿）。
    """
    await _setup_company("c-sal-b3")

    from datetime import date
    from decimal import Decimal

    from db.session import session_scope
    from services.accounting_core_service import posted_entries_for_reports
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, update_salary_records,
        accrue_salary_sheet,
    )
    from services.tax_filing_export_service import (
        _account_movements, _build_balance_rows, _current_balance_map, _profit_map,
    )

    await create_employee("c-sal-b3", "周十四", "技术部", 20000, 20000)
    sheet = await get_or_create_salary_sheet("c-sal-b3", "2027-10")
    await replace_salary_allocations("c-sal-b3", "owner-1", "2027-10", [])
    await update_salary_records("c-sal-b3", "2027-10", [
        {**sheet["records"][0], "personal_tax": "1500.00"},
    ])
    await accrue_salary_sheet("c-sal-b3", "owner-1", "2027-10")

    # 独立 oracle：计提凭证贷方金额（净额进 221101、单位社保进 221102、
    # 个人社保公积金进 221104、个税进 2221012）
    gross = Decimal("20000.00")
    personal_ss = (gross * Decimal("0.185")).quantize(Decimal("0.01"))
    company_ss = (gross * Decimal("0.33")).quantize(Decimal("0.01"))
    p_tax = Decimal("1500.00")
    net = (gross - personal_ss - p_tax).quantize(Decimal("0.01"))

    async with session_scope() as session:
        entries = await posted_entries_for_reports(session, "c-sal-b3")
    movements = _account_movements(entries, date(2027, 1, 1), date(2027, 10, 31))
    current = _current_balance_map({}, movements)
    # _build_balance_rows 返回 (当期行, 期初行) 二元组
    rows, _opening_rows = _build_balance_rows(current, {}, _profit_map(movements))

    assert rows["salary_payable"] == net + company_ss + personal_ss, \
        "资产负债表应付职工薪酬行必须等于 221101+221102+221104 子级真实发生额"
    assert rows["other_receivable"] == Decimal("0"), \
        "个人社保公积金已重分类为负债，不再挂其他应收款"
    # 反向锁死：子级口径一旦被改回精确码匹配，行会归零
    assert rows["salary_payable"] != Decimal("0")


@pytest.mark.parametrize(
    "description,company_id,statement_id",
    [
        ("代发工资 2027-10", "c-sal-suggest", "stmt-oct-pay"),
        ("代发工资", "c-sal-suggest-no-month", "stmt-oct-pay-no-month"),
    ],
)
@pytest.mark.asyncio
async def test_prior_month_salary_paid_by_current_month_statement_is_not_assigned_to_current_month(
    description, company_id, statement_id
):
    await _setup_company(company_id)

    from services.bank_service import ensure_bank_schema
    from services.database import get_conn
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        accrue_salary_sheet, apply_salary_payment_from_statement, create_employee,
        get_or_create_salary_sheet, list_salary_payment_suggestions,
    )

    await ensure_bank_schema()
    await create_employee(company_id, "测试员工", "技术部", 8000, 5000)
    october = await get_or_create_salary_sheet(company_id, "2027-10")
    await replace_salary_allocations(company_id, "owner-1", "2027-10", [])
    october = await accrue_salary_sheet(company_id, "owner-1", "2027-10")
    november = await get_or_create_salary_sheet(company_id, "2027-11")
    await replace_salary_allocations(company_id, "owner-1", "2027-11", [])
    november = await accrue_salary_sheet(company_id, "owner-1", "2027-11")
    assert october["records"][0]["net_salary"] == november["records"][0]["net_salary"]
    net_salary = october["records"][0]["net_salary"]

    conn = await get_conn()
    try:
        await conn.execute(
            "INSERT INTO bank_statements (id, company_id, transaction_date, description, amount, balance, statement_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (
                statement_id, company_id, "2027-11-10", description,
                f"-{net_salary}", statement_id, "2027-11-10T00:00:00+00:00",
            ),
        )
        await conn.commit()
    finally:
        await conn.close()

    suggestions = await list_salary_payment_suggestions(company_id)
    assert len(suggestions["suggestions"]) == 1
    item = suggestions["suggestions"][0]
    assert item["month"] == "2027-10"
    assert item["payDate"] == "2027-11-10"
    assert item["confidence"] == "high"

    result = await apply_salary_payment_from_statement(
        company_id, "owner-1", "2027-10", statement_id, f"req-suggest-pay-{statement_id}"
    )
    assert result["status"] == "paid"
    assert result["linked"] is True

    paid_october = await get_or_create_salary_sheet(company_id, "2027-10")
    untouched_november = await get_or_create_salary_sheet(company_id, "2027-11")
    assert paid_october["status"] == "paid"
    assert untouched_november["status"] == "accrued"


@pytest.mark.asyncio
async def test_salary_close_check_blocks_only_missing_accrual():
    await _setup_company("c-sal-close")

    from db.session import session_scope
    from services.accounting_core_service import period_close_checks
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        accrue_salary_sheet, create_employee, get_or_create_salary_sheet,
    )

    await create_employee("c-sal-close", "测试员工", "技术部", 6000, 4000)
    await get_or_create_salary_sheet("c-sal-close", "2027-07")

    async with session_scope() as session:
        checks = await period_close_checks(session, "c-sal-close", "2027-07")
    payroll = next(item for item in checks["checks"] if item["code"] == "PAYROLL_ACCRUAL")
    assert payroll["status"] == "blocking"
    assert checks["canClose"] is False

    await replace_salary_allocations("c-sal-close", "owner-1", "2027-07", [])
    await accrue_salary_sheet("c-sal-close", "owner-1", "2027-07")

    async with session_scope() as session:
        checks = await period_close_checks(session, "c-sal-close", "2027-07")
    payroll = next(item for item in checks["checks"] if item["code"] == "PAYROLL_ACCRUAL")
    assert payroll["status"] == "passed"
    assert checks["canClose"] is True
    # 当月计提、次月发放是权责发生制的正常节奏，不在当月结账时报「未发放」。
    notices = {item["code"] for item in checks["notices"]}
    assert "SALARY_PAYABLE_OUTSTANDING" not in notices

    # 到了次月结账仍未发放，才真正超期：提示并给出最早欠发月份。
    async with session_scope() as session:
        next_checks = await period_close_checks(session, "c-sal-close", "2027-08")
    notice = next(
        item for item in next_checks["notices"]
        if item["code"] == "SALARY_PAYABLE_OUTSTANDING"
    )
    assert notice["suggestedMonth"] == "2027-07"
    assert notice["records"][0]["businessPeriod"] == "2027-07"
    assert "超过次月发薪窗口" in notice["message"]


@pytest.mark.asyncio
async def test_bank_salary_payment_cannot_be_booked_as_expense_directly():
    await _setup_company("c-bank-salary-guard")

    from fastapi import HTTPException
    from deps.auth import AuthContext
    from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
    from services.bank_service import classify_single_statement, ensure_bank_schema
    from services.database import get_conn

    await ensure_bank_schema()
    conn = await get_conn()
    try:
        await conn.execute(
            "INSERT INTO bank_statements (id, company_id, transaction_date, description, amount, balance, statement_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (
                "stmt-salary-guard", "c-bank-salary-guard", "2027-11-10", "代发工资 2027-10",
                "-5000.00", "stmt-salary-guard", "2027-11-10T00:00:00+00:00",
            ),
        )
        await conn.commit()
    finally:
        await conn.close()

    classified = await classify_single_statement("c-bank-salary-guard", "stmt-salary-guard")
    assert classified["accountCode"] == "221101"
    assert classified["requiresSalarySettlement"] is True

    with pytest.raises(HTTPException) as exc_info:
        await confirm_suggestion(
            ConfirmSuggestionRequest(
                statement_id="stmt-salary-guard",
                type="new",
                new_entry={
                    "direction": "expense",
                    "amount": 5000,
                    "category": "工资",
                    "title": "代发工资",
                },
            ),
            ctx=AuthContext(user_id="owner-1", company_id="c-bank-salary-guard"),
            request=None,
            session=None,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "SALARY_REQUIRES_PAYROLL"


@pytest.mark.asyncio
async def test_business_context_overrides_tax_social_security_statement_to_non_operating_expense(monkeypatch):
    await _setup_company("c-bank-tax-ss-penalty")

    from fastapi import HTTPException
    from db.session import session_scope
    from deps.auth import AuthContext
    from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
    from services import llm
    from services.bank_service import classify_single_statement, ensure_bank_schema
    from services.database import get_conn
    from services.entry_service import get_entry

    await ensure_bank_schema()
    conn = await get_conn()
    try:
        await conn.execute(
            "INSERT INTO bank_statements (id, company_id, transaction_date, description, amount, balance, statement_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (
                "stmt-tax-penalty", "c-bank-tax-ss-penalty", "2027-10-15",
                "税费社保 (对方: 金库杭州市余杭区支库)",
                "-200.00", "stmt-tax-penalty", "2027-10-15T00:00:00+00:00",
            ),
        )
        await conn.execute(
            "INSERT INTO bank_statements (id, company_id, transaction_date, description, amount, balance, statement_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (
                "stmt-pure-ss-guard", "c-bank-tax-ss-penalty", "2027-10-16",
                "社会保险费扣款",
                "-800.00", "stmt-pure-ss-guard", "2027-10-16T00:00:00+00:00",
            ),
        )
        await conn.commit()
    finally:
        await conn.close()

    # 无业务说明时不得把「税费社保」通道直接判成社保（2026-10 个税逾期罚款
    # 曾被判 221102 社保公积金并提示去工资表核销）：先给税费缴纳模糊组让用户拍板；
    # 补充个税罚款说明后一律走 LLM 重判为营业外支出（5711）
    initial = await classify_single_statement("c-bank-tax-ss-penalty", "stmt-tax-penalty")
    assert initial["category"] == "税费缴纳"
    assert initial["requiresConfirmation"] is True
    assert not initial.get("requiresSalarySettlement")

    # 摘要明确写了险种时仍归工资模块
    pure_ss = await classify_single_statement("c-bank-tax-ss-penalty", "stmt-pure-ss-guard")
    assert pure_ss["category"] == "社保公积金"

    llm_calls = []

    def fake_llm(system_prompt, user_message, fallback, **kwargs):
        llm_calls.append(user_message)
        return {
            "direction": "expense",
            "category": "罚款支出",
            "accountCode": "5711011",
            "accountName": "营业外支出-罚款支出",
            "title": "未及时申报个税罚款",
            "confidence": 0.96,
            "reason": "未及时申报个税被税务局罚款属于税务行政处罚，应计入营业外支出-罚款支出",
        }

    monkeypatch.setattr(llm, "safe_chat_text_json", fake_llm)
    reclassified = await classify_single_statement(
        "c-bank-tax-ss-penalty",
        "stmt-tax-penalty",
        "这笔是未及时申报个税被税务局罚了200元",
    )
    assert len(llm_calls) == 1
    assert "用户补充说明：这笔是未及时申报个税被税务局罚了200元" in llm_calls[0]
    assert reclassified["category"] == "罚款支出"
    assert reclassified["accountCode"] == "5711011"

    # 纯社保扣款（未写说明且无「税」字）仍被拦回工资模块
    async with session_scope() as session:
        with pytest.raises(HTTPException) as exc_info:
            await confirm_suggestion(
                ConfirmSuggestionRequest(
                    statement_id="stmt-pure-ss-guard",
                    type="new",
                    new_entry={
                        "direction": "expense",
                        "amount": 800,
                        "category": "其他支出",
                        "accountCode": "5602999",
                        "title": "社会保险费",
                    },
                ),
                ctx=AuthContext(user_id="owner-1", company_id="c-bank-tax-ss-penalty"),
                request=None,
                session=session,
                idempotency_key=None,
            )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "PAYROLL_LIABILITY_REQUIRES_PAYROLL"

    # 税费社保通道改判为罚款支出（5711011）后，提交确认必须正常入账并对账
    async with session_scope() as session:
        confirmed = await confirm_suggestion(
            ConfirmSuggestionRequest(
                statement_id="stmt-tax-penalty",
                type="new",
                new_entry={
                    "direction": "expense",
                    "amount": 200,
                    "category": reclassified["category"],
                    "accountCode": reclassified["accountCode"],
                    "accountName": reclassified["accountName"],
                    "occurDate": "2027-10-15",
                    "title": reclassified["title"],
                    "remark": "业务说明：这笔是未及时申报个税被税务局罚了200元",
                },
            ),
            ctx=AuthContext(user_id="owner-1", company_id="c-bank-tax-ss-penalty"),
            request=None,
            session=session,
            idempotency_key=None,
        )
    assert confirmed["status"] == "created"
    saved = await get_entry(confirmed["entryId"], user_id="owner-1", company_id="c-bank-tax-ss-penalty")
    assert saved["category"] == "罚款支出"
    assert saved["voucherLines"]["debit"][0]["accountCode"] == "5711011"


@pytest.mark.asyncio
async def test_reversing_payment_voucher_restores_sheet_and_allows_repayment():
    await _setup_company("c-sal-repay")

    from db.session import session_scope
    from services.accounting_core_service import reverse_journal
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        accrue_salary_sheet, create_employee, get_or_create_salary_sheet, pay_salary_sheet,
    )

    await create_employee("c-sal-repay", "吴十五", "技术部", 9000, 5000)
    await get_or_create_salary_sheet("c-sal-repay", "2027-08")
    await replace_salary_allocations("c-sal-repay", "owner-1", "2027-08", [])
    accrued = await accrue_salary_sheet("c-sal-repay", "owner-1", "2027-08")
    paid = await pay_salary_sheet(
        "c-sal-repay", "owner-1", "2027-08", pay_date="2027-09-10"
    )
    assert paid["status"] == "paid"
    first_payment_id = paid["payment_voucher_id"]

    async with session_scope() as session:
        await reverse_journal(
            session, first_payment_id,
            company_id="c-sal-repay", user_id="owner-1",
            request_id="req-repay-reverse", reason="测试冲销发放凭证",
        )

    healed = await get_or_create_salary_sheet("c-sal-repay", "2027-08")
    assert healed["status"] == "accrued"
    assert healed["payment_voucher_id"] is None
    assert healed["paymentStatus"] == "unpaid"
    assert healed["payment_generation"] == 1
    assert healed["accrual_voucher_id"] == accrued["accrual_voucher_id"]

    repaid = await pay_salary_sheet(
        "c-sal-repay", "owner-1", "2027-08", pay_date="2027-09-11"
    )
    assert repaid["status"] == "paid"
    assert repaid["payment_voucher_id"] != first_payment_id


@pytest.mark.asyncio
async def test_legacy_combined_social_payment_blocks_housing_fund_repayment():
    await _setup_company("c-sal-legacy-hf")

    from services.database import get_conn
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        accrue_salary_sheet, create_employee, get_or_create_salary_sheet,
        pay_payroll_liability,
    )

    await create_employee("c-sal-legacy-hf", "郑十六", "技术部", 10000, 10000)
    await get_or_create_salary_sheet("c-sal-legacy-hf", "2027-07")
    await replace_salary_allocations("c-sal-legacy-hf", "owner-1", "2027-07", [])
    accrued = await accrue_salary_sheet("c-sal-legacy-hf", "owner-1", "2027-07")

    conn = await get_conn()
    try:
        await conn.execute(
            "UPDATE salary_sheets SET ss_paid_voucher_id = ?, ss_payment_scope = 'combined' "
            "WHERE company_id = ? AND month = ?",
            (accrued["accrual_voucher_id"], "c-sal-legacy-hf", "2027-07"),
        )
        await conn.commit()
    finally:
        await conn.close()

    sheet = await get_or_create_salary_sheet("c-sal-legacy-hf", "2027-07")
    assert sheet["hfPaymentStatus"] == "posted"
    with pytest.raises(ValueError, match="历史社保合并缴纳"):
        await pay_payroll_liability(
            "c-sal-legacy-hf", "owner-1", "2027-07", "housing_fund",
            pay_date="2027-08-10",
        )


# --- 2026-10-23：账套级费率、个税试算、导出 ---

def test_split_withheld_keeps_total_and_ratio():
    """代扣合计拆回社保/公积金：合计守恒，且不能被 min() 截断成社保 0。"""
    from services.salary_service import _split_withheld

    ss, hf = _split_withheld(Decimal("1125"), Decimal("525"), Decimal("600"))
    assert (ss, hf) == (Decimal("525.00"), Decimal("600.00"))

    # 合计比公积金期望还小时，按比例分摊而不是把社保压成 0
    ss2, hf2 = _split_withheld(Decimal("300"), Decimal("525"), Decimal("400"))
    assert ss2 + hf2 == Decimal("300.00")
    assert ss2 > 0 and hf2 > 0

    # 未配置公积金（0%）时整笔算社保，保持旧行为
    ss3, hf3 = _split_withheld(Decimal("525"), Decimal("525"), Decimal("0"))
    assert (ss3, hf3) == (Decimal("525.00"), Decimal("0.00"))


@pytest.mark.asyncio
async def test_payroll_rates_override_and_recalculate():
    """账套级公积金比例：改 12% 后草稿重算，缴公积金金额跟着变。"""
    await _setup_company("c-sal-rates")

    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, get_payroll_rates,
        recalculate_draft_withholdings, save_payroll_rates,
    )

    await create_employee("c-sal-rates", "孙七", "行政部", 10000, 5000)
    sheet = await get_or_create_salary_sheet("c-sal-rates", "2027-06")
    # 默认杭州口径：个人 5000×18.5%=925，单位 5000×33%=1650
    assert Decimal(sheet["records"][0]["personal_social_security"]) == Decimal("925.00")
    assert Decimal(sheet["records"][0]["company_social_security"]) == Decimal("1650.00")

    rates = await get_payroll_rates("c-sal-rates")
    assert rates["isCustomized"] is False

    saved = await save_payroll_rates(
        "c-sal-rates", {"housingPersonal": "0.12", "housingCompany": "0.12"}
    )
    assert saved["isCustomized"] is True
    assert Decimal(saved["rates"]["housingPersonal"]) == Decimal("0.120000")

    updated = await recalculate_draft_withholdings("c-sal-rates", "2027-06")
    # 个人 = 525 社保 + 600 公积金 = 1125；单位 = 1250 + 600 = 1850
    assert Decimal(updated["records"][0]["personal_social_security"]) == Decimal("1125.00")
    assert Decimal(updated["records"][0]["company_social_security"]) == Decimal("1850.00")
    assert Decimal(updated["records"][0]["net_salary"]) == Decimal("8875.00")
    # 拆分后两笔缴纳额：社保 525+1250，公积金 600+600
    assert Decimal(updated["socialSecurityDue"]) == Decimal("1775.00")
    assert Decimal(updated["housingFundDue"]) == Decimal("1200.00")


@pytest.mark.asyncio
async def test_payroll_rates_rejects_invalid_value():
    """费率越界必须报错，不能静默写进账套档案。"""
    await _setup_company("c-sal-rates-bad")

    from services.salary_service import save_payroll_rates

    with pytest.raises(ValueError):
        await save_payroll_rates("c-sal-rates-bad", {"housingPersonal": "1.5"})


@pytest.mark.asyncio
async def test_tax_preview_cumulative_prepayment():
    """个税累计预扣：前两月已计提，第三月按累计口径算本期应扣。"""
    await _setup_company("c-sal-iit")

    from services.salary_service import (
        accrue_salary_sheet, create_employee, get_or_create_salary_sheet,
    )
    from services.salary_iit_service import preview_individual_income_tax

    employee = await create_employee(
        "c-sal-iit", "赵六", "行政部", 10000, 5000, hire_date="2027-01-05"
    )
    for month in ("2027-01", "2027-02"):
        await get_or_create_salary_sheet("c-sal-iit", month)
        await accrue_salary_sheet("c-sal-iit", "owner-1", month)

    preview = await preview_individual_income_tax(
        "c-sal-iit", employee["id"], "2027-03", gross=10000, withheld=925,
    )
    assert preview["months"] == 3
    assert Decimal(preview["cumulativeIncome"]) == Decimal("30000.00")
    assert Decimal(preview["cumulativeBasicDeduction"]) == Decimal("15000.00")
    assert Decimal(preview["cumulativeSpecialDeduction"]) == Decimal("2775.00")
    assert Decimal(preview["taxableIncome"]) == Decimal("12225.00")
    assert Decimal(preview["taxDue"]) == Decimal("366.75")


@pytest.mark.asyncio
async def test_tax_preview_offsets_prepaid_tax():
    """已预缴税额要抵减：本期应扣 = 累计应纳税额 − 已预缴。"""
    await _setup_company("c-sal-iit-offset")

    from services.salary_service import (
        accrue_salary_sheet, create_employee, get_or_create_salary_sheet,
        update_salary_records,
    )
    from services.salary_iit_service import preview_individual_income_tax

    employee = await create_employee(
        "c-sal-iit-offset", "钱九", "行政部", 30000, 5000, hire_date="2027-01-01"
    )
    sheet = await get_or_create_salary_sheet("c-sal-iit-offset", "2027-01")
    records = sheet["records"]
    records[0]["personal_tax"] = 500
    await update_salary_records("c-sal-iit-offset", "2027-01", records)
    await accrue_salary_sheet("c-sal-iit-offset", "owner-1", "2027-01")

    preview = await preview_individual_income_tax(
        "c-sal-iit-offset", employee["id"], "2027-02", gross=30000, withheld=925,
    )
    # 累计 60000 − 10000 − 1850 = 48150 → 10% − 2520 = 2295；已预缴 500 → 本期 1795
    assert Decimal(preview["cumulativeTax"]) == Decimal("2295.00")
    assert Decimal(preview["prepaidTax"]) == Decimal("500.00")
    assert Decimal(preview["taxDue"]) == Decimal("1795.00")


@pytest.mark.asyncio
async def test_salary_sheet_export_contains_totals_and_rates():
    """工资表导出：含明细、合计行与费率口径。"""
    await _setup_company("c-sal-export")

    from services.salary_service import (
        create_employee, export_payslip_csv, export_salary_sheet_csv,
        get_or_create_salary_sheet,
    )

    await create_employee("c-sal-export", "周八", "行政部", 8000, 5000)
    sheet = await get_or_create_salary_sheet("c-sal-export", "2027-07")
    body, filename = await export_salary_sheet_csv("c-sal-export", "2027-07")
    assert filename == "工资表-2027-07.csv"
    assert "合计" in body
    assert "7075.00" in body  # 实发 = 8000 − 925
    assert "费率口径" in body

    record_id = sheet["records"][0]["id"]
    slip_body, slip_name = await export_payslip_csv("c-sal-export", "2027-07", record_id)
    assert slip_name == "工资条-2027-07-周八.csv"
    assert "实发工资" in slip_body


# --- 2026-10-23：账套级部门字典 ---

@pytest.mark.asyncio
async def test_departments_default_and_auto_register():
    """默认三部门；员工建到新部门时自动登记进字典，不用先去设置页。"""
    await _setup_company("c-sal-dept")

    from services.salary_service import create_employee, get_departments

    data = await get_departments("c-sal-dept")
    assert [item["name"] for item in data["departments"]] == ["研发部", "销售部", "行政部"]
    assert data["customized"] is False

    await create_employee("c-sal-dept", "吴一", "总经办", 8000, 5000)

    data = await get_departments("c-sal-dept")
    names = [item["name"] for item in data["departments"]]
    assert "总经办" in names
    dept = next(item for item in data["departments"] if item["name"] == "总经办")
    assert dept["isRd"] is False
    assert dept["headcount"] == 1
    assert data["customized"] is True


@pytest.mark.asyncio
async def test_departments_cannot_remove_in_use():
    """还有在职员工的部门不允许删掉，避免工资表出现游离部门。"""
    await _setup_company("c-sal-dept-del")

    from services.salary_service import create_employee, save_departments

    await create_employee("c-sal-dept-del", "郑二", "总经办", 8000, 5000)

    with pytest.raises(ValueError, match="还有在职员工"):
        await save_departments("c-sal-dept-del", [{"name": "研发部", "isRd": True}])

    saved = await save_departments(
        "c-sal-dept-del",
        [{"name": "研发部", "isRd": True}, {"name": "总经办", "isRd": False}],
    )
    assert [item["name"] for item in saved["departments"]] == ["研发部", "总经办"]


@pytest.mark.asyncio
async def test_custom_rd_department_requires_allocation():
    """把自定义部门标成研发后，计提必须登记工时——否则钱进不了研发支出。"""
    await _setup_company("c-sal-dept-rd")

    from services.salary_service import (
        accrue_salary_sheet, create_employee, get_or_create_salary_sheet, save_departments,
    )

    await create_employee("c-sal-dept-rd", "王三", "技术部", 10000, 5000)
    await get_or_create_salary_sheet("c-sal-dept-rd", "2027-08")

    # 技术部默认不是研发部门：可以直接计提
    accrued = await accrue_salary_sheet("c-sal-dept-rd", "owner-1", "2027-08")
    assert accrued["status"] == "accrued"

    await save_departments("c-sal-dept-rd", [{"name": "技术部", "isRd": True}])
    await get_or_create_salary_sheet("c-sal-dept-rd", "2027-09")
    with pytest.raises(ValueError, match="必须登记研发项目工时"):
        await accrue_salary_sheet("c-sal-dept-rd", "owner-1", "2027-09")


@pytest.mark.asyncio
async def test_backfill_social_insurance_does_not_skew_housing_fund():
    """回填社保（如大病互助或尾差调整）不带偏公积金，计提与缴纳分毫不差。"""
    await _setup_company("c-sal-split-bf")

    from services.entry_service import get_entry
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, update_salary_records,
        accrue_salary_sheet, pay_payroll_liability,
    )

    await create_employee("c-sal-split-bf", "张三", "行政部", 10000, 5000)
    sheet = await get_or_create_salary_sheet("c-sal-split-bf", "2027-05")
    await replace_salary_allocations("c-sal-split-bf", "owner-1", "2027-05", [])

    rec = sheet["records"][0]
    # 默认按 5000 基数：个人社保 525，个人公积金 400，单位社保 1250，单位公积金 400
    assert Decimal(rec["personal_housing_fund"]) == Decimal("400.00")
    assert Decimal(rec["personal_social_insurance"]) == Decimal("525.00")
    assert Decimal(rec["company_housing_fund"]) == Decimal("400.00")
    assert Decimal(rec["company_social_insurance"]) == Decimal("1250.00")

    # 模拟用户对照电子税务局核定表回填：
    # 假设医保含 4.00 元大病险，个人社保实际为 529.00（+4），单位社保实际为 1255.00（+5）
    # 公积金保持 400.00 不动
    updated = await update_salary_records("c-sal-split-bf", "2027-05", [
        {
            **rec,
            "personal_social_insurance": "529.00",
            "personal_housing_fund": "400.00",
            "company_social_insurance": "1255.00",
            "company_housing_fund": "400.00",
        }
    ])
    up_rec = updated["records"][0]
    # 个人合计 = 529 + 400 = 929；单位合计 = 1255 + 400 = 1655
    assert Decimal(up_rec["personal_social_security"]) == Decimal("929.00")
    assert Decimal(up_rec["company_social_security"]) == Decimal("1655.00")
    assert Decimal(up_rec["personal_housing_fund"]) == Decimal("400.00")
    assert Decimal(up_rec["company_housing_fund"]) == Decimal("400.00")
    assert Decimal(up_rec["net_salary"]) == Decimal("9071.00") # 10000 - 929

    # 计提记账
    accrued = await accrue_salary_sheet("c-sal-split-bf", "owner-1", "2027-05")
    acc_entry = await get_entry(accrued["accrual_voucher_id"], user_id="owner-1", company_id="c-sal-split-bf")
    lines = acc_entry["voucherLines"]

    # 借方：工资 10000，单位社保 1255，单位公积金 400
    assert _sum_by_code(lines["debit"], "5602001") == Decimal("10000.00")
    assert _sum_by_code(lines["debit"], "5602016") == Decimal("1255.00")
    assert _sum_by_code(lines["debit"], "5602017") == Decimal("400.00")

    # 贷方：实发 9071，单位社保 1255，单位公积金 400，代扣个人 929
    assert _sum_by_code(lines["credit"], "221101") == Decimal("9071.00")
    assert _sum_by_code(lines["credit"], "221102") == Decimal("1255.00")
    assert _sum_by_code(lines["credit"], "221105") == Decimal("400.00")
    assert _sum_by_code(lines["credit"], "221104") == Decimal("929.00")

    # 缴纳社保：单位社保 1255 + 个人社保 529 = 1784
    ss = await pay_payroll_liability("c-sal-split-bf", "owner-1", "2027-05", "social_security", pay_date="2027-06-10")
    ss_entry = await get_entry(ss["ss_paid_voucher_id"], user_id="owner-1", company_id="c-sal-split-bf")
    assert Decimal(str(ss_entry["amount"])) == Decimal("1784.00")

    # 缴纳公积金：单位公积金 400 + 个人公积金 400 = 800
    hf = await pay_payroll_liability("c-sal-split-bf", "owner-1", "2027-05", "housing_fund", pay_date="2027-06-10")
    hf_entry = await get_entry(hf["hf_paid_voucher_id"], user_id="owner-1", company_id="c-sal-split-bf")
    assert Decimal(str(hf_entry["amount"])) == Decimal("800.00")


@pytest.mark.asyncio
async def test_batch_tax_preview_after_social_security_backfill():
    """回填社保后一键批量试算全员个税，专项扣除按新社保+公积金精确扣除。"""
    await _setup_company("c-sal-batch-iit")

    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, update_salary_records,
    )
    from services.salary_iit_service import preview_sheet_individual_income_tax

    # 员工 A：工资 12000，基数 5000；员工 B：工资 8000，基数 5000
    emp_a = await create_employee("c-sal-batch-iit", "员工A", "行政部", 12000, 5000, hire_date="2027-01-01")
    emp_b = await create_employee("c-sal-batch-iit", "员工B", "行政部", 8000, 5000, hire_date="2027-01-01")

    sheet = await get_or_create_salary_sheet("c-sal-batch-iit", "2027-01")
    recs = sheet["records"]
    rec_a = next(r for r in recs if r["employee_id"] == emp_a["id"])
    rec_b = next(r for r in recs if r["employee_id"] == emp_b["id"])

    # 回填员工 A 个人社保 525 + 公积金 400 = 925；员工 B 个人社保 525 + 公积金 400 = 925
    # 批量试算
    batch_res = await preview_sheet_individual_income_tax(
        "c-sal-batch-iit",
        "2027-01",
        [
            {"employeeId": emp_a["id"], "gross": Decimal("12000.00"), "withheld": Decimal("925.00")},
            {"employeeId": emp_b["id"], "gross": Decimal("8000.00"), "withheld": Decimal("925.00")},
        ],
    )
    items = {item["employeeId"]: item for item in batch_res["items"]}

    # A: 12000 - 5000 - 925 = 6075 -> 3% = 182.25
    assert Decimal(items[emp_a["id"]]["taxableIncome"]) == Decimal("6075.00")
    assert Decimal(items[emp_a["id"]]["taxDue"]) == Decimal("182.25")

    # B: 8000 - 5000 - 925 = 2075 -> 3% = 62.25
    assert Decimal(items[emp_b["id"]]["taxableIncome"]) == Decimal("2075.00")
    assert Decimal(items[emp_b["id"]]["taxDue"]) == Decimal("62.25")

    # 合计 182.25 + 62.25 = 244.50
    assert Decimal(batch_res["totalTaxDue"]) == Decimal("244.50")


@pytest.mark.asyncio
async def test_parse_tax_rates_screenshot(monkeypatch):
    """验证多模态视觉模型解析社保核定表截图，提取单位与个人费率。"""
    from services.salary_service import parse_tax_rates_screenshot

    def mock_chat_vision_json(prompt, image_url, **kwargs):
        assert kwargs.get("model") == "qwen-vl-ocr"
        return {
            "province": "浙江省",
            "items": [
                {"name": "企业职工基本养老保险费", "payer": "company", "rate": 0.16},
                {"name": "企业职工基本养老保险费", "payer": "personal", "rate": 0.08},
                {"name": "失业保险费", "payer": "company", "rate": 0.005},
                {"name": "失业保险费", "payer": "personal", "rate": 0.005},
                {"name": "基本医疗保险费", "payer": "company", "rate": 0.095},
                {"name": "基本医疗保险费", "payer": "personal", "rate": 0.02},
                {"name": "工伤保险费", "payer": "company", "rate": 0.002},
            ],
            "socialCompany": 0.262,
            "socialPersonal": 0.105,
            "housingCompany": 0.0,
            "housingPersonal": 0.0,
            "summary": "单位社保 26.2%，个人社保 10.5%",
        }

    monkeypatch.setattr("services.llm.chat_vision_json", mock_chat_vision_json)

    res = await parse_tax_rates_screenshot("data:image/png;base64,AAAA")
    assert res["success"] is True
    assert res["province"] == "浙江省"
    assert res["socialCompany"] == 0.262
    assert res["socialPersonal"] == 0.105
    assert len(res["items"]) == 7


@pytest.mark.asyncio
async def test_company_region_auto_matches_payroll_rates():
    """企业注册地自动匹配费率：浙江企业预设 26.2%/10.5%，江苏企业预设 24.2%/10.5%。"""
    import json
    from services.salary_service import (
        default_payroll_rates, get_payroll_rates, match_region_from_company,
    )
    from services.database import get_conn

    # 1. 区域关键字匹配逻辑
    assert match_region_from_company({"name": "杭州芥子星辰智能科技有限公司"}) == "浙江"
    assert match_region_from_company({"name": "浙江某某进出口贸易有限公司"}) == "浙江"
    assert match_region_from_company({"name": "南京某某软件有限公司"}) == "江苏"
    assert match_region_from_company({"name": "深圳某某网络有限公司"}) == "深圳"
    assert match_region_from_company({"name": "北京某某传媒有限公司"}) == "北京"
    assert match_region_from_company({"name": "无地域普通公司"}) is None

    # 2. 默认费率字典计算
    zj_rates = default_payroll_rates({"name": "杭州南山科技有限公司"})
    assert zj_rates["socialCompany"] == Decimal("0.262")
    assert zj_rates["socialPersonal"] == Decimal("0.105")

    js_rates = default_payroll_rates({"name": "苏州工业园区科技有限公司"})
    assert js_rates["socialCompany"] == Decimal("0.242")
    assert js_rates["socialPersonal"] == Decimal("0.105")

    # 3. 真实 DB 账套查询自动带出
    await _setup_company("c-zj-company")
    conn = await get_conn()
    try:
        await conn.execute(
            "UPDATE auth_companies SET payload = ? WHERE company_id = ?",
            (json.dumps({"name": "杭州某某科技有限公司"}), "c-zj-company"),
        )
        await conn.commit()
    finally:
        await conn.close()

    res = await get_payroll_rates("c-zj-company")
    assert res["region"] == "浙江"
    assert Decimal(res["rates"]["socialCompany"]) == Decimal("0.262")
    assert Decimal(res["rates"]["socialPersonal"]) == Decimal("0.105")
    assert res["isCustomized"] is False


@pytest.mark.asyncio
async def test_salary_sheet_auto_carries_forward_previous_month():
    """跨月开表自动延续固定工资与回填五险一金，一次性奖金/扣款清零并重算累计个税。"""
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        accrue_salary_sheet,
        get_or_create_salary_sheet,
        update_salary_records,
        create_employee,
    )
    company_id = "c-carry-forward-test"
    await _setup_company(company_id)

    # 1. 创建员工 A（老员工，2026-10 入职）与 员工 B（新员工，2026-11 入职）
    emp_a = await create_employee(
        company_id=company_id,
        name="张三",
        department="管理部",
        base_salary=Decimal("10000.00"),
        social_security_base=Decimal("5000.00"),
        hire_date="2026-10-01",
    )
    emp_b = await create_employee(
        company_id=company_id,
        name="李四",
        department="管理部",
        base_salary=Decimal("8000.00"),
        social_security_base=Decimal("4000.00"),
        hire_date="2026-11-01",
    )

    # 2. 2026-10 月工资表：此时只有张三在职，且无上月表
    sheet_10 = await get_or_create_salary_sheet(company_id, "2026-10")
    assert sheet_10["carryForward"]["hasPreviousSheet"] is False
    assert len(sheet_10["records"]) == 1
    rec_a_10 = sheet_10["records"][0]
    assert rec_a_10["employee_name"] == "张三"

    # 3. 用户在 10 月录入/回填税局实缴社保、固定津贴与一次性奖金/扣款，并确认计提
    update_data = [{
        "id": rec_a_10["id"],
        "base_salary": "10000.00",
        "bonus": "1000.00",
        "allowance": "500.00",
        "deduction": "200.00",
        "personal_social_insurance": "800.00",
        "personal_housing_fund": "600.00",
        "company_social_insurance": "2000.00",
        "company_housing_fund": "600.00",
        "personal_tax": "150.00",
    }]
    updated_10 = await update_salary_records(company_id, "2026-10", update_data)
    rec_a_10_up = updated_10["records"][0]
    assert rec_a_10_up["net_salary"] == "9750.00"  # 10000 + 1000 + 500 - 200 - 1400 - 150
    assert rec_a_10_up["total_cost"] == "13900.00"  # 11300 + 2600
    await replace_salary_allocations(company_id, "owner-1", "2026-10", [])
    await accrue_salary_sheet(company_id, "owner-1", "2026-10")

    # 4. 2026-11 月自动创建工资表：
    #    - 张三沿用花名册基本工资(10000)、固定津贴(500)、回填五险一金(800/600 & 2000/600)
    #    - 上月一次性奖金(1000)与考勤扣款(200)自动清零为 0.00
    #    - 累计预扣法重算个税：(11300+10500 - 10000 - 2800)*3% - 150 = 120.00
    sheet_11 = await get_or_create_salary_sheet(company_id, "2026-11")
    assert len(sheet_11["records"]) == 2
    rec_a_11 = next(r for r in sheet_11["records"] if r["employee_id"] == emp_a["id"])
    rec_b_11 = next(r for r in sheet_11["records"] if r["employee_id"] == emp_b["id"])

    assert rec_a_11["base_salary"] == "10000.00"
    assert rec_a_11["bonus"] == "0.00"
    assert rec_a_11["allowance"] == "500.00"
    assert rec_a_11["deduction"] == "0.00"
    assert rec_a_11["personal_social_insurance"] == "800.00"
    assert rec_a_11["personal_housing_fund"] == "600.00"
    assert rec_a_11["company_social_insurance"] == "2000.00"
    assert rec_a_11["company_housing_fund"] == "600.00"
    assert rec_a_11["personal_tax"] == "120.00"
    assert rec_a_11["net_salary"] == "8980.00"  # 10500 - 1400 - 120
    assert rec_a_11["total_cost"] == "13100.00"  # 10500 + 2600

    cf = sheet_11["carryForward"]
    assert cf["hasPreviousSheet"] is True
    assert cf["sourceMonth"] == "2026-10"
    assert cf["isIdenticalToPrevious"] is False
    assert cf["clearedBonusTotal"] == "1000.00"
    assert cf["clearedDeductionTotal"] == "200.00"
    assert any("一次性奖金" in reason for reason in cf["changeReasons"])
    assert any("考勤扣款" in reason for reason in cf["changeReasons"])
    assert any("新增员工：李四" in reason for reason in cf["changeReasons"])

    # 李四（新员工）：走初始配置
    assert rec_b_11["base_salary"] == "8000.00"
    assert rec_b_11["bonus"] == "0.00"
    assert rec_b_11["personal_tax"] == "0.00"

    # 5. 留有修改入口：当月微调张三的奖金与税额
    up_11 = [{
        "id": rec_a_11["id"],
        "base_salary": "10000.00",
        "bonus": "2000.00",
        "allowance": "500.00",
        "deduction": "0.00",
        "personal_social_insurance": "800.00",
        "personal_housing_fund": "600.00",
        "company_social_insurance": "2000.00",
        "company_housing_fund": "600.00",
        "personal_tax": "180.00",
    }]
    res_11 = await update_salary_records(company_id, "2026-11", up_11)
    rec_a_11_mod = next(r for r in res_11["records"] if r["employee_id"] == emp_a["id"])
    assert rec_a_11_mod["bonus"] == "2000.00"
    assert rec_a_11_mod["net_salary"] == "10920.00"  # 12500 - 1400 - 180


@pytest.mark.asyncio
async def test_employee_independent_housing_fund_base_and_ss_clamp_isolation(monkeypatch):
    """员工独立公积金基数：None 跟随社保、0 停缴、独立正数不被社保下限错拉高。"""
    import config
    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, save_payroll_rates,
    )

    monkeypatch.setattr(config, "SOCIAL_SECURITY_BASE_MIN", 7460)
    await _setup_company("c-sal-hf-base")
    await save_payroll_rates("c-sal-hf-base", {
        "socialPersonal": "0.10",
        "socialCompany": "0.20",
        "housingPersonal": "0.08",
        "housingCompany": "0.08",
    })

    emp_follow = await create_employee("c-sal-hf-base", "甲跟随", "技术部", 10000, 5000, housing_fund_base=None)
    emp_lower = await create_employee("c-sal-hf-base", "乙独立低基数", "技术部", 10000, 7460, housing_fund_base=2690)
    emp_zero = await create_employee("c-sal-hf-base", "丙不缴公积金", "技术部", 10000, 7460, housing_fund_base=0)

    assert emp_follow["housing_fund_base"] is None
    assert Decimal(emp_lower["housing_fund_base"]) == Decimal("2690.00")
    assert Decimal(emp_zero["housing_fund_base"]) == Decimal("0.00")

    sheet = await get_or_create_salary_sheet("c-sal-hf-base", "2027-07")
    by_emp = {r["employee_id"]: r for r in sheet["records"]}

    # 甲：未设置独立基数，社保被兜底到 7460，公积金同样跟随 7460 -> 7460*8%=596.80
    assert Decimal(by_emp[emp_follow["id"]]["personal_social_insurance"]) == Decimal("746.00")
    assert Decimal(by_emp[emp_follow["id"]]["personal_housing_fund"]) == Decimal("596.80")

    # 乙：显式设公积金基数 2690，不被社保下限 7460 拉高 -> 2690*8%=215.20
    assert Decimal(by_emp[emp_lower["id"]]["personal_social_insurance"]) == Decimal("746.00")
    assert Decimal(by_emp[emp_lower["id"]]["personal_housing_fund"]) == Decimal("215.20")
    assert Decimal(by_emp[emp_lower["id"]]["company_housing_fund"]) == Decimal("215.20")

    # 丙：公积金基数 0 -> 社保照常算 746.00，公积金严格为 0.00
    assert Decimal(by_emp[emp_zero["id"]]["personal_social_insurance"]) == Decimal("746.00")
    assert Decimal(by_emp[emp_zero["id"]]["personal_housing_fund"]) == Decimal("0.00")
    assert Decimal(by_emp[emp_zero["id"]]["company_housing_fund"]) == Decimal("0.00")


@pytest.mark.asyncio
async def test_asymmetric_housing_fund_rates_and_sheet_calculation():
    """单位与个人公积金比例不同（如单位 8%、个人 10%）时各自精确算入工资与待缴额。"""
    from services.salary_service import (
        create_employee, get_or_create_salary_sheet, recalculate_draft_withholdings, save_payroll_rates,
    )

    await _setup_company("c-sal-hf-asym")
    await save_payroll_rates("c-sal-hf-asym", {
        "socialPersonal": "0.10",
        "socialCompany": "0.20",
        "housingPersonal": "0.10",
        "housingCompany": "0.08",
    })
    await create_employee("c-sal-hf-asym", "丁员工", "运营部", 12000, 6000, housing_fund_base=5000)

    sheet = await get_or_create_salary_sheet("c-sal-hf-asym", "2027-08")
    rec = sheet["records"][0]
    # 个人：6000*10%(600) + 5000*10%(500) = 1100；单位：6000*20%(1200) + 5000*8%(400) = 1600
    assert Decimal(rec["personal_housing_fund"]) == Decimal("500.00")
    assert Decimal(rec["company_housing_fund"]) == Decimal("400.00")
    assert Decimal(rec["personal_social_security"]) == Decimal("1100.00")
    assert Decimal(rec["company_social_security"]) == Decimal("1600.00")
    assert Decimal(sheet["housingFundDue"]) == Decimal("900.00")

    # 改成单位 7%、个人 9% 重算草稿
    await save_payroll_rates("c-sal-hf-asym", {"housingCompany": "0.07", "housingPersonal": "0.09"})
    recalculated = await recalculate_draft_withholdings("c-sal-hf-asym", "2027-08")
    rec2 = recalculated["records"][0]
    assert Decimal(rec2["personal_housing_fund"]) == Decimal("450.00")
    assert Decimal(rec2["company_housing_fund"]) == Decimal("350.00")
    assert Decimal(recalculated["housingFundDue"]) == Decimal("800.00")


@pytest.mark.asyncio
async def test_update_single_employee_housing_fund_base_recalculates_only_target_and_skips_accrued():
    """编辑单个员工并联动重算草稿：只重算该员工、汇总全表公积金，且对已计提月份安全无害。"""
    from deps.auth import AuthContext
    from routers.salary import EmployeeUpdate, update_employee
    from services.salary_service import (
        accrue_salary_sheet, create_employee, get_or_create_salary_sheet,
        save_payroll_rates, update_salary_records,
    )

    await _setup_company("c-sal-single-recalc")
    await save_payroll_rates("c-sal-single-recalc", {
        "socialPersonal": "0.10",
        "socialCompany": "0.20",
        "housingPersonal": "0.10",
        "housingCompany": "0.10",
    })
    emp1 = await create_employee("c-sal-single-recalc", "员工一", "研发部", 10000, 5000)
    emp2 = await create_employee("c-sal-single-recalc", "员工二", "行政部", 8000, 4000)
    sheet = await get_or_create_salary_sheet("c-sal-single-recalc", "2027-09")

    rec2 = next(r for r in sheet["records"] if r["employee_id"] == emp2["id"])
    # 先手工修改员工二当月奖金与实缴公积金，确保局部重算不会覆盖无关员工
    await update_salary_records("c-sal-single-recalc", "2027-09", [{
        "id": rec2["id"],
        "base_salary": "8000.00",
        "bonus": "1500.00",
        "allowance": "0.00",
        "deduction": "0.00",
        "personal_social_insurance": "400.00",
        "personal_housing_fund": "300.00",
        "company_social_insurance": "800.00",
        "company_housing_fund": "300.00",
        "personal_tax": "0.00",
    }])

    ctx = AuthContext(user_id="owner-1", company_id="c-sal-single-recalc")
    updated_emp1 = await update_employee(
        emp1["id"],
        EmployeeUpdate(housing_fund_base=Decimal("2500.00")),
        month="2027-09",
        recalculate=True,
        ctx=ctx,
    )
    assert Decimal(updated_emp1["housing_fund_base"]) == Decimal("2500.00")

    sheet_after = await get_or_create_salary_sheet("c-sal-single-recalc", "2027-09")
    r1_after = next(r for r in sheet_after["records"] if r["employee_id"] == emp1["id"])
    r2_after = next(r for r in sheet_after["records"] if r["employee_id"] == emp2["id"])
    assert Decimal(r1_after["personal_housing_fund"]) == Decimal("250.00")
    assert Decimal(r1_after["company_housing_fund"]) == Decimal("250.00")
    assert Decimal(r2_after["bonus"]) == Decimal("1500.00")
    assert Decimal(r2_after["personal_housing_fund"]) == Decimal("300.00")
    assert Decimal(sheet_after["housingFundDue"]) == Decimal("1100.00")  # (250+250) + (300+300)

    # 重置回 None（同社保基数 5000）
    reset_emp1 = await update_employee(
        emp1["id"],
        EmployeeUpdate(housing_fund_base=None),
        month="2027-09",
        recalculate=True,
        ctx=ctx,
    )
    assert reset_emp1["housing_fund_base"] is None
    sheet_reset = await get_or_create_salary_sheet("c-sal-single-recalc", "2027-09")
    r1_reset = next(r for r in sheet_reset["records"] if r["employee_id"] == emp1["id"])
    assert Decimal(r1_reset["personal_housing_fund"]) == Decimal("500.00")

    # 计提过账后，再次改档案带 recalculate=True 应忽略而非报错，且不动已计提工资表
    from datetime import date
    from db.session import session_scope
    from services.rd_allocation_service import replace_salary_allocations
    from services.rd_service import create_project

    async with session_scope() as session:
        proj = await create_project(
            session, "c-sal-single-recalc", "owner-1",
            {
                "code": "RD-HF-1",
                "name": "研发专项",
                "type": "self_started",
                "status": "active",
                "startDate": date(2027, 1, 1),
                "endDate": date(2027, 12, 31),
            },
            request_id="t-hf-single",
        )
    await replace_salary_allocations("c-sal-single-recalc", "owner-1", "2027-09", [{
        "employeeId": emp1["id"], "projectId": proj["id"],
        "allocationRate": 1, "allocationBasis": "工时100%",
    }])
    await accrue_salary_sheet("c-sal-single-recalc", "owner-1", "2027-09")
    await update_employee(
        emp1["id"],
        EmployeeUpdate(housing_fund_base=Decimal("0")),
        month="2027-09",
        recalculate=True,
        ctx=ctx,
    )
    sheet_accrued = await get_or_create_salary_sheet("c-sal-single-recalc", "2027-09")
    r1_accrued = next(r for r in sheet_accrued["records"] if r["employee_id"] == emp1["id"])
    assert Decimal(r1_accrued["personal_housing_fund"]) == Decimal("500.00")


@pytest.mark.asyncio
async def test_save_payroll_rates_partial_update_preserves_region_defaults():
    """部分更新费率（仅传公积金）时保留注册地省市预设社保费率，不退回系统默认 25%。"""
    from db.models import Company
    from db.session import session_scope
    from sqlalchemy import select
    from services.salary_service import save_payroll_rates

    await _setup_company("c-sal-hz-partial")
    async with session_scope() as session:
        comp = (await session.execute(select(Company).where(Company.company_id == "c-sal-hz-partial"))).scalar_one()
        comp.payload = {**(comp.payload or {}), "name": "杭州星辰科技有限公司"}

    saved = await save_payroll_rates("c-sal-hz-partial", {"housingPersonal": "0.12", "housingCompany": "0.12"})
    assert Decimal(saved["rates"]["socialCompany"]) == Decimal("0.262")
    assert Decimal(saved["rates"]["socialPersonal"]) == Decimal("0.105")
    assert Decimal(saved["rates"]["housingPersonal"]) == Decimal("0.12")
    assert Decimal(saved["rates"]["housingCompany"]) == Decimal("0.12")


@pytest.mark.asyncio
async def test_update_employee_coerces_bool_flags_to_int_for_postgres(monkeypatch):
    """编辑员工时将 is_owner/is_active 布尔值强转为精确 int（非 bool），防止 PostgreSQL asyncpg 报类型错。"""
    import services.salary_service as salary_service
    from deps.auth import AuthContext
    from routers.salary import EmployeeUpdate, update_employee as route_update_employee

    await _setup_company("c-sal-pg-flags")
    emp = await salary_service.create_employee(
        "c-sal-pg-flags", "杨云飞", "行政部", 5000, 5122, housing_fund_base=5000
    )

    captured_updates: list[tuple[str, list]] = []
    real_get_conn = salary_service.get_conn

    class _ConnSpy:
        def __init__(self, inner):
            self._inner = inner

        async def execute(self, sql, params=None):
            if sql.startswith("UPDATE employees SET "):
                captured_updates.append((sql, list(params or [])))
            if params is not None:
                return await self._inner.execute(sql, params)
            return await self._inner.execute(sql)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    async def spy_get_conn():
        return _ConnSpy(await real_get_conn())

    monkeypatch.setattr(salary_service, "get_conn", spy_get_conn)
    ctx = AuthContext(user_id="owner-1", company_id="c-sal-pg-flags")

    # 1. 路由层传 EmployeeUpdate(is_owner=False, ...) 模拟前端编辑保存
    res_false = await route_update_employee(
        emp["id"],
        EmployeeUpdate(
            name="杨云飞",
            department="行政部",
            base_salary=Decimal("5000.00"),
            social_security_base=Decimal("5122.00"),
            housing_fund_base=Decimal("5000.00"),
            is_owner=False,
        ),
        ctx=ctx,
    )
    assert res_false["is_owner"] == 0
    assert Decimal(res_false["housing_fund_base"]) == Decimal("5000.00")

    # 2. 直接传 bool flags 给 service，验证 is_active=True / is_owner=True 同样强转为精确 int
    res_true = await salary_service.update_employee(
        "c-sal-pg-flags",
        emp["id"],
        {"is_owner": True, "is_active": True},
    )
    assert res_true is not None and res_true["is_owner"] == 1

    # 3. 显式传 is_owner=None 时忽略该非空标记列，不触发 NOT NULL 违约
    res_none = await salary_service.update_employee(
        "c-sal-pg-flags",
        emp["id"],
        {"is_owner": None, "name": "杨云飞"},
    )
    assert res_none is not None and res_none["is_owner"] == 1

    assert len(captured_updates) == 3
    sql0, params0 = captured_updates[0]
    assert "is_owner = ?" in sql0
    assert params0[5] == 0 and type(params0[5]) is int

    sql1, params1 = captured_updates[1]
    assert "is_active = ?" in sql1 and "is_owner = ?" in sql1
    assert params1[0] == 1 and type(params1[0]) is int
    assert params1[1] == 1 and type(params1[1]) is int

    sql2, _ = captured_updates[2]
    assert "is_owner = ?" not in sql2


@pytest.mark.asyncio
async def test_unaccrue_salary_sheet_reverses_accrual_and_allows_reaccrue():
    """已过账工资计提可合规红字冲销并退回草稿，幂等且改完可重新计提。"""
    cid = "c-sal-unaccrue-ok"
    await _setup_company(cid)

    from sqlalchemy import select
    from db.models import JournalEntry
    from db.session import session_scope
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        accrue_salary_sheet, create_employee, get_or_create_salary_sheet,
        unaccrue_salary_sheet, update_salary_records,
    )

    await create_employee(cid, "周十", "行政部", 10000, 6000)
    sheet = await get_or_create_salary_sheet(cid, "2027-10")
    await replace_salary_allocations(cid, "owner-1", "2027-10", [])
    await update_salary_records(cid, "2027-10", [
        {**sheet["records"][0], "personal_tax": "500.00"},
    ])
    accrued = await accrue_salary_sheet(cid, "owner-1", "2027-10")
    assert accrued["status"] == "accrued"
    first_voucher_id = accrued["accrual_voucher_id"]

    # 撤销确认：生成红字冲销凭证，计提分录全部归零，退回草稿且 generation 递增
    unaccrued = await unaccrue_salary_sheet(
        cid, "owner-1", "2027-10", reason="漏算奖金重算", request_id="req-unaccrue-1",
    )
    assert unaccrued["status"] == "draft"
    assert unaccrued["accrual_voucher_id"] is None
    assert unaccrued["alreadyDraft"] is False
    assert unaccrued["reversalVoucherId"]
    assert int(unaccrued["accrual_generation"]) == 1

    async with session_scope() as session:
        orig = (await session.execute(select(JournalEntry).where(JournalEntry.id == first_voucher_id))).scalar_one()
        rev = (await session.execute(select(JournalEntry).where(JournalEntry.id == unaccrued["reversalVoucherId"]))).scalar_one()
        assert orig.status == "reversed"
        assert rev.status == "posted"
        assert rev.reversal_of_id == first_voucher_id

    for code in ("221101", "221102", "221104", "2221012"):
        assert await _account_net_balance(cid, code) == 0, f"{code} 撤销确认后未归零"

    # 重复调用幂等，不生成第二张冲销凭证
    idem = await unaccrue_salary_sheet(
        cid, "owner-1", "2027-10", reason="重复点击", request_id="req-unaccrue-2",
    )
    assert idem["status"] == "draft"
    assert idem["alreadyDraft"] is True
    assert idem["reversalVoucherId"] is None

    # 修改奖金后重计提成功
    await update_salary_records(cid, "2027-10", [
        {**unaccrued["records"][0], "bonus": "2000.00"},
    ])
    re_accrued = await accrue_salary_sheet(cid, "owner-1", "2027-10")
    assert re_accrued["status"] == "accrued"
    assert re_accrued["accrual_voucher_id"] != first_voucher_id
    assert await _account_net_balance(cid, "221101") != 0


@pytest.mark.asyncio
async def test_unaccrue_salary_sheet_blocks_when_downstream_payment_active():
    """存在已过账/待复核的发放或税费缴纳凭证时，拦截撤销确认。"""
    cid = "c-sal-unaccrue-block"
    await _setup_company(cid)

    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        accrue_salary_sheet, create_employee, get_or_create_salary_sheet,
        pay_salary_sheet, unaccrue_salary_sheet,
    )

    await create_employee(cid, "吴十一", "行政部", 8000, 5000)
    await get_or_create_salary_sheet(cid, "2027-10")
    await replace_salary_allocations(cid, "owner-1", "2027-10", [])
    accrued = await accrue_salary_sheet(cid, "owner-1", "2027-10")
    await pay_salary_sheet(cid, "owner-1", "2027-10", pay_date="2027-11-05")

    with pytest.raises(ValueError, match="工资发放"):
        await unaccrue_salary_sheet(cid, "owner-1", "2027-10", reason="误操作撤销")

    still = await get_or_create_salary_sheet(cid, "2027-10")
    assert still["accrual_voucher_id"] == accrued["accrual_voucher_id"]
    assert still["status"] == "paid"


@pytest.mark.asyncio
async def test_unaccrue_salary_sheet_blocks_when_period_closed():
    """已关账月份禁止绕过期间校验静默冲销到次月，必须抛出 PERIOD_CLOSED。"""
    cid = "c-sal-unaccrue-closed"
    await _setup_company(cid)

    from datetime import date
    from sqlalchemy import select
    from core.errors import PERIOD_CLOSED, AccountingError
    from db.models import AccountingPeriod
    from db.session import session_scope
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        accrue_salary_sheet, create_employee, get_or_create_salary_sheet,
        unaccrue_salary_sheet,
    )

    await create_employee(cid, "郑十二", "行政部", 8000, 5000)
    await get_or_create_salary_sheet(cid, "2027-10")
    await replace_salary_allocations(cid, "owner-1", "2027-10", [])
    await accrue_salary_sheet(cid, "owner-1", "2027-10")

    async with session_scope() as session:
        period = (
            await session.execute(
                select(AccountingPeriod).where(
                    AccountingPeriod.company_id == cid,
                    AccountingPeriod.period_start == date(2027, 10, 1),
                )
            )
        ).scalar_one()
        period.status = "closed"

    with pytest.raises(AccountingError) as exc_info:
        await unaccrue_salary_sheet(cid, "owner-1", "2027-10", reason="关账期间尝试撤销")
    assert exc_info.value.code == PERIOD_CLOSED


@pytest.mark.asyncio
async def test_unaccrue_salary_sheet_deletes_pending_review_accrual_voucher():
    """复核模式下待复核计提凭证撤回时直接删除草稿/待审凭证并退回草稿。"""
    cid = "c-sal-unaccrue-pr"
    await _setup_company(cid)

    from sqlalchemy import select
    from db.models import AuthUser, Company, CompanyMember, JournalEntry
    from db.session import session_scope
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        accrue_salary_sheet, create_employee, get_or_create_salary_sheet,
        unaccrue_salary_sheet,
    )

    now = "2027-01-01T00:00:00+00:00"
    async with session_scope() as session:
        company = (await session.execute(select(Company).where(Company.company_id == cid))).scalar_one()
        company.posting_policy = "maker_checker"
        if await session.get(AuthUser, "reviewer-1") is None:
            session.add(AuthUser(
                user_id="reviewer-1", openid="", phone="", phone_verified_at="",
                wechat_openid_bound_at="", current_company_id=cid,
                nickname="复核员", avatar="", created_at=now, last_login_at=now, updated_at=now,
            ))
        session.add(CompanyMember(company_id=cid, user_id="reviewer-1", role="accountant", status="active"))

    await create_employee(cid, "王十四", "行政部", 9000, 6000)
    await get_or_create_salary_sheet(cid, "2027-10")
    await replace_salary_allocations(cid, "owner-1", "2027-10", [])
    accrued = await accrue_salary_sheet(cid, "owner-1", "2027-10")
    assert accrued["status"] == "pending_review"
    pending_id = accrued["accrual_voucher_id"]

    res = await unaccrue_salary_sheet(
        cid, "owner-1", "2027-10", reason="待复核撤回重改", request_id="req-unaccrue-pr",
    )
    assert res["status"] == "draft"
    assert res["reversalVoucherId"] is None
    assert res["alreadyDraft"] is False

    async with session_scope() as session:
        row = (await session.execute(select(JournalEntry).where(JournalEntry.id == pending_id))).scalar_one_or_none()
        assert row is None

    re_accrued = await accrue_salary_sheet(cid, "owner-1", "2027-10")
    assert re_accrued["status"] == "pending_review"
    assert re_accrued["accrual_voucher_id"] != pending_id


@pytest.mark.asyncio
async def test_unaccrue_salary_sheet_cascade_reverses_payments_and_unmatches_bank_statement():
    """已发薪完结态启用 include_payments=True：原子红冲全部工资与税费凭证、解绑银行流水对账，退回草稿可重发。"""
    cid = "c-sal-unaccrue-cascade"
    await _setup_company(cid)

    from services.bank_service import _ensure_schema as _ensure_bank_schema
    from services.database import get_conn
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        _ensure_db_ready, accrue_salary_sheet, apply_salary_payment_from_statement,
        create_employee, get_or_create_salary_sheet, pay_payroll_liability,
        pay_salary_sheet, unaccrue_salary_sheet, update_salary_records,
    )

    await _ensure_db_ready()
    await _ensure_bank_schema()

    await create_employee(cid, "陈十五", "行政部", 12000, 6000)
    sheet = await get_or_create_salary_sheet(cid, "2027-10")
    await replace_salary_allocations(cid, "owner-1", "2027-10", [])
    sheet = await update_salary_records(cid, "2027-10", [
        {**sheet["records"][0], "personal_tax": "300.00"},
    ])
    accrued = await accrue_salary_sheet(cid, "owner-1", "2027-10")
    net_salary = accrued["records"][0]["net_salary"]

    # 插入银行流水并完成发薪自动核销，再依次缴纳社保、公积金、个税
    stmt_id = "stmt-sal-cascade-1"
    conn = await get_conn()
    try:
        await conn.execute(
            "INSERT INTO bank_statements (id, company_id, transaction_date, description, amount, balance, statement_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (stmt_id, cid, "2027-11-10", "代发10月工资", f"-{net_salary}", stmt_id, "2027-11-10T00:00:00+00:00"),
        )
        await conn.commit()
    finally:
        await conn.close()

    paid_res = await apply_salary_payment_from_statement(
        cid, "owner-1", "2027-10", stmt_id, request_id="req-sal-pay-cascade",
    )
    assert paid_res["linked"] is True
    await pay_payroll_liability(cid, "owner-1", "2027-10", "social_security", pay_date="2027-11-12")
    await pay_payroll_liability(cid, "owner-1", "2027-10", "housing_fund", pay_date="2027-11-12")
    settled = await pay_payroll_liability(cid, "owner-1", "2027-10", "tax", pay_date="2027-11-12")
    assert settled["status"] == "paid"
    assert settled["ssPaymentStatus"] == "posted"
    assert settled["hfPaymentStatus"] == "posted"
    assert settled["taxPaymentStatus"] == "posted"

    # 连带冲销重算
    unaccrued = await unaccrue_salary_sheet(
        cid, "owner-1", "2027-10",
        reason="发薪完结后发现奖金漏发",
        request_id="req-unaccrue-cascade",
        include_payments=True,
    )
    assert unaccrued["status"] == "draft"
    assert unaccrued["alreadyDraft"] is False
    assert unaccrued["accrual_voucher_id"] is None
    assert unaccrued["payment_voucher_id"] is None
    assert unaccrued["ss_paid_voucher_id"] is None
    assert unaccrued["hf_paid_voucher_id"] is None
    assert unaccrued["tax_paid_voucher_id"] is None
    assert len(unaccrued["reversedVoucherIds"]) == 5
    assert unaccrued["unmatchedStatementCount"] == 1

    # 银行流水对账必须彻底解绑
    conn = await get_conn()
    try:
        stmt_row = await (await conn.execute(
            "SELECT matched_entry_id FROM bank_statements WHERE company_id = ? AND id = ?",
            (cid, stmt_id),
        )).fetchone()
        match_rows = await (await conn.execute(
            "SELECT entry_id FROM bank_statement_matches WHERE company_id = ? AND statement_id = ?",
            (cid, stmt_id),
        )).fetchall()
        assert stmt_row is not None and stmt_row["matched_entry_id"] is None
        assert len(match_rows) == 0
    finally:
        await conn.close()

    # 所有往来挂账与银行科目净额归零
    for code in ("221101", "221102", "221105", "221104", "2221012", "1002"):
        assert await _account_net_balance(cid, code) == 0, f"{code} 连带冲销后未归零"

    # 改奖金后重新计提并发薪缴纳，不得撞来源唯一性冲突
    await update_salary_records(cid, "2027-10", [
        {**unaccrued["records"][0], "bonus": "1000.00"},
    ])
    re_accrued = await accrue_salary_sheet(cid, "owner-1", "2027-10")
    assert re_accrued["status"] == "accrued"
    re_paid = await pay_salary_sheet(cid, "owner-1", "2027-10", pay_date="2027-11-15")
    assert re_paid["status"] == "paid"


@pytest.mark.asyncio
async def test_unaccrue_salary_sheet_cascade_rolls_back_atomically_when_payment_period_closed():
    """次月发薪期间已关账而计提月未关账时，连带冲销必须整笔原子回滚，绝不半截冲销。"""
    cid = "c-sal-unaccrue-atomic"
    await _setup_company(cid)

    from datetime import date
    from sqlalchemy import select
    from core.errors import PERIOD_CLOSED, AccountingError
    from db.models import AccountingPeriod, JournalEntry
    from db.session import session_scope
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        accrue_salary_sheet, create_employee, get_or_create_salary_sheet,
        pay_salary_sheet, unaccrue_salary_sheet,
    )

    await create_employee(cid, "林十六", "行政部", 8000, 5000)
    await get_or_create_salary_sheet(cid, "2027-10")
    await replace_salary_allocations(cid, "owner-1", "2027-10", [])
    accrued = await accrue_salary_sheet(cid, "owner-1", "2027-10")
    paid = await pay_salary_sheet(cid, "owner-1", "2027-10", pay_date="2027-11-08")

    # 仅关闭 2027-11（发薪月），2027-10（计提月）保持未关账
    async with session_scope() as session:
        nov_period = (
            await session.execute(
                select(AccountingPeriod).where(
                    AccountingPeriod.company_id == cid,
                    AccountingPeriod.period_start == date(2027, 11, 1),
                )
            )
        ).scalar_one()
        nov_period.status = "closed"

    with pytest.raises(AccountingError) as exc_info:
        await unaccrue_salary_sheet(
            cid, "owner-1", "2027-10",
            reason="跨月部分关账测试",
            request_id="req-unaccrue-atomic",
            include_payments=True,
        )
    assert exc_info.value.code == PERIOD_CLOSED

    # 原计提与发放凭证均须维持 posted，工资表保持 paid
    async with session_scope() as session:
        acc_entry = await session.get(JournalEntry, accrued["accrual_voucher_id"])
        pay_entry = await session.get(JournalEntry, paid["payment_voucher_id"])
        assert acc_entry is not None and acc_entry.status == "posted"
        assert pay_entry is not None and pay_entry.status == "posted"

    still = await get_or_create_salary_sheet(cid, "2027-10")
    assert still["status"] == "paid"
    assert still["accrual_voucher_id"] == accrued["accrual_voucher_id"]
    assert still["payment_voucher_id"] == paid["payment_voucher_id"]


@pytest.mark.asyncio
async def test_opening_month_salary_payment_settles_opening_221101_balance():
    """建账首月（2026-10-01 启用）发放 9 月工资：冲销 221101 期初贷方余额，不记费用、不超额冲销。"""
    cid = "c-sal-opening-settle"
    await _setup_company(cid)

    from fastapi import HTTPException
    from sqlalchemy import select
    from db.models import Company, JournalEntry, JournalLine, SourceTransaction
    from db.session import session_scope
    from deps.auth import AuthContext
    from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
    from services.accounting_core_service import issue_step_up_token
    from services.bank_service import ensure_bank_schema
    from services.database import get_conn
    from services.opening_balance_service import save_opening_balances

    async with session_scope() as session:
        company = (await session.execute(select(Company).where(Company.company_id == cid))).scalar_one()
        company.payload = {**(company.payload or {}), "startDate": "2026-10-01"}
        token = await issue_step_up_token(
            session, company_id=cid, user_id="owner-1", action="adjust_opening_balance"
        )
        await save_opening_balances(
            cid,
            2026,
            [
                {"account_code": "1002", "account_name": "银行存款", "debit_balance": "50000.00", "credit_balance": "0.00"},
                {"account_code": "221101", "account_name": "应付职工薪酬-工资", "debit_balance": "0.00", "credit_balance": "9250.00"},
                {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0.00", "credit_balance": "40750.00"},
            ],
            user_id="owner-1",
            step_up_token=token,
            request_id="req-open-sal-1",
            start_period="2026-10",
            session=session,
        )

    await ensure_bank_schema()
    conn = await get_conn()
    try:
        for sid in ("stmt-open-sal-1", "stmt-open-sal-2"):
            await conn.execute(
                "INSERT INTO bank_statements (id, company_id, transaction_date, description, amount, balance, statement_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                (
                    sid, cid, "2026-10-12", "APP移动代发 · 26年9月工资",
                    "-9250.00", sid, "2026-10-12T00:00:00+00:00",
                ),
            )
        await conn.commit()
    finally:
        await conn.close()

    # 1) 未显式传入 settleOpeningPayroll 时，仍保持 409 拦截并在 detail.openingPayroll 返回可冲销提示
    async with session_scope() as session:
        with pytest.raises(HTTPException) as exc_info:
            await confirm_suggestion(
                ConfirmSuggestionRequest(
                    statement_id="stmt-open-sal-1",
                    type="new",
                    new_entry={
                        "direction": "expense",
                        "amount": 9250,
                        "category": "工资发放",
                        "title": "APP移动代发 · 26年9月工资",
                    },
                ),
                ctx=AuthContext(user_id="owner-1", company_id=cid),
                request=None,
                session=session,
                idempotency_key=None,
            )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "SALARY_REQUIRES_PAYROLL"
    assert exc_info.value.detail["openingPayroll"]["canSettle"] is True
    assert exc_info.value.detail["openingPayroll"]["availableOpeningCredit"] == "9250.00"

    # 2) 显式携带 settleOpeningPayroll=True 后，冲销 221101（借 221101 / 贷 1002），禁止借记 5602001
    async with session_scope() as session:
        res = await confirm_suggestion(
            ConfirmSuggestionRequest(
                statement_id="stmt-open-sal-1",
                type="new",
                settleOpeningPayroll=True,
                new_entry={
                    "direction": "expense",
                    "amount": 9250,
                    "category": "工资发放",
                    "title": "APP移动代发 · 26年9月工资",
                },
            ),
            ctx=AuthContext(user_id="owner-1", company_id=cid),
            request=None,
            session=session,
            idempotency_key=None,
        )
    assert res["status"] == "created"
    assert res["settledOpeningPayroll"] is True
    entry_id = res["entryId"]

    async with session_scope() as session:
        entry = await session.get(JournalEntry, entry_id)
        assert entry is not None
        src = await session.get(SourceTransaction, entry.source_transaction_id)
        assert src is not None and src.source_type == "opening_payroll_settlement"
        lines = (
            await session.execute(select(JournalLine).where(JournalLine.journal_entry_id == entry_id))
        ).scalars().all()
        debits = [(l.account_code, Decimal(l.amount)) for l in lines if l.direction == "debit"]
        credits = [(l.account_code, Decimal(l.amount)) for l in lines if l.direction == "credit"]
        assert debits == [("221101", Decimal("9250.00"))]
        assert credits == [("1002", Decimal("9250.00"))]

    # 3) 同额第二笔再次携带 settleOpeningPayroll=True，因期初余额已被冲减至 0，必须被 409 拦截
    async with session_scope() as session:
        with pytest.raises(HTTPException) as exc_dup:
            await confirm_suggestion(
                ConfirmSuggestionRequest(
                    statement_id="stmt-open-sal-2",
                    type="new",
                    settleOpeningPayroll=True,
                    new_entry={
                        "direction": "expense",
                        "amount": 9250,
                        "category": "工资发放",
                        "title": "APP移动代发 · 26年9月工资",
                    },
                ),
                ctx=AuthContext(user_id="owner-1", company_id=cid),
                request=None,
                session=session,
                idempotency_key=None,
            )
    assert exc_dup.value.status_code == 409
    assert exc_dup.value.detail["openingPayroll"]["canSettle"] is False
    assert exc_dup.value.detail["openingPayroll"]["reason"] == "opening_balance_insufficient"


@pytest.mark.asyncio
async def test_opening_month_salary_payment_rejects_when_opening_221101_insufficient():
    """期初 221101 余额为 0 时，即使带 settleOpeningPayroll=True 也必须拦截，绝不产生负负债。"""
    cid = "c-sal-opening-insuff"
    await _setup_company(cid)

    from fastapi import HTTPException
    from sqlalchemy import select
    from db.models import Company
    from db.session import session_scope
    from deps.auth import AuthContext
    from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
    from services.bank_service import ensure_bank_schema
    from services.database import get_conn

    async with session_scope() as session:
        company = (await session.execute(select(Company).where(Company.company_id == cid))).scalar_one()
        company.payload = {**(company.payload or {}), "startDate": "2026-10-01"}

    await ensure_bank_schema()
    conn = await get_conn()
    try:
        await conn.execute(
            "INSERT INTO bank_statements (id, company_id, transaction_date, description, amount, balance, statement_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            ("stmt-insuff", cid, "2026-10-12", "APP移动代发 · 26年9月工资", "-9250.00", "stmt-insuff", "2026-10-12T00:00:00+00:00"),
        )
        await conn.commit()
    finally:
        await conn.close()

    async with session_scope() as session:
        with pytest.raises(HTTPException) as exc_info:
            await confirm_suggestion(
                ConfirmSuggestionRequest(
                    statement_id="stmt-insuff",
                    type="new",
                    settleOpeningPayroll=True,
                    new_entry={
                        "direction": "expense",
                        "amount": 9250,
                        "category": "工资发放",
                        "title": "APP移动代发 · 26年9月工资",
                    },
                ),
                ctx=AuthContext(user_id="owner-1", company_id=cid),
                request=None,
                session=session,
                idempotency_key=None,
            )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["openingPayroll"]["eligible"] is True
    assert exc_info.value.detail["openingPayroll"]["canSettle"] is False
    assert exc_info.value.detail["openingPayroll"]["reason"] == "opening_balance_insufficient"


@pytest.mark.asyncio
async def test_ai_suggest_enriches_opening_payroll_settlement_metadata():
    """ai_suggest_matches 对工资流水附带 payrollSettlement：有期初时返回 opening_balance，无期初时返回 opening_balance_missing。"""
    from sqlalchemy import select
    from db.models import Company
    from db.session import session_scope
    from services.accounting_core_service import issue_step_up_token
    from services.bank_service import ai_suggest_matches, ensure_bank_schema
    from services.database import get_conn
    from services.opening_balance_service import save_opening_balances

    cid_ok = "c-sal-ai-open-ok"
    cid_miss = "c-sal-ai-open-miss"
    await _setup_company(cid_ok)
    await _setup_company(cid_miss)

    async with session_scope() as session:
        c_ok = (await session.execute(select(Company).where(Company.company_id == cid_ok))).scalar_one()
        c_ok.payload = {**(c_ok.payload or {}), "startDate": "2026-10-01"}
        token = await issue_step_up_token(
            session, company_id=cid_ok, user_id="owner-1", action="adjust_opening_balance"
        )
        await save_opening_balances(
            cid_ok,
            2026,
            [
                {"account_code": "1002", "account_name": "银行存款", "debit_balance": "50000.00", "credit_balance": "0.00"},
                {"account_code": "221101", "account_name": "应付职工薪酬-工资", "debit_balance": "0.00", "credit_balance": "9250.00"},
                {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0.00", "credit_balance": "40750.00"},
            ],
            user_id="owner-1",
            step_up_token=token,
            request_id="req-open-ai-ok",
            start_period="2026-10",
            session=session,
        )
        c_miss = (await session.execute(select(Company).where(Company.company_id == cid_miss))).scalar_one()
        c_miss.payload = {**(c_miss.payload or {}), "startDate": "2026-10-01"}

    await ensure_bank_schema()
    conn = await get_conn()
    try:
        for cid, sid in ((cid_ok, "stmt-ai-ok"), (cid_miss, "stmt-ai-miss")):
            await conn.execute(
                "INSERT INTO bank_statements (id, company_id, transaction_date, description, amount, balance, statement_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                (sid, cid, "2026-10-12", "APP移动代发 · 26年9月工资", "-9250.00", sid, "2026-10-12T00:00:00+00:00"),
            )
        await conn.commit()
    finally:
        await conn.close()

    sugs_ok = await ai_suggest_matches(cid_ok, month="2026-10")
    assert len(sugs_ok) == 1
    assert sugs_ok[0]["payrollSettlement"]["mode"] == "opening_balance"
    assert sugs_ok[0]["payrollSettlement"]["accountCode"] == "221101"
    assert sugs_ok[0]["requiresReview"] is False

    sugs_miss = await ai_suggest_matches(cid_miss, month="2026-10")
    assert len(sugs_miss) == 1
    assert sugs_miss[0]["payrollSettlement"]["mode"] == "opening_balance_missing"
    assert sugs_miss[0]["requiresReview"] is True


@pytest.mark.asyncio
async def test_tax_channel_exact_social_due_attaches_payroll_settlement():
    """税单号通道的社保扣款：摘要无法区分险种，但金额命中已计提社保应缴额时必须给工资表核销，而不是让用户猜税种。"""
    cid = "c-sal-taxch-ss"
    await _setup_company(cid)

    from db.session import session_scope
    from services.bank_service import (
        _enrich_payroll_settlement_suggestions,
        ensure_bank_schema,
    )
    from services.database import get_conn
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        accrue_salary_sheet,
        create_employee,
        get_or_create_salary_sheet,
    )

    await create_employee(cid, "钱一", "技术部", 8000, 5000)
    sheet = await get_or_create_salary_sheet(cid, "2026-10")
    await replace_salary_allocations(cid, "owner-1", "2026-10", [])
    await accrue_salary_sheet(cid, "owner-1", "2026-10")

    record = sheet["records"][0]
    personal_ss = money_of(
        Decimal(str(record["personal_social_security"])) - Decimal(str(record["personal_housing_fund"]))
    )
    company_ss = money_of(
        Decimal(str(record["company_social_security"])) - Decimal(str(record["company_housing_fund"]))
    )
    due = money_of(personal_ss + company_ss)
    assert await _account_net_balance(cid, "1002") == 0  # 计提不动银行

    stmt_id = "stmt-taxch-ss"
    await ensure_bank_schema()
    conn = await get_conn()
    try:
        await conn.execute(
            "INSERT INTO bank_statements (id, company_id, transaction_date, description, amount, balance, statement_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (
                stmt_id, cid, "2026-11-10",
                "税款 | 00TX:000990:20261110:000YST:实时缴税:税单号:433016261013026301 (对方: 暂收款)",
                f"-{due}", stmt_id, "2026-11-10T00:00:00+00:00",
            ),
        )
        await conn.commit()
    finally:
        await conn.close()

    suggestions = [{
        "statementId": stmt_id,
        "statement": {
            "date": "2026-11-10",
            "description": "税款 | 00TX:000990:20261110:000YST:实时缴税:税单号:433016261013026301 (对方: 暂收款)",
            "amount": f"-{due}",
        },
        "type": "new",
        "confidence": 0.7,
        "reason": "税费通道扣款",
        "suggestedEntry": {
            "direction": "expense",
            "category": "税费缴纳",
            "accountCode": "2221026",
            "accountName": "应交增值税（小规模纳税人专用）",
            "title": "税款",
            "amount": due,
        },
    }]
    await _enrich_payroll_settlement_suggestions(cid, suggestions)

    sug = suggestions[0]
    assert sug["payrollSettlement"]["mode"] == "salary_sheet"
    assert sug["payrollSettlement"]["kind"] == "social_security"
    assert sug["payrollSettlement"]["month"] == "2026-10"
    assert Decimal(str(sug["payrollSettlement"]["amount"])) == due
    # 分类必须被纠正：继续挂税费缴纳会让用户在 5 个税类候选里拍板
    assert sug["suggestedEntry"]["category"] == "社保公积金"
    assert sug["suggestedEntry"]["accountCode"] == "221102"
    assert sug["requiresReview"] is False


@pytest.mark.asyncio
async def test_tax_channel_exact_iit_due_maps_to_iit_payable():
    """税单号通道的个税扣款：命中工资表代扣个税应缴额时归入个税代缴（2221012），不是税费缴纳/所得税费用。"""
    cid = "c-sal-taxch-tax"
    await _setup_company(cid)

    from services.bank_service import (
        _enrich_payroll_settlement_suggestions,
        ensure_bank_schema,
    )
    from services.database import get_conn
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        accrue_salary_sheet,
        create_employee,
        get_or_create_salary_sheet,
        update_salary_records,
    )

    await create_employee(cid, "孙二", "技术部", 20000, 20000)
    sheet = await get_or_create_salary_sheet(cid, "2026-10")
    await replace_salary_allocations(cid, "owner-1", "2026-10", [])
    await update_salary_records(cid, "2026-10", [
        {**sheet["records"][0], "personal_tax": "1500.00"},
    ])
    await accrue_salary_sheet(cid, "owner-1", "2026-10")

    stmt_id = "stmt-taxch-tax"
    await ensure_bank_schema()
    conn = await get_conn()
    try:
        await conn.execute(
            "INSERT INTO bank_statements (id, company_id, transaction_date, description, amount, balance, statement_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (
                stmt_id, cid, "2026-11-15",
                "税款 | 00TX:000990:20261115:000YST:实时缴税:税单号:333016261000522728 (对方: 暂收款)",
                "-1500.00", stmt_id, "2026-11-15T00:00:00+00:00",
            ),
        )
        await conn.commit()
    finally:
        await conn.close()

    suggestions = [{
        "statementId": stmt_id,
        "statement": {
            "date": "2026-11-15",
            "description": "税款 | 00TX:000990:20261115:000YST:实时缴税:税单号:333016261000522728 (对方: 暂收款)",
            "amount": "-1500.00",
        },
        "type": "new",
        "confidence": 0.7,
        "reason": "税费通道扣款",
        "suggestedEntry": {
            "direction": "expense",
            "category": "税费缴纳",
            "accountCode": "2221026",
            "accountName": "应交增值税（小规模纳税人专用）",
            "title": "税款",
            "amount": "1500.00",
        },
    }]
    await _enrich_payroll_settlement_suggestions(cid, suggestions)

    sug = suggestions[0]
    assert sug["payrollSettlement"]["kind"] == "tax"
    assert sug["payrollSettlement"]["month"] == "2026-10"
    assert sug["suggestedEntry"]["category"] == "个税代缴"
    assert sug["suggestedEntry"]["accountCode"] == "2221012"


@pytest.mark.asyncio
async def test_confirm_iit_category_routes_to_payroll_guard():
    """银行页选「个税代缴」不得直接入账，必须 409 引导工资模块核销 2221012。"""
    cid = "c-sal-taxch-guard"
    await _setup_company(cid)

    from services.bank_service import ensure_bank_schema
    from services.database import get_conn

    await ensure_bank_schema()
    conn = await get_conn()
    try:
        await conn.execute(
            "INSERT INTO bank_statements (id, company_id, transaction_date, description, amount, balance, statement_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (
                "stmt-taxch-guard", cid, "2026-11-15",
                "个人所得税缴纳",
                "-800.00", "stmt-taxch-guard", "2026-11-15T00:00:00+00:00",
            ),
        )
        await conn.commit()
    finally:
        await conn.close()

    from fastapi import HTTPException
    from db.session import session_scope
    from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
    from deps.auth import AuthContext

    async with session_scope() as session:
        with pytest.raises(HTTPException) as exc_info:
            await confirm_suggestion(
                ConfirmSuggestionRequest(
                    statement_id="stmt-taxch-guard",
                    type="new",
                    new_entry={
                        "direction": "expense",
                        "amount": 800,
                        "category": "个税代缴",
                        "accountCode": "2221012",
                        "title": "个税缴纳",
                    },
                ),
                ctx=AuthContext(user_id="owner-1", company_id=cid),
                request=None,
                session=session,
                idempotency_key=None,
            )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "PAYROLL_TAX_REQUIRES_PAYROLL"


@pytest.mark.asyncio
async def test_carry_forward_identical_month_marks_no_change_and_carries_rd_allocations():
    """无奖金无变动月份自动带出后 isIdenticalToPrevious=True，并仅继承当月仍有效的研发工时分配。"""
    from datetime import date
    from db.session import session_scope
    from services.rd_allocation_service import get_salary_allocations, replace_salary_allocations
    from services.rd_service import create_project
    from services.salary_service import (
        accrue_salary_sheet,
        create_employee,
        get_or_create_salary_sheet,
    )

    cid = "c-cf-identical-rd"
    await _setup_company(cid)

    async with session_scope() as session:
        valid_proj = await create_project(
            session,
            cid,
            "owner-1",
            {
                "code": "RD-CF-ACTIVE",
                "name": "跨月延续项目",
                "type": "self_started",
                "status": "active",
                "startDate": date(2026, 1, 1),
                "endDate": date(2026, 12, 31),
            },
            request_id="req-cf-proj-1",
        )
        expired_proj = await create_project(
            session,
            cid,
            "owner-1",
            {
                "code": "RD-CF-EXPIRED",
                "name": "9月结题项目",
                "type": "self_started",
                "status": "completed",
                "startDate": date(2026, 1, 1),
                "endDate": date(2026, 9, 30),
            },
            request_id="req-cf-proj-2",
        )

    emp = await create_employee(
        cid, "研发老张", "研发部", Decimal("5000.00"), Decimal("5000.00"), hire_date="2026-01-01"
    )
    await get_or_create_salary_sheet(cid, "2026-09")
    await replace_salary_allocations(
        cid,
        "owner-1",
        "2026-09",
        [
            {
                "employeeId": emp["id"],
                "projectId": valid_proj["id"],
                "allocationRate": 0.6,
                "allocationBasis": "核心研发 60%",
            },
            {
                "employeeId": emp["id"],
                "projectId": expired_proj["id"],
                "allocationRate": 0.4,
                "allocationBasis": "一期收尾 40%",
            },
        ],
    )
    await accrue_salary_sheet(cid, "owner-1", "2026-09")

    sheet_10 = await get_or_create_salary_sheet(cid, "2026-10")
    cf = sheet_10["carryForward"]
    assert cf["hasPreviousSheet"] is True
    assert cf["sourceMonth"] == "2026-09"
    assert cf["isIdenticalToPrevious"] is True
    assert cf["changeReasons"] == []

    allocs_10 = await get_salary_allocations(cid, "owner-1", "2026-10")
    assert len(allocs_10) == 1
    assert allocs_10[0]["projectId"] == valid_proj["id"]
    assert Decimal(allocs_10[0]["allocationRate"]) == Decimal("0.600000") or Decimal(allocs_10[0]["allocationRate"]) == Decimal("0.6")


@pytest.mark.asyncio
async def test_carry_forward_and_sync_previous_respect_roster_updates():
    """花名册调薪调基后新建草稿或手动重新同步上月，必须采用最新花名册基数并拒绝对已计提表同步。"""
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import (
        accrue_salary_sheet,
        create_employee,
        get_or_create_salary_sheet,
        sync_draft_sheet_from_previous,
        update_employee,
        update_salary_records,
    )

    cid = "c-cf-sync-roster"
    await _setup_company(cid)

    emp = await create_employee(
        cid, "陈花名册", "行政部", Decimal("6000.00"), Decimal("5000.00"), hire_date="2026-09-01"
    )
    sheet_09 = await get_or_create_salary_sheet(cid, "2026-09")
    await update_salary_records(
        cid,
        "2026-09",
        [{**sheet_09["records"][0], "allowance": "300.00", "bonus": "500.00"}],
    )
    await replace_salary_allocations(cid, "owner-1", "2026-09", [])
    await accrue_salary_sheet(cid, "owner-1", "2026-09")

    # 10月草稿先按旧花名册开出，之后用户在10月改乱了奖金并在花名册调薪调基
    sheet_10_init = await get_or_create_salary_sheet(cid, "2026-10")
    await update_salary_records(
        cid,
        "2026-10",
        [{**sheet_10_init["records"][0], "bonus": "9999.00"}],
    )
    await update_employee(
        cid,
        emp["id"],
        {
            "base_salary": Decimal("9000.00"),
            "social_security_base": Decimal("8000.00"),
        },
    )

    synced = await sync_draft_sheet_from_previous(cid, "2026-10", user_id="owner-1")
    rec_10 = synced["records"][0]
    assert rec_10["base_salary"] == "9000.00"
    assert rec_10["allowance"] == "300.00"
    assert rec_10["bonus"] == "0.00"
    # 新社保公积金基数 8000 按默认杭州口径重算（个人 18.5%=1480，单位 33%=2640）
    assert Decimal(rec_10["personal_social_security"]) == Decimal("1480.00")
    assert Decimal(rec_10["company_social_security"]) == Decimal("2640.00")
    assert any("基本工资" in r for r in synced["carryForward"]["changeReasons"])

    # 已计提后禁止再同步覆盖
    await accrue_salary_sheet(cid, "owner-1", "2026-10")
    with pytest.raises(ValueError, match="仅草稿状态"):
        await sync_draft_sheet_from_previous(cid, "2026-10", user_id="owner-1")


@pytest.mark.asyncio
async def test_opening_month_payroll_waterfall_settles_remaining_221101_for_personal_withholding():
    """情况 A：旧账期初 221101 挂应发工资 11500（含个人代扣 2250），221102/221105 仅挂单位部分；首月发薪+扣社保+扣公积金瀑布冲销后全部清零。"""
    cid = "c-sal-opening-waterfall"
    await _setup_company(cid)

    from sqlalchemy import select
    from db.models import Company, JournalLine
    from db.session import session_scope
    from deps.auth import AuthContext
    from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
    from services.accounting_core_service import issue_step_up_token
    from services.bank_service import (
        _evaluate_opening_payroll_settlement,
        ai_suggest_matches,
        ensure_bank_schema,
    )
    from services.database import get_conn
    from services.opening_balance_service import save_opening_balances

    async with session_scope() as session:
        company = (await session.execute(select(Company).where(Company.company_id == cid))).scalar_one()
        company.payload = {**(company.payload or {}), "startDate": "2026-10-01"}
        token = await issue_step_up_token(
            session, company_id=cid, user_id="owner-1", action="adjust_opening_balance"
        )
        await save_opening_balances(
            cid,
            2026,
            [
                {"account_code": "1002", "account_name": "银行存款", "debit_balance": "50000.00", "credit_balance": "0.00"},
                {"account_code": "221101", "account_name": "应付职工薪酬-工资", "debit_balance": "0.00", "credit_balance": "11500.00"},
                {"account_code": "221102", "account_name": "应付职工薪酬-单位社保", "debit_balance": "0.00", "credit_balance": "3000.00"},
                {"account_code": "221105", "account_name": "应付职工薪酬-住房公积金", "debit_balance": "0.00", "credit_balance": "1000.00"},
                {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0.00", "credit_balance": "34500.00"},
            ],
            user_id="owner-1",
            step_up_token=token,
            request_id="req-open-wf",
            start_period="2026-10",
            session=session,
        )

    await ensure_bank_schema()
    conn = await get_conn()
    try:
        for sid, dt, desc, amt in (
            ("stmt-wf-sal", "2026-10-12", "APP移动代发 · 26年9月工资", "-9250.00"),
            ("stmt-wf-ss", "2026-10-15", "社会保险费扣款（9月社保）", "-4250.00"),
            ("stmt-wf-hf", "2026-10-16", "住房公积金扣款（9月公积金）", "-2000.00"),
        ):
            await conn.execute(
                "INSERT INTO bank_statements (id, company_id, transaction_date, description, amount, balance, statement_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                (sid, cid, dt, desc, amt, sid, f"{dt}T00:00:00+00:00"),
            )
        await conn.commit()
    finally:
        await conn.close()

    # 1) 初始建议：工资 9250 提示冲销后剩余 2250.00 可用于后续个人代扣部分
    sugs_1 = {s["statementId"]: s for s in await ai_suggest_matches(cid, month="2026-10")}
    sal_sug = sugs_1["stmt-wf-sal"]
    assert sal_sug["payrollSettlement"]["mode"] == "opening_balance"
    assert sal_sug["payrollSettlement"]["remainingOpeningCredit"] == "2250.00"
    assert "2250.00" in sal_sug["reason"]

    async with session_scope() as session:
        res_sal = await confirm_suggestion(
            ConfirmSuggestionRequest(
                statement_id="stmt-wf-sal",
                type="new",
                settleOpeningPayroll=True,
                new_entry={
                    "direction": "expense",
                    "amount": 9250,
                    "category": "工资发放",
                    "title": "APP移动代发 · 26年9月工资",
                },
            ),
            ctx=AuthContext(user_id="owner-1", company_id=cid),
            request=None,
            session=session,
            idempotency_key=None,
        )
    async with session_scope() as session:
        lines_sal = (
            await session.execute(select(JournalLine).where(JournalLine.journal_entry_id == res_sal["entryId"]))
        ).scalars().all()
        assert {(l.account_code, Decimal(l.amount)) for l in lines_sal if l.direction == "debit"} == {
            ("221101", Decimal("9250.00")),
        }
        assert {(l.account_code, Decimal(l.amount)) for l in lines_sal if l.direction == "credit"} == {
            ("1002", Decimal("9250.00")),
        }

    # 2) 发薪完成后，社保 4250 建议自动拆分为 221102(3000) + 221101(1250)
    sugs_2 = {s["statementId"]: s for s in await ai_suggest_matches(cid, month="2026-10")}
    ss_sug = sugs_2["stmt-wf-ss"]
    assert ss_sug["payrollSettlement"]["mode"] == "opening_balance"
    assert ss_sug["payrollSettlement"]["allocations"] == [
        {"accountCode": "221102", "accountName": "应付职工薪酬-单位社保", "amount": "3000.00"},
        {"accountCode": "221101", "accountName": "应付职工薪酬-工资", "amount": "1250.00"},
    ]
    assert "221102" in ss_sug["reason"] and "221101" in ss_sug["reason"]

    async with session_scope() as session:
        res_ss = await confirm_suggestion(
            ConfirmSuggestionRequest(
                statement_id="stmt-wf-ss",
                type="new",
                settleOpeningPayroll=True,
                new_entry={
                    "direction": "expense",
                    "amount": 4250,
                    "category": "社保公积金",
                    "title": "社会保险费扣款（9月社保）",
                },
            ),
            ctx=AuthContext(user_id="owner-1", company_id=cid),
            request=None,
            session=session,
            idempotency_key=None,
        )
    async with session_scope() as session:
        lines_ss = (
            await session.execute(select(JournalLine).where(JournalLine.journal_entry_id == res_ss["entryId"]))
        ).scalars().all()
        assert {(l.account_code, Decimal(l.amount)) for l in lines_ss if l.direction == "debit"} == {
            ("221102", Decimal("3000.00")),
            ("221101", Decimal("1250.00")),
        }
        assert {(l.account_code, Decimal(l.amount)) for l in lines_ss if l.direction == "credit"} == {
            ("1002", Decimal("4250.00")),
        }

    # 3) 公积金 2000 建议自动拆分为 221105(1000) + 221101(1000)
    sugs_3 = {s["statementId"]: s for s in await ai_suggest_matches(cid, month="2026-10")}
    hf_sug = sugs_3["stmt-wf-hf"]
    assert hf_sug["payrollSettlement"]["mode"] == "opening_balance"
    assert hf_sug["payrollSettlement"]["allocations"] == [
        {"accountCode": "221105", "accountName": "应付职工薪酬-住房公积金", "amount": "1000.00"},
        {"accountCode": "221101", "accountName": "应付职工薪酬-工资", "amount": "1000.00"},
    ]

    async with session_scope() as session:
        res_hf = await confirm_suggestion(
            ConfirmSuggestionRequest(
                statement_id="stmt-wf-hf",
                type="new",
                settleOpeningPayroll=True,
                new_entry={
                    "direction": "expense",
                    "amount": 2000,
                    "category": "社保公积金",
                    "title": "住房公积金扣款（9月公积金）",
                },
            ),
            ctx=AuthContext(user_id="owner-1", company_id=cid),
            request=None,
            session=session,
            idempotency_key=None,
        )
    async with session_scope() as session:
        lines_hf = (
            await session.execute(select(JournalLine).where(JournalLine.journal_entry_id == res_hf["entryId"]))
        ).scalars().all()
        assert {(l.account_code, Decimal(l.amount)) for l in lines_hf if l.direction == "debit"} == {
            ("221105", Decimal("1000.00")),
            ("221101", Decimal("1000.00")),
        }
        assert {(l.account_code, Decimal(l.amount)) for l in lines_hf if l.direction == "credit"} == {
            ("1002", Decimal("2000.00")),
        }

        # 4) 三笔冲销后，221101/221102/221104/221105 期初可用余额全部归零
        rem_ss = await _evaluate_opening_payroll_settlement(
            session,
            cid,
            {"transaction_date": "2026-10-20", "description": "9月社保", "amount": "-1.00"},
            "liability",
        )
        assert rem_ss["availableOpeningCredit"] == "0.00"
        assert rem_ss["canSettle"] is False


@pytest.mark.asyncio
async def test_opening_month_tax_channel_statement_exposes_opening_social_settlement():
    """实时缴税通道流水不改动默认税费多义分类，但预附 payrollLiabilitySettlement 供切换「社保公积金」后一键冲销期初 221102+221101。"""
    cid = "c-sal-tax-channel-open"
    await _setup_company(cid)

    from sqlalchemy import select
    from db.models import Company, JournalEntry, JournalLine, SourceTransaction
    from db.session import session_scope
    from deps.auth import AuthContext
    from routers.bank import ConfirmSuggestionRequest, confirm_suggestion
    from services.accounting_core_service import issue_step_up_token
    from services.bank_service import ai_suggest_matches, ensure_bank_schema
    from services.database import get_conn
    from services.opening_balance_service import save_opening_balances

    async with session_scope() as session:
        company = (await session.execute(select(Company).where(Company.company_id == cid))).scalar_one()
        company.payload = {**(company.payload or {}), "startDate": "2026-10-01"}
        token = await issue_step_up_token(
            session, company_id=cid, user_id="owner-1", action="adjust_opening_balance"
        )
        await save_opening_balances(
            cid,
            2026,
            [
                {"account_code": "1002", "account_name": "银行存款", "debit_balance": "50000.00", "credit_balance": "0.00"},
                {"account_code": "221101", "account_name": "应付职工薪酬-工资", "debit_balance": "0.00", "credit_balance": "2250.00"},
                {"account_code": "221102", "account_name": "应付职工薪酬-单位社保", "debit_balance": "0.00", "credit_balance": "2445.00"},
                {"account_code": "3001", "account_name": "实收资本", "debit_balance": "0.00", "credit_balance": "45305.00"},
            ],
            user_id="owner-1",
            step_up_token=token,
            request_id="req-open-tax-ch",
            start_period="2026-10",
            session=session,
        )

    await ensure_bank_schema()
    conn = await get_conn()
    try:
        await conn.execute(
            "INSERT INTO bank_statements (id, company_id, transaction_date, description, amount, balance, statement_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (
                "stmt-tax-ch-ss",
                cid,
                "2026-10-10",
                "暂收款·税款 · 00TX:000990:20261010:000YST26101017G7U3ZA:实时缴税:税单号:433016261013026301",
                "-3670.00",
                "stmt-tax-ch-ss",
                "2026-10-10T00:00:00+00:00",
            ),
        )
        await conn.commit()
    finally:
        await conn.close()

    sugs = await ai_suggest_matches(cid, month="2026-10")
    assert len(sugs) == 1
    sug = sugs[0]
    assert sug["suggestedEntry"]["category"] == "税费缴纳"
    assert "payrollSettlement" not in sug
    assert sug["suggestedEntry"].get("requiresSalarySettlement") is not True

    liab = sug["payrollLiabilitySettlement"]
    assert liab["mode"] == "opening_balance"
    assert liab["allocations"] == [
        {"accountCode": "221102", "accountName": "应付职工薪酬-单位社保", "amount": "2445.00"},
        {"accountCode": "221101", "accountName": "应付职工薪酬-工资", "amount": "1225.00"},
    ]
    assert "221102" in liab["reason"] and "221101" in liab["reason"]

    async with session_scope() as session:
        res = await confirm_suggestion(
            ConfirmSuggestionRequest(
                statement_id="stmt-tax-ch-ss",
                type="new",
                settleOpeningPayroll=True,
                new_entry={
                    "direction": "expense",
                    "amount": 3670,
                    "category": "社保公积金",
                    "title": "9月社保缴费",
                },
            ),
            ctx=AuthContext(user_id="owner-1", company_id=cid),
            request=None,
            session=session,
            idempotency_key=None,
        )
    assert res["settledOpeningPayroll"] is True

    async with session_scope() as session:
        entry = await session.get(JournalEntry, res["entryId"])
        assert entry is not None
        src = await session.get(SourceTransaction, entry.source_transaction_id)
        assert src is not None and src.source_type == "opening_payroll_settlement"
        lines = (
            await session.execute(select(JournalLine).where(JournalLine.journal_entry_id == res["entryId"]))
        ).scalars().all()
        assert {(l.account_code, Decimal(l.amount)) for l in lines if l.direction == "debit"} == {
            ("221102", Decimal("2445.00")),
            ("221101", Decimal("1225.00")),
        }
        assert {(l.account_code, Decimal(l.amount)) for l in lines if l.direction == "credit"} == {
            ("1002", Decimal("3670.00")),
        }







