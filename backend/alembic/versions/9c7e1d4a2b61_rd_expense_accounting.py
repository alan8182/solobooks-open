# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""R&D project and expense accounting dimensions.

Revision ID: 9c7e1d4a2b61
Revises: f3a7c91d2b10
"""

from alembic import op
import sqlalchemy as sa


revision = "9c7e1d4a2b61"
down_revision = "f3a7c91d2b10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    money = sa.Numeric(20, 2, asdecimal=True)
    rate = sa.Numeric(12, 6, asdecimal=True)
    op.create_table(
        "rd_projects",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("company_id", sa.String(128), sa.ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("manager", sa.String(255)),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date()),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("initiation_document_status", sa.String(24), nullable=False, server_default="missing"),
        sa.Column("notes", sa.Text()),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "code", name="uq_rd_project_company_code"),
        sa.UniqueConstraint("company_id", "id", name="uq_rd_project_company_id"),
        sa.CheckConstraint("status IN ('draft','active','completed','archived')", name="ck_rd_project_status"),
        sa.CheckConstraint("initiation_document_status IN ('missing','partial','complete')", name="ck_rd_project_document_status"),
        sa.CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_rd_project_dates"),
    )
    op.create_index("ix_rd_project_company_status", "rd_projects", ["company_id", "status"])
    op.create_table(
        "rd_journal_line_details",
        sa.Column("journal_line_id", sa.String(128), sa.ForeignKey("journal_lines.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("company_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("expense_type", sa.String(32), nullable=False),
        sa.Column("original_amount", money, nullable=False),
        sa.Column("rd_amount", money, nullable=False),
        sa.Column("allocation_rate", rate, nullable=False, server_default="1"),
        sa.Column("allocation_method", sa.String(64), nullable=False, server_default="direct"),
        sa.Column("allocation_basis", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["rd_projects.company_id", "rd_projects.id"],
            name="fk_rd_line_project_company",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("expense_type IN ('personnel','direct_input','depreciation_amortization','design','testing','outsourced','other')", name="ck_rd_line_expense_type"),
        sa.CheckConstraint("original_amount > 0", name="ck_rd_line_original_positive"),
        sa.CheckConstraint("rd_amount > 0 AND rd_amount <= original_amount", name="ck_rd_line_amount_range"),
        sa.CheckConstraint("allocation_rate > 0 AND allocation_rate <= 1", name="ck_rd_line_allocation_rate"),
        sa.CheckConstraint("allocation_method IN ('direct','timesheet','usage','area','quantity','other')", name="ck_rd_line_allocation_method"),
    )
    op.create_index(
        "ix_rd_line_company_project_type",
        "rd_journal_line_details",
        ["company_id", "project_id", "expense_type"],
    )
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION prevent_posted_rd_detail_mutation()
            RETURNS trigger AS $$
            DECLARE parent_status text;
            BEGIN
                SELECT je.status INTO parent_status
                FROM journal_entries je
                JOIN journal_lines jl ON jl.journal_entry_id = je.id
                WHERE jl.id = COALESCE(OLD.journal_line_id, NEW.journal_line_id);
                IF parent_status IN ('posted', 'reversed') THEN
                    RAISE EXCEPTION 'ENTRY_POSTED_IMMUTABLE:%', COALESCE(OLD.journal_line_id, NEW.journal_line_id);
                END IF;
                RETURN COALESCE(NEW, OLD);
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_rd_detail_immutable
            BEFORE UPDATE OR DELETE ON rd_journal_line_details
            FOR EACH ROW EXECUTE FUNCTION prevent_posted_rd_detail_mutation();
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_rd_detail_immutable ON rd_journal_line_details")
        op.execute("DROP FUNCTION IF EXISTS prevent_posted_rd_detail_mutation()")
    op.drop_index("ix_rd_line_company_project_type", table_name="rd_journal_line_details")
    op.drop_table("rd_journal_line_details")
    op.drop_index("ix_rd_project_company_status", table_name="rd_projects")
    op.drop_table("rd_projects")
