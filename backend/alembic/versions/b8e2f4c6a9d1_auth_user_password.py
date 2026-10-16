# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""密码登录：auth_users 增加 password_hash / password_updated_at。

Revision ID: b8e2f4c6a9d1
Revises: e1f5a7b9c2d4
"""

from alembic import op
import sqlalchemy as sa

revision = "b8e2f4c6a9d1"
down_revision = "e1f5a7b9c2d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "auth_users",
        sa.Column("password_hash", sa.String(255), nullable=False, server_default=""),
    )
    op.add_column(
        "auth_users",
        sa.Column("password_updated_at", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("auth_users", "password_updated_at")
    op.drop_column("auth_users", "password_hash")
