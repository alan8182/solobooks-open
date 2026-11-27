# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks license v2.0.

"""主体注册地（province / city）。

注册地决定「去电子税务局」跳到哪个省的站点，也参与社保费率口径匹配。
它存在 auth_companies.payload 里，不需要迁移；这里锁住两条：
- 新建主体时随 extra 落库（新建表单必填，后端不强制，兼容老客户端）；
- 编辑时能写入、也能清空（清空后前端回落省份地图，不能 500）。
"""
from __future__ import annotations

import uuid


async def _ensure_schema() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _cleanup(company_id: str) -> None:
    from sqlalchemy import delete

    from db.models import Company
    from db.session import session_scope

    async with session_scope() as session:
        await session.execute(delete(Company).where(Company.company_id == company_id))


async def test_company_region_is_created_and_updated():
    await _ensure_schema()
    from services.account_service import create_company, get_company, update_company

    owner = f"u-region-{uuid.uuid4().hex[:8]}"
    company = await create_company(
        owner,
        "注册地测试主体",
        {"taxId": "91330100MA2TEST001", "province": "浙江省", "city": "宁波市"},
    )
    company_id = company["company_id"]
    try:
        assert company["province"] == "浙江省"
        assert company["city"] == "宁波市"

        updated = await update_company(owner, company_id, {"province": "广东省", "city": "深圳市"})
        assert updated["province"] == "广东省"
        assert updated["city"] == "深圳市"
        assert (await get_company(company_id))["city"] == "深圳市"

        # 只改名称时不得顺手清掉注册地
        renamed = await update_company(owner, company_id, {"name": "注册地测试主体（改名）"})
        assert renamed["province"] == "广东省"
        assert renamed["city"] == "深圳市"

        # 清空：前端「编辑主体」允许改回未设置，落成空串而不是残留旧省
        cleared = await update_company(owner, company_id, {"province": "", "city": ""})
        assert cleared["province"] == ""
        assert cleared["city"] == ""
    finally:
        await _cleanup(company_id)
