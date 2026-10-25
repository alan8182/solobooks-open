# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""资产处理确认包：建卡 + 折旧/摊销补提编排测试。"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import db.models  # noqa: F401
from db.base import Base
from db.models import FixedAsset, IntangibleAsset, JournalEntry
from services.accounting_core_service import create_entry, ensure_tenant
from services.asset_closure_service import (
    build_asset_closure_payload,
    execute_asset_closure,
)


@pytest.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _purchase(session, company_id: str, entry_id: str, category: str, account: str, amount: str):
    return await create_entry(
        session,
        {
            "id": entry_id,
            "direction": "expense",
            "amount": amount,
            "title": "购办公电脑及显示器" if account == "1601" else "购管理软件",
            "category": category,
            "occurDate": "2026-01-15",
            "dedupFingerprint": f"asset-closure:{company_id}:{entry_id}",
            "voucherAuthority": "system",
            "voucherLines": {
                "debit": [{"accountCode": account, "accountName": category, "amount": amount}],
                "credit": [{"accountCode": "1002", "accountName": "银行存款", "amount": amount}],
            },
        },
        company_id=company_id,
        user_id="owner",
        request_id="t",
        confirm=True,
    )


@pytest.mark.asyncio
async def test_execute_asset_closure_creates_cards_and_catches_up(sessions):
    async with sessions() as session:
        async with session.begin():
            await ensure_tenant(session, "c-asset-closure", "owner")
            fixed_entry = await _purchase(
                session, "c-asset-closure", "entry-fa", "固定资产", "1601", "16800.00"
            )
            intangible_entry = await _purchase(
                session, "c-asset-closure", "entry-ia", "无形资产", "1701", "120000.00"
            )
            before = (
                await session.execute(
                    select(JournalEntry).where(JournalEntry.company_id == "c-asset-closure")
                )
            ).scalars().all()

            result = await execute_asset_closure(
                session,
                {
                    "kind": "asset_closure",
                    "month": "2026-03",
                    "throughPeriod": "2026-03",
                    "fixedCards": [
                        {
                            "sourceEntryId": fixed_entry.id,
                            "name": "购办公电脑及显示器",
                            "category": "electronics",
                            "acquisitionDate": "2026-01-15",
                            "availableForUseDate": "2026-01-15",
                            "originalValue": "16800.00",
                            "usefulLifeMonths": 36,
                            "depreciationMethod": "straight_line",
                        }
                    ],
                    "intangibleCards": [
                        {
                            "sourceEntryId": intangible_entry.id,
                            "name": "购管理软件",
                            "category": "software",
                            "acquisitionDate": "2026-01-15",
                            "availableForUseDate": "2026-01-15",
                            "originalValue": "120000.00",
                            "usefulLifeMonths": 120,
                        }
                    ],
                    "runFixedDepreciation": True,
                    "runIntangibleAmortization": True,
                },
                company_id="c-asset-closure",
                user_id="owner",
                request_id="asset-closure",
            )

            assert result["fixedCards"][0]["status"] == "created"
            assert result["intangibleCards"][0]["status"] == "created"
            assert [p["period"] for p in result["fixedDepreciation"]["periods"]] == [
                "2026-02", "2026-03",
            ]
            assert [p["period"] for p in result["intangibleAmortization"]["periods"]] == [
                "2026-01", "2026-02", "2026-03",
            ]

            fixed_asset = (
                await session.execute(select(FixedAsset).where(FixedAsset.source_entry_id == fixed_entry.id))
            ).scalar_one()
            intangible_asset = (
                await session.execute(
                    select(IntangibleAsset).where(IntangibleAsset.source_entry_id == intangible_entry.id)
                )
            ).scalar_one()
            assert fixed_asset.status == "active"
            assert intangible_asset.status == "active"

            after = (
                await session.execute(
                    select(JournalEntry).where(JournalEntry.company_id == "c-asset-closure")
                )
            ).scalars().all()
            # 只新增补提凭证，不新增取得凭证
            assert len(after) == len(before) + 5


def test_build_payload_keeps_closed_periods_out_of_execution():
    preview = {
        "month": "2026-10",
        "fixedAssets": {
            "missingCards": [],
            "dueDepreciation": [{"period": "2026-09", "amount": 10, "count": 1}],
            "blockedItems": [{"kind": "fixed_depreciation_period", "period": "2026-09", "reason": "期间已关账"}],
        },
        "intangibles": {
            "missingCards": [],
            "dueAmortization": [{"period": "2026-10", "amount": 20, "count": 1}],
            "blockedItems": [],
        },
    }
    payload = build_asset_closure_payload(preview)
    assert payload["runFixedDepreciation"] is False
    assert payload["runIntangibleAmortization"] is True
    assert payload["preview"] == preview


def test_execute_asset_closure_result_is_json_safe():
    # 仅验证 Decimal 在结果中会被序列化为 JSON 基础类型。
    from services.accounting_core_service import _json_safe

    assert _json_safe({"amount": Decimal("1.23")}) == {"amount": "1.23"}
