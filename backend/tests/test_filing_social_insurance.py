# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""社保费申报事项：只有工资表已计提社保的月份才提示。

工资数据走真实链路（员工 → 工资表 → 计提凭证 → 征期物化 → 作业包），不打桩。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests.test_salary_module import _setup_company


async def _company(company_id: str) -> None:
    """建账套并把记账起点钉在 2027 年初，避免用例随真实日期漂移成「建档前期间」。"""
    await _setup_company(company_id)

    from db.models import Company
    from db.session import session_scope

    async with session_scope() as session:
        row = await session.get(Company, company_id)
        row.payload = {**(row.payload or {}), "startDate": "2027-01-01"}


async def _accrue(company_id: str, month: str) -> dict:
    from services.rd_allocation_service import replace_salary_allocations
    from services.salary_service import accrue_salary_sheet, get_or_create_salary_sheet

    await get_or_create_salary_sheet(company_id, month)
    await replace_salary_allocations(company_id, "owner-1", month, [])
    return await accrue_salary_sheet(company_id, "owner-1", month)


async def _reverse(company_id: str, entry_id: str) -> None:
    from db.session import session_scope
    from services.accounting_core_service import reverse_journal

    async with session_scope() as session:
        await reverse_journal(
            session, entry_id, company_id=company_id, user_id="owner-1",
            request_id=f"req-{entry_id}", reason="测试冲销计提凭证",
        )


async def _social_rows(company_id: str) -> dict:
    from services import filing_deadline_service as svc

    await svc.refresh_deadlines(company_id, year=2027)
    return {
        row["period"]: row
        for row in await svc.list_deadlines(company_id)
        if row["taxKind"] == "social_insurance"
    }


def test_social_due_date_is_15th_of_the_same_month_without_weekend_shift():
    """社保缴的是当月费款，不能套个税的次月 15 日；2027-05-15 是周六，也不顺延。"""
    from services.filing_deadline_service import compute_due_date

    assert compute_due_date("social_insurance", "2026-10") == date(2026, 10, 15)
    assert compute_due_date("social_insurance", "2027-05") == date(2027, 5, 15)
    assert compute_due_date("social_insurance", "bad") is None


@pytest.mark.asyncio
async def test_prefilled_draft_sheet_is_not_a_social_signal():
    """打开工资页会按档案基数预填草稿（弹窗默认 4000），不能因此判定有人交社保。"""
    await _company("c-si-draft")

    from services.salary_service import create_employee, get_or_create_salary_sheet

    await create_employee("c-si-draft", "赵一", "技术部", 8000, 4000)
    sheet = await get_or_create_salary_sheet("c-si-draft", "2027-05")

    assert Decimal(sheet["records"][0]["company_social_security"]) > 0
    assert await _social_rows("c-si-draft") == {}


@pytest.mark.asyncio
async def test_company_without_payroll_has_no_social_deadlines():
    """没人交社保的账套不能被内置清单铺出 12 条待办；其他月度事项不受影响。"""
    await _company("c-si-none")

    from services import filing_deadline_service as svc

    assert await _social_rows("c-si-none") == {}
    kinds = {row["taxKind"] for row in await svc.list_deadlines("c-si-none")}
    assert "iit_withholding" in kinds


@pytest.mark.asyncio
async def test_posted_accrual_creates_social_deadline_for_that_month_only():
    await _company("c-si-accrued")

    from services.salary_service import create_employee, social_insurance_month_summary

    await create_employee("c-si-accrued", "钱二", "技术部", 8000, 5000)
    await _accrue("c-si-accrued", "2027-05")

    rows = await _social_rows("c-si-accrued")
    assert set(rows) == {"2027-05"}
    assert rows["2027-05"]["status"] == "pending"
    assert rows["2027-05"]["dueDate"] == "2027-05-15"
    assert rows["2027-05"]["taxName"] == "社保费申报缴纳"

    # 个人 5000×10.5%、单位 5000×25%，与工资表「缴纳社保」按钮同一金额，不含公积金
    summary = await social_insurance_month_summary("c-si-accrued", 2027)
    assert summary["2027-05"]["personal"] == Decimal("525.00")
    assert summary["2027-05"]["company"] == Decimal("1250.00")
    assert summary["2027-05"]["ssPaid"] is False


