# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""经营所得个人所得税测算引擎与征期物化测试。

计划验收断言（plans/business-income-iit-plan.md §6）：
- 应纳税所得额 10 万（个体户）→ 100000×20%−10500 = 9500，减半 = 4750
- 同额（个独）→ 9500，不减半
- 月报公司每月物化 prepay 征期；季报公司每季
- ltd 主体不出现 iit 征期
- B 表截止日 = 次年 3-31（非 5-31）
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest


async def _ensure_schema() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _create_company(company_id: str, entity_type: str, **extra_payload) -> None:
    from db.models import Company
    from db.session import session_scope

    payload = {"type": entity_type, **extra_payload}
    async with session_scope() as session:
        session.add(
            Company(
                company_id=company_id,
                owner_user_id="u-iit",
                payload=payload,
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )


class TestProgressiveTax:
    """五级超额累进 + 速算扣除数（经营所得税率表，非综合所得税率表）。"""

    def test_bracket_boundaries(self):
        from services.business_income_iit_service import progressive_tax

        assert progressive_tax(Decimal("30000")) == Decimal("1500.00")
        assert progressive_tax(Decimal("30001")) == Decimal("1500.10")
        assert progressive_tax(Decimal("90000")) == Decimal("7500.00")
        assert progressive_tax(Decimal("100000")) == Decimal("9500.00")
        assert progressive_tax(Decimal("300000")) == Decimal("49500.00")
        assert progressive_tax(Decimal("500000")) == Decimal("109500.00")

    def test_negative_clamped_to_zero(self):
        from services.business_income_iit_service import progressive_tax

        assert progressive_tax(Decimal("-5000")) == Decimal("0.00")


class TestTaxPayable:
    def test_plan_acceptance_individual_business_halved(self):
        """应纳税所得额 10 万（个体户）→ 9500 减半 = 4750。"""
        from services.business_income_iit_service import tax_payable

        result = tax_payable(Decimal("100000"), "个体工商户", as_of=date(2026, 10, 18))
        assert result.tax == Decimal("4750.00")
        assert result.halving_reduction == Decimal("4750.00")
        assert result.halving_applied is True
        assert result.rate == Decimal("0.20")

    def test_plan_acceptance_sole_proprietorship_not_halved(self):
        """同额（个独）→ 9500，不减半（12 号公告只覆盖个体工商户）。"""
        from services.business_income_iit_service import tax_payable

        result = tax_payable(Decimal("100000"), "个人独资企业", as_of=date(2026, 10, 18))
        assert result.tax == Decimal("9500.00")
        assert result.halving_applied is False

    def test_halving_policy_expired(self):
        """政策至 2027-12-31，到期后个体户也不减半。"""
        from services.business_income_iit_service import tax_payable

        result = tax_payable(Decimal("100000"), "个体工商户", as_of=date(2028, 1, 1))
        assert result.tax == Decimal("9500.00")
        assert result.halving_applied is False

    def test_halving_above_2m_cap_is_partial(self):
        """超过 200 万：仅 200 万以内部分对应税额减半，超出部分全额。"""
        from services.business_income_iit_service import (
            HALVING_CAP,
            progressive_tax,
            tax_payable,
        )

        taxable = Decimal("2100000")
        result = tax_payable(taxable, "个体工商户", as_of=date(2026, 10, 18))
        tax_at_cap = progressive_tax(HALVING_CAP)
        expected = tax_at_cap * Decimal("0.5") + (progressive_tax(taxable) - tax_at_cap)
        assert result.tax == expected
        assert result.halving_reduction == progressive_tax(taxable) - expected

    def test_zero_or_negative_income(self):
        from services.business_income_iit_service import tax_payable

        result = tax_payable(Decimal("-12000"), "个体工商户")
        assert result.tax == Decimal("0.00")
        assert result.taxable_income == Decimal("0.00")


class TestDueDates:
    def test_prepay_quarter_uses_holiday_override(self):
        from services.filing_deadline_service import compute_due_date

        assert compute_due_date("business_income_iit_prepay", "2026-Q3") == date(2026, 10, 22)

    def test_prepay_month_falls_back_to_month_rule(self):
        from services.filing_deadline_service import compute_due_date

        # 2026-09 的月度申报期：次月 15 日（10 月黄金周顺延至 10-22）
        assert compute_due_date("business_income_iit_prepay", "2026-09") == date(2026, 10, 22)

    def test_annual_due_is_march_31_not_may_31(self):
        """B 表次年 3 月 31 日——绝不可复用企税汇算的 5-31。"""
        from services.filing_deadline_service import compute_due_date
        from services.business_income_iit_service import annual_due_date

        assert compute_due_date("business_income_iit_annual", "2026") == date(2027, 3, 31)
        assert annual_due_date(2026) == date(2027, 3, 31)


@pytest.mark.asyncio
async def test_refresh_deadlines_individual_business_materializes_iit():
    """个体户：物化经营所得 prepay + annual，不物化 cit。"""
    await _ensure_schema()
    await _create_company("c-iit-ib", "个体工商户")

    from services import filing_deadline_service as svc

    await svc.refresh_deadlines("c-iit-ib", year=2026)
    rows = await svc.list_deadlines("c-iit-ib")
    kinds = {r["taxKind"] for r in rows}

    assert "cit" not in kinds and "cit_annual" not in kinds
    assert "business_income_iit_annual" in kinds
    prepay = [r for r in rows if r["taxKind"] == "business_income_iit_prepay"]
    # 缺省季报：4 个季度期间，Q3 按 2026 公告顺延到 10-22
    assert len(prepay) == 4
    q3 = next(r for r in prepay if r["period"] == "2026-Q3")
    assert q3["dueDate"] == "2026-10-22"
    annual = next(r for r in rows if r["taxKind"] == "business_income_iit_annual")
    assert annual["period"] == "2026" and annual["dueDate"] == "2027-03-31"


@pytest.mark.asyncio
async def test_refresh_deadlines_monthly_cycle_materializes_twelve():
    """月报配置：物化 12 个月度 prepay 征期（用户按税局核定设置）。"""
    await _ensure_schema()
    await _create_company("c-iit-month", "个体工商户", businessIncomeIitFilingCycle="月报")

    from services import filing_deadline_service as svc

    await svc.refresh_deadlines("c-iit-month", year=2026)
    rows = await svc.list_deadlines("c-iit-month")
    prepay = [r for r in rows if r["taxKind"] == "business_income_iit_prepay"]

    assert len(prepay) == 12
    assert {r["period"] for r in prepay} == {f"2026-{m:02d}" for m in range(1, 13)}
    # 月报期间不出现季度格式
    assert not any("Q" in r["period"] for r in prepay)


@pytest.mark.asyncio
async def test_refresh_deadlines_ltd_skips_iit_and_waives_stock():
    """有限公司：不物化 iit 征期；先按个体户建档后改为有限公司的存量自动豁免。"""
    await _ensure_schema()
    await _create_company("c-iit-ltd", "个体工商户")

    from services import filing_deadline_service as svc

    await svc.refresh_deadlines("c-iit-ltd", year=2026)
    before = await svc.list_deadlines("c-iit-ltd")
    assert any(r["taxKind"] == "business_income_iit_prepay" for r in before)

    # 主体类型改为有限公司
    from db.models import Company
    from db.session import session_scope
    async with session_scope() as session:
        row = await session.get(Company, "c-iit-ltd")
        row.payload = {"type": "有限责任公司"}

    await svc.refresh_deadlines("c-iit-ltd", year=2026)
    after = await svc.list_deadlines("c-iit-ltd")
    iit_rows = [r for r in after if r["taxKind"].startswith("business_income_iit")]
    assert iit_rows, "存量行仍在（豁免而非删除）"
    assert all(r["status"] == "waived" for r in iit_rows)
    assert not any(r["status"] == "pending" and r["taxKind"].startswith("business_income_iit") for r in after)


@pytest.mark.asyncio
async def test_prepayment_assist_builds_card(monkeypatch):
    """A 表预缴卡：累计口径取利润表 ytd，个体户减半、字段组装完整。"""
    from services import business_income_iit_service as svc
    from services.tax_filing_export_service import build_statement_data

    class _FakeStatement:
        profit_ytd = {
            "revenue": Decimal("100000.00"),
            "total_profit": Decimal("40000.00"),
            "net_profit": Decimal("40000.00"),
        }

    async def fake_statement(*_args, **_kwargs):
        return _FakeStatement()

    monkeypatch.setattr(
        "services.tax_filing_export_service.build_statement_data", fake_statement
    )
    assert build_statement_data is not svc  # 确认打桩在源模块上

    await _ensure_schema()
    await _create_company("c-iit-card", "个体工商户")

    card = await svc.build_prepayment_assist("u-iit", "c-iit-card", "2026-Q3")
    assert card["applicable"] is True
    assert card["filingCycle"] == "quarter"
    assert card["revenue"] == Decimal("100000.00")
    assert card["profitTotal"] == Decimal("40000.00")
    # 累计应纳税所得额 4 万落 10% 档 → 40000×10%−1500=2500 → 减半 1250
    assert card["taxableIncome"] == Decimal("40000.00")
    assert card["tax"] == Decimal("1250.00")
    assert card["rate"] == Decimal("0.10")
    assert card["halvingApplied"] is True
    assert card["suggestedPayable"] == Decimal("1250.00")
    assert any("核定征收" in note for note in card["notes"])

    # ltd 主体：卡片不适用
    await _create_company("c-iit-card-ltd", "有限责任公司")
    card_ltd = await svc.build_prepayment_assist("u-iit", "c-iit-card-ltd", "2026-Q3")
    assert card_ltd == {"applicable": False, "reason": "entity_type_not_applicable"}


@pytest.mark.asyncio
async def test_annual_settlement_assist_builds_card(monkeypatch):
    """B 表汇算卡：调增、扣除、减半、已预缴抵减与应补退税。"""
    from decimal import Decimal

    from services import business_income_iit_service as svc
    from services import filing_package_service as fps

    class _FakeStatement:
        profit_ytd = {
            "revenue": Decimal("500000.00"),
            "total_profit": Decimal("120000.00"),
        }

    async def fake_statement(*_args, **_kwargs):
        return _FakeStatement()

    async def fake_scan(_company_id: str, _fiscal_year: int):
        return (
            Decimal("8000.00"),  # 无票支出
            [{"entryId": "e1", "title": "无票材料款", "occurDate": "2026-05-01", "amount": Decimal("8000.00")}],
            Decimal("96000.00"),  # 全年人力成本（业主工资确认的参考基数）
        )

    monkeypatch.setattr(
        "services.tax_filing_export_service.build_statement_data", fake_statement
    )
    monkeypatch.setattr(svc, "_annual_expense_scan", fake_scan)

    await _ensure_schema()
    await _create_company("c-iit-b1", "个体工商户")

    card = await svc.build_annual_settlement_assist(
        "u-iit", "c-iit-b1", 2026,
        owner_salary=Decimal("12000"),
        prepaid_total=Decimal("5000"),
    )
    assert card["applicable"] is True
    # 调增 = 业主工资 12000 + 无票支出 8000 = 20000
    assert card["adjustments"]["total"] == Decimal("20000.00")
    # 扣除默认 6 万
    assert card["deductions"]["statutory"] == Decimal("60000.00")
    # 应纳税所得额 = 120000 + 20000 − 60000 = 80000 → 10% 档 6500 → 减半 3250
    assert card["taxableIncome"] == Decimal("80000.00")
    assert card["tax"] == Decimal("3250.00")
    assert card["halvingApplied"] is True
    # 应补 = 3250 − 已预缴 5000 → 应退 1750
    assert card["finalPayable"] == Decimal("0.00")
    assert card["finalRefund"] == Decimal("1750.00")
    # 业主工资提示 + 6 万重复扣除提示
    assert any("业主" in note for note in card["notes"])
    assert any("6 万" in note for note in card["notes"])
    assert card["salaryPaidTotal"] == Decimal("96000.00")

    # 亏损年度：应纳税所得额归零、提示结转弥补
    class _LossStatement:
        profit_ytd = {"revenue": Decimal("50000.00"), "total_profit": Decimal("-20000.00")}

    async def fake_loss(*_args, **_kwargs):
        return _LossStatement()

    monkeypatch.setattr(
        "services.tax_filing_export_service.build_statement_data", fake_loss
    )
    loss_card = await svc.build_annual_settlement_assist("u-iit", "c-iit-b1", 2026)
    assert loss_card["taxableIncome"] == Decimal("0.00")
    assert loss_card["tax"] == Decimal("0.00")
    assert any("结转" in note for note in loss_card["notes"])

    # ltd：不适用
    await _create_company("c-iit-b-ltd", "有限责任公司")
    card_ltd = await svc.build_annual_settlement_assist("u-iit", "c-iit-b-ltd", 2026)
    assert card_ltd == {"applicable": False, "reason": "entity_type_not_applicable"}

    # 个独：不减半（先恢复盈利报表打桩，前面亏损打桩已覆盖）。
    # 应纳税所得额同 68000（无 ownerSalary 调增）→ 68000×10%−1500 = 5300
    monkeypatch.setattr(
        "services.tax_filing_export_service.build_statement_data", fake_statement
    )
    await _create_company("c-iit-b-sp", "个人独资企业")
    card_sp = await svc.build_annual_settlement_assist("u-iit", "c-iit-b-sp", 2026)
    assert card_sp["taxableIncome"] == Decimal("68000.00")
    assert card_sp["tax"] == Decimal("5300.00")
    assert card_sp["halvingApplied"] is False

    # package 服务仍能正常引出（确认无循环导入）
    assert hasattr(fps, "build_filing_package")


@pytest.mark.asyncio
async def test_owner_flag_and_auto_prefill(monkeypatch):
    """名册业主标记（P3）：is_owner 员工的年度应发工资自动预填 B 表调增，手工传参可覆盖。"""
    from decimal import Decimal

    from services import business_income_iit_service as svc
    from services import salary_service

    class _FakeStatement:
        profit_ytd = {"revenue": Decimal("500000.00"), "total_profit": Decimal("120000.00")}

    async def fake_statement(*_args, **_kwargs):
        return _FakeStatement()

    async def fake_scan(_company_id: str, _fiscal_year: int):
        return Decimal("0"), [], Decimal("96000.00")

    monkeypatch.setattr(
        "services.tax_filing_export_service.build_statement_data", fake_statement
    )
    monkeypatch.setattr(svc, "_annual_expense_scan", fake_scan)

    await _ensure_schema()
    await _create_company("c-iit-owner", "个体工商户")

    # 名册：业主 + 普通员工
    owner = await salary_service.create_employee(
        "c-iit-owner", "老板本人", "管理部", "8000.00", "4000.00", is_owner=True
    )
    await salary_service.create_employee(
        "c-iit-owner", "员工甲", "运营部", "6000.00", "3000.00"
    )
    staff = await salary_service.list_employees("c-iit-owner")
    assert {e["name"]: e["is_owner"] for e in staff} == {"老板本人": 1, "员工甲": 0}

    # 更新标记：普通员工改业主、业主改回
    await salary_service.update_employee("c-iit-owner", staff[1]["id"], {"is_owner": 1})
    staff = await salary_service.list_employees("c-iit-owner")
    assert all(e["is_owner"] == 1 for e in staff)
    await salary_service.update_employee("c-iit-owner", staff[1]["id"], {"is_owner": 0})

    # 造 2026 年工资记录：业主应发 = 8000 + 500 奖金 × 12 个月 = 102000
    import sqlite3

    from db.session import database_url

    db_path = database_url().split("///", 1)[1]
    conn = sqlite3.connect(db_path)
    try:
        for month in range(1, 13):
            sheet_id = f"sheet-owner-{month:02d}"
            conn.execute(
                "INSERT OR IGNORE INTO salary_sheets (id, company_id, month, status, created_at, updated_at) "
                "VALUES (?, ?, ?, 'accrued', '2027-01-01', '2027-01-01')",
                (sheet_id, "c-iit-owner", f"2026-{month:02d}"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO salary_records (id, sheet_id, employee_id, employee_name, "
                "base_salary, bonus, allowance, deduction, personal_social_security, personal_tax, "
                "company_social_security, net_salary, total_cost, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, '2027-01-01', '2027-01-01')",
                (f"rec-owner-{month:02d}", sheet_id, owner["id"], "老板本人",
                 "8000.00", "500.00", "0"),
            )
        conn.commit()
    finally:
        conn.close()

    auto_card = await svc.build_annual_settlement_assist("u-iit", "c-iit-owner", 2026)
    assert auto_card["adjustments"]["ownerSalarySource"] == "auto"
    # 业主应发 = (8000 + 500) × 12 = 102000
    assert auto_card["adjustments"]["ownerSalary"] == Decimal("102000.00")
    assert any("业主/投资者」标记" in note for note in auto_card["notes"])

    # 手工覆盖：ownerSalary 显式传入 → manual，不取名册
    manual_card = await svc.build_annual_settlement_assist(
        "u-iit", "c-iit-owner", 2026, owner_salary=Decimal("90000")
    )
    assert manual_card["adjustments"]["ownerSalarySource"] == "manual"
    assert manual_card["adjustments"]["ownerSalary"] == Decimal("90000.00")

    # 无业主标记的公司：auto 预填 0 + 去标记提示
    await _create_company("c-iit-noowner", "个体工商户")
    zero_card = await svc.build_annual_settlement_assist("u-iit", "c-iit-noowner", 2026)
    assert zero_card["adjustments"]["ownerSalarySource"] == "auto"
    assert zero_card["adjustments"]["ownerSalary"] == Decimal("0.00")
    assert any("没有标记" in note for note in zero_card["notes"])


@pytest.mark.asyncio
async def test_annual_expense_scan_real_import_path():
    """回归：_annual_expense_scan 必须走真实 DB 导入路径（不得借道 accounting_core_service）。

    2026-10-18 冒烟测试发现 B 表接口 500：该函数曾从 accounting_core_service
    导入并不存在的 session_scope；既有单测全部 mock 掉本函数，导入错误从未暴露。
    """
    from services import business_income_iit_service as svc
    from services.accounting_core_service import create_entry, seed_accounts
    from db.session import session_scope

    await _ensure_schema()
    await _create_company("c-iit-scan", "个体工商户")

    async with session_scope() as session:
        await seed_accounts(session, "c-iit-scan")

    async def _post(entry_id: str, payload: dict) -> None:
        async with session_scope() as session:
            await create_entry(
                session, payload,
                company_id="c-iit-scan", user_id="u-iit",
                request_id=entry_id, confirm=True,
            )

    # 无票支出（无开票状态/票号）→ 调增
    await _post("scan-undoc", {
        "id": "scan-undoc", "direction": "expense", "amount": "8000.00",
        "title": "无票材料款", "category": "原材料", "occurDate": "2026-05-01",
        "sourceType": "manual",
        "voucherLines": {
            "debit": [{"accountCode": "1403", "accountName": "原材料", "amount": "8000.00"}],
            "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "8000.00"}],
        },
    })
    # 有票支出 → 不调增
    await _post("scan-documented", {
        "id": "scan-documented", "direction": "expense", "amount": "3000.00",
        "title": "有票办公用品", "category": "办公费", "occurDate": "2026-05-02",
        "sourceType": "manual", "billingStatus": "has_invoice",
        "invoiceType": "增值税电子普通发票", "invoiceNumber": "25312000000012345",
        "voucherLines": {
            "debit": [{"accountCode": "5602002", "accountName": "管理费用-办公费", "amount": "3000.00"}],
            "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "3000.00"}],
        },
    })
    # 手工人力成本（有票，隔离工资基数逻辑）→ 计入工资基数
    await _post("scan-hr", {
        "id": "scan-hr", "direction": "expense", "amount": "5000.00",
        "title": "临时劳务费", "category": "人力成本", "occurDate": "2026-06-01",
        "sourceType": "manual", "billingStatus": "has_invoice",
        "invoiceType": "增值税电子普通发票", "invoiceNumber": "25312000000054321",
        "voucherLines": {
            "debit": [{"accountCode": "5602001", "accountName": "管理费用-工资", "amount": "5000.00"}],
            "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "5000.00"}],
        },
    })
    # 收入与跨年支出 → 均不计入
    await _post("scan-income", {
        "id": "scan-income", "direction": "income", "amount": "30000.00",
        "title": "销售收入", "category": "主营业务收入", "occurDate": "2026-07-01",
        "sourceType": "manual",
        "voucherLines": {
            "debit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "30000.00"}],
            "credit": [{"accountCode": "5001", "accountName": "主营业务收入", "amount": "30000.00"}],
        },
    })
    await _post("scan-prev-year", {
        "id": "scan-prev-year", "direction": "expense", "amount": "9999.00",
        "title": "上年费用", "category": "原材料", "occurDate": "2025-03-01",
        "sourceType": "manual",
        "voucherLines": {
            "debit": [{"accountCode": "1403", "accountName": "原材料", "amount": "9999.00"}],
            "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": "9999.00"}],
        },
    })

    undocumented, detail, salary_paid = await svc._annual_expense_scan("c-iit-scan", 2026)
    assert undocumented == Decimal("8000.00")  # 仅无票材料款
    assert salary_paid == Decimal("5000.00")  # 仅手工人力成本
    assert [d["title"] for d in detail] == ["无票材料款"]


def test_c_table_hint_present():
    """C 表提示：A/B 表卡与税种指引都要带"两处以上合并 C 表"。"""
    from services import filing_deadline_service
    from services.business_income_iit_service import (
        MULTI_SOURCE_C_TABLE_NOTE,
        tax_payable,
    )

    assert "C 表" in MULTI_SOURCE_C_TABLE_NOTE
    assert "C 表" in filing_deadline_service.TAX_KINDS["business_income_iit_annual"]["guide"]
    # 减半/个独分流不受提示影响
    assert tax_payable(Decimal("100000"), "个体工商户", as_of=date(2026, 10, 18)).tax == Decimal("4750.00")


@pytest.mark.asyncio
async def test_agent_filing_package_tool_includes_iit(monkeypatch):
    """Agent 报税工具视图：非公司主体带 businessIncomeIit 卡与 usage 引导。"""
    from agent.tools import ToolContext, _generate_filing_package
    from services import filing_package_service as fps

    async def fake_package(user_id: str, company_id: str, period: str):
        return {
            "period": period,
            "summary": {"vatPayable": 0, "citPayable": 0, "iitPayable": 1250.0,
                        "readyToFile": True, "blockingIssues": [], "warnings": [], "overdue": []},
            "vat": {"deadline": None},
            "cit": None,
            "citNotice": {"citApplicable": False, "incomeTaxKind": "business_income_iit"},
            "businessIncomeIit": {
                "applicable": True, "period": period, "filingCycle": "quarter",
                "entityType": "individual_business", "revenue": 100000.0,
                "expenses": 60000.0, "profitTotal": 40000.0, "taxableIncome": 40000.0,
                "tax": 1250.0, "halvingReduction": 1250.0, "halvingApplied": True,
                "suggestedPayable": 1250.0, "previouslyPaid": 0.0,
                "notes": ["仅查账征收"], "deadline": None,
            },
            "crossCheck": {"consistent": True, "differences": []},
            "preferences": [],
            "checklist": [],
        }

    monkeypatch.setattr(fps, "build_filing_package", fake_package)

    view = await _generate_filing_package(ToolContext(user_id="u", company_id="c"), "2026-Q3")
    assert view["businessIncomeIit"]["halvingApplied"] is True
    assert view["businessIncomeIit"]["suggestedPayable"] == 1250.0
    assert "经营所得" in view["usage"]
    assert "C 表" in view["usage"]
    assert "3 月 31 日" in view["usage"]


@pytest.mark.asyncio
async def test_agent_filing_package_tool_includes_cit_cost_and_fields(monkeypatch):
    """Agent 报税工具视图：公司主体带完整的 cit 字段（含成本与逐行申报字段）。"""
    from agent.tools import ToolContext, _generate_filing_package
    from services import filing_package_service as fps

    async def fake_package(user_id: str, company_id: str, period: str):
        return {
            "period": period,
            "summary": {"vatPayable": 0, "citPayable": 500.0, "readyToFile": True, "blockingIssues": [], "warnings": [], "overdue": []},
            "vat": {"deadline": None},
            "cit": {
                "period": period,
                "revenue": 100000.0,
                "cost": 60000.0,
                "totalProfit": 40000.0,
                "autoAdjustmentTotal": 2000.0,
                "taxableIncome": 42000.0,
                "preferentialTax": 2100.0,
                "fields": [
                    {"line": "1", "label": "营业收入（本年累计）", "amount": 100000.0},
                    {"line": "2", "label": "营业成本（本年累计）", "amount": 60000.0},
                    {"line": "18", "label": "利润总额（本年累计）", "amount": 40000.0},
                    {"line": "18A", "label": "纳税调增（系统测算）", "amount": 2000.0},
                    {"line": "25", "label": "实际利润额", "amount": 42000.0},
                ],
                "note": "小型微利优惠",
                "deadline": "2026-10-24",
            },
            "citNotice": {"citApplicable": True, "incomeTaxKind": "cit"},
            "businessIncomeIit": None,
            "crossCheck": {"consistent": True, "differences": []},
            "preferences": [],
            "checklist": [],
        }

    monkeypatch.setattr(fps, "build_filing_package", fake_package)

    view = await _generate_filing_package(ToolContext(user_id="u", company_id="c-cit"), "2026-Q3")
    cit = view["cit"]
    assert cit is not None
    assert cit["revenue"] == 100000.0
    assert cit["cost"] == 60000.0
    assert cit["autoAdjustmentTotal"] == 2000.0
    assert cit["taxableIncome"] == 42000.0
    assert len(cit["fields"]) == 5
    assert cit["fields"][1]["label"] == "营业成本（本年累计）"
