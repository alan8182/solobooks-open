# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""邮件单据通道：mailbox_addresses + inbox_documents 两表。

Revision ID: b2d6f8a1c4e7
Revises: c4f8b1a7d3e9
Create Date: 2026-11-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b2d6f8a1c4e7"
down_revision = "c4f8b1a7d3e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mailbox_addresses",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(128),
            sa.ForeignKey("auth_companies.company_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("address_code", sa.String(32), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "inbox_documents",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(128),
            sa.ForeignKey("auth_companies.company_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_ext", sa.String(16), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("stored_name", sa.String(128), nullable=False),
        sa.Column("import_kind", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("note", sa.String(255), nullable=False, server_default=""),
        sa.Column("source", sa.String(16), nullable=False, server_default="email"),
        sa.Column("email_from", sa.String(255), nullable=False, server_default=""),
        sa.Column("email_subject", sa.String(512), nullable=False, server_default=""),
        sa.Column("email_message_id", sa.String(512), nullable=False, server_default=""),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','consumed','dismissed','unsupported')",
            name="ck_inbox_document_status",
        ),
        sa.UniqueConstraint("company_id", "content_sha256", name="uq_inbox_company_sha256"),
    )
    op.create_index("ix_inbox_documents_company_created", "inbox_documents", ["company_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_inbox_documents_company_created", table_name="inbox_documents")
    op.drop_table("inbox_documents")
    op.drop_table("mailbox_addresses")
