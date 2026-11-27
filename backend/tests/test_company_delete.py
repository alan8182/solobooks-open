# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""主体永久删除：数据清理、三重校验与 API 接线。"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text


async def _ensure_schema() -> None:
    from db.base import Base
    import db.models  # noqa: F401
    from db.session import get_engine
    from services.audit_service import _ensure_schema as audit_schema
    from services.bank_service import _ensure_schema as bank_schema
    from services.salary_service import _ensure_db_ready as salary_ready
    from services.todo_service import _ensure_db_ready as todo_ready

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await audit_schema()
    await bank_schema()
    await salary_ready()
    await todo_ready()


async def _ensure_fallback_access() -> None:
    """开发兜底身份固定为 (test_user_001, c1)，require_auth 每次请求都会校验
    对 c1 的归属；隔离测试库未必有 c1，缺则按 owner 补齐（幂等）。"""
    from db.models import Company
    from db.session import session_scope

    async with session_scope() as session:
        if await session.get(Company, "c1") is None:
            session.add(
                Company(
                    company_id="c1",
                    owner_user_id="test_user_001",
                    payload={"name": "兜底主体"},
                    created_at="2026-01-01T00:00:00+00:00",
                    updated_at="2026-01-01T00:00:00+00:00",
                )
            )


async def _seed_company(company_id: str, name: str, owner: str = "u-del") -> None:
    from db.models import (
        AgentAction,
        AuditEvent,
        Company,
        CompanyMember,
        CompanyProfile,
        FilingDeadline,
        LegacyEntryProjection,
        MCPAccessToken,
    )
    from db.session import session_scope

    now = "2026-01-01T00:00:00+00:00"
    async with session_scope() as session:
        session.add(
            Company(
                company_id=company_id,
                owner_user_id=owner,
                payload={"name": name},
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            CompanyMember(company_id=company_id, user_id=owner, role="owner", status="active")
        )
        session.add(CompanyProfile(company_id=company_id, profile={"industry": "软件"}))
        session.add(
            AgentAction(
                company_id=company_id,
                user_id=owner,
                action_type="propose_voucher",
                title="草稿",
            )
        )
        session.add(
            FilingDeadline(
                company_id=company_id,
                tax_kind="vat",
                period="2026-Q1",
                due_date=date(2026, 4, 15),
            )
        )
        session.add(
            MCPAccessToken(
                company_id=company_id,
                user_id=owner,
                name="test",
                token_hash=(company_id * 4)[:64],
                scopes="read",
            )
        )
        session.add(
            LegacyEntryProjection(
                id=f"e-{company_id}",
                user_id=owner,
                company_id=company_id,
                payload="{}",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            AuditEvent(
                company_id=company_id,
                user_id=owner,
                action="test",
                entity_type="company",
                request_id="r-1",
            )
        )

    # 原生 SQL 表：工资表/工资条
    from services.salary_service import create_employee, get_or_create_salary_sheet

    await create_employee(company_id, "张三", "运营", "8000", "0")
    await get_or_create_salary_sheet(company_id, "2026-01")


async def _count(table: str, company_id: str) -> int:
    from db.session import session_scope

    async with session_scope() as session:
        row = (
            await session.execute(
                text(f'SELECT COUNT(*) FROM "{table}" WHERE company_id = :cid'),
                {"cid": company_id},
            )
        ).first()
        return int(row[0])


async def _count_salary_records(company_id: str) -> int:
    from db.session import session_scope

    async with session_scope() as session:
        row = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM salary_records WHERE sheet_id IN "
                    "(SELECT id FROM salary_sheets WHERE company_id = :cid)"
                ),
                {"cid": company_id},
            )
        ).first()
        return int(row[0])


@pytest.mark.asyncio
async def test_purge_removes_all_company_scoped_rows():
    await _ensure_schema()
    await _seed_company("c-del-1", "待删主体")

    from services.company_delete_service import purge_company

    result = await purge_company("u-del", "c-del-1", "待删主体")

    assert result["deleted"] is True
    assert isinstance(result["deletedRows"], dict)
    for table in (
        "company_members",
        "company_profiles",
        "agent_actions",
        "filing_deadlines",
        "mcp_tokens",
        "entries",
        "audit_events",
        "salary_sheets",
    ):
        assert await _count(table, "c-del-1") == 0, table
    assert await _count_salary_records("c-del-1") == 0
    # 主体行已删除
    from db.models import Company
    from db.session import session_scope

    async with session_scope() as session:
        assert await session.get(Company, "c-del-1") is None


