# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""M3：征期日历表。

Revision ID: e6b8c3f1a7d2
Revises: d2f4a6b8c1e9
"""

from alembic import op
import sqlalchemy as sa


revision = "e6b8c3f1a7d2"
down_revision = "d2f4a6b8c1e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "filing_deadlines",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(128),
            sa.ForeignKey("auth_companies.company_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tax_kind", sa.String(32), nullable=False),
        sa.Column("period", sa.String(16), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filed_evidence", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("company_id", "tax_kind", "period", name="uq_filing_deadline"),
        sa.CheckConstraint("status IN ('pending','filed','waived')", name="ck_filing_deadline_status"),
    )
    op.create_index("ix_filing_deadlines_company_due", "filing_deadlines", ["company_id", "due_date"])


def downgrade() -> None:
    op.drop_index("ix_filing_deadlines_company_due", table_name="filing_deadlines")
    op.drop_table("filing_deadlines")
