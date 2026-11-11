# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""税号硬校验：申报作业包与对外报送报表都不允许税号为空。

建账入口（新增主体表单、对话建账卡）已把税号改成必填；这里守的是存量主体
和任何绕过入口产生的无税号数据——生成申报作业包 / 导出报送 XLS 前仍然硬拦，
否则表头会是空的，用户在电子税务局上传后才发现。
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


async def _create_company(company_id: str, payload: dict) -> None:
    from db.models import Company
    from db.session import session_scope

    created_at = "2026-01-01T00:00:00+00:00"
    async with session_scope() as session:
        session.add(
            Company(
                company_id=company_id,
                owner_user_id="u-taxid",
                payload=payload,
                created_at=created_at,
                updated_at=created_at,
            )
        )


@pytest.mark.asyncio
async def test_filing_package_blocks_when_tax_id_missing():
    await _ensure_schema()
    await _create_company("c-no-taxid", {"name": "未填税号公司"})

    from services import filing_package_service as fps

    with pytest.raises(ValueError, match="统一社会信用代码"):
        await fps.build_filing_package("u-taxid", "c-no-taxid", "2026-Q3")


def test_statement_checks_treat_missing_tax_id_as_blocking_error():
    from services.tax_filing_export_service import (
        build_statement_data_from_entries,
        parse_period,
    )

    data = build_statement_data_from_entries(
        [],
        [],
        parse_period("2026-Q3", "quarter"),
        {"name": "未填税号公司"},
    )

    tax_id_check = next(c for c in data.checks if "统一社会信用代码" in c["message"])
    assert tax_id_check["level"] == "error"
