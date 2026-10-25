# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""从业人数派生：名册口径（is_active）与主体类型下限。"""
from __future__ import annotations

import pytest


async def _ensure_schema() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine
    from services.salary_service import _ensure_db_ready

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    # employees 表由 salary_service 的原生 SQL 建表，不在 ORM metadata 里
    await _ensure_db_ready()


async def _create_company(company_id: str, entity_type: str | None = None) -> None:
    from db.models import Company
    from db.session import session_scope

    payload = {"type": entity_type} if entity_type else {}
    async with session_scope() as session:
        session.add(
            Company(
                company_id=company_id,
                owner_user_id="u-hc",
                payload=payload,
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )


async def _add_employee(company_id: str, name: str) -> str:
    from services.salary_service import create_employee

    row = await create_employee(company_id, name, "运营", "8000", "0")
    return str(row["id"])


@pytest.mark.asyncio
async def test_archived_employees_not_counted():
    """归档（is_active=0）员工不计入在职人数。"""
    await _ensure_schema()
    await _create_company("c-hc-active", "有限责任公司")
    await _add_employee("c-hc-active", "张三")
    await _add_employee("c-hc-active", "李四")
    archived = await _add_employee("c-hc-active", "王五")

    from services.headcount_service import get_headcount
    from services.salary_service import archive_employee

    await archive_employee("c-hc-active", archived)
    hc = await get_headcount("c-hc-active")

    assert hc.raw == 2
    assert hc.count == 2
    assert hc.is_corporate is True
    assert hc.needs_employee_setup is False


@pytest.mark.asyncio
async def test_corporate_floor_is_one():
    """公司制名册为空：税务口径仍按 1 人（法定代表人），并标记需录入员工。"""
    await _ensure_schema()
    await _create_company("c-hc-ltd-empty", "有限责任公司")

    from services.headcount_service import get_headcount

    hc = await get_headcount("c-hc-ltd-empty")

    assert hc.raw == 0
    assert hc.count == 1
    assert hc.needs_employee_setup is True


@pytest.mark.asyncio
async def test_individual_business_allows_zero():
    """个人独资/个体工商户无雇工：允许 0 人，不提示录入员工。"""
    await _ensure_schema()
    await _create_company("c-hc-solo-empty", "个体工商户")

    from services.headcount_service import get_headcount

    hc = await get_headcount("c-hc-solo-empty")

    assert hc.raw == 0
    assert hc.count == 0
    assert hc.is_corporate is False
    assert hc.needs_employee_setup is False


@pytest.mark.asyncio
async def test_unknown_entity_type_falls_back_to_corporate():
    """主体类型缺省/脏值按公司制兜底，保持存量行为。"""
    await _ensure_schema()
    await _create_company("c-hc-unknown")

    from services.headcount_service import get_headcount

    hc = await get_headcount("c-hc-unknown")
    assert hc.entity_type == "ltd"
    assert hc.count == 1


@pytest.mark.asyncio
async def test_explicit_entity_type_skips_lookup():
    """调用方传入主体类型时按传入值计算（避免重复查库）。"""
    from services.headcount_service import get_headcount

    hc = await get_headcount("c-hc-not-exist", "个体工商户")
    assert hc.raw == 0
    assert hc.count == 0
