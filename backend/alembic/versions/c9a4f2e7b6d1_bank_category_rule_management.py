# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Expand bank category rules into tenant-scoped, observable business rules.

Revision ID: c9a4f2e7b6d1
Revises: b3f8c1d6e2a4
"""

from alembic import op
import sqlalchemy as sa


revision = "c9a4f2e7b6d1"
down_revision = "b3f8c1d6e2a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = [
        sa.Column("id", sa.Text(), nullable=True),
        sa.Column("counterparty_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("purpose_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("account_code", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.98"),
        sa.Column("source_statement_id", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("rule_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_hit_at", sa.Text(), nullable=True),
        sa.Column("last_hit_statement_id", sa.Text(), nullable=True),
        sa.Column("override_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amount_min", sa.Numeric(20, 2), nullable=True),
        sa.Column("amount_max", sa.Numeric(20, 2), nullable=True),
        sa.Column("amount_hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=True),
    ]
    for column in columns:
        op.add_column("bank_category_rules", column)

    op.execute(
        """
        UPDATE bank_category_rules
        SET id = 'legacy|' || company_id || '|' || direction || '|' || match_pattern
        WHERE id IS NULL OR id = ''
        """
    )
    op.execute(
        """
        UPDATE bank_category_rules
        SET counterparty_key = match_pattern
        WHERE counterparty_key IS NULL OR counterparty_key = ''
        """
    )
    op.execute(
        """
        UPDATE bank_category_rules
        SET created_at = updated_at
        WHERE created_at IS NULL OR created_at = ''
        """
    )
    op.create_index(
        "idx_bank_category_rule_id",
        "bank_category_rules",
        ["id"],
        unique=True,
    )
    op.create_index(
        "idx_bank_category_rule_lookup",
        "bank_category_rules",
        ["company_id", "direction", "enabled", "counterparty_key", "purpose_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_bank_category_rule_lookup", table_name="bank_category_rules")
    op.drop_index("idx_bank_category_rule_id", table_name="bank_category_rules")
    with op.batch_alter_table("bank_category_rules") as batch_op:
        for name in (
            "created_at",
            "amount_hit_count",
            "amount_max",
            "amount_min",
            "override_count",
            "last_hit_statement_id",
            "last_hit_at",
            "hit_count",
            "rule_version",
            "enabled",
            "created_by",
            "source_statement_id",
            "confidence",
            "account_code",
            "purpose_key",
            "counterparty_key",
            "id",
        ):
            batch_op.drop_column(name)
