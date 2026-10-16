# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""MCP 接入 M1：mcp_call_logs 调用留痕表。

Revision ID: f7a9c1e3d5b7
Revises: d5e7f9a1c3b5
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f7a9c1e3d5b7"
down_revision = "d5e7f9a1c3b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_call_logs",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("token_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("company_id", sa.String(128), nullable=False),
        sa.Column("tool", sa.String(128), nullable=False),
        sa.Column("arguments_digest", sa.Text(), nullable=False, server_default=""),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mcp_call_logs_token_created", "mcp_call_logs", ["token_id", "created_at"])
    op.create_index("ix_mcp_call_logs_company_created", "mcp_call_logs", ["company_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_mcp_call_logs_company_created", table_name="mcp_call_logs")
    op.drop_index("ix_mcp_call_logs_token_created", table_name="mcp_call_logs")
    op.drop_table("mcp_call_logs")
