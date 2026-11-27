# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""ledger_book_snapshots.book_kind 放宽 'voucher'（Phase 2 凭证册装订）。

凭证册复用快照表（payload 泛型 JSON / period / book_no / 审计字段全通用），
只放宽 ``ck_book_snapshot_kind`` CHECK，不加列。

方言策略：
- PostgreSQL（生产）：原生 ``ALTER TABLE ... DROP/ADD CONSTRAINT``；
- SQLite（测试库由 create_all 按 ORM 生成，不走本迁移演进）：迁移测试
  需要验证「旧形态真的放宽」，所以对手写重建做完整支持——列清单冻结自
  d1f4a6c8b2e3（建表）+ d2b5f8a1c4e7（补 pdf_* 四列），含全部索引与约束。
  不用 batch 反射：SQLite 对复合 named CHECK 的反射重建会产出损坏 DDL（实测）。

回滚：downgrade 前必须已处理（作废并导出）本账套的 voucher 册——存在
voucher 行时 downgrade 直接报错拒绝执行，绝不静默删用户数据（A2）。

Revision ID: e9b3d7f2c5a8
Revises: b8e2d4c6a1f3
Create Date: 2026-11-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e9b3d7f2c5a8"
down_revision = "b8e2d4c6a1f3"
branch_labels = None
depends_on = None

_TABLE = "ledger_book_snapshots"
_CONSTRAINT = "ck_book_snapshot_kind"
_OLD = "book_kind IN ('monthly','yearly')"
_NEW = "book_kind IN ('monthly','yearly','voucher')"

# 冻结的完整表定义（d1f4a6c8b2e3 建表 + d2b5f8a1c4e7 补列后的生产形态）
_REBUILD_COLUMNS = """
    id VARCHAR(128) PRIMARY KEY,
    company_id VARCHAR(128) NOT NULL,
    book_kind VARCHAR(16) NOT NULL DEFAULT 'monthly',
    period VARCHAR(7) NOT NULL,
    book_no VARCHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'issued',
    payload JSON NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 1,
    line_count INTEGER NOT NULL DEFAULT 0,
    anchor JSON,
    generated_by VARCHAR(128),
    generated_at TIMESTAMP,
    void_reason TEXT,
    voided_by VARCHAR(128),
    voided_at TIMESTAMP,
    pdf_path TEXT,
    pdf_sha256 VARCHAR(64),
    pdf_size_bytes BIGINT,
    pdf_generated_at TIMESTAMP,
    CONSTRAINT ck_book_snapshot_status CHECK (status IN ('issued','void')),
    CONSTRAINT {kind_name} CHECK ({kind_check}),
    CONSTRAINT uq_book_snapshot_number UNIQUE (company_id, book_kind, period, book_no)
"""


def _is_postgres(bind) -> bool:
    return bind.dialect.name == "postgresql"


def _table_sql(bind) -> str:
    return str(
        bind.execute(
            sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"),
            {"name": _TABLE},
        ).scalar()
        or ""
    )


def _sqlite_needs_rebuild(bind, target_check: str) -> bool:
    """幂等守卫：表定义已含目标枚举（按 'voucher' 是否出现判断）则跳过。"""
    raw = _table_sql(bind)
    should_contain = "'voucher'" in target_check
    return ("'voucher'" in raw) != should_contain


def _sqlite_rebuild(bind, kind_check: str) -> None:
    tmp = f"{_TABLE}_rebuild_tmp"
    bind.execute(
        sa.text(_REBUILD_COLUMNS.format(kind_name=_CONSTRAINT, kind_check=kind_check).join(
            [f"CREATE TABLE {tmp} (", ")"]
        ))
    )
    bind.execute(
        sa.text(
            f"INSERT INTO {tmp} SELECT id, company_id, book_kind, period, book_no, status, "
            "payload, content_hash, page_count, line_count, anchor, generated_by, generated_at, "
            "void_reason, voided_by, voided_at, pdf_path, pdf_sha256, pdf_size_bytes, pdf_generated_at "
            f"FROM {_TABLE}"
        )
    )
    bind.execute(sa.text(f"DROP TABLE {_TABLE}"))
    bind.execute(sa.text(f"ALTER TABLE {tmp} RENAME TO {_TABLE}"))


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        return
    if _is_postgres(bind):
        bind.execute(sa.text(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CONSTRAINT}"))
        bind.execute(sa.text(f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_CONSTRAINT} CHECK ({_NEW})"))
        return
    if not _sqlite_needs_rebuild(bind, _NEW):
        return
    _sqlite_rebuild(bind, _NEW)


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        return
    voucher_rows = bind.execute(
        sa.text(f"SELECT COUNT(*) FROM {_TABLE} WHERE book_kind = 'voucher'")
    ).scalar()
    if voucher_rows:
        raise RuntimeError(
            f"存在 {int(voucher_rows)} 册凭证册快照，回滚前请先作废并导出后清理（A2 禁止静默删数据）"
        )
    if _is_postgres(bind):
        bind.execute(sa.text(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CONSTRAINT}"))
        bind.execute(sa.text(f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_CONSTRAINT} CHECK ({_OLD})"))
        return
    if not _sqlite_needs_rebuild(bind, _OLD):
        return
    _sqlite_rebuild(bind, _OLD)
