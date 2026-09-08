"""Fix PostgreSQL partial unique indexes for optional identities.

Revision ID: e2f6a8c41d73
Revises: d7e4b2c91a06
"""

from alembic import op
import sqlalchemy as sa


revision = "e2f6a8c41d73"
down_revision = "d7e4b2c91a06"
branch_labels = None
depends_on = None


def _create_partial_indexes() -> None:
    op.create_index(
        "uniq_auth_users_openid_nonempty",
        "auth_users",
        ["openid"],
        unique=True,
        sqlite_where=sa.text("openid != ''"),
        postgresql_where=sa.text("openid != ''"),
    )
    op.create_index(
        "uniq_auth_users_phone_nonempty",
        "auth_users",
        ["phone"],
        unique=True,
        sqlite_where=sa.text("phone != ''"),
        postgresql_where=sa.text("phone != ''"),
    )


def upgrade() -> None:
    op.drop_index("uniq_auth_users_openid_nonempty", table_name="auth_users")
    op.drop_index("uniq_auth_users_phone_nonempty", table_name="auth_users")
    _create_partial_indexes()


def downgrade() -> None:
    op.drop_index("uniq_auth_users_openid_nonempty", table_name="auth_users")
    op.drop_index("uniq_auth_users_phone_nonempty", table_name="auth_users")
    op.create_index(
        "uniq_auth_users_openid_nonempty", "auth_users", ["openid"],
        unique=True, sqlite_where=sa.text("openid != ''"),
    )
    op.create_index(
        "uniq_auth_users_phone_nonempty", "auth_users", ["phone"],
        unique=True, sqlite_where=sa.text("phone != ''"),
    )
