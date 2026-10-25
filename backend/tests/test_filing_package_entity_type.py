# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""主体类型（组织形式）分支：个人独资企业/个体工商户不缴企业所得税。

覆盖三层出口：申报作业包（cit 置空）、征期日历（cit 不物化 + 存量豁免）、
优惠建议/清单（不再出现企业所得税条目）。
"""
from __future__ import annotations

import pytest


async def _ensure_schema() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _create_company(
    company_id: str,
    *,
    entity_type: str | None = None,
    created_at: str = "2026-01-01T00:00:00+00:00",
) -> None:
    from db.models import Company
    from db.session import session_scope

    payload = {"type": entity_type} if entity_type else {}
    async with session_scope() as session:
        session.add(
            Company(
                company_id=company_id,
                owner_user_id="u-ent",
                payload=payload,
                created_at=created_at,
                updated_at=created_at,
            )
        )


async def _set_entity_type(company_id: str, entity_type: str) -> None:
    from db.models import Company
    from db.session import session_scope

    async with session_scope() as session:
        row = await session.get(Company, company_id)
        row.payload = {**(row.payload or {}), "type": entity_type}


def test_entity_type_normalization():
    from services import company_entity_service as svc

    assert svc.normalize_entity_type("有限责任公司") == svc.ENTITY_LTD
    assert svc.normalize_entity_type("个人独资企业") == svc.ENTITY_SOLE_PROPRIETORSHIP
    assert svc.normalize_entity_type("个体工商户") == svc.ENTITY_INDIVIDUAL_BUSINESS
    assert svc.normalize_entity_type("INDIVIDUAL_BUSINESS") == svc.ENTITY_INDIVIDUAL_BUSINESS
    # 缺省/脏数据按有限公司兜底，保持存量行为
    assert svc.normalize_entity_type(None) == svc.ENTITY_LTD
    assert svc.normalize_entity_type("某某奇怪主体") == svc.ENTITY_LTD

    assert svc.pays_corporate_income_tax("有限责任公司") is True
    assert svc.pays_corporate_income_tax("个人独资企业") is False
    assert svc.pays_corporate_income_tax("个体工商户") is False


def _fake_vat() -> dict:
    return {
        "totalSales": 0.0,
        "isBelowThreshold": True,
        "totalVatPayable": 0.0,
        "surchargeTotal": 0.0,
        "readyToFile": True,
        "blockingIssues": [],
        "warnings": [],
        "filingFields": [],
        "items": [],
    }


async def _fake_cross(*_args, **_kwargs) -> dict:
    return {"consistent": True, "differences": []}


def _patch_collaborators(monkeypatch, *, cit_calls: dict, iit_calls: dict | None = None):
    from services import filing_package_service as fps

    async def fake_vat(*_args, **_kwargs) -> dict:
        return _fake_vat()

    async def fake_cit(*_args, **_kwargs) -> dict:
        cit_calls["count"] += 1
        return {
            "revenue": 0.0,
            "totalProfit": 1000.0,
            "preferentialTax": 50.0,
            "taxableBase": 1000.0,
            "note": "",
            "fields": [],
        }

    async def fake_iit(*_args, **_kwargs) -> dict:
        if iit_calls is not None:
            iit_calls["count"] += 1
        return {
            "applicable": True,
            "period": "2026-Q4",
            "periodType": "quarter",
            "filingCycle": "quarter",
            "entityType": "individual_business",
            "revenue": 100000.0,
            "expenses": 60000.0,
            "profitTotal": 40000.0,
            "taxableIncome": 40000.0,
            "tax": 3750.0,
            "halvingReduction": 3750.0,
            "rate": 0.2,
            "bracketLabel": "不超过 300,000 元部分",
            "halvingApplied": True,
            "previouslyPaid": 0.0,
            "suggestedPayable": 3750.0,
            "notes": [],
        }

    monkeypatch.setattr(fps, "_vat_assist", fake_vat)
    monkeypatch.setattr(fps, "_cit_estimate", fake_cit)
    monkeypatch.setattr(fps, "_cross_check", _fake_cross)
    monkeypatch.setattr(fps.business_income_iit_service, "build_prepayment_assist", fake_iit)


@pytest.mark.asyncio
async def test_individual_business_package_has_no_cit(monkeypatch):
    """个体工商户：不算 CIT、不返回 cit、不出现企业所得税优惠与汇算清单。"""
    await _ensure_schema()
    await _create_company("c-ent-ib", entity_type="个体工商户")
    calls = {"count": 0}
    iit_calls = {"count": 0}
    _patch_collaborators(monkeypatch, cit_calls=calls, iit_calls=iit_calls)

    from services import filing_package_service as fps

    pkg = await fps.build_filing_package("u-ent", "c-ent-ib", "2026-Q4")

    assert calls["count"] == 0
    assert pkg["cit"] is None
    assert pkg["citNotice"]["citApplicable"] is False
    assert pkg["citNotice"]["incomeTaxKind"] == "business_income_iit"
    assert pkg["summary"]["citPayable"] == 0
    assert not any(p["code"] == "cit_small_micro" for p in pkg["preferences"])
    assert all(item["taxKind"] != "cit_annual" for item in pkg["checklist"])
    # 经营所得 A 表预缴卡：个体户出卡，含减半口径与征期状态
    assert iit_calls["count"] == 1
    assert pkg["businessIncomeIit"] is not None
    assert pkg["businessIncomeIit"]["halvingApplied"] is True
    assert pkg["businessIncomeIit"]["suggestedPayable"] == 3750.0
    assert pkg["summary"]["iitPayable"] == 3750.0
    assert any(item["taxKind"] == "business_income_iit_prepay" for item in pkg["checklist"])
    assert "核定征收" in pkg["citNotice"]["notice"]


@pytest.mark.asyncio
async def test_corporate_entity_keeps_cit(monkeypatch):
    """有限责任公司：行为不变，CIT 正常测算，Q4 保留汇算清缴清单。"""
    await _ensure_schema()
    await _create_company("c-ent-ltd", entity_type="有限责任公司")
    calls = {"count": 0}
    iit_calls = {"count": 0}
    _patch_collaborators(monkeypatch, cit_calls=calls, iit_calls=iit_calls)

    from services import filing_package_service as fps

    pkg = await fps.build_filing_package("u-ent", "c-ent-ltd", "2026-Q4")

    assert calls["count"] == 1
    assert pkg["cit"] is not None
    assert pkg["cit"]["preferentialTax"] == 50.0
    assert pkg["citNotice"]["citApplicable"] is True
    assert pkg["summary"]["citPayable"] == 50.0
    assert any(p["code"] == "cit_small_micro" for p in pkg["preferences"])
    assert any(item["taxKind"] == "cit_annual" for item in pkg["checklist"])
    # 有限公司缴企业所得税，不出经营所得个税卡
    assert iit_calls["count"] == 0
    assert pkg["businessIncomeIit"] is None
    assert pkg["summary"]["iitPayable"] == 0
    assert all(item["taxKind"] != "business_income_iit_prepay" for item in pkg["checklist"])


@pytest.mark.asyncio
async def test_non_corporate_entity_has_no_cit_deadlines():
    """个体工商户：征期日历不物化 cit / cit_annual（增值税等照常）。"""
    await _ensure_schema()
    await _create_company("c-ent-ib-dl", entity_type="个体工商户")

    from services import filing_deadline_service as svc

    await svc.refresh_deadlines("c-ent-ib-dl", year=2026)
    kinds = {r["taxKind"] for r in await svc.list_deadlines("c-ent-ib-dl")}

    assert "cit" not in kinds
    assert "cit_annual" not in kinds
    assert "vat" in kinds
    assert "iit_withholding" in kinds


@pytest.mark.asyncio
async def test_switch_to_non_corporate_waives_stock_cit_deadlines():
    """先按有限公司建档、后改为个体工商户：存量 pending 的企业所得税征期自动豁免。"""
    await _ensure_schema()
    await _create_company("c-ent-switch", entity_type="有限责任公司")

    from services import filing_deadline_service as svc

    await svc.refresh_deadlines("c-ent-switch", year=2026)
    before = {(r["taxKind"], r["period"]): r for r in await svc.list_deadlines("c-ent-switch")}
    assert any(k[0] == "cit" and v["status"] == "pending" for k, v in before.items())

    await _set_entity_type("c-ent-switch", "个体工商户")
    await svc.refresh_deadlines("c-ent-switch", year=2026)
    after = {(r["taxKind"], r["period"]): r for r in await svc.list_deadlines("c-ent-switch")}

    cit_rows = [v for k, v in after.items() if k[0] in ("cit", "cit_annual")]
    assert cit_rows, "存量行仍在（豁免而非删除）"
    assert all(row["status"] == "waived" for row in cit_rows)
