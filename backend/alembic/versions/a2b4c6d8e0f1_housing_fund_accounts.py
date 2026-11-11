# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""补住房公积金科目：221105 应付职工薪酬-住房公积金 + 5602017 管理费用-住房公积金

期初导入第三方（自记账等）科目余额表时公积金无处可落：负债侧 2211 子级此前
只有「代扣社保公积金」（个人代扣语义），费用侧 5602x 无公积金费用科目。
本迁移为存量账套确定性补齐两个末级科目；新账套由 seed_accounts 从
chart_of_accounts.json 自动播种。

存量库走幂等的 SELECT+INSERT/UPDATE（不用 ON CONFLICT，兼容无
(company_id, code) 唯一约束的遗留 schema），不碰 is_active（用户停用的
科目不复活），downgrade 不删行（子级被凭证引用后删除会让历史凭证无法冲销）。

Revision ID: a2b4c6d8e0f1
Revises: b7e3d1c8a4f2
Create Date: 2026-11-05
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

from core.chart_policy import chart_is_leaf

revision = "a2b4c6d8e0f1"
down_revision = "b7e3d1c8a4f2"
branch_labels = None
depends_on = None


CHART_JSON = Path(__file__).resolve().parents[2] / "data" / "chart_of_accounts.json"

TARGET_CODES = ("221105", "5602017")


def _table_exists(bind, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


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
                # is_leaf 走 core.chart_policy 单一口径（与 seed_accounts / sync 脚本一致）
                "is_leaf": chart_is_leaf(code, info),
            }
    return out


def _normal_side(code: str, direction: Any) -> str:
    if direction in ("debit", "credit"):
        return direction
    return "credit" if code.startswith(("2", "3")) else "debit"


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "chart_accounts"):
        return
    company_ids = [row[0] for row in bind.execute(
        sa.text("SELECT DISTINCT company_id FROM chart_accounts")
    ).fetchall()]
    now = datetime.now(timezone.utc).isoformat()

    # 不用 ON CONFLICT upsert：遗留库的 chart_accounts 可能没有 (company_id, code)
    # 唯一约束，先 SELECT 再 INSERT/UPDATE 在任何后端、任何历史 schema 上均可执行。
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
    # 科目元数据不做回退：子级科目一旦被凭证引用，删掉会让历史凭证无法冲销。
    return None
