# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""CIT 测算：从业人数来自名册派生，完整度只看限制行业。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from deps.auth import AuthContext
from models.schemas import CitAssistRequest


def _statement():
    """最小利润表/资产负债表快照（免走真实分录）。"""
    return SimpleNamespace(
        profit_ytd={
            "revenue": 10000,
            "cost": 0,
            "tax_surcharge": 0,
            "sales_expense": 0,
            "research_expense": 0,
            "management_expense": 0,
            "finance_expense": 0,
            "investment_income": 0,
            "operating_profit": 6500,
            "non_operating_income": 0,
            "non_operating_expense": 0,
            "total_profit": 6500,
            "net_profit": 6500,
            "income_tax": 0,
        },
        balance_period_opening={"asset_total": 0},
        balance_current={"asset_total": 6500},
        period=SimpleNamespace(
            label="2031-Q2",
            fiscal_year_start=date(2031, 1, 1),
            end=date(2031, 6, 30),
        ),
    )


def _patch(monkeypatch, company, headcount=None, *, headcount_raises: bool = False):
    from routers import report as report_router

    async def fake_statement(*_args, **_kwargs):
        return _statement()

    async def fake_profile(*_args, **_kwargs):
        return {"current_company": company}

    async def fake_headcount(*_args, **_kwargs):
        if headcount_raises:
            raise RuntimeError("employee table unavailable")
        return headcount

    monkeypatch.setattr(report_router, "build_statement_data", fake_statement)
    monkeypatch.setattr(report_router, "get_user_profile", fake_profile)
    monkeypatch.setattr(report_router.headcount_service, "get_headcount", fake_headcount)


async def _call(req: CitAssistRequest):
    from routers.report import cit_assist

    return await cit_assist(req=req, ctx=AuthContext(user_id="u-cit", company_id="c-cit"), session=None)


def _req(**kwargs) -> CitAssistRequest:
    return CitAssistRequest(period="2031-Q2", periodType="quarter", manualAdjustmentsConfirmed=True, **kwargs)


@pytest.mark.asyncio
async def test_corporate_empty_roster_uses_floor_one(monkeypatch):
    """公司制名册为空：按 1 人判定，限制行业已确认 → 小型微利优惠生效。"""
    from services.headcount_service import Headcount

    _patch(
        monkeypatch,
        {"company_id": "c-cit", "type": "有限责任公司", "restrictedIndustry": False},
        Headcount(raw=0, count=1, entity_type="ltd", is_corporate=True),
    )
    result = await _call(_req())

    assert result.employeeAverage == Decimal("1.00")
    assert result.companyTaxProfileComplete is True
    assert result.smallMicroEligibility == "eligible"
    assert result.taxReduction == Decimal("1300.00")
    assert result.blockingIssues == []


@pytest.mark.asyncio
async def test_individual_business_zero_headcount(monkeypatch):
    """个体工商户无雇工：人数为 0，不报错。"""
    from services.headcount_service import Headcount

    _patch(
        monkeypatch,
        {"company_id": "c-cit", "type": "个体工商户", "restrictedIndustry": False},
        Headcount(raw=0, count=0, entity_type="individual_business", is_corporate=False),
    )
    result = await _call(_req())

    assert result.employeeAverage == Decimal("0.00")
    assert result.companyTaxProfileComplete is True


@pytest.mark.asyncio
async def test_missing_restricted_industry_stays_undetermined(monkeypatch):
    """限制行业未确认：仍为 undetermined，阻断项指向主体管理。"""
    from services.headcount_service import Headcount

    _patch(
        monkeypatch,
        {"company_id": "c-cit", "type": "有限责任公司"},
        Headcount(raw=1, count=1, entity_type="ltd", is_corporate=True),
    )
    result = await _call(_req())

    assert result.smallMicroEligibility == "undetermined"
    assert result.companyTaxProfileComplete is False
    assert any("主体管理" in issue for issue in result.blockingIssues)
    assert result.readyToFile is False


@pytest.mark.asyncio
async def test_dirty_payload_strings_do_not_crash(monkeypatch):
    """历史脏值（空串）不再触发 Decimal 异常。"""
    from services.headcount_service import Headcount

    _patch(
        monkeypatch,
        {"company_id": "c-cit", "restrictedIndustry": "", "employeeCount": ""},
        Headcount(raw=1, count=1, entity_type="ltd", is_corporate=True),
    )
    result = await _call(_req())

    assert result.companyTaxProfileComplete is False
    assert result.employeeAverage == Decimal("1.00")


@pytest.mark.asyncio
async def test_headcount_failure_falls_back_to_request(monkeypatch):
    """名册读取失败：回退请求体季初/季末平均，并阻止 beginnerReady。"""
    _patch(
        monkeypatch,
        {"company_id": "c-cit", "restrictedIndustry": False},
        headcount_raises=True,
    )
    result = await _call(_req(employeeCountStart=4, employeeCountEnd=6))

    assert result.employeeAverage == Decimal("5.00")
    assert result.beginnerReady is False
    assert any("员工名册" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_derived_headcount_wins_over_request(monkeypatch):
    """名册可用时，请求体的季初/季末人数被忽略。"""
    from services.headcount_service import Headcount

    _patch(
        monkeypatch,
        {"company_id": "c-cit", "restrictedIndustry": False},
        Headcount(raw=1, count=1, entity_type="ltd", is_corporate=True),
    )
    result = await _call(_req(employeeCountStart=999, employeeCountEnd=999))

    assert result.employeeAverage == Decimal("1.00")
