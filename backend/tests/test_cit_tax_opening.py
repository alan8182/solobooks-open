# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""期初税务备忘接入企业所得税预缴：A200000 第 24/30 行。

备忘随期初保存，只在其 startPeriod 所属纳税年度内生效；跨年沿用会把同一笔
亏损/预缴重复抵扣，必须按 0 处理。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest


async def _ensure_schema() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _seed_company(company_id: str, memo: dict, *, tax_id: str) -> None:
    from db.models import Company, CompanyProfile
    from db.session import session_scope

    async with session_scope() as session:
        session.add(
            Company(
                company_id=company_id,
                owner_user_id="u-cit",
                payload={"taxId": tax_id, "type": "有限责任公司", "restrictedIndustry": False},
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )
        session.add(CompanyProfile(company_id=company_id, profile={"taxOpening": memo}))


def _statement(fiscal_year: int, *, total_profit: int = 100000) -> SimpleNamespace:
    return SimpleNamespace(
        profit_ytd={
            "revenue": 300000,
            "cost": 200000,
            "tax_surcharge": 0,
            "sales_expense": 0,
            "research_expense": 0,
            "management_expense": 0,
            "finance_expense": 0,
            "investment_income": 0,
            "operating_profit": total_profit,
            "non_operating_income": 0,
            "non_operating_expense": 0,
            "total_profit": total_profit,
            "net_profit": total_profit,
            "income_tax": 0,
        },
        balance_period_opening={"asset_total": 0},
        balance_current={"asset_total": 6500},
        period=SimpleNamespace(
            label=f"{fiscal_year}-Q2",
            fiscal_year_start=date(fiscal_year, 1, 1),
            end=date(fiscal_year, 6, 30),
        ),
    )


def test_apply_tax_opening_caps_loss_and_ignores_other_years():
    from services.cit_tax_opening import apply_tax_opening

    memo = {"startPeriod": "2026-06", "priorYearLoss": 50000, "prepaidCitTax": 15000}
    same_year = apply_tax_opening(Decimal("20000.00"), memo, 2026)
    assert same_year["applies"] is True
    assert same_year["lossDeducted"] == Decimal("20000.00")
    assert same_year["prepaidCitTax"] == Decimal("15000.00")

    other_year = apply_tax_opening(Decimal("20000.00"), memo, 2027)
    assert other_year == {
        "applies": False,
        "priorYearLoss": Decimal("0.00"),
        "lossDeducted": Decimal("0.00"),
        "prepaidCitTax": Decimal("0.00"),
    }

    # 没有 startPeriod 就无法判断年度，一律不使用
    no_start = apply_tax_opening(
        Decimal("20000.00"), {"priorYearLoss": 5000}, 2026,
    )
    assert no_start["applies"] is False
    assert no_start["lossDeducted"] == Decimal("0.00")

    # 脏数据负数不能反向做大应纳税所得额
    dirty = apply_tax_opening(
        Decimal("20000.00"),
        {"startPeriod": "2026-06", "priorYearLoss": -5000, "prepaidCitTax": -100},
        2026,
    )
    assert dirty["lossDeducted"] == Decimal("0.00")
    assert dirty["prepaidCitTax"] == Decimal("0.00")


def test_package_summary_keeps_zero_when_prepaid_covers_cit():
    """已预缴覆盖本期税额时，摘要不能用 or 回退成 5% 正税额。"""
    from services.filing_package_service import _cit_payable

    assert _cit_payable({"taxPayable": 0, "preferentialTax": 50}) == 0
    assert _cit_payable({"preferentialTax": 50}) == 50
    assert _cit_payable(None) == 0


@pytest.mark.asyncio
async def test_cit_estimate_deducts_prior_loss_and_prepaid_from_opening_memo(monkeypatch):
    """同年度备忘：第 24 行抵扣亏损、第 30 行抵扣已预缴，第 32 行取净额。"""
    from services import filing_package_service as fps

    await _ensure_schema()
    await _seed_company(
        "c-cit-memo",
        {"startPeriod": "2026-06", "priorYearLoss": 50000, "prepaidCitTax": 15000},
        tax_id="91330100MA2MEMO001",
    )

    async def fake_statement(*_args, **_kwargs):
        return _statement(2026)

    monkeypatch.setattr(fps, "build_statement_data", fake_statement)

    out = await fps._cit_estimate("u-cit", "c-cit-memo", "2026-Q2", entries=[])

    assert out["taxOpeningApplied"] is True
    assert out["priorYearLoss"] == 50000.0
    assert out["taxableBase"] == 50000.0
    assert out["preferentialTax"] == 2500.0
    assert out["prepaidCitTax"] == 15000.0
    assert out["taxPayable"] == 0.0
    assert "已预缴税额覆盖本期应纳" in out["note"]

    lines = {row["line"]: row["amount"] for row in out["fields"]}
    assert lines["24"] == 50000.0
    assert lines["30"] == 15000.0
    assert lines["32"] == 0.0


@pytest.mark.asyncio
async def test_cit_estimate_ignores_opening_memo_from_other_year(monkeypatch):
    """去年建账的备忘不能拿到今年重复抵扣。"""
    from services import filing_package_service as fps

    await _ensure_schema()
    await _seed_company(
        "c-cit-memo-old",
        {"startPeriod": "2025-06", "priorYearLoss": 50000, "prepaidCitTax": 15000},
        tax_id="91330100MA2MEMO002",
    )

    async def fake_statement(*_args, **_kwargs):
        return _statement(2026)

    monkeypatch.setattr(fps, "build_statement_data", fake_statement)

    out = await fps._cit_estimate("u-cit", "c-cit-memo-old", "2026-Q2", entries=[])

    assert out["taxOpeningApplied"] is False
    assert out["priorYearLoss"] == 0.0
    assert out["prepaidCitTax"] == 0.0
    assert out["taxableBase"] == 100000.0
    assert out["taxPayable"] == 5000.0


@pytest.mark.asyncio
async def test_cit_assist_falls_back_to_opening_memo(monkeypatch):
    """cit-assist 未传第 24/30 行时使用期初备忘，并标注来源。"""
    from deps.auth import AuthContext
    from models.schemas import CitAssistRequest
    from routers import report as report_router
    from services.headcount_service import Headcount

    await _ensure_schema()
    await _seed_company(
        "c-cit-memo-api",
        {"startPeriod": "2031-06", "priorYearLoss": 2000, "prepaidCitTax": 100},
        tax_id="91330100MA2MEMO003",
    )

    async def fake_statement(*_args, **_kwargs):
        return _statement(2031, total_profit=6500)

    async def fake_profile(*_args, **_kwargs):
        return {"current_company": {
            "company_id": "c-cit-memo-api",
            "type": "有限责任公司",
            "restrictedIndustry": False,
        }}

    async def fake_headcount(*_args, **_kwargs):
        return Headcount(raw=1, count=1, entity_type="ltd", is_corporate=True)

    monkeypatch.setattr(report_router, "build_statement_data", fake_statement)
    monkeypatch.setattr(report_router, "get_user_profile", fake_profile)
    monkeypatch.setattr(report_router.headcount_service, "get_headcount", fake_headcount)

    result = await report_router.cit_assist(
        req=CitAssistRequest(period="2031-Q2", periodType="quarter", manualAdjustmentsConfirmed=True),
        ctx=AuthContext(user_id="u-cit", company_id="c-cit-memo-api"),
        session=None,
    )

    fields = {row.line: row for row in result.filingFields}
    assert fields["24"].amount == Decimal("2000.00")
    assert fields["24"].source == "期初税务备忘"
    assert fields["30"].amount == Decimal("100.00")
    assert result.estimatedTax == Decimal("125.00")


@pytest.mark.asyncio
async def test_cit_assist_explicit_values_override_opening_memo(monkeypatch):
    """显式传 0 表示本期不抵扣，不能被期初备忘覆盖。"""
    from deps.auth import AuthContext
    from models.schemas import CitAssistRequest
    from routers import report as report_router
    from services.headcount_service import Headcount

    await _ensure_schema()
    await _seed_company(
        "c-cit-memo-override",
        {"startPeriod": "2031-06", "priorYearLoss": 2000, "prepaidCitTax": 100},
        tax_id="91330100MA2MEMO004",
    )

    async def fake_statement(*_args, **_kwargs):
        return _statement(2031, total_profit=6500)

    async def fake_profile(*_args, **_kwargs):
        return {"current_company": {
            "company_id": "c-cit-memo-override",
            "type": "有限责任公司",
            "restrictedIndustry": False,
        }}

    async def fake_headcount(*_args, **_kwargs):
        return Headcount(raw=1, count=1, entity_type="ltd", is_corporate=True)

    monkeypatch.setattr(report_router, "build_statement_data", fake_statement)
    monkeypatch.setattr(report_router, "get_user_profile", fake_profile)
    monkeypatch.setattr(report_router.headcount_service, "get_headcount", fake_headcount)

    result = await report_router.cit_assist(
        req=CitAssistRequest(
            period="2031-Q2", periodType="quarter", manualAdjustmentsConfirmed=True,
            priorYearLoss=0, prepaidTax=0,
        ),
        ctx=AuthContext(user_id="u-cit", company_id="c-cit-memo-override"),
        session=None,
    )

    fields = {row.line: row for row in result.filingFields}
    assert fields["24"].amount == Decimal("0.00")
    assert fields["24"].source == "人工确认"
    assert fields["30"].amount == Decimal("0.00")
    assert result.estimatedTax == Decimal("325.00")
