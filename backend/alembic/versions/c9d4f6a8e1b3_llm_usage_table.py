# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""管理后台 M1：llm_usage 用量明细表。

Revision ID: c9d4f6a8e1b3
Revises: a7c3e9f1b2d4
"""

from alembic import op
import sqlalchemy as sa


revision = "c9d4f6a8e1b3"
down_revision = "a7c3e9f1b2d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(128), nullable=True),
        sa.Column("company_id", sa.String(128), nullable=True),
        sa.Column("scene", sa.String(64), nullable=False, server_default="other"),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("error_type", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("status IN ('ok','error','circuit_open')", name="ck_llm_usage_status"),
    )
    op.create_index("ix_llm_usage_created_at", "llm_usage", ["created_at"])
    op.create_index("ix_llm_usage_company_created", "llm_usage", ["company_id", "created_at"])
    op.create_index("ix_llm_usage_user_created", "llm_usage", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_usage_user_created", table_name="llm_usage")
    op.drop_index("ix_llm_usage_company_created", table_name="llm_usage")
    op.drop_index("ix_llm_usage_created_at", table_name="llm_usage")
    op.drop_table("llm_usage")
