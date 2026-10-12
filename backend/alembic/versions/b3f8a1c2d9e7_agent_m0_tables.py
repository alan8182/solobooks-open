"""Agent M0 基础设施：待确认动作表 + 公司画像表。

Revision ID: b3f8a1c2d9e7
Revises: e2f6a8c41d73
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "b3f8a1c2d9e7"
down_revision = "e2f6a8c41d73"
branch_labels = None
depends_on = None

JSON_VALUE = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "agent_actions",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(128),
            sa.ForeignKey("auth_companies.company_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("conversation_id", sa.String(128), nullable=True),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("evidence", JSON_VALUE, nullable=False),
        sa.Column("result", JSON_VALUE, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(128), nullable=True),
        sa.CheckConstraint("status IN ('pending','approved','rejected','expired')", name="ck_agent_action_status"),
        sa.CheckConstraint("action_type IN ('propose_voucher')", name="ck_agent_action_type"),
    )
    op.create_index("ix_agent_actions_company_status", "agent_actions", ["company_id", "status"])

    op.create_table(
        "company_profiles",
        sa.Column(
            "company_id",
            sa.String(128),
            sa.ForeignKey("auth_companies.company_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("profile", JSON_VALUE, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    op.drop_table("company_profiles")
    op.drop_index("ix_agent_actions_company_status", table_name="agent_actions")
    op.drop_table("agent_actions")
