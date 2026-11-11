# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""后台可维护行业模板的契约与建账科目回归测试。"""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from db.base import Base
from db.models import ChartAccount, Company, IndustryTemplate
import db.models  # noqa: F401
from services.accounting_core_service import ensure_tenant
from services.industry_template_service import (
    IndustryTemplateValues,
    create_template,
    list_templates,
    seed_industry_templates,
    update_template,
)
from services.token_service import create_access_token
from tests.conftest import TEST_CORE_DB


@pytest.fixture
async def template_sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _template_payload(
    *,
    code: str,
    name: str,
    enabled: bool = True,
) -> IndustryTemplateValues:
    return IndustryTemplateValues.model_validate(
        {
            "code": code,
            "name": name,
            "groupCode": "grp_test",
            "groupName": "测试行业",
            "description": "后台维护测试模板",
            "restrictedDefault": False,
            "hidden": True,
            "enabled": enabled,
            "sortOrder": 900000,
            "config": {
                "accountPresets": [
                    {
                        "code": "5602998",
                        "name": "测试行业服务费",
                        "parentCode": "5602",
                        "type": "费用",
                        "category": "损益",
                        "direction": "debit",
                        "isLeaf": True,
                    }
                ],
                "modules": ["测试模块"],
                "notes": ["仅用于测试"],
            },
        }
    )


@pytest.mark.asyncio
async def test_seed_catalog_and_backend_updates_are_versioned(template_sessions):
    async with template_sessions() as session:
        async with session.begin():
            rows = await list_templates(
                session,
                include_hidden=True,
                include_disabled=True,
            )
            by_code = {row.code: row for row in rows}

            assert len(rows) == 91
            assert by_code["ind_other"].name == "其他行业"
            assert len(by_code["ind___09_03"].config["accountPresets"]) == 3

            software = by_code["ind___09_03"]
            values = IndustryTemplateValues.model_validate(
                {
                    "code": software.code,
                    "name": "软件服务（后台维护）",
                    "groupCode": software.group_code,
                    "groupName": software.group_name,
                    "description": "管理员修改",
                    "restrictedDefault": software.restricted_default,
                    "hidden": software.hidden,
                    "enabled": software.enabled,
                    "sortOrder": software.sort_order,
                    "config": software.config,
                }
            )
            updated = await update_template(
                session,
                software.id,
                values,
                user_id="admin-template",
            )
            assert updated is not None
            assert updated.version == 2

            # 再次 seed 不能覆盖管理员已修改的内置模板。
            await seed_industry_templates(session)
            refreshed = await session.get(IndustryTemplate, software.id)
            assert refreshed.name == "软件服务（后台维护）"
            assert refreshed.version == 2


@pytest.mark.asyncio
async def test_template_code_is_immutable_after_creation(template_sessions):
    async with template_sessions() as session:
        async with session.begin():
            created = await create_template(
                session,
                _template_payload(code="ind_test_custom", name="测试自定义行业"),
                user_id="admin-template",
            )
            values = _template_payload(code="ind_test_renamed", name="改名行业")
            with pytest.raises(ValueError, match="不可修改"):
                await update_template(
                    session,
                    created.id,
                    values,
                    user_id="admin-template",
                )


@pytest.mark.asyncio
async def test_industry_presets_seed_empty_new_accounts(template_sessions):
    company_id = "c-industry-software"
    async with template_sessions() as session:
        async with session.begin():
            session.add(
                Company(
                    company_id=company_id,
                    owner_user_id="u-industry-owner",
                    payload={
                        "name": "软件测试公司",
                        "industry": "软件和信息技术服务业",
                        "industryCode": "ind___09_03",
                        "industryTemplateVersion": 1,
                    },
                    created_at="2026-01-01T00:00:00+00:00",
                    updated_at="",
                )
            )
            await session.flush()
            await ensure_tenant(session, company_id, "u-industry-owner")

    async with template_sessions() as session:
        rows = {
            row.code: row
            for row in (
                await session.execute(
                    select(ChartAccount).where(ChartAccount.company_id == company_id)
                )
            )
            .scalars()
            .all()
        }
        assert {"5001003", "5602201", "5602202"}.issubset(rows)
        assert rows["5001003"].name == "软件服务收入"
        assert rows["5602201"].name == "云服务与接口费"
        assert rows["5602202"].name == "软件订阅费"


