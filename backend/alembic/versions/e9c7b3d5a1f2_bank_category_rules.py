# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""银行流水企业记忆规则表 bank_category_rules。

该表此前只有 services.bank_service._ensure_schema() 里的 SQLite 方言 DDL 一条
创建路径：Postgres 兼容层会静默跳过 CREATE TABLE，且迁移目录一直没有这张表，
导致 /api/bank/ai-suggest 查表直接 500、AI 对账建议全灭。
用 IF NOT EXISTS 保证对已手工补表的环境（本地/生产热修）幂等。

Revision ID: e9c7b3d5a1f2
Revises: b3d8f2a6c4e1
"""

from alembic import op


revision = "e9c7b3d5a1f2"
down_revision = "b3d8f2a6c4e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bank_category_rules (
            company_id TEXT NOT NULL,
            direction TEXT NOT NULL,
            match_pattern TEXT NOT NULL,
            category TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (company_id, direction, match_pattern)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bank_category_rules")
