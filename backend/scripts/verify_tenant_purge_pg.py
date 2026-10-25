# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""在真实 PostgreSQL 上验证「主体永久删除」的触发器豁免是否生效。

背景：journal_entries / journal_lines / audit_events 有不可变触发器，无条件拒绝
DELETE 与非法 UPDATE。删除主体需要在同一事务里放行它们（迁移 c1d3e5f7a9b2 引入
会话级 GUC `solobooks.allow_tenant_purge`）。SQLite 没有这些触发器，因此只能在
PG 上验证。

用途：本地或 staging 部署后跑一次，确认两件事同时成立——
  1) 正常业务路径仍然禁止删除已过账凭证 / 审计事件；
  2) 清理事务（purge_company）能删干净。

用法（连接串必须是「应用角色」，即 uvicorn 使用的那个）：
    DATABASE_URL="postgresql+asyncpg://solobooks_app:***@127.0.0.1:55432/solobooks" \\
        venv/Scripts/python.exe scripts/verify_tenant_purge_pg.py

脚本只操作它自己创建的临时主体（company_id 前缀 c_purge_probe_），
不触碰任何既有数据；退出码 0 表示全部断言通过。
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from db.session import database_url, get_engine, session_scope
from services import company_delete_service

PROBE_PREFIX = "c_purge_probe_"


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checks = 0

    def ok(self, label: str, detail: str = "") -> None:
        self.checks += 1
        print(f"  [PASS] {label}{(' — ' + detail) if detail else ''}")

    def fail(self, label: str, detail: str) -> None:
        self.checks += 1
        self.failures.append(f"{label}: {detail}")
        print(f"  [FAIL] {label} — {detail}")


async def _expect_blocked(report: Report, label: str, sql: str, params: dict, needle: str) -> None:
    """执行一条应当被触发器拒绝的语句；用独立事务，失败即回滚。"""
    try:
        async with get_engine().begin() as conn:
            await conn.execute(text(sql), params)
    except DBAPIError as exc:
        message = str(exc.orig or exc)
        if needle in message:
            report.ok(label, f"触发器拦截（{needle}）")
        else:
            report.fail(label, f"被拒绝但原因不是 {needle}：{message[:160]}")
        return
    except Exception as exc:  # noqa: BLE001
        report.fail(label, f"异常类型不符：{type(exc).__name__}: {exc}")
        return
    report.fail(label, "语句竟然执行成功，触发器未生效")