@pytest.mark.asyncio
async def test_industry_presets_do_not_backfill_existing_accounts(template_sessions):
    company_id = "c-industry-existing"
    async with template_sessions() as session:
        async with session.begin():
            session.add(
                Company(
                    company_id=company_id,
                    owner_user_id="u-industry-owner",
                    payload={
                        "name": "已有软件测试公司",
                        "industry": "软件和信息技术服务业",
                        "industryCode": "ind___09_03",
                        "industryTemplateVersion": 1,
                    },
                    created_at="2026-01-01T00:00:00+00:00",
                    updated_at="",
                )
            )
            await session.flush()
            session.add(
                ChartAccount(
                    id=f"acct-{company_id}-5602201",
                    company_id=company_id,
                    code="5602201",
                    name="企业自有云服务科目",
                    parent_code="5602",
                    level=3,
                    category="损益",
                    type="费用",
                    normal_side="debit",
                    is_leaf=True,
                    is_system=False,
                    is_active=True,
                    created_at="2026-01-01T00:00:00+00:00",
                    updated_at="2026-01-01T00:00:00+00:00",
                )
            )
            await session.flush()
            await ensure_tenant(session, company_id, "u-industry-owner")

    async with template_sessions() as session:
        rows = {
            row.code: row
            for row in (
                await session.execute(
                    select(ChartAccount).where(ChartAccount.company_id == company_id)
                )
            )
            .scalars()
            .all()
        }
        assert rows["5602201"].name == "企业自有云服务科目"
        assert rows["5602201"].is_system is False
        assert "5001003" not in rows
        assert "5602202" not in rows


def _seed_api_users() -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        conn.executemany(
            """
            INSERT OR REPLACE INTO auth_users (
                user_id, openid, phone, phone_verified_at, wechat_openid_bound_at,
                current_company_id, nickname, avatar, is_platform_admin, is_disabled,
                created_at, last_login_at, updated_at
            ) VALUES (?, '', ?, '', '', 'c-industry-api', ?, '', ?, 0,
                      '2026-01-01', '2026-01-01', '')
            """,
            [
                ("u-industry-admin", "13800009001", "行业管理员", 1),
                ("u-industry-normal", "13800009002", "普通用户", 0),
            ],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO company_members "
            "(company_id, user_id, role, status) VALUES ('c-industry-api', ?, 'owner', 'active')",
            [("u-industry-admin",), ("u-industry-normal",)],
        )
        conn.commit()
    finally:
        conn.close()


def _cleanup_api_fixture(created_company_id: str = "") -> None:
    conn = sqlite3.connect(TEST_CORE_DB)
    try:
        if created_company_id:
            conn.execute(
                "DELETE FROM auth_companies WHERE company_id = ?",
                (created_company_id,),
            )
        conn.execute(
            "DELETE FROM admin_audit_logs WHERE target_type = 'industry_template' "
            "AND target_id LIKE 'ind_test_api%'"
        )
        conn.execute("DELETE FROM industry_templates WHERE code LIKE 'ind_test_api%'")
        conn.execute(
            "DELETE FROM company_members WHERE company_id = 'c-industry-api' "
            "AND user_id IN ('u-industry-admin', 'u-industry-normal')"
        )
        conn.execute(
            "DELETE FROM auth_users WHERE user_id IN "
            "('u-industry-admin', 'u-industry-normal')"
        )
        conn.commit()
    finally:
        conn.close()


def _headers(user_id: str) -> dict[str, str]:
    token = create_access_token({"sub": user_id, "company_id": "c-industry-api"})
    return {"Authorization": f"Bearer {token}"}