@pytest.mark.asyncio
async def test_wrong_confirm_name_keeps_data():
    await _ensure_schema()
    await _seed_company("c-del-2", "核对名称主体")

    from services.company_delete_service import CompanyDeleteError, purge_company

    with pytest.raises(CompanyDeleteError):
        await purge_company("u-del", "c-del-2", "错的名称")

    assert await _count("company_members", "c-del-2") == 1
    assert await _count("entries", "c-del-2") == 1


@pytest.mark.asyncio
async def test_non_owner_cannot_delete():
    await _ensure_schema()
    await _seed_company("c-del-3", "他人主体", owner="u-owner")

    from services.company_delete_service import CompanyNotFoundError, purge_company

    with pytest.raises(CompanyNotFoundError):
        await purge_company("u-del", "c-del-3", "他人主体")


@pytest.mark.asyncio
async def test_current_company_cannot_be_deleted():
    await _ensure_schema()
    await _seed_company("c-del-4", "当前主体")

    from db.models import AuthUser
    from db.session import session_scope

    async with session_scope() as session:
        session.add(
            AuthUser(
                user_id="u-del",
                current_company_id="c-del-4",
                created_at="2026-01-01T00:00:00+00:00",
                last_login_at="2026-01-01T00:00:00+00:00",
            )
        )

    from services.company_delete_service import CurrentCompanyDeleteError, purge_company

    with pytest.raises(CurrentCompanyDeleteError):
        await purge_company("u-del", "c-del-4", "当前主体")

    assert await _count("company_members", "c-del-4") == 1


@pytest.mark.asyncio
async def test_purge_clears_other_members_current_company():
    """回归：其他成员当前账套指向被删主体时，删除不能因 NOT NULL 约束失败。

    真实 PG 上 auth_users.current_company_id 是 NOT NULL（默认空串）；
    早期实现写 NULL，只要存在这样的成员（如多成员公司），清理事务就会整笔回滚。
    """
    await _ensure_schema()
    await _seed_company("c-del-5", "多成员主体", owner="u-owner-5")

    from db.models import AuthUser
    from db.session import session_scope
    from services.company_delete_service import purge_company

    async with session_scope() as session:
        session.add(
            AuthUser(
                user_id="u-member-5",
                current_company_id="c-del-5",
                nickname="成员",
                created_at="2026-01-01T00:00:00+00:00",
                last_login_at="2026-01-01T00:00:00+00:00",
            )
        )

    result = await purge_company("u-owner-5", "c-del-5", "多成员主体")
    assert result["deleted"] is True

    async with session_scope() as session:
        member = await session.get(AuthUser, "u-member-5")
        assert member is not None
        assert member.current_company_id == ""


def _issue_delete_token(company_id: str, user_id: str = "test_user_001") -> str:
    """按真实链路签发 delete_company 二次认证令牌（单次使用）。"""
    import asyncio

    from db.session import session_scope
    from services.accounting_core_service import issue_step_up_token

    async def _run() -> str:
        async with session_scope() as session:
            return await issue_step_up_token(
                session,
                company_id=company_id,
                user_id=user_id,
                action="delete_company",
            )

    return asyncio.run(_run())


def test_delete_api_rejects_name_only_without_step_up(client, auth_headers):
    """回归：永久删除不再只凭主体名称，缺短信二次认证必须 403 且数据不动。"""
    import asyncio

    async def _run() -> None:
        await _ensure_schema()
        await _ensure_fallback_access()
        await _seed_company("c-del-nostep", "缺二次认证主体", owner="test_user_001")

    asyncio.run(_run())

    resp = client.request(
        "DELETE",
        "/api/auth/companies/c-del-nostep",
        json={"confirmName": "缺二次认证主体"},
        headers=auth_headers,
    )
    assert resp.status_code == 403

    import asyncio as _asyncio

    async def _still_there() -> int:
        return await _count("company_members", "c-del-nostep")

    assert _asyncio.run(_still_there()) == 1


