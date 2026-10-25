# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""个税扣缴提示：按主体类型分叉，公司制 0 人引导录入员工。"""
from __future__ import annotations

import pytest


def test_iit_hint_variants():
    from services.filing_package_service import _iit_hint
    from services.headcount_service import Headcount

    hint, needs = _iit_hint(Headcount(0, 1, "ltd", True), "有限责任公司")
    assert needs is True
    assert "法定代表人" in hint

    hint, needs = _iit_hint(Headcount(2, 2, "ltd", True), "有限责任公司")
    assert needs is False
    assert "按月申报" in hint

    hint, needs = _iit_hint(Headcount(0, 0, "individual_business", False), "个体工商户")
    assert needs is False
    assert "无雇工" in hint

    hint, needs = _iit_hint(Headcount(1, 1, "sole_proprietorship", False), "个人独资企业")
    assert needs is False
    assert "有雇工" in hint

    hint, needs = _iit_hint(None, "ltd")
    assert needs is False
    assert "无法读取" in hint


@pytest.mark.asyncio
async def test_checklist_item_carries_employee_setup_action(monkeypatch):
    """作业包清单项带结构化字段，供前端渲染「去录入员工」。"""
    from tests.test_filing_package_entity_type import (
        _create_company,
        _ensure_schema,
        _patch_collaborators,
    )

    await _ensure_schema()
    await _create_company("c-iit-hint", entity_type="有限责任公司")
    _patch_collaborators(monkeypatch, cit_calls={"count": 0})

    from services import filing_package_service as fps
    from services.headcount_service import Headcount

    async def fake_headcount(*_args, **_kwargs):
        return Headcount(raw=0, count=1, entity_type="ltd", is_corporate=True)

    monkeypatch.setattr(fps.headcount_service, "get_headcount", fake_headcount)

    pkg = await fps.build_filing_package("u-iit", "c-iit-hint", "2026-Q2")
    item = next(i for i in pkg["checklist"] if i["taxKind"] == "iit_withholding")

    assert item["needsEmployeeSetup"] is True
    assert item["actionPath"] == "/mine/salary"
    assert item["actionLabel"] == "去录入员工"
    assert "法定代表人" in item["hint"]


@pytest.mark.asyncio
async def test_checklist_item_individual_business_has_no_action(monkeypatch):
    from tests.test_filing_package_entity_type import (
        _create_company,
        _ensure_schema,
        _patch_collaborators,
    )

    await _ensure_schema()
    await _create_company("c-iit-hint-ib", entity_type="个体工商户")
    _patch_collaborators(monkeypatch, cit_calls={"count": 0})

    from services import filing_package_service as fps
    from services.headcount_service import Headcount

    async def fake_headcount(*_args, **_kwargs):
        return Headcount(raw=0, count=0, entity_type="individual_business", is_corporate=False)

    monkeypatch.setattr(fps.headcount_service, "get_headcount", fake_headcount)

    pkg = await fps.build_filing_package("u-iit", "c-iit-hint-ib", "2026-Q2")
    item = next(i for i in pkg["checklist"] if i["taxKind"] == "iit_withholding")

    assert item["needsEmployeeSetup"] is False
    assert item["actionPath"] is None
    assert "无雇工" in item["hint"]
