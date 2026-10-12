"""管理后台 M2：admin_audit_logs 操作留痕表。

Revision ID: e1f5a7b9c2d4
Revises: c9d4f6a8e1b3
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "e1f5a7b9c2d4"
down_revision = "c9d4f6a8e1b3"
branch_labels = None
depends_on = None

JSON_VALUE = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("operator_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False, server_default="user"),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("detail", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_admin_audit_created", "admin_audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_admin_audit_created", table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")
