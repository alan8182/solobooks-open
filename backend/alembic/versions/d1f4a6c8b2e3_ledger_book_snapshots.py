# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""电子账簿快照表：签发即定版，可重打、可对照。

Revision ID: d1f4a6c8b2e3
Revises: c7a1d3f5b9e2
Create Date: 2026-10-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d1f4a6c8b2e3"
down_revision = "c7a1d3f5b9e2"
branch_labels = None
depends_on = None


JSON_VALUE = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def _existing_tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "ledger_book_snapshots" in _existing_tables():
        return
    op.create_table(
        "ledger_book_snapshots",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(128),
            sa.ForeignKey("auth_companies.company_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("book_kind", sa.String(16), nullable=False, server_default="monthly"),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("book_no", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="issued"),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("anchor", JSON_VALUE, nullable=False),
        sa.Column("generated_by", sa.String(128)),
        sa.Column("generated_at", sa.DateTime(timezone=True)),
        sa.Column("void_reason", sa.Text()),
        sa.Column("voided_by", sa.String(128)),
        sa.Column("voided_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "company_id", "book_kind", "period", "book_no", name="uq_book_snapshot_number"
        ),
        sa.CheckConstraint("book_kind IN ('monthly','yearly')", name="ck_book_snapshot_kind"),
        sa.CheckConstraint("status IN ('issued','void')", name="ck_book_snapshot_status"),
    )
    op.create_index(
        "ix_book_snapshot_company_period",
        "ledger_book_snapshots",
        ["company_id", "period"],
    )


def downgrade() -> None:
    if "ledger_book_snapshots" not in _existing_tables():
        return
    op.drop_index("ix_book_snapshot_company_period", table_name="ledger_book_snapshots")
    op.drop_table("ledger_book_snapshots")
