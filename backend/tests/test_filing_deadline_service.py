# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""征期日历：建档前期间不物化/存量自动豁免 + 手动豁免闭环。"""
from __future__ import annotations

import pytest


async def _ensure_schema() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _create_company(company_id: str, created_at: str) -> None:
    from db.models import Company
    from db.session import session_scope

    async with session_scope() as session:
        session.add(
            Company(
                company_id=company_id,
                owner_user_id="u-1",
                payload={},
                created_at=created_at,
                updated_at=created_at,
            )
        )


@pytest.mark.asyncio
async def test_refresh_skips_pre_onboarding_periods():
    """建档（2026-07-08）前已截止的期间不再物化：Q1 与 1-5 月个税不出现。"""
    await _ensure_schema()
    await _create_company("c-filing-a", "2026-07-08T00:00:00+00:00")

    from services import filing_deadline_service as svc

    await svc.refresh_deadlines("c-filing-a", year=2026)
    by_key = {(r["taxKind"], r["period"]): r for r in await svc.list_deadlines("c-filing-a")}

    assert ("vat", "2026-Q1") not in by_key
    assert ("iit_withholding", "2026-01") not in by_key
    # 个税 2026-05 征期 2026-06-15 早于建档日，同样不物化
    assert ("iit_withholding", "2026-05") not in by_key
    # 建档后的期间正常物化为待申报
    assert by_key[("vat", "2026-Q2")]["status"] == "pending"


@pytest.mark.asyncio
async def test_existing_pre_onboarding_stock_auto_waived():
    """历史版本已物化的建档前 pending 存量，refresh 时自动豁免；已办结的不受影响。"""
    await _ensure_schema()
    await _create_company("c-filing-b", "2026-07-08T00:00:00+00:00")

    from datetime import date

    from db.models import FilingDeadline, new_id
    from db.session import session_scope

    async with session_scope() as session:
        session.add(
            FilingDeadline(
                id=new_id(), company_id="c-filing-b", tax_kind="vat",
                period="2026-Q1", due_date=date(2026, 4, 20), status="pending",
            )
        )
        session.add(
            FilingDeadline(
                id=new_id(), company_id="c-filing-b", tax_kind="iit_withholding",
                period="2026-03", due_date=date(2026, 4, 15), status="filed",
            )
        )

    from services import filing_deadline_service as svc

    result = await svc.refresh_deadlines("c-filing-b", year=2026)
    by_key = {(r["taxKind"], r["period"]): r for r in await svc.list_deadlines("c-filing-b")}

    assert result["autoWaived"] == 1
    assert by_key[("vat", "2026-Q1")]["status"] == "waived"
    assert by_key[("iit_withholding", "2026-03")]["status"] == "filed"
    # 被豁免的那条不再注入催办待办
    assert svc.deadline_todos([by_key[("vat", "2026-Q1")]]) == []


@pytest.mark.asyncio
async def test_waive_deadline_only_from_pending():
    """手动豁免：pending 可豁免；已豁免/已办结的不能再豁免。"""
    await _ensure_schema()
    await _create_company("c-filing-c", "2026-01-01T00:00:00+00:00")

    from services import filing_deadline_service as svc

    await svc.refresh_deadlines("c-filing-c", year=2026)
    target = next(
        r for r in await svc.list_deadlines("c-filing-c")
        if r["taxKind"] == "vat" and r["period"] == "2026-Q1"
    )

    waived = await svc.waive_deadline("c-filing-c", "u-1", target["id"], reason="pre_period")
    assert waived["status"] == "waived"

    with pytest.raises(ValueError):
        await svc.waive_deadline("c-filing-c", "u-1", target["id"])
