# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""R&D evidence document registry.

Revision ID: d7e4b2c91a06
Revises: c4d8a6e2f913
"""

from alembic import op
import sqlalchemy as sa


revision = "d7e4b2c91a06"
down_revision = "c4d8a6e2f913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rd_evidence_documents",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("company_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("journal_entry_id", sa.String(128), nullable=True),
        sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("reference_no", sa.String(255), nullable=True),
        sa.Column("file_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="available"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["journal_entry_id"], ["journal_entries.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["company_id", "project_id"], ["rd_projects.company_id", "rd_projects.id"],
            name="fk_rd_evidence_project_company", ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "document_type IN ('initiation','invoice','receipt','contract','timesheet','material_issue','usage_log','acceptance','other')",
            name="ck_rd_evidence_document_type",
        ),
        sa.CheckConstraint("status IN ('available','pending_review','missing')", name="ck_rd_evidence_status"),
        sa.CheckConstraint("reference_no IS NOT NULL OR file_url IS NOT NULL", name="ck_rd_evidence_reference"),
    )
    op.create_index(
        "ix_rd_evidence_company_project_entry", "rd_evidence_documents",
        ["company_id", "project_id", "journal_entry_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_rd_evidence_company_project_entry", table_name="rd_evidence_documents")
    op.drop_table("rd_evidence_documents")