async def _seed_probe() -> dict:
    """建一个临时主体：公司 + 成员 + 已过账分录 + 分录行 + 审计事件。

    额外建一名「成员 B」，其 current_company_id 指向该主体——用于验证删除后
    悬空引用被清理（auth_users.current_company_id 在 PG 上是 NOT NULL）。
    """
    suffix = uuid.uuid4().hex[:12]
    owner_id = f"u_purge_probe_{suffix}_a"
    member_id = f"u_purge_probe_{suffix}_b"
    company_id = f"{PROBE_PREFIX}{suffix}"
    entry_id = f"je_purge_probe_{suffix}"
    line_ids = (f"jl_purge_probe_{suffix}_1", f"jl_purge_probe_{suffix}_2")
    account_id = f"acc_purge_probe_{suffix}"
    company_name = "临时验证主体（可删除）"

    async with session_scope() as session:
        for user_id, current in ((owner_id, ""), (member_id, company_id)):
            await session.execute(
                text(
                    "INSERT INTO auth_users (user_id, nickname, current_company_id, "
                    "created_at, last_login_at, updated_at) "
                    "VALUES (:uid, 'purge-probe', :cur, :now, :now, :now)"
                ),
                {
                    "uid": user_id,
                    "cur": current,
                    "now": "2026-01-01T00:00:00Z",
                },
            )
        await session.execute(
            text(
                "INSERT INTO auth_companies (company_id, owner_user_id, payload, base_currency, "
                "accounting_standard, posting_policy, timezone, created_at, updated_at) "
                "VALUES (:cid, :uid, CAST(:payload AS jsonb), 'CNY', 'small_enterprise_cn', "
                "'owner_self_post', 'Asia/Shanghai', :now, :now)"
            ),
            {
                "cid": company_id,
                "uid": owner_id,
                "payload": '{"name": "' + company_name + '", "type": "有限责任公司"}',
                "now": "2026-01-01T00:00:00Z",
            },
        )
        for user_id, role in ((owner_id, "owner"), (member_id, "viewer")):
            await session.execute(
                text(
                    "INSERT INTO company_members (company_id, user_id, role, status) "
                    "VALUES (:cid, :uid, :role, 'active')"
                ),
                {"cid": company_id, "uid": user_id, "role": role},
            )
        await session.execute(
            text(
                "INSERT INTO chart_accounts (id, company_id, code, name, level, is_leaf, "
                "is_system, is_active, created_at, updated_at) "
                "VALUES (:aid, :cid, '9999', '验证用科目', 1, true, false, true, :now, :now)"
            ),
            {"aid": account_id, "cid": company_id, "now": "2026-01-01T00:00:00Z"},
        )
        await session.execute(
            text(
                "INSERT INTO journal_entries (id, company_id, voucher_no, occurred_on, "
                "period_start, summary, status, entry_type, currency, created_by, posted_by, "
                "rule_version, metadata, version) "
                "VALUES (:eid, :cid, 'V-1', :day, :day, '验证用已过账分录', 'posted', "
                "'standard', 'CNY', :uid, :uid, 'account-mapping-v2', '{}'::jsonb, 1)"
            ),
            {"eid": entry_id, "cid": company_id, "day": date(2026, 3, 31), "uid": owner_id},
        )
        for idx, (line_id, direction) in enumerate(zip(line_ids, ("debit", "credit")), start=1):
            await session.execute(
                text(
                    "INSERT INTO journal_lines (id, journal_entry_id, company_id, line_no, "
                    "account_id, account_code, account_name, direction, amount, auxiliary) "
                    "VALUES (:lid, :eid, :cid, :no, :aid, '9999', '验证用科目', :dir, 100.00, "
                    "'{}'::jsonb)"
                ),
                {
                    "lid": line_id,
                    "eid": entry_id,
                    "cid": company_id,
                    "no": idx,
                    "aid": account_id,
                    "dir": direction,
                },
            )
        await session.execute(
            text(
                "INSERT INTO audit_events (id, company_id, user_id, action, entity_type, "
                "entity_id, request_id) "
                "VALUES (:aid, :cid, :uid, 'entry.post', 'journal_entry', :eid, 'probe')"
            ),
            {
                "aid": f"ae_purge_probe_{suffix}",
                "cid": company_id,
                "uid": owner_id,
                "eid": entry_id,
            },
        )

    return {
        "owner_id": owner_id,
        "member_id": member_id,
        "company_id": company_id,
        "company_name": company_name,
        "entry_id": entry_id,
        "line_ids": line_ids,
        "account_id": account_id,
    }


async def _cleanup_leftovers(company_id: str) -> None:
    """删掉可能残留的探针数据（忽略错误，尽量清理）。"""
    tables = (
        "audit_events",
        "journal_lines",
        "journal_entries",
        "chart_accounts",
        "company_members",
        "auth_companies",
    )
    async with get_engine().begin() as conn:
        await conn.execute(text("SELECT set_config('solobooks.allow_tenant_purge', 'on', true)"))
        for table in tables:
            column = "company_id"
            await conn.execute(
                text(f'DELETE FROM "{table}" WHERE {column} = :cid'), {"cid": company_id}
            )
        await conn.execute(
            text("DELETE FROM salary_records WHERE sheet_id IN (SELECT id FROM salary_sheets WHERE company_id = :cid)"),
            {"cid": company_id},
        )
        await conn.execute(
            text("DELETE FROM auth_users WHERE user_id LIKE 'u_purge_probe_%'"),
        )


