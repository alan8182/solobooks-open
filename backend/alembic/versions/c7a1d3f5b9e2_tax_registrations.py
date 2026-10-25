# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""税费种认定表：把电子税务局的「税费种认定信息」搬进系统。

一张表即可，不动 filing_deadlines——认定行与征期不是一对一：
多个认定行（城建税/教育费附加/地方教育附加）归属同一张申报表，
征期按 return_group 物化，唯一键 (company_id, tax_kind, period) 语义仍然成立。

Revision ID: c7a1d3f5b9e2
Revises: b5c2e8a4f6d1
Create Date: 2026-10-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c7a1d3f5b9e2"
down_revision = "b5c2e8a4f6d1"
branch_labels = None
depends_on = None


def _existing_tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "tax_registrations" in _existing_tables():
        return
    op.create_table(
        "tax_registrations",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(128),
            sa.ForeignKey("auth_companies.company_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("levy_item", sa.String(64), nullable=False),
        sa.Column("levy_category", sa.String(128), nullable=False, server_default=""),
        sa.Column("levy_sub_item", sa.String(128), nullable=False, server_default=""),
        sa.Column("filing_cycle", sa.String(16), nullable=False),
        sa.Column("filing_cycle_text", sa.String(32), nullable=False, server_default=""),
        sa.Column("deadline_text", sa.String(64), nullable=False, server_default=""),
        sa.Column("deadline_rule", sa.String(32), nullable=False, server_default="month_15"),
        sa.Column("due_month_offset", sa.Integer()),
        sa.Column("due_day", sa.Integer()),
        sa.Column("levy_rate", sa.Numeric(12, 6)),
        sa.Column("tax_rate", sa.Numeric(14, 6)),
        sa.Column("rate_unit", sa.String(32), nullable=False, server_default=""),
        sa.Column("main_flag", sa.String(16), nullable=False, server_default="main"),
        sa.Column("collection_method", sa.String(16), nullable=False, server_default="self"),
        sa.Column("return_group", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("note", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "company_id",
            "levy_item",
            "levy_category",
            "levy_sub_item",
            "filing_cycle",
            name="uq_tax_registration_row",
        ),
        sa.CheckConstraint(
            "filing_cycle IN ('month','quarter','year','other')",
            name="ck_tax_registration_cycle",
        ),
        sa.CheckConstraint("status IN ('active','inactive')", name="ck_tax_registration_status"),
        sa.CheckConstraint(
            "main_flag IN ('main','vat_attachment')", name="ck_tax_registration_main_flag"
        ),
        sa.CheckConstraint(
            "collection_method IN ('self','withholding')",
            name="ck_tax_registration_collection",
        ),
        sa.CheckConstraint(
            "deadline_rule IN ('month_15','quarter_15','year_15','next_year_0331',"
            "'next_year_0531','next_year_0630','next_year_0930','custom','none')",
            name="ck_tax_registration_deadline_rule",
        ),
    )
    op.create_index(
        "ix_tax_registrations_company_status", "tax_registrations", ["company_id", "status"]
    )


def downgrade() -> None:
    if "tax_registrations" not in _existing_tables():
        return
    op.drop_index("ix_tax_registrations_company_status", table_name="tax_registrations")
    op.drop_table("tax_registrations")
