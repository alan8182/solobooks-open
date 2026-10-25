# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""申报周期与结账周期解耦：增值税月报/季报独立、周期切换清理、本期义务推导。

关键不变量：
- 增值税申报周期由 vatFilingCycle 决定，与经营所得个税周期互不影响；
- 周期切换后，旧周期格式的 pending 征期自动豁免，已申报的不动；
- 结账始终按月（会计期间不变），申报期只是"汇总口径"。
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
    payload: dict | None = None,
    created_at: str = "2026-01-01T00:00:00+00:00",
) -> None:
    from db.models import Company
    from db.session import session_scope

    async with session_scope() as session:
        session.add(
            Company(
                company_id=company_id,
                owner_user_id="u-cycle",
                payload=payload or {},
                created_at=created_at,
                updated_at=created_at,
            )
        )


async def _set_payload(company_id: str, **updates) -> None:
    from db.models import Company
    from db.session import session_scope

    async with session_scope() as session:
        row = await session.get(Company, company_id)
        row.payload = {**(row.payload or {}), **updates}


async def _deadlines(company_id: str) -> dict:
    from services import filing_deadline_service as svc

    return {(r["taxKind"], r["period"]): r for r in await svc.list_deadlines(company_id)}


@pytest.mark.asyncio
async def test_vat_monthly_cycle_materializes_months_not_quarters():
    """vatFilingCycle=month：物化 12 个月度征期，不再出现季度征期。"""
    await _ensure_schema()
    await _create_company("c-cycle-vat-month", {"vatFilingCycle": "month"})

    from services import filing_deadline_service as svc

    await svc.refresh_deadlines("c-cycle-vat-month", year=2026)
    by_key = await _deadlines("c-cycle-vat-month")

    assert ("vat", "2026-09") in by_key
    assert ("vat", "2026-Q3") not in by_key
    # 月度申报期：次月 15 日（2026-10 遇国庆长假顺延至 10-22）
    assert by_key[("vat", "2026-09")]["dueDate"] == "2026-10-22"


@pytest.mark.asyncio
async def test_vat_quarterly_is_default():
    """未配置申报周期时按季报（税局对小规模的默认口径）。"""
    await _ensure_schema()
    await _create_company("c-cycle-vat-default")

    from services import filing_deadline_service as svc

    result = await svc.refresh_deadlines("c-cycle-vat-default", year=2026)
    by_key = await _deadlines("c-cycle-vat-default")

    assert result["cycles"]["vat"] == "quarter"
    assert ("vat", "2026-Q3") in by_key
    assert ("vat", "2026-09") not in by_key


@pytest.mark.asyncio
async def test_cycle_switch_waives_old_pending_periods():
    """季报改月报：季度 pending 自动豁免，月度期间接管，已申报记录不受影响。"""
    await _ensure_schema()
    await _create_company("c-cycle-switch")

    from services import filing_deadline_service as svc

    await svc.refresh_deadlines("c-cycle-switch", year=2026)
    before = await _deadlines("c-cycle-switch")
    assert before[("vat", "2026-Q3")]["status"] == "pending"

    # 模拟 Q2 已申报
    await svc.mark_filed(
        "c-cycle-switch", "u-cycle", before[("vat", "2026-Q2")]["id"], evidence={"note": "已申报"}
    )

    await _set_payload("c-cycle-switch", vatFilingCycle="month")
    await svc.refresh_deadlines("c-cycle-switch", year=2026)
    after = await _deadlines("c-cycle-switch")

    assert after[("vat", "2026-Q3")]["status"] == "waived"
    assert after[("vat", "2026-09")]["status"] == "pending"
    # 已申报的不被清理
    assert after[("vat", "2026-Q2")]["status"] == "filed"

    # 当年改回季报：季度待办恢复为 pending，月度被豁免，不会永久停在「已豁免」
    await _set_payload("c-cycle-switch", vatFilingCycle="quarter")
    await svc.refresh_deadlines("c-cycle-switch", year=2026)
    restored = await _deadlines("c-cycle-switch")

    assert restored[("vat", "2026-Q3")]["status"] == "pending"
    assert restored[("vat", "2026-09")]["status"] == "waived"
    assert restored[("vat", "2026-Q2")]["status"] == "filed"


@pytest.mark.asyncio
async def test_vat_and_iit_cycles_are_independent():
    """增值税月报 + 经营所得季报能共存：两个字段互不覆盖。"""
    await _ensure_schema()
    await _create_company(
        "c-cycle-mixed",
        {"type": "个体工商户", "vatFilingCycle": "month", "businessIncomeIitFilingCycle": "quarter"},
    )

    from services import filing_deadline_service as svc

    result = await svc.refresh_deadlines("c-cycle-mixed", year=2026)
    by_key = await _deadlines("c-cycle-mixed")

    assert result["cycles"] == {"vat": "month", "business_income_iit_prepay": "quarter"}
    assert ("vat", "2026-08") in by_key
    assert ("business_income_iit_prepay", "2026-Q3") in by_key
    assert ("business_income_iit_prepay", "2026-08") not in by_key


@pytest.mark.asyncio
async def test_obligations_follow_vat_cycle():
    """结账月份 → 本期申报义务：月报指向当月，季报指向所在季度。"""
    await _ensure_schema()
    await _create_company("c-cycle-ob-month", {"vatFilingCycle": "month"})
    await _create_company("c-cycle-ob-quarter", {"vatFilingCycle": "quarter"})

    from services import filing_deadline_service as svc

    monthly = await svc.obligations_for_month("c-cycle-ob-month", "2026-09")
    quarterly = await svc.obligations_for_month("c-cycle-ob-quarter", "2026-09")

    vat_month = next(o for o in monthly["obligations"] if o["taxKind"] == "vat")
    vat_quarter = next(o for o in quarterly["obligations"] if o["taxKind"] == "vat")
    assert vat_month["period"] == "2026-09"
    assert vat_month["cycle"] == "month"
    assert vat_quarter["period"] == "2026-Q3"
    assert vat_quarter["cycle"] == "quarter"
    assert monthly["vatCycleLabel"] == "月报"
    assert monthly["quarter"] == "2026-Q3"


@pytest.mark.asyncio
async def test_obligations_respect_entity_type():
    """有限公司不出现经营所得 A 表；个体工商户不出现企业所得税。"""
    await _ensure_schema()
    await _create_company("c-cycle-ltd", {"type": "有限责任公司"})
    await _create_company("c-cycle-ind", {"type": "个体工商户"})

    from services import filing_deadline_service as svc

    ltd = await svc.obligations_for_month("c-cycle-ltd", "2026-09")
    ind = await svc.obligations_for_month("c-cycle-ind", "2026-09")
    ltd_kinds = {o["taxKind"] for o in ltd["obligations"]}
    ind_kinds = {o["taxKind"] for o in ind["obligations"]}

    assert "vat" in ltd_kinds
    assert "vat" in ind_kinds
    assert "cit" in ltd_kinds
    assert "business_income_iit_prepay" not in ltd_kinds
    assert "cit" not in ind_kinds
    assert "business_income_iit_prepay" in ind_kinds