def test_admin_can_manage_templates_and_regular_user_is_read_only(client):
    _seed_api_users()
    created_company_id = ""
    try:
        normal_headers = _headers("u-industry-normal")
        admin_headers = _headers("u-industry-admin")

        public_catalog = client.get("/api/industry-templates", headers=normal_headers)
        assert public_catalog.status_code == 200
        assert public_catalog.json()["total"] == 91
        assert client.get("/api/admin/industry-templates", headers=normal_headers).status_code == 403

        payload = {
            "code": "ind_test_api_custom",
            "name": "API 自定义行业",
            "groupCode": "grp_test",
            "groupName": "测试行业",
            "description": "由管理后台维护",
            "restrictedDefault": False,
            "hidden": True,
            "enabled": True,
            "sortOrder": 900001,
            "config": {
                "accountPresets": [],
                "modules": [],
                "notes": [],
            },
        }
        created = client.post(
            "/api/admin/industry-templates",
            json=payload,
            headers=admin_headers,
        )
        assert created.status_code == 200, created.text
        template = created.json()
        assert template["version"] == 1

        payload["name"] = "API 自定义行业（新版）"
        updated = client.put(
            f"/api/admin/industry-templates/{template['id']}",
            json=payload,
            headers=admin_headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["version"] == 2
        assert updated.json()["name"] == "API 自定义行业（新版）"

        payload["code"] = "ind_test_api_renamed"
        rejected = client.put(
            f"/api/admin/industry-templates/{template['id']}",
            json=payload,
            headers=admin_headers,
        )
        assert rejected.status_code == 409

        company = client.post(
            "/api/auth/companies",
            json={
                "name": "API 软件公司",
                "taxId": "91330100TEST000001",
                "startDate": "2026-01-01",
                "industryCode": "ind___09_03",
            },
            headers=normal_headers,
        )
        assert company.status_code == 200, company.text
        company_body = company.json()
        created_company_id = company_body["company_id"]
        assert company_body["industry"] == "软件和信息技术服务业"
        assert company_body["industryCode"] == "ind___09_03"
        assert company_body["industryTemplateVersion"] == 1
        assert company_body["industryTemplateAppliedAt"]

        updated_company = client.put(
            f"/api/auth/companies/{created_company_id}",
            json={"industryCode": "ind___13_02"},
            headers=normal_headers,
        )
        assert updated_company.status_code == 200, updated_company.text
        assert updated_company.json()["industry"] == "专业技术服务业"
        assert updated_company.json()["industryCode"] == "ind___13_02"

        cleared_company = client.put(
            f"/api/auth/companies/{created_company_id}",
            json={"industryCode": ""},
            headers=normal_headers,
        )
        assert cleared_company.status_code == 200, cleared_company.text
        assert cleared_company.json()["industry"] == ""
        assert cleared_company.json()["industryCode"] == ""
        assert "industryTemplateVersion" not in cleared_company.json()
        assert "industryTemplateAppliedAt" not in cleared_company.json()

        invalid_update = client.put(
            f"/api/auth/companies/{created_company_id}",
            json={"industryCode": "ind_not_exists"},
            headers=normal_headers,
        )
        assert invalid_update.status_code == 422

        invalid = client.post(
            "/api/auth/companies",
            json={
                "name": "无效行业公司",
                "taxId": "91330100TEST000002",
                "startDate": "2026-01-01",
                "industryCode": "ind_not_exists",
            },
            headers=normal_headers,
        )
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["code"] == "INVALID_INDUSTRY_TEMPLATE"

        audit_logs = client.get(
            "/api/admin/audit-logs",
            params={"targetType": "industry_template"},
            headers=admin_headers,
        ).json()["items"]
        actions = [item["action"] for item in audit_logs if item["targetId"] == template["id"]]
        assert "create_industry_template" in actions
        assert "update_industry_template" in actions
    finally:
        _cleanup_api_fixture(created_company_id)
