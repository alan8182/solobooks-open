# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""主体永久删除（purge）：清空该主体下全部业务数据，不可恢复。

设计要点：
- 只允许主体所有者删除，且必须精确输入主体名称；
- 正在使用中的主体不允许删除（前端先切换）；
- 按「先子表后父表」顺序清理，兼容 PG 的 RESTRICT 外键；
- 表清单按 `company_id` 列动态发现，新增业务表无需改这里；
- PostgreSQL 的不可变触发器通过会话级 GUC `solobooks.allow_tenant_purge`
  受控放行（见迁移 c1d3e5f7a9b2），正常业务路径不受影响；
- 删除完成后在 `admin_audit_logs` 留一条平台级记录（不含业务数据），
  这是唯一保留的痕迹。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Set

from sqlalchemy import text

from db.models import AdminAuditLog, AuthUser, Company
from db.session import database_url, session_scope

logger = logging.getLogger(__name__)

# PG 中这些表对 rd_projects 是 RESTRICT 外键，必须先删
_PRIORITY_TABLES = (
    "rd_journal_line_details",
    "rd_salary_allocations",
    "rd_evidence_documents",
    # FK → journal_entries：必须先删，否则 PostgreSQL 拒删凭证
    "reconciliation_links",
)

# 不按 company_id 关联、需要特殊清理的表
_SALARY_RECORDS_SQL = (
    "DELETE FROM salary_records WHERE sheet_id IN "
    "(SELECT id FROM salary_sheets WHERE company_id = :cid)"
)
_JOURNAL_LINES_SQL = (
    "DELETE FROM journal_lines WHERE journal_entry_id IN "
    "(SELECT id FROM journal_entries WHERE company_id = :cid)"
)


class CompanyDeleteError(ValueError):
    """删除主体失败（可展示给用户）。"""


class CompanyNotFoundError(CompanyDeleteError):
    """主体不存在或非本人所有。"""


class CurrentCompanyDeleteError(CompanyDeleteError):
    """正在使用中的主体不允许删除。"""


def _is_postgres() -> bool:
    return database_url().startswith("postgresql")


async def _all_tables(session) -> Set[str]:
    if _is_postgres():
        rows = await session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
        )
        return {str(row[0]) for row in rows}
    rows = await session.execute(
        text("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
    )
    return {str(row[0]) for row in rows}


async def _tables_with_company_id(session, all_tables: Set[str]) -> Set[str]:
    if _is_postgres():
        rows = await session.execute(
            text(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'company_id'"
            )
        )
        return {str(row[0]) for row in rows} & all_tables
    found: Set[str] = set()
    for name in all_tables:
        info = await session.execute(text(f'PRAGMA table_info("{name}")'))
        if any(str(col[1]) == "company_id" for col in info):
            found.add(name)
    return found


async def purge_company(
    user_id: str, company_id: str, confirm_name: str
) -> Dict[str, Any]:
    """永久删除主体及其全部业务数据；返回各表删除行数。"""
    async with session_scope() as session:
        company = await session.get(Company, company_id)
        if company is None or company.owner_user_id != user_id:
            raise CompanyNotFoundError("主体不存在或无权删除")

        payload = company.payload or {}
        company_name = str(payload.get("name") or "")
        if (confirm_name or "").strip() != company_name:
            raise CompanyDeleteError("主体名称不匹配，请输入完整名称后再试")

        user = await session.get(AuthUser, user_id)
        if user is not None and user.current_company_id == company_id:
            raise CurrentCompanyDeleteError("请先切换到其他主体，再删除当前主体")

        # 受控放行不可变触发器（仅 PG）
        if _is_postgres():
            await session.execute(
                text("SELECT set_config('solobooks.allow_tenant_purge', 'on', true)")
            )

        all_tables = await _all_tables(session)
        counts: Dict[str, int] = {}

        async def _delete(sql: str, table: str) -> None:
            result = await session.execute(text(sql), {"cid": company_id})
            deleted = int(result.rowcount or 0)
            if deleted:
                counts[table] = counts.get(table, 0) + deleted

        # 1) 无 company_id 的子表，先于父表清理
        if {"salary_records", "salary_sheets"} <= all_tables:
            await _delete(_SALARY_RECORDS_SQL, "salary_records")
        if {"journal_lines", "journal_entries"} <= all_tables:
            await _delete(_JOURNAL_LINES_SQL, "journal_lines")

        # 2) 含 company_id 的表：RESTRICT 子表优先，其余随后
        owned = await _tables_with_company_id(session, all_tables)
        owned.discard("auth_companies")
        ordered = [t for t in _PRIORITY_TABLES if t in owned]
        ordered += [t for t in sorted(owned) if t not in _PRIORITY_TABLES]
        for table in ordered:
            await _delete(f'DELETE FROM "{table}" WHERE company_id = :cid', table)

        # 3) 主体行本身
        await session.execute(
            text("DELETE FROM auth_companies WHERE company_id = :cid"), {"cid": company_id}
        )
        # 4) 其他成员/用户仍指向该主体的当前账套，清空避免悬空引用
        #    注意：auth_users.current_company_id 是 NOT NULL（默认空串），
        #    置 NULL 会在 PG/SQLite 上直接触发约束错误，必须写空串。
        await session.execute(
            text("UPDATE auth_users SET current_company_id = '' WHERE current_company_id = :cid"),
            {"cid": company_id},
        )

        # 5) 平台级留痕（不在主体数据范围内，删除后仍保留）
        session.add(
            AdminAuditLog(
                operator_id=user_id,
                action="delete_company",
                target_type="company",
                target_id=company_id,
                detail={
                    "companyName": company_name,
                    "deletedRows": counts,
                    "purgedAt": datetime.now(timezone.utc).isoformat(),
                },
            )
        )

        logger.warning(
            "主体已永久删除 company=%s name=%s operator=%s rows=%s",
            company_id,
            company_name,
            user_id,
            sum(counts.values()),
        )
        return {
            "deleted": True,
            "companyId": company_id,
            "companyName": company_name,
            "deletedRows": counts,
        }
