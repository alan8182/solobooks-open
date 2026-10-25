# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""P0 WP2：辅助核算主数据（partners / departments / projects）。

行级辅助核算继续存 journal_lines.auxiliary JSON（管道已全通），
本迁移只建维度主数据，不建行级子表——避免 JSON 与子表双写一致性负担。

Revision ID: b8d2f0e5c3a7
Revises: a7c1e9d4b2f6
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8d2f0e5c3a7"
down_revision = "a7c1e9d4b2f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "partners",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(128),
            sa.ForeignKey("auth_companies.company_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(16), nullable=False, server_default="both"),
        sa.Column("tax_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("contact", sa.String(64), nullable=False, server_default=""),
        sa.Column("phone", sa.String(64), nullable=False, server_default=""),
        sa.Column("address", sa.String(255), nullable=False, server_default=""),
        sa.Column("mnemonic", sa.String(64), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("company_id", "code", name="uq_partner_company_code"),
        sa.CheckConstraint("type IN ('customer','supplier','both')", name="ck_partner_type"),
    )
    op.create_index("ix_partner_company_name", "partners", ["company_id", "name"])
    op.create_index("ix_partner_company_active", "partners", ["company_id", "is_active"])

    op.create_table(
        "departments",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(128),
            sa.ForeignKey("auth_companies.company_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("company_id", "code", name="uq_department_company_code"),
    )
    op.create_index("ix_department_company_name", "departments", ["company_id", "name"])

    op.create_table(
        "projects",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(128),
            sa.ForeignKey("auth_companies.company_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("company_id", "code", name="uq_project_company_code"),
    )
    op.create_index("ix_project_company_name", "projects", ["company_id", "name"])


def downgrade() -> None:
    op.drop_index("ix_project_company_name", table_name="projects")
    op.drop_table("projects")
    op.drop_index("ix_department_company_name", table_name="departments")
    op.drop_table("departments")
    op.drop_index("ix_partner_company_active", table_name="partners")
    op.drop_index("ix_partner_company_name", table_name="partners")
    op.drop_table("partners")
