# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""P0 WP5：备份归档留痕表（backup_records）。

归档工作簿落盘 backend/data/archives/，本表记录每次导出的期间、
文件、大小与数据指纹，供"重新下载"和审计对账。

Revision ID: c9e3a1f6d4b8
Revises: b8d2f0e5c3a7
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9e3a1f6d4b8"
down_revision = "b8d2f0e5c3a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backup_records",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(128),
            sa.ForeignKey("auth_companies.company_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False, server_default=""),
        sa.Column("period_from", sa.String(7), nullable=False, server_default=""),
        sa.Column("period_to", sa.String(7), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("voucher_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data_fingerprint", sa.String(128), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_backup_records_company_created", "backup_records", ["company_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_backup_records_company_created", table_name="backup_records")
    op.drop_table("backup_records")
