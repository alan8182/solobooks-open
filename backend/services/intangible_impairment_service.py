# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""无形资产减值测试：迹象评估与测试留痕（P2）。

设计方案：《无形资产模块设计方案.md》第 3.4 节、第 4 章④、第 12 章。
- 《小企业会计准则》资产按成本计量、**不计提减值准备**（provision_amount 恒 0，
  DB CHECK 已锁），因此本服务**不生成任何凭证**；
- `conclusion = impairment_noted` 的记录会在月结检查项 IMPAIRMENT_TEST_DUE
  与台账中高亮，并建议发起年限变更（未来适用法）；
- `carrying_amount` 由系统带出（原值 − 累计摊销），不由客户端上报。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountingError, NOT_FOUND
from core.money import money, money_text
from db.models import IntangibleAsset, IntangibleImpairmentTest
from services.accounting_core_service import (
    POSTING_ROLES,
    WRITE_ROLES,
    ensure_tenant,
    record_audit,
    require_role,
)
from services.amortization_engine import _validate_period
from services.intangible_asset_service import _parse_date

TRIGGER_LABELS = {
    "tech_obsolete": "技术过时",
    "legal_invalid": "法律失效",
    "market_decline": "市场环境恶化",
    "idle_long": "长期闲置",
    "other": "其他",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def serialize_test(test: IntangibleImpairmentTest, asset: IntangibleAsset | None = None) -> dict[str, Any]:
    return {
        "id": test.id,
        "assetId": test.asset_id,
        "assetCode": asset.code if asset else "",
        "assetName": asset.name if asset else "",
        "testPeriod": test.test_period,
        "testDate": test.test_date.isoformat(),
        "triggerType": test.trigger_type,
        "triggerLabel": TRIGGER_LABELS.get(test.trigger_type, test.trigger_type),
        "indicatorDescription": test.indicator_description,
        "carryingAmount": money(test.carrying_amount),
        "recoverableAmount": money(test.recoverable_amount),
        "conclusion": test.conclusion,
        "provisionAmount": money(test.provision_amount),
        "evidenceRef": test.evidence_ref,
        "assessedBy": test.assessed_by,
        "reviewedBy": test.reviewed_by,
        "reviewedAt": test.reviewed_at.isoformat() if test.reviewed_at else None,
        "createdAt": test.created_at.isoformat() if test.created_at else "",
    }


async def _get_test(session: AsyncSession, test_id: str, company_id: str) -> IntangibleImpairmentTest:
    test = (
        await session.execute(
            select(IntangibleImpairmentTest).where(
                IntangibleImpairmentTest.id == test_id,
                IntangibleImpairmentTest.company_id == company_id,
            )
        )
    ).scalar_one_or_none()
    if test is None:
        raise AccountingError(NOT_FOUND, "减值测试记录不存在", 404)
    return test


async def create_test(
    session: AsyncSession,
    payload: dict[str, Any],
    *,
    company_id: str,
    user_id: str,
    request_id: str,
) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    await require_role(session, company_id, user_id, WRITE_ROLES)
    asset = (
        await session.execute(
            select(IntangibleAsset).where(
                IntangibleAsset.id == str(payload.get("assetId") or ""),
                IntangibleAsset.company_id == company_id,
                IntangibleAsset.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if asset is None:
        raise AccountingError(NOT_FOUND, "无形资产不存在", 404)
    if asset.status not in ("active", "idle", "fully_amortized"):
        raise AccountingError(
            "ASSET_NOT_TESTABLE", "仅摊销中 / 已停用 / 已摊完的无形资产可以做减值评估", 409
        )

    test_period = _validate_period(payload.get("testPeriod"))
    test_date = _parse_date(payload.get("testDate"), field="测试日期")
    trigger_type = str(payload.get("triggerType") or "")
    if trigger_type not in TRIGGER_LABELS:
        raise AccountingError("INVALID_TRIGGER_TYPE", "无效的减值迹象类型", 422)
    description = str(payload.get("indicatorDescription") or "").strip()
    if not description:
        raise AccountingError("INDICATOR_DESCRIPTION_REQUIRED", "请填写减值迹象描述", 422)
    recoverable = money(payload.get("recoverableAmount"), allow_negative=False)
    carrying = money(money(asset.original_value) - money(asset.accumulated_amortization or 0))
    conclusion = "impairment_noted" if recoverable < carrying else "no_impairment"

    duplicate = (
        await session.execute(
            select(IntangibleImpairmentTest.id).where(
                IntangibleImpairmentTest.company_id == company_id,
                IntangibleImpairmentTest.asset_id == asset.id,
                IntangibleImpairmentTest.test_period == test_period,
            )
        )
    ).scalar_one_or_none()
    if duplicate:
        raise AccountingError(
            "IMPAIRMENT_TEST_DUPLICATE",
            f"{asset.code} 在 {test_period} 已存在减值测试记录",
            409,
            {"impairmentTestId": duplicate},
        )

    test = IntangibleImpairmentTest(
        company_id=company_id,
        asset_id=asset.id,
        test_period=test_period,
        test_date=test_date,
        trigger_type=trigger_type,
        indicator_description=description,
        carrying_amount=carrying,
        recoverable_amount=recoverable,
        conclusion=conclusion,
        provision_amount=money(0),  # 小企业会计准则：不计提减值准备
        evidence_ref=str(payload.get("evidenceRef") or "").strip() or None,
        assessed_by=user_id,
        created_at=_now(),
    )
    session.add(test)
    await session.flush()
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="intangible.impairment.create",
        entity_type="intangible_impairment_test",
        entity_id=test.id,
        request_id=request_id,
        after=serialize_test(test, asset),
    )
    result = serialize_test(test, asset)
    if conclusion == "impairment_noted":
        result["suggestion"] = (
            "存在减值迹象：建议复核使用年限并发起变更（未来适用法）。"
            "小企业会计准则不计提减值准备，损失将在处置/核销时通过营业外支出体现。"
        )
    return result


async def review_test(
    session: AsyncSession, test_id: str, *, company_id: str, user_id: str, request_id: str
) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    await require_role(session, company_id, user_id, POSTING_ROLES)
    test = await _get_test(session, test_id, company_id)
    if test.reviewed_by:
        raise AccountingError("IMPAIRMENT_TEST_REVIEWED", "该记录已复核", 409)
    before = {"conclusion": test.conclusion, "reviewedBy": None}
    test.reviewed_by = user_id
    test.reviewed_at = _now()
    await session.flush()
    asset = await session.get(IntangibleAsset, test.asset_id)
    await record_audit(
        session,
        company_id=company_id,
        user_id=user_id,
        action="intangible.impairment.review",
        entity_type="intangible_impairment_test",
        entity_id=test.id,
        request_id=request_id,
        before=before,
        after={"reviewedBy": user_id},
    )
    result = serialize_test(test, asset)
    if test.conclusion == "impairment_noted":
        result["suggestion"] = (
            "复核确认存在减值迹象：建议发起年限变更单（未来适用法），缩短剩余期间摊销。"
        )
    return result


async def list_tests(
    session: AsyncSession, *, company_id: str, user_id: str, asset_id: str | None = None
) -> dict[str, Any]:
    await ensure_tenant(session, company_id, user_id)
    query = (
        select(IntangibleImpairmentTest, IntangibleAsset)
        .join(IntangibleAsset, IntangibleAsset.id == IntangibleImpairmentTest.asset_id)
        .where(IntangibleImpairmentTest.company_id == company_id)
        .order_by(IntangibleImpairmentTest.test_period.desc(), IntangibleImpairmentTest.created_at.desc())
    )
    if asset_id:
        query = query.where(IntangibleImpairmentTest.asset_id == asset_id)
    rows = (await session.execute(query)).all()
    items = [serialize_test(t, a) for t, a in rows]
    return {
        "items": items,
        "unreviewedCount": sum(1 for t in items if not t["reviewedBy"] and t["conclusion"] == "impairment_noted"),
    }
