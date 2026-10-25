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
    gross = Decimal("10000.00")
    company_ss = (gross * Decimal("0.33")).quantize(Decimal("0.01"))  # 25% 社保 + 8% 公积金
    r_cost = gross + company_ss
    assert rd_total == (r_cost * Decimal("0.6")).quantize(Decimal("0.01"))
    # 40% 余额拆成两条管理费用明细：工资池与单位社保池各自按工时分摊
    assert admin_salary == (gross * Decimal("0.4")).quantize(Decimal("0.01"))
    assert admin_social == (company_ss * Decimal("0.4")).quantize(Decimal("0.01"))
    assert admin_salary + admin_social == (r_cost - rd_total).quantize(Decimal("0.01"))
    # 贷方四个明细科目齐备，且四者之和 == 借方合计
    personal_ss = (gross * Decimal("0.185")).quantize(Decimal("0.01"))
    net = gross - personal_ss
    assert sum(Decimal(str(l["amount"])) for l in lines["credit"] if l["accountCode"] == "221101") == net
    assert sum(Decimal(str(l["amount"])) for l in lines["credit"] if l["accountCode"] == "221102") == company_ss
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
    """五笔分录闭环：221101/221102/221104/2221012 期末余额全部归零。"""
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
    assert _sum_by_code(e1["voucherLines"]["debit"], "5602016") == company_ss
    assert _sum_by_code(e1["voucherLines"]["credit"], "221101") == net
    assert _sum_by_code(e1["voucherLines"]["credit"], "221102") == company_ss
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

    # V4 缴公积金：单独一笔，仍冲 221102/221104
    e4 = await get_entry(hf["hf_paid_voucher_id"], user_id="owner-1", company_id="c-sal-loop")
    assert _sum_by_code(e4["voucherLines"]["debit"], "221102") == company_hf
    assert _sum_by_code(e4["voucherLines"]["debit"], "221104") == personal_hf
    assert _sum_by_code(e4["voucherLines"]["credit"], "1002") == company_hf + personal_hf

    # V5 缴个税
    e5 = await get_entry(tax["tax_paid_voucher_id"], user_id="owner-1", company_id="c-sal-loop")
    assert _sum_by_code(e5["voucherLines"]["debit"], "2221012") == p_tax
    assert _sum_by_code(e5["voucherLines"]["credit"], "1002") == p_tax

    # 闭环断言：四个中间科目余额归零，银行净流出 = 总人工成本
    for code in ("221101", "221102", "221104", "2221012"):
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
    rd_total = _sum_by_code(lines["debit"], "430100101")
    assert admin_salary + admin_social + rd_total == total_gross + total_ss
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

    # 研发丁：gross 5000 / c_ss 1650，RD 池 money(5000×0.999999)=5000.00 全额进研发
    # 研发戊：gross 3000 / c_ss 990，RD 池 2999.70 + 989.90，残差 0.30 + 0.10 归管理费用
    assert _sum_by_code(lines["debit"], "430100101") == Decimal("10639.60")
    assert _sum_by_code(lines["debit"], "5602001") == Decimal("0.30")
    assert _sum_by_code(lines["debit"], "5602016") == Decimal("0.10")
    # 贷方：净工资 4075+2445、单位社保 1650+990、个人社保 925+555
    assert _sum_by_code(lines["credit"], "221101") == Decimal("6520.00")
    assert _sum_by_code(lines["credit"], "221102") == Decimal("2640.00")
    assert _sum_by_code(lines["credit"], "221104") == Decimal("1480.00")
    # 每个员工的三条项目明细合计精确等于其 RD 池（最大余数法闭合）
    rd_lines = [l for l in lines["debit"] if l["accountCode"] == "430100101"]
    ding = [l for l in rd_lines if "研发丁" in l["rdDetail"]["notes"]]
    wu = [l for l in rd_lines if "研发戊" in l["rdDetail"]["notes"]]
    assert sum((Decimal(str(l["amount"])) for l in ding), Decimal("0")) == Decimal("6650.00")
    assert sum((Decimal(str(l["amount"])) for l in wu), Decimal("0")) == Decimal("3989.60")


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
    notices = {item["code"] for item in checks["notices"]}
    assert "SALARY_PAYABLE_OUTSTANDING" in notices


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
