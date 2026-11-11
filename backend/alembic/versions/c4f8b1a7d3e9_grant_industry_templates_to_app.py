# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""补 industry_templates 对应用角色的表权限。

现象：新增主体页「所属行业」下拉永远为空。GET /api/industry-templates 返回 500，
日志里是 asyncpg ``permission denied for table industry_templates``——应用连的是
``solobooks_app``，而该表的 owner 是 ``solobooks_owner`` 且 relacl 为空。

根因：d4b7e2a9c6f1 建表时未继承 schema 的 ALTER DEFAULT PRIVILEGES。这是全库唯一
一张缺 SELECT 的表（其余 60+ 张均有 ``solobooks_app=arwdDxt``），说明该表的创建
路径绕过了默认权限——本地库是在 owner 会话里手工/脚本建的，而非走应用 DDL。缺的
是授权，不是数据：表为空、seed 由 list_templates 在首次读取时补齐。

修法：按 schema 现有默认权限给应用角色补同一套 ACL。用 DO 块 + 角色存在性判断，
SQLite（本地测试库）下整个动作跳过——它没有角色概念，也没有这道权限墙。

Revision ID: c4f8b1a7d3e9
Revises: a2b4c6d8e0f1
Create Date: 2026-11-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "c4f8b1a7d3e9"
down_revision = "a2b4c6d8e0f1"
branch_labels = None
depends_on = None

# 与 deploy/postgres/init/001-application-role.sh 的默认权限保持一致，
# 免得下次新建表又从另一条路径漏掉。
_APP_ROLE_SQL = """
DO $$
DECLARE
    app_role text;
BEGIN
    SELECT rolname INTO app_role FROM pg_roles WHERE rolname = 'solobooks_app';
    IF app_role IS NULL THEN
        RETURN;
    END IF;
    EXECUTE format(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.industry_templates TO %I',
        app_role
    );
END
$$;
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(sa.text(_APP_ROLE_SQL))


def downgrade() -> None:
    # 不回收：回收会让「所属行业」下拉再次变空，而这正是本迁移要修的事故。
    # 表权限是运行必需品，不是可回滚的数据变更。
    pass