def test_delete_api_requires_exact_name(client, auth_headers):
    """HTTP 接线：错误名称 400，正确名称 200。"""
    import asyncio

    async def _run() -> None:
        await _ensure_schema()
        await _ensure_fallback_access()
        # 认证兜底身份是 test_user_001；确保它不是当前主体
        from db.models import AuthUser
        from db.session import session_scope

        async with session_scope() as session:
            user = await session.get(AuthUser, "test_user_001")
            if user is None:
                session.add(
                    AuthUser(
                        user_id="test_user_001",
                        current_company_id="c1",
                        created_at="2026-01-01T00:00:00+00:00",
                        last_login_at="2026-01-01T00:00:00+00:00",
                    )
                )
            elif user.current_company_id == "c-del-api":
                user.current_company_id = "c1"
        await _seed_company("c-del-api", "接口删除主体", owner="test_user_001")

    asyncio.run(_run())

    bad = client.request(
        "DELETE",
        "/api/auth/companies/c-del-api",
        json={
            "confirmName": "错名",
            "stepUpToken": _issue_delete_token("c-del-api"),
        },
        headers=auth_headers,
    )
    assert bad.status_code == 400

    ok = client.request(
        "DELETE",
        "/api/auth/companies/c-del-api",
        json={
            "confirmName": "接口删除主体",
            "stepUpToken": _issue_delete_token("c-del-api"),
        },
        headers=auth_headers,
    )
    assert ok.status_code == 200
    assert ok.json()["deleted"] is True


def test_delete_api_tolerates_stored_name_whitespace(client, auth_headers):
    """存量主体名带前导/尾随空格时（早期创建入口未 trim），按可见名删除应成功。"""
    import asyncio

    async def _run() -> None:
        await _ensure_schema()
        await _ensure_fallback_access()
        await _seed_company("c-del-ws", " 空格主体名 ", owner="test_user_001")

    asyncio.run(_run())

    ok = client.request(
        "DELETE",
        "/api/auth/companies/c-del-ws",
        json={
            "confirmName": "空格主体名",
            "stepUpToken": _issue_delete_token("c-del-ws"),
        },
        headers=auth_headers,
    )
    assert ok.status_code == 200
    assert ok.json()["companyName"] == "空格主体名"


def test_purge_url_normalizes_to_asyncpg():
    """专用连接串允许沿用迁移脚本的 psycopg/postgres 写法，运行时统一转 asyncpg。"""
    from services.company_delete_service import _normalize_purge_url

    assert _normalize_purge_url("postgresql+psycopg://u:p@h:5432/db") == (
        "postgresql+asyncpg://u:p@h:5432/db"
    )
    assert _normalize_purge_url("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert _normalize_purge_url("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert _normalize_purge_url("postgresql+asyncpg://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"

    with pytest.raises(ValueError):
        _normalize_purge_url("")


@pytest.mark.asyncio
async def test_purge_fails_closed_without_owner_connection(monkeypatch):
    """PG 上未配置属主连接时必须明确拒绝，不能回落到应用角色硬删。"""
    import services.company_delete_service as svc

    monkeypatch.setattr(svc, "_is_postgres", lambda: True)
    monkeypatch.setattr(svc, "TENANT_PURGE_DATABASE_URL", "")

    with pytest.raises(svc.CompanyDeleteError) as exc:
        await svc.purge_company("u-del", "c-del-no-owner-url", "任意名称")
    assert "TENANT_PURGE_DATABASE_URL" in str(exc.value)


def test_reconciliation_links_are_purged_before_journal_entries():
    """PostgreSQL 上 reconciliation_links 有指向 journal_entries 的外键，
    按字母序会先删凭证而报 FK 冲突，必须排进优先删除列表。"""
    from services.company_delete_service import _PRIORITY_TABLES

    assert "reconciliation_links" in _PRIORITY_TABLES
