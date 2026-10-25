# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Solo Audit Share：audit_shares / share_annotations 两张协审表。

Revision ID: b3d8f2a6c4e1
Revises: a7c9e1f3b5d2
Create Date: 2026-10-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b3d8f2a6c4e1"
down_revision = "a7c9e1f3b5d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_shares",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(128),
            sa.ForeignKey("auth_companies.company_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("anonymized", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "snapshot",
            postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_shares_company_month", "audit_shares", ["company_id", "month"])

    op.create_table(
        "share_annotations",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "share_id",
            sa.String(128),
            sa.ForeignKey("audit_shares.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("company_id", sa.String(128), nullable=False),
        sa.Column("anchor_type", sa.String(24), nullable=False, server_default="entry"),
        sa.Column("anchor_id", sa.String(128)),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author_name", sa.String(64), nullable=False, server_default="协审会计师"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("resolved_by", sa.String(128)),
        sa.Column("resolution_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "anchor_type IN ('overall','entry','check_item','report_section')",
            name="ck_share_annotation_anchor",
        ),
        sa.CheckConstraint(
            "status IN ('open','acknowledged','resolved','dismissed')",
            name="ck_share_annotation_status",
        ),
    )
    op.create_index("ix_share_annotations_share", "share_annotations", ["share_id", "created_at"])
    op.create_index("ix_share_annotations_company", "share_annotations", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_share_annotations_company", table_name="share_annotations")
    op.drop_index("ix_share_annotations_share", table_name="share_annotations")
    op.drop_table("share_annotations")
    op.drop_index("ix_audit_shares_company_month", table_name="audit_shares")
    op.drop_table("audit_shares")
