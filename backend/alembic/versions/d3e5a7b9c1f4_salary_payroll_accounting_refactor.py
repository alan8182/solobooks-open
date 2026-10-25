# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""工资记账分录重构：科目明细拆分 + 业主标识补齐

- COA 元数据对齐：为 1221/2211 新增子级明细并把父级翻转为非末级，
  新增 5602016 管理费用-单位社保。存量库走幂等的 SELECT+INSERT/UPDATE
  （不用 ON CONFLICT，兼容无 (company_id, code) 唯一约束的遗留 schema），
  不动 journal_lines，不重新启用被用户停用的科目。
- employees.is_owner 补齐：f3a7c91d2b10 建表时漏了该列，此前只靠
  salary_service._ensure_schema() 的运行时 ALTER 兜底，
  生产库若未访问过 /api/salary，create_employee 的 INSERT 会直接失败。

Revision ID: d3e5a7b9c1f4
Revises: b8d2f6a0c4e8
Create Date: 2026-10-18
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

from core.chart_policy import chart_is_leaf

revision = "d3e5a7b9c1f4"
down_revision = "b8d2f6a0c4e8"
branch_labels = None
depends_on = None


CHART_JSON = Path(__file__).resolve().parents[2] / "data" / "chart_of_accounts.json"

# 本次需要对齐的科目。非明细编码（长度 <= 4）即使 JSON 未声明 isLeaf
# 也保留末级 —— 历史上它们被当作可直接记账的科目使用（如 5602）。
TARGET_CODES = (
    "1221", "122101", "122102", "122103",
    "2211", "221101", "221102", "221103", "221104",
    "2221012",
    "5602001", "5602014", "5602016",
)


def _column_names(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _target_definitions() -> dict[str, dict[str, Any]]:
    doc = json.loads(CHART_JSON.read_text(encoding="utf-8"))
    groups = doc.get("accounts", {})
    out: dict[str, dict[str, Any]] = {}
    for group, accounts in groups.items():
        for code, info in accounts.items():
            if code not in TARGET_CODES:
                continue
            is_sub = len(code) > 4
            out[code] = {
                "name": str(info.get("name") or code),
                "category": group,
                "type": info.get("type"),
                "direction": info.get("direction"),
                "parent_code": info.get("parentCode") or (code[:4] if is_sub else None),
                "level": 1 + int(is_sub) + int(len(code) > 7),
                # is_leaf 走 core.chart_policy 单一口径（与 seed_accounts / sync 脚本一致）：
                # 1221/2211/2221 是纯容器转非末级，4 位总账保持末级。
                "is_leaf": chart_is_leaf(code, info),
            }
    return out


def _normal_side(code: str, direction: Any) -> str:
    if direction in ("debit", "credit"):
        return direction
    return "credit" if code.startswith(("2", "3")) else "debit"


def _table_exists(bind, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def upgrade() -> None:
    bind = op.get_bind()

    # 1) employees.is_owner 补齐（幂等）
    if _table_exists(bind, "employees") and "is_owner" not in _column_names(bind, "employees"):
        op.add_column(
            "employees",
            sa.Column("is_owner", sa.Integer, nullable=False, server_default="0"),
        )

    # 2) COA 元数据对齐
    if not _table_exists(bind, "chart_accounts"):
        return
    company_ids = [row[0] for row in bind.execute(
        sa.text("SELECT DISTINCT company_id FROM chart_accounts")
    ).fetchall()]
    now = datetime.now(timezone.utc).isoformat()

    # 不用 ON CONFLICT upsert：遗留库（accounts_service 原始 DDL、早期版本）的
    # chart_accounts 可能没有 (company_id, code) 唯一约束，ON CONFLICT 会直接
    # 报 "no unique constraint matching"。先 SELECT 再 INSERT/UPDATE 在任何后端、
    # 任何历史 schema 上均可执行。
    # UPDATE 不碰 is_active：用户手工停用的科目不能被迁移重新启用。
    for company_id in company_ids:
        for code, info in _target_definitions().items():
            existing = bind.execute(
                sa.text("SELECT id FROM chart_accounts WHERE company_id = :cid AND code = :code"),
                {"cid": company_id, "code": code},
            ).first()
            params = {
                "id": f"acct-{company_id}-{code}",
                "cid": company_id,
                "code": code,
                "name": info["name"],
                "parent": info["parent_code"],
                "level": info["level"],
                "cat": info["category"],
                "type": info["type"],
                "side": _normal_side(code, info["direction"]),
                "leaf": info["is_leaf"],
                "now": now,
            }
            if existing is None:
                bind.execute(
                    sa.text(
                        "INSERT INTO chart_accounts "
                        "(id, company_id, code, name, parent_code, level, category, type, "
                        " normal_side, is_leaf, is_system, is_active, created_at, updated_at) "
                        "VALUES (:id, :cid, :code, :name, :parent, :level, :cat, :type, "
                        " :side, :leaf, true, true, :now, :now)"
                    ),
                    params,
                )
            else:
                bind.execute(
                    sa.text(
                        "UPDATE chart_accounts SET "
                        " name = :name, parent_code = :parent, level = :level, "
                        " category = :cat, type = :type, normal_side = :side, "
                        " is_leaf = :leaf, updated_at = :now "
                        "WHERE company_id = :cid AND code = :code"
                    ),
                    params,
                )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "employees") and "is_owner" in _column_names(bind, "employees"):
        with op.batch_alter_table("employees") as batch:
            batch.drop_column("is_owner")
    # 科目元数据不做回退：子级科目一旦被凭证引用，删掉会让历史凭证无法冲销。
