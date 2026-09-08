"""R&D source allocations for payroll.

Revision ID: c4d8a6e2f913
Revises: 9c7e1d4a2b61
"""

from alembic import op
import sqlalchemy as sa


revision = "c4d8a6e2f913"
down_revision = "9c7e1d4a2b61"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rd_salary_allocations",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("company_id", sa.String(128), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("employee_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("allocation_rate", sa.Numeric(12, 6, asdecimal=True), nullable=False),
        sa.Column("allocation_basis", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["rd_projects.company_id", "rd_projects.id"],
            name="fk_rd_salary_project_company",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("company_id", "month", "employee_id", "project_id", name="uq_rd_salary_employee_project_month"),
        sa.CheckConstraint("allocation_rate > 0 AND allocation_rate <= 1", name="ck_rd_salary_rate"),
        sa.CheckConstraint("length(month) = 7", name="ck_rd_salary_month"),
    )
    op.create_index(
        "ix_rd_salary_company_month_employee",
        "rd_salary_allocations",
        ["company_id", "month", "employee_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_rd_salary_company_month_employee", table_name="rd_salary_allocations")
    op.drop_table("rd_salary_allocations")
