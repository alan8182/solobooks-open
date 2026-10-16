# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""MCP 接入 M0：mcp_tokens 个人访问令牌表。

Revision ID: d5e7f9a1c3b5
Revises: b8e2f4c6a9d1
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d5e7f9a1c3b5"
down_revision = "b8e2f4c6a9d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_tokens",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("company_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("scopes", sa.String(255), nullable=False, server_default="read"),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mcp_tokens_user", "mcp_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_mcp_tokens_user", table_name="mcp_tokens")
    op.drop_table("mcp_tokens")
