# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""工资模块补丁：salary_sheets 增加社保/个税缴纳凭证列。

Revision ID: f0a2c4e6b8d1
Revises: e6b8c3f1a7d2
"""

from alembic import op
import sqlalchemy as sa


revision = "f0a2c4e6b8d1"
down_revision = "e6b8c3f1a7d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("salary_sheets", sa.Column("ss_paid_voucher_id", sa.String(128), nullable=True))
    op.add_column("salary_sheets", sa.Column("tax_paid_voucher_id", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("salary_sheets", "tax_paid_voucher_id")
    op.drop_column("salary_sheets", "ss_paid_voucher_id")
