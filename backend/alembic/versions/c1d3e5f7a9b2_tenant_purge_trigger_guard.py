# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""主体永久删除：给不可变触发器增加受控豁免开关。

背景：journal_entries / journal_lines / audit_events 的不可变触发器会无条件
拒绝 DELETE，导致「删除主体并清空全部数据」在 PostgreSQL 上无法执行。

做法：触发器开头检查会话级 GUC `solobooks.allow_tenant_purge`；只有删除主体的
清理事务会 `set_config(..., true)` 打开它。正常业务路径不受影响，UPDATE 保护
也保持不变。
"""
from __future__ import annotations

from alembic import op

revision = "c1d3e5f7a9b2"
down_revision = "f7a9c1e3d5b7"
branch_labels = None
depends_on = None


_GUARDED_FUNCTIONS = """
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

_ORIGINAL_FUNCTIONS = """
CREATE OR REPLACE FUNCTION prevent_posted_journal_mutation()
RETURNS trigger AS $$
BEGIN
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
    RAISE EXCEPTION 'AUDIT_EVENT_IMMUTABLE:%', OLD.id;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(_GUARDED_FUNCTIONS)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(_ORIGINAL_FUNCTIONS)