@pytest.mark.asyncio
async def test_accrual_without_social_amount_has_no_deadline():
    await _company("c-si-zero")

    from services.salary_service import create_employee

    await create_employee("c-si-zero", "孙三", "技术部", 8000, 0)
    await _accrue("c-si-zero", "2027-05")

    assert await _social_rows("c-si-zero") == {}


@pytest.mark.asyncio
async def test_housing_fund_only_month_is_not_a_social_deadline():
    """工资表社保列是社保公积金合并口径：只交公积金不等于有社保申报义务。"""
    await _company("c-si-hf")

    from services.salary_service import create_employee, save_payroll_rates

    await save_payroll_rates("c-si-hf", {"socialPersonal": "0", "socialCompany": "0"})
    await create_employee("c-si-hf", "李四", "技术部", 8000, 5000)
    sheet = await _accrue("c-si-hf", "2027-05")

    assert Decimal(str(sheet["records"][0]["company_social_security"])) > 0
    assert await _social_rows("c-si-hf") == {}


@pytest.mark.asyncio
async def test_reversed_accrual_waives_deadline_and_reaccrual_restores_it():
    """计提被冲销后不能仍提示；重新计提后同一条记录恢复，不重复建行。"""
    await _company("c-si-reverse")

    from services.salary_service import create_employee

    await create_employee("c-si-reverse", "周五", "技术部", 8000, 5000)
    accrued = await _accrue("c-si-reverse", "2027-05")
    original = (await _social_rows("c-si-reverse"))["2027-05"]

    await _reverse("c-si-reverse", accrued["accrual_voucher_id"])
    waived = (await _social_rows("c-si-reverse"))["2027-05"]
    assert waived["status"] == "waived"
    assert waived["filedEvidence"]["reason"] == "salary_social_changed"

    await _accrue("c-si-reverse", "2027-05")
    restored = (await _social_rows("c-si-reverse"))["2027-05"]
    assert restored["status"] == "pending"
    assert restored["id"] == original["id"]


@pytest.mark.asyncio
async def test_filed_deadline_survives_accrual_reversal():
    """用户已办结的申报是税局侧事实，账上冲销计提不能把它改掉。"""
    await _company("c-si-filed")

    from services import filing_deadline_service as svc
    from services.salary_service import create_employee

    await create_employee("c-si-filed", "吴六", "技术部", 8000, 5000)
    accrued = await _accrue("c-si-filed", "2027-05")
    row = (await _social_rows("c-si-filed"))["2027-05"]
    await svc.mark_filed("c-si-filed", "owner-1", row["id"])

    await _reverse("c-si-filed", accrued["accrual_voucher_id"])

    assert (await _social_rows("c-si-filed"))["2027-05"]["status"] == "filed"


@pytest.mark.asyncio
async def test_salary_read_failure_keeps_pending_social_rows(monkeypatch):
    """工资表读取失败不等于没人交社保：不能把待办误豁免，也不能拖垮其他税种。"""
    await _company("c-si-fail")

    from services import filing_deadline_service as svc
    from services import salary_service
    from services.salary_service import create_employee

    await create_employee("c-si-fail", "郑七", "技术部", 8000, 5000)
    await _accrue("c-si-fail", "2027-05")
    assert (await _social_rows("c-si-fail"))["2027-05"]["status"] == "pending"

    async def boom(*_args, **_kwargs):
        raise RuntimeError("salary tables unavailable")

    monkeypatch.setattr(salary_service, "social_insurance_month_summary", boom)

    rows = await _social_rows("c-si-fail")
    assert rows["2027-05"]["status"] == "pending"
    assert any(row["taxKind"] == "vat" for row in await svc.list_deadlines("c-si-fail"))


@pytest.mark.asyncio
async def test_obligations_for_month_include_social_only_for_accrued_month():
    await _company("c-si-oblig")

    from services import filing_deadline_service as svc
    from services.salary_service import create_employee

    await create_employee("c-si-oblig", "王八", "技术部", 8000, 5000)
    await _accrue("c-si-oblig", "2027-05")

    def social(result: dict) -> list:
        return [o for o in result["obligations"] if o["taxKind"] == "social_insurance"]

    may = social(await svc.obligations_for_month("c-si-oblig", "2027-05"))
    assert [(o["period"], o["cycle"], o["dueDate"]) for o in may] == [("2027-05", "month", "2027-05-15")]
    assert social(await svc.obligations_for_month("c-si-oblig", "2027-06")) == []