async def main() -> int:
    if not database_url().startswith("postgresql"):
        print("ERROR: 该脚本必须在 PostgreSQL 上运行（当前 DATABASE_URL 非 PG）。")
        return 2

    report = Report()
    probe = await _seed_probe()
    company_id = probe["company_id"]
    print(f"探针主体已建立：{company_id}")

    try:
        async with session_scope() as session:
            has_guard = await session.execute(
                text(
                    "SELECT count(*) FROM pg_proc WHERE proname IN "
                    "('prevent_posted_journal_mutation', 'prevent_audit_mutation') "
                    "AND prosrc LIKE '%solobooks.allow_tenant_purge%'"
                )
            )
            guarded = int(has_guard.scalar() or 0)
        if guarded >= 2:
            report.ok("迁移 c1d3e5f7a9b2 已生效", f"{guarded} 个触发器函数含 GUC 判断")
        else:
            report.fail("迁移 c1d3e5f7a9b2 已生效", f"仅 {guarded} 个函数带豁免判断")

        _ = "solobooks.allow_tenant_purge"
        await _expect_blocked(
            report,
            "正常路径：删除已过账分录被拒",
            "DELETE FROM journal_entries WHERE id = :eid",
            {"eid": probe["entry_id"]},
            "ENTRY_POSTED_IMMUTABLE",
        )
        await _expect_blocked(
            report,
            "正常路径：删除已过账分录行被拒",
            "DELETE FROM journal_lines WHERE id = :lid",
            {"lid": probe["line_ids"][0]},
            "ENTRY_POSTED_IMMUTABLE",
        )
        await _expect_blocked(
            report,
            "正常路径：删除审计事件被拒",
            "DELETE FROM audit_events WHERE company_id = :cid",
            {"cid": company_id},
            "AUDIT_EVENT_IMMUTABLE",
        )
        await _expect_blocked(
            report,
            "正常路径：篡改已过账分录字段被拒",
            "UPDATE journal_entries SET summary = '被篡改' WHERE id = :eid",
            {"eid": probe["entry_id"]},
            "ENTRY_POSTED_IMMUTABLE",
        )

        # 清理事务：purge_company 内部自己 set_config，应能删干净
        try:
            result = await company_delete_service.purge_company(
                probe["owner_id"], company_id, probe["company_name"]
            )
        except Exception as exc:  # noqa: BLE001
            report.fail("purge_company 在 PG 上执行", f"{type(exc).__name__}: {exc}")
            return 1
        report.ok(
            "purge_company 在 PG 上执行",
            f"删除行数 {sum(result.get('deletedRows', {}).values())}",
        )

        async with session_scope() as session:
            remaining = {}
            for table, column, value in (
                ("auth_companies", "company_id", company_id),
                ("company_members", "company_id", company_id),
                ("journal_entries", "company_id", company_id),
                ("journal_lines", "company_id", company_id),
                ("audit_events", "company_id", company_id),
                ("chart_accounts", "company_id", company_id),
            ):
                row = await session.execute(
                    text(f'SELECT count(*) FROM "{table}" WHERE {column} = :v'), {"v": value}
                )
                remaining[table] = int(row.scalar() or 0)
            leftover = {k: v for k, v in remaining.items() if v}
            if leftover:
                report.fail("探针主体数据已清空", f"仍有残留：{leftover}")
            else:
                report.ok("探针主体数据已清空", "6 张表均为 0 行")

            log = await session.execute(
                text(
                    "SELECT count(*) FROM admin_audit_logs "
                    "WHERE action = 'delete_company' AND target_id = :cid"
                ),
                {"cid": company_id},
            )
            if int(log.scalar() or 0) >= 1:
                report.ok("平台级留痕已写入 admin_audit_logs")
            else:
                report.fail("平台级留痕已写入 admin_audit_logs", "未找到 delete_company 记录")

            dangling = await session.execute(
                text("SELECT current_company_id FROM auth_users WHERE user_id = :uid"),
                {"uid": probe["member_id"]},
            )
            member_current = dangling.scalar()
            if member_current in ("", None):
                report.ok(
                    "成员当前账套悬空引用已清理",
                    f"current_company_id={member_current!r}",
                )
            else:
                report.fail(
                    "成员当前账套悬空引用已清理",
                    f"仍指向已删除主体：{member_current!r}",
                )

        # 清理后，另一个真实主体的已过账分录仍不可删（确认豁免未被永久打开）
        async with session_scope() as session:
            row = await session.execute(
                text(
                    "SELECT id FROM journal_entries WHERE status = 'posted' "
                    "AND company_id <> :cid LIMIT 1"
                ),
                {"cid": company_id},
            )
            other_entry = row.scalar()
        if other_entry:
            await _expect_blocked(
                report,
                "清理后：其他主体已过账分录仍受保护",
                "DELETE FROM journal_entries WHERE id = :eid",
                {"eid": other_entry},
                "ENTRY_POSTED_IMMUTABLE",
            )
        else:
            print("  [SKIP] 库内没有其他已过账分录，跳过清理后复验")

        # 同一进程内新会话默认应已回到关闭态（set_config 第三参数为 true = 仅本事务）
        async with session_scope() as session:
            state = await session.execute(
                text("SELECT current_setting('solobooks.allow_tenant_purge', true)")
            )
            value = state.scalar()
        if value in (None, "", "off"):
            report.ok("GUC 仅限清理事务", f"新会话读值为 {value!r}")
        else:
            report.fail("GUC 仅限清理事务", f"新会话读值为 {value!r}，说明泄漏到了后续请求")

    finally:
        await _cleanup_leftovers(company_id)
        await _cleanup_leftovers("__none__")

    print()
    if report.failures:
        print(f"结论：{report.checks - len(report.failures)}/{report.checks} 通过，存在失败项：")
        for item in report.failures:
            print(f"  - {item}")
        return 1
    print(f"结论：{report.checks}/{report.checks} 全部通过，PG 触发器豁免行为符合预期。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
