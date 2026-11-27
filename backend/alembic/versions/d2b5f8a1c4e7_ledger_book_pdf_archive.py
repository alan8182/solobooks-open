# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""电子账簿快照补 PDF 归档字段。

签发时除了 JSON 快照，还必须落盘一份带封面/目录/书签的正式 PDF：
- ``pdf_path`` / ``pdf_sha256`` / ``pdf_size_bytes`` 记录归档件位置与完整性；
- ``pdf_generated_at`` 记录落盘时刻，便于审计「签发即归档」。

四列全部可空：迁移前的历史快照没有归档件，下载时只读现渲染，回填必须走
``scripts/repair_ledger_book_pdf.py`` 显式入口（A2 禁止读时静默改数据）。

Revision ID: d2b5f8a1c4e7
Revises: b4e1a7c9d2f3
Create Date: 2026-11-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d2b5f8a1c4e7"
down_revision = "b4e1a7c9d2f3"
branch_labels = None
depends_on = None

_TABLE = "ledger_book_snapshots"


def _column_names(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        return
    existing = _column_names(bind)
    if "pdf_path" not in existing:
        op.add_column(_TABLE, sa.Column("pdf_path", sa.Text(), nullable=True))
    if "pdf_sha256" not in existing:
        op.add_column(_TABLE, sa.Column("pdf_sha256", sa.String(length=64), nullable=True))
    if "pdf_size_bytes" not in existing:
        op.add_column(_TABLE, sa.Column("pdf_size_bytes", sa.BigInteger(), nullable=True))
    if "pdf_generated_at" not in existing:
        op.add_column(_TABLE, sa.Column("pdf_generated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        return
    existing = _column_names(bind)
    missing = [name for name in ("pdf_generated_at", "pdf_size_bytes", "pdf_sha256", "pdf_path") if name in existing]
    if not missing:
        return
    # SQLite 不支持一次删多列，走 batch 重建表；PostgreSQL 同样兼容
    with op.batch_alter_table(_TABLE) as batch:
        for name in missing:
            batch.drop_column(name)
