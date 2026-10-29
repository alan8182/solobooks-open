# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""AI 用量套餐与周期计数（配额体系预留，额度数值待定）。

auth_users 增加 plan_code / plan_anchor_at / quota_bonus_tokens；
新增 user_quota_counters 周期计数表。额度未设限（core/plans.py 全 None），
限制逻辑由 services/quota_service.py 惰性生效，本迁移只铺结构。
"""

import sqlalchemy as sa
from alembic import op


revision = "a7c3e9f1b5d2"
down_revision = "f6a2c8e4b9d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "auth_users",
        sa.Column("plan_code", sa.String(32), nullable=False, server_default="trial"),
    )
    op.add_column("auth_users", sa.Column("plan_anchor_at", sa.Text(), nullable=True))
    op.add_column(
        "auth_users",
        sa.Column("quota_bonus_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "user_quota_counters",
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("period_type", sa.String(16), nullable=False),
        sa.Column("period_start", sa.String(10), nullable=False),
        sa.Column("used_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "period_type", "period_start"),
    )


def downgrade() -> None:
    op.drop_table("user_quota_counters")
    op.drop_column("auth_users", "quota_bonus_tokens")
    op.drop_column("auth_users", "plan_anchor_at")
    op.drop_column("auth_users", "plan_code")