@pytest.mark.asyncio
async def test_package_lists_accrued_social_month_and_follows_payment(monkeypatch):
    """作业包只列已计提的月份，金额带两位小数；缴社保凭证入账后改为提示确认办结。"""
    await _company("c-si-pkg")

    from services import filing_deadline_service as svc
    from services import filing_package_service as fps
    from services.salary_service import create_employee, pay_payroll_liability
    from tests.test_filing_package_entity_type import _patch_collaborators

    _patch_collaborators(monkeypatch, cit_calls={"count": 0})
    await create_employee("c-si-pkg", "冯九", "技术部", 8000, 5000)
    await _accrue("c-si-pkg", "2027-05")
    await svc.refresh_deadlines("c-si-pkg", year=2027)

    pkg = await fps.build_filing_package("owner-1", "c-si-pkg", "2027-Q2")
    items = [i for i in pkg["checklist"] if i["taxKind"] == "social_insurance"]
    assert [i["itemPeriod"] for i in items] == ["2027-05"]
    item = items[0]
    assert item["name"] == "社保费申报缴纳（5 月）"
    assert item["status"] == "pending"
    assert item["deadlineId"]
    assert item["dueDate"] == "2027-05-15"
    assert "¥1775.00" in item["hint"]
    assert "不含公积金" in item["hint"]
    assert item["actionPath"] == "/mine/salary"

    await pay_payroll_liability("c-si-pkg", "owner-1", "2027-05", "social_security", pay_date="2027-05-20")
    pkg = await fps.build_filing_package("owner-1", "c-si-pkg", "2027-Q2")
    item = next(i for i in pkg["checklist"] if i["taxKind"] == "social_insurance")
    assert "缴社保凭证已入账" in item["hint"]
    assert item["actionPath"] is None
    # 缴款凭证不等于用户确认已申报：状态仍由用户办结
    assert item["status"] == "pending"


@pytest.mark.asyncio
async def test_package_without_social_month_has_no_social_item(monkeypatch):
    await _company("c-si-pkg-none")

    from services import filing_package_service as fps
    from tests.test_filing_package_entity_type import _patch_collaborators

    _patch_collaborators(monkeypatch, cit_calls={"count": 0})
    pkg = await fps.build_filing_package("owner-1", "c-si-pkg-none", "2027-Q2")

    assert all(i["taxKind"] != "social_insurance" for i in pkg["checklist"])


@pytest.mark.asyncio
async def test_tax_registration_does_not_trim_social_item(monkeypatch):
    """同步过税费种认定表的账套，认定表里没有的税种会被裁掉，但社保不走认定表。"""
    await _company("c-si-reg")

    from services import filing_deadline_service as svc
    from services import filing_package_service as fps
    from services import salary_service, tax_registration_service

    totals = {"personal": Decimal("525.00"), "company": Decimal("1250.00"), "ssPaid": False}

    async def fake_registrations(_company_id):
        return [{
            "returnGroup": "vat", "filingCycle": "quarter", "deadlineRule": "quarter_15",
            "dueMonthOffset": None, "dueDay": None, "levyItem": "增值税", "levyCategory": "",
        }]

    async def fake_summary(_company_id, _year):
        return {"2027-05": totals}

    monkeypatch.setattr(tax_registration_service, "list_registrations", fake_registrations)
    monkeypatch.setattr(salary_service, "social_insurance_month_summary", fake_summary)

    schedule = await svc.expected_schedule("c-si-reg", 2027)
    assert schedule["source"] == "registration"
    assert schedule["items"][("social_insurance", "2027-05")] == date(2027, 5, 15)

    kinds = {
        item["taxKind"]
        for item in fps._checklist(
            "2027-Q2", "2027-Q2", active_groups={"vat"}, social_months={"2027-05": totals}
        )
    }
    assert "social_insurance" in kinds
    assert "stamp_duty" not in kinds
