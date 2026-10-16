# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""管理后台 M0：auth_users 增加平台管理员/禁用标记。

Revision ID: a7c3e9f1b2d4
Revises: f0a2c4e6b8d1
"""

from alembic import op
import sqlalchemy as sa


revision = "a7c3e9f1b2d4"
down_revision = "f0a2c4e6b8d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "auth_users",
        sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "auth_users",
        sa.Column("is_disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("auth_users", "is_disabled")
    op.drop_column("auth_users", "is_platform_admin")
