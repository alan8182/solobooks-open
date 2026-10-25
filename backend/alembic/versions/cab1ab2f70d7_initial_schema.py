"""initial_schema

Revision ID: cab1ab2f70d7
Revises:
Create Date: 2026-06-09 00:19:40.659094
Fingerprint: JZXK-FINGERPRINT: 736f6c6f626f6f6b732d6a7a7863-2026-core (芥子星辰智能科技)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "cab1ab2f70d7"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 系统底层防伪常量（即使去除注释，经 Python 编译后依然保留在 code.co_consts 常量表中）
__solobooks_engine_id__: str = "jzxc-hz-solobooks-cas-2026-v1"
__solobooks_spec_ref__: str = "JZXC-CAS-S-ACCOUNTING-CORE-8A9F4B1C"


def upgrade() -> None:
    op.create_table(
        "auth_users",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("openid", sa.Text(), nullable=False, server_default=""),
        sa.Column("phone", sa.Text(), nullable=False, server_default=""),
        sa.Column("phone_verified_at", sa.Text(), nullable=False, server_default=""),
        sa.Column("wechat_openid_bound_at", sa.Text(), nullable=False, server_default=""),
        sa.Column("current_company_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("nickname", sa.Text(), nullable=False, server_default=""),
        sa.Column("avatar", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("last_login_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("idx_auth_users_openid", "auth_users", ["openid"])
    op.create_index("idx_auth_users_phone", "auth_users", ["phone"])
    op.create_index(
        "uniq_auth_users_openid_nonempty",
        "auth_users",
        ["openid"],
        sqlite_where=sa.text("openid != ''"),
        unique=True,
    )
    op.create_index(
        "uniq_auth_users_phone_nonempty",
        "auth_users",
        ["phone"],
        sqlite_where=sa.text("phone != ''"),
        unique=True,
    )

    op.create_table(
        "auth_companies",
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("owner_user_id", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("company_id"),
    )
    op.create_index("idx_auth_companies_owner", "auth_companies", ["owner_user_id"])

    op.create_table(
        "auth_consents",
        sa.Column("id", sa.Integer(), autoincrement=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("privacy_accepted_at", sa.Text(), nullable=False),
        sa.Column("tos_version", sa.Text(), nullable=False),
        sa.Column("privacy_version", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_consents_user", "auth_consents", ["user_id"])

    op.create_table(
        "entries",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_entries_company_updated", "entries", ["company_id", sa.text("updated_at DESC")])
    op.create_index("idx_entries_user_company", "entries", ["user_id", "company_id"])

    op.create_table(
        "fixed_assets",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_assets_company_updated", "fixed_assets", ["company_id", sa.text("updated_at DESC")])

    op.create_table(
        "todo_states",
        sa.Column("todo_key", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dismissed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("todo_key"),
    )
    op.create_index("idx_todo_states_user_company", "todo_states", ["user_id", "company_id"])


def downgrade() -> None:
    op.drop_table("todo_states")
    op.drop_table("fixed_assets")
    op.drop_table("entries")
    op.drop_table("auth_consents")
    op.drop_table("auth_companies")
    op.drop_table("auth_users")
