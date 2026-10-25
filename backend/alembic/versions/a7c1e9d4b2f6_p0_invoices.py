# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""P0 WP1：发票规范化主数据（invoices 表）。

把发票从 entries.payload 的"影子数据"提升为一等实体：
- 进销项台账可直接查，不必再 json_extract 全表扫；
- 去重走 (company_id, dedup_fingerprint) 唯一约束；
- 为后续一键取票 / 价税自动化留落点。

存量回填不在迁移里做（数据量大、需按公司幂等重跑），由
services/invoice_ledger_service.backfill_invoices 在首次访问台账时执行。

Revision ID: a7c1e9d4b2f6
Revises: c4d91a6b2e07
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7c1e9d4b2f6"
down_revision = "c4d91a6b2e07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoices",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(128),
            sa.ForeignKey("auth_companies.company_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entry_id",
            sa.String(128),
            sa.ForeignKey("journal_entries.id", ondelete="SET NULL"),
        ),
        sa.Column("direction", sa.String(16), nullable=False, server_default="expense"),
        sa.Column("invoice_number", sa.String(64), nullable=False, server_default=""),
        sa.Column("invoice_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("invoice_type", sa.String(64), nullable=False, server_default=""),
        sa.Column("invoice_status", sa.String(32), nullable=False, server_default="normal"),
        sa.Column("invoice_date", sa.Date()),
        sa.Column("buyer_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("buyer_tax_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("seller_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("seller_tax_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("amount_excl_tax", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("amount_incl_tax", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("tax_rate", sa.String(16), nullable=False, server_default=""),
        sa.Column("item_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("remark", sa.Text(), nullable=False, server_default=""),
        sa.Column("partner_id", sa.String(128)),
        sa.Column("dedup_fingerprint", sa.String(255), nullable=False, server_default=""),
        sa.Column("source", sa.String(32), nullable=False, server_default="invoice_import"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("direction IN ('income','expense')", name="ck_invoice_direction"),
        sa.CheckConstraint("invoice_status IN ('normal','red','reversed')", name="ck_invoice_status"),
        sa.UniqueConstraint("company_id", "dedup_fingerprint", name="uq_invoice_company_fingerprint"),
    )
    op.create_index("ix_invoice_company_number", "invoices", ["company_id", "invoice_number"])
    op.create_index("ix_invoice_company_date", "invoices", ["company_id", "invoice_date"])
    op.create_index(
        "ix_invoice_company_direction_date", "invoices", ["company_id", "direction", "invoice_date"]
    )


def downgrade() -> None:
    op.drop_index("ix_invoice_company_direction_date", table_name="invoices")
    op.drop_index("ix_invoice_company_date", table_name="invoices")
    op.drop_index("ix_invoice_company_number", table_name="invoices")
    op.drop_table("invoices")
