# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""申报作业包的企业所得税税前扣除资料状态（原报表中心「税务汇总」页并入）。"""
from __future__ import annotations

import pytest


def _entry(direction: str, amount: str, occurred: str, tax_deductible=None) -> dict:
    entry = {"direction": direction, "amount": amount, "occurDate": occurred}
    if tax_deductible is not None:
        entry["taxDeductible"] = tax_deductible
    return entry


@pytest.mark.asyncio
async def test_deduction_materials_aggregates_by_tax_deductible(monkeypatch):
    """按期间聚合支出：taxDeductible=True/False/未标记 三态分桶，收入与期间外不计入。"""
    from services import filing_package_service as fps

    entries = [
        _entry("expense", "100.00", "2026-08-01", True),
        _entry("expense", "50.00", "2026-09-10", False),
        _entry("expense", "30.00", "2026-08-20"),
        _entry("expense", "70.00", "2026-06-30", True),  # 季度外（Q2 末日）
        _entry("income", "9900.99", "2026-08-01", True),  # 收入不参与扣除
    ]

    async def fake_entries(_company_id: str) -> list[dict]:
        return entries

    monkeypatch.setattr(fps, "_authoritative_entries", fake_entries)

    result = await fps._deduction_materials("c-ded", "2026-Q3", "quarter")

    assert result == {
        "bookExpenses": 180.0,
        "totalDeductible": 100.0,
        "totalNonDeductible": 50.0,
        "pendingDeductibility": 30.0,
    }


async def _ensure_schema() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _create_company(company_id: str) -> None:
    from db.models import Company
    from db.session import session_scope

    async with session_scope() as session:
        session.add(
            Company(
                company_id=company_id,
                owner_user_id="u-ded",
                payload={},
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )


@pytest.mark.asyncio
async def test_package_exposes_deduction_materials(monkeypatch):
    """build_filing_package 输出包含 deductionMaterials 四项。"""
    await _ensure_schema()
    await _create_company("c-ded-pkg")

    from services import filing_package_service as fps

    async def fake_vat(*_args, **_kwargs) -> dict:
        return {
            "totalSales": 0.0, "isBelowThreshold": True, "totalVatPayable": 0.0,
            "surchargeTotal": 0.0, "readyToFile": True, "blockingIssues": [],
            "warnings": [], "filingFields": [], "items": [],
        }

    async def fake_cit(*_args, **_kwargs) -> dict:
        return {"revenue": 0.0, "totalProfit": 0.0, "preferentialTax": 0.0,
                "taxableBase": 0.0, "note": "", "fields": []}

    async def fake_cross(*_args, **_kwargs) -> dict:
        return {"consistent": True, "differences": []}

    async def fake_deduction(*_args, **_kwargs) -> dict:
        return {
            "bookExpenses": 180.0,
            "totalDeductible": 100.0,
            "totalNonDeductible": 50.0,
            "pendingDeductibility": 30.0,
        }

    monkeypatch.setattr(fps, "_vat_assist", fake_vat)
    monkeypatch.setattr(fps, "_cit_estimate", fake_cit)
    monkeypatch.setattr(fps, "_cross_check", fake_cross)
    monkeypatch.setattr(fps, "_deduction_materials", fake_deduction)

    pkg = await fps.build_filing_package("u-ded", "c-ded-pkg", "2026-Q3")

    assert pkg["deductionMaterials"]["bookExpenses"] == 180.0
    assert pkg["deductionMaterials"]["pendingDeductibility"] == 30.0
