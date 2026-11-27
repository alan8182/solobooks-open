# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""微信开放平台网站应用扫码登录：身份列 + 一次性 ticket 表。

- ``auth_users`` 新增 ``wechat_web_openid`` / ``wechat_unionid`` /
  ``wechat_web_bound_at``：只由网站微信登录的专用绑定函数写入，绝不与
  小程序 ``openid`` 混用；``server_default=""`` 兼容 PG 存量行与裸 SQL 插入。
- 两个身份列建**双方言** partial unique index（空串不参与唯一约束），
  防止同一微信身份被并发绑定到两个账号。
- 新表 ``wechat_login_tickets``：只存 ticket 的 SHA-256，明文仅存在于
  HttpOnly cookie；purpose 区分 login / bind，used_at 保证一次性。

Revision ID: f2b6d8e4a9c1
Revises: e5f1a9c3b7d2
Create Date: 2026-11-26
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "f2b6d8e4a9c1"
down_revision = "e5f1a9c3b7d2"
branch_labels = None
depends_on = None

_IDENTITY_COLUMNS = (
    "wechat_web_openid",
    "wechat_unionid",
    "wechat_web_bound_at",
)
_PARTIAL_INDEXES = (
    ("uniq_auth_users_wechat_web_openid_nonempty", "wechat_web_openid"),
    ("uniq_auth_users_wechat_unionid_nonempty", "wechat_unionid"),
)


def _has_column(bind, table: str, column: str) -> bool:
    return column in {col["name"] for col in sa.inspect(bind).get_columns(table)}


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def _has_index(bind, table: str, index_name: str) -> bool:
    return index_name in {idx["name"] for idx in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "auth_users", "wechat_web_openid"):
        op.add_column(
            "auth_users",
            sa.Column("wechat_web_openid", sa.String(255), nullable=False, server_default=""),
        )
    if not _has_column(bind, "auth_users", "wechat_unionid"):
        op.add_column(
            "auth_users",
            sa.Column("wechat_unionid", sa.String(255), nullable=False, server_default=""),
        )
    if not _has_column(bind, "auth_users", "wechat_web_bound_at"):
        op.add_column(
            "auth_users",
            sa.Column("wechat_web_bound_at", sa.Text(), nullable=False, server_default=""),
        )

    for index_name, column in _PARTIAL_INDEXES:
        if _has_column(bind, "auth_users", column) and not _has_index(bind, "auth_users", index_name):
            op.create_index(
                index_name,
                "auth_users",
                [column],
                unique=True,
                sqlite_where=sa.text(f"{column} != ''"),
                postgresql_where=sa.text(f"{column} != ''"),
            )

    if not _has_table(bind, "wechat_login_tickets"):
        op.create_table(
            "wechat_login_tickets",
            sa.Column("ticket_hash", sa.String(64), primary_key=True),
            sa.Column("openid", sa.String(255), nullable=False, server_default=""),
            sa.Column("unionid", sa.String(255), nullable=False, server_default=""),
            sa.Column("nickname", sa.String(255), nullable=False, server_default=""),
            sa.Column("avatar", sa.Text(), nullable=False, server_default=""),
            sa.Column("privacy_accepted_at", sa.Text(), nullable=False, server_default=""),
            sa.Column("tos_version", sa.String(32), nullable=False, server_default=""),
            sa.Column("privacy_version", sa.String(32), nullable=False, server_default=""),
            sa.Column("purpose", sa.String(16), nullable=False),
            sa.Column("user_id", sa.String(128), nullable=True),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.Text(), nullable=False),
            sa.Column("used_at", sa.Text(), nullable=False, server_default=""),
        )
        op.create_index(
            "ix_wechat_login_tickets_expires",
            "wechat_login_tickets",
            ["expires_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()

    if _has_table(bind, "wechat_login_tickets"):
        op.drop_index("ix_wechat_login_tickets_expires", table_name="wechat_login_tickets")
        op.drop_table("wechat_login_tickets")

    for index_name, _column in _PARTIAL_INDEXES:
        if _has_index(bind, "auth_users", index_name):
            op.drop_index(index_name, table_name="auth_users")

    cols = [c for c in _IDENTITY_COLUMNS if _has_column(bind, "auth_users", c)]
    if not cols:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("auth_users") as batch:
            for col in cols:
                batch.drop_column(col)
    else:
        for col in cols:
            op.drop_column("auth_users", col)
