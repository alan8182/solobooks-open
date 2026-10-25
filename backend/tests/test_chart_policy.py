# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""chart_policy 单一口径的守护测试。

背景：seed_accounts 的 upsert 曾用 ``info.get("is_leaf")`` 直读 COA JSON，
与迁移 / sync 脚本的 CONTAINER_CODES 口径漂移，导致存量租户每次
``ensure_tenant`` 都把 5602/5601/5603/5401/1101/1012 翻成非末级，
期初余额、recurring、期末结转、历史凭证冲销全部 422。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from core.chart_policy import CONTAINER_CODES, chart_is_leaf
from db.base import Base
from db.models import ChartAccount
import db.models  # noqa: F401
from services.accounting_core_service import ensure_tenant

CHART_JSON = Path(__file__).resolve().parent.parent / "data" / "chart_of_accounts.json"

# 纯容器：父级不能直接记账
CONTAINERS = ("1221", "2211", "2221")
# 4 位总账：即使 JSON 声明 isLeaf=false 也必须保持末级（历史被直接记账）
FOUR_DIGIT_PARENTS = ("5602", "5601", "5603", "5401", "1101", "1012", "1001", "1002")
# 本次新增/启用的明细末级
NEW_LEAVES = (
    "122101", "122102", "122103",
    "221101", "221102", "221103", "221104",
    "2221012",
    "5602001", "5602014", "5602016",
)


def _load_coa() -> dict[str, dict]:
    doc = json.loads(CHART_JSON.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for _group, accounts in doc.get("accounts", {}).items():
        for code, info in accounts.items():
            out[str(code)] = info
    return out


def test_chart_is_leaf_policy_rules():
    """三态判定：容器→False；4 位总账→True；明细按声明。"""
    assert CONTAINER_CODES == {"1221", "2211", "2221"}
    # 容器：即使 JSON 未声明 isLeaf 也是非末级
    assert chart_is_leaf("1221", {"name": "其他应收款"}) is False
    assert chart_is_leaf("2211", {"name": "应付职工薪酬"}) is False
    assert chart_is_leaf("2221", {"name": "应交税费"}) is False
    # 4 位总账：即使 JSON 明确声明 isLeaf=false 也强制末级
    assert chart_is_leaf("5602", {"isLeaf": False}) is True
    assert chart_is_leaf("5601", {"isLeaf": False}) is True
    assert chart_is_leaf("1012", {"isLeaf": False}) is True
    # 4 位总账：JSON 未声明时同样末级
    assert chart_is_leaf("1001", {}) is True
    # 明细科目：按 JSON 声明，缺省 True
    assert chart_is_leaf("5602016", {"isLeaf": True}) is True
    assert chart_is_leaf("221101", {"isLeaf": True}) is True
    assert chart_is_leaf("221101", {}) is True
    # 明细科目显式非末级时尊重声明（未来若再拆子级）
    assert chart_is_leaf("5602", {}) is True  # 4 位优先，见上
    assert chart_is_leaf("999999", {"isLeaf": False}) is False


def test_chart_policy_against_full_coa():
    """对 COA 全量科目跑一遍，锁定容器集合与 4 位总账集合。"""
    coa = _load_coa()
    assert coa, "chart_of_accounts.json 应能解析出科目"
    for code in CONTAINERS:
        assert code in coa, f"{code} 应在 COA 中"
        assert chart_is_leaf(code, coa[code]) is False, f"{code} 必须是纯容器"
    for code in FOUR_DIGIT_PARENTS:
        assert code in coa, f"{code} 应在 COA 中"
        assert chart_is_leaf(code, coa[code]) is True, f"4 位总账 {code} 必须保持末级"
    for code in NEW_LEAVES:
        assert code in coa, f"{code} 应在 COA 中"
        assert chart_is_leaf(code, coa[code]) is True, f"明细科目 {code} 必须是末级"


@pytest.fixture
async def policy_sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_seed_accounts_leaf_flags_follow_chart_policy(policy_sessions):
    """新建租户：seed 出来的 is_leaf 与 chart_policy 完全一致。"""
    async with policy_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-policy-seed", "owner-policy-seed")
    async with policy_sessions() as session:
        async with session.begin():
            rows = {
                row.code: row
                for row in (
                    await session.execute(
                        select(ChartAccount).where(ChartAccount.company_id == "c-policy-seed")
                    )
                ).scalars().all()
            }
    for code in CONTAINERS:
        assert rows[code].is_leaf is False, f"{code} 应为容器"
    for code in FOUR_DIGIT_PARENTS + NEW_LEAVES:
        assert rows[code].is_leaf is True, f"{code} 应为末级"


@pytest.mark.asyncio
async def test_second_ensure_tenant_does_not_flip_four_digit_parents(policy_sessions):
    """存量租户反复 ensure_tenant：4 位总账不被翻成非末级，用户停用不被重新启用。

    这是 A 问题的回归护栏：upsert 曾按 JSON isLeaf 直写，5602 等父级每次请求
    都会被翻掉，期初余额 / 结转 / 冲销随后全部 422。
    """
    company_id, user_id = "c-policy-existing", "owner-policy-existing"
    async with policy_sessions() as session:
        async with session.begin():
            await ensure_tenant(session, company_id, user_id)
    # 模拟存量库：用户手工停用了某个科目，且 5602 曾被人直接记账
    async with policy_sessions() as session:
        async with session.begin():
            row = (
                await session.execute(
                    select(ChartAccount).where(
                        ChartAccount.company_id == company_id, ChartAccount.code == "5602001"
                    )
                )
            ).scalars().one()
            row.is_active = False
            await session.flush()
    # 第二次（及第三次）ensure_tenant：幂等，不翻转 4 位父级，不重启用停用科目
    for _ in range(2):
        async with policy_sessions() as session:
            async with session.begin():
                await ensure_tenant(session, company_id, user_id)
    async with policy_sessions() as session:
        async with session.begin():
            rows = {
                row.code: row
                for row in (
                    await session.execute(
                        select(ChartAccount).where(ChartAccount.company_id == company_id)
                    )
                ).scalars().all()
            }
    for code in FOUR_DIGIT_PARENTS:
        assert rows[code].is_leaf is True, f"{code} 反复 ensure_tenant 后必须仍是末级"
    for code in CONTAINERS:
        assert rows[code].is_leaf is False, f"{code} 反复 ensure_tenant 后必须仍是容器"
    assert rows["5602001"].is_active is False, "用户停用的科目不得被重新启用"
