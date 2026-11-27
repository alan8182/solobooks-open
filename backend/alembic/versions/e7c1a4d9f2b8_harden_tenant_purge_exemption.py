# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""不可变触发器豁免收紧：只有数据库属主可开启主体清理开关。

背景：c1d3e5f7a9b2 引入的 `solobooks.allow_tenant_purge` 是普通会话级 GUC，
PostgreSQL 允许**任意角色** `set_config(...)`。也就是说应用角色
（solobooks_app）自己就能打开豁免，触发器只剩「防误改」，防不了应用被 SQL
注入或逻辑 bug 绕过——这与 A1「已过账不可变」的承诺不符。

做法：豁免必须同时满足
1. GUC 为 'on'；
2. `current_user` 是当前数据库的属主（deploy 中即 solobooks_owner）。

应用角色不是属主，即使自己设置 GUC 也会被触发器拒绝；主体永久删除改由
services/company_delete_service 用专用属主连接执行（TENANT_PURGE_DATABASE_URL），
正常业务路径仍走应用角色。

回滚：执行 `alembic downgrade` 恢复 c1d3e5f7a9b2 的「仅检查 GUC」版本。
"""
from __future__ import annotations

from alembic import op

revision = "e7c1a4d9f2b8"
down_revision = "c6f9d2a1b8e4"
branch_labels = None
depends_on = None


_HARDENED_FUNCTIONS = """
CREATE OR REPLACE FUNCTION prevent_posted_journal_mutation()
RETURNS trigger AS $$
BEGIN
    IF current_setting('solobooks.allow_tenant_purge', true) = 'on'
       AND current_user = (
           SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()
       ) THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    IF TG_OP = 'DELETE' AND OLD.status IN ('posted', 'reversed') THEN
        RAISE EXCEPTION 'ENTRY_POSTED_IMMUTABLE:%', OLD.id;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.status IN ('posted', 'reversed') THEN
        IF NOT (
            OLD.status = 'posted' AND NEW.status = 'reversed'
            AND OLD.company_id = NEW.company_id
            AND OLD.occurred_on = NEW.occurred_on
            AND OLD.period_start = NEW.period_start
            AND OLD.summary = NEW.summary
            AND OLD.voucher_no IS NOT DISTINCT FROM NEW.voucher_no
        ) THEN
            RAISE EXCEPTION 'ENTRY_POSTED_IMMUTABLE:%', OLD.id;
        END IF;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION prevent_posted_line_mutation()
RETURNS trigger AS $$
DECLARE parent_status text;
BEGIN
    IF current_setting('solobooks.allow_tenant_purge', true) = 'on'
       AND current_user = (
           SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()
       ) THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    SELECT status INTO parent_status FROM journal_entries
    WHERE id = COALESCE(OLD.journal_entry_id, NEW.journal_entry_id);
    IF parent_status IN ('posted', 'reversed') THEN
        RAISE EXCEPTION 'ENTRY_POSTED_IMMUTABLE:%', COALESCE(OLD.journal_entry_id, NEW.journal_entry_id);
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION prevent_audit_mutation()
RETURNS trigger AS $$
BEGIN
    IF current_setting('solobooks.allow_tenant_purge', true) = 'on'
       AND current_user = (
           SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()
       ) THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    RAISE EXCEPTION 'AUDIT_EVENT_IMMUTABLE:%', OLD.id;
END;
$$ LANGUAGE plpgsql;
"""

# c1d3e5f7a9b2 的版本：任意角色打开 GUC 即可豁免。
_LEGACY_FUNCTIONS = """
CREATE OR REPLACE FUNCTION prevent_posted_journal_mutation()
RETURNS trigger AS $$
BEGIN
    IF current_setting('solobooks.allow_tenant_purge', true) = 'on' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    IF TG_OP = 'DELETE' AND OLD.status IN ('posted', 'reversed') THEN
        RAISE EXCEPTION 'ENTRY_POSTED_IMMUTABLE:%', OLD.id;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.status IN ('posted', 'reversed') THEN
        IF NOT (
            OLD.status = 'posted' AND NEW.status = 'reversed'
            AND OLD.company_id = NEW.company_id
            AND OLD.occurred_on = NEW.occurred_on
            AND OLD.period_start = NEW.period_start
            AND OLD.summary = NEW.summary
            AND OLD.voucher_no IS NOT DISTINCT FROM NEW.voucher_no
        ) THEN
            RAISE EXCEPTION 'ENTRY_POSTED_IMMUTABLE:%', OLD.id;
        END IF;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION prevent_posted_line_mutation()
RETURNS trigger AS $$
DECLARE parent_status text;
BEGIN
    IF current_setting('solobooks.allow_tenant_purge', true) = 'on' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    SELECT status INTO parent_status FROM journal_entries
    WHERE id = COALESCE(OLD.journal_entry_id, NEW.journal_entry_id);
    IF parent_status IN ('posted', 'reversed') THEN
        RAISE EXCEPTION 'ENTRY_POSTED_IMMUTABLE:%', COALESCE(OLD.journal_entry_id, NEW.journal_entry_id);
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION prevent_audit_mutation()
RETURNS trigger AS $$
BEGIN
    IF current_setting('solobooks.allow_tenant_purge', true) = 'on' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    RAISE EXCEPTION 'AUDIT_EVENT_IMMUTABLE:%', OLD.id;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(_HARDENED_FUNCTIONS)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(_LEGACY_FUNCTIONS)
