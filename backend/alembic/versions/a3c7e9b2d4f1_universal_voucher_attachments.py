# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""扩展 attachments 通用原始凭证底座：银行回单/对账单、支付截图、报销单、工资表、合同。

方言策略：
- PostgreSQL（生产）：原生 ADD COLUMN IF NOT EXISTS / DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT / CREATE INDEX IF NOT EXISTS；
- SQLite（测试迁移验证）：用冻结列清单显式重建表并刷新 5 条索引，避开 batch_alter_table 反射 named CHECK 产出的损坏 DDL（参见 e9b3d7f2c5a8）。

回滚方式：清理新增 document_type 或非发票挂接字段后执行 downgrade，恢复为仅含发票原件字段与约束的表结构（磁盘文件只增不减）。

Revision ID: a3c7e9b2d4f1
Revises: f1c4e8a2b9d6
Create Date: 2026-11-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "a3c7e9b2d4f1"
down_revision = "f1c4e8a2b9d6"
branch_labels = None
depends_on = None

_TABLE = "attachments"
_CONSTRAINT = "ck_attachment_document_type"
_OLD_TYPES = "'einvoice_xml','einvoice_pdf','einvoice_ofd','other'"
_NEW_TYPES = (
    "'einvoice_xml','einvoice_pdf','einvoice_ofd',"
    "'bank_receipt','bank_receipt_batch','bank_statement_pdf',"
    "'payment_screenshot','reimbursement_form','payroll_sheet','contract','other'"
)

_BASE_COLUMNS = """
    id VARCHAR(128) PRIMARY KEY,
    company_id VARCHAR(128) NOT NULL REFERENCES auth_companies(company_id) ON DELETE CASCADE,
    invoice_id VARCHAR(128) REFERENCES invoices(id) ON DELETE SET NULL,
    {extra_columns}
    document_type VARCHAR(32) NOT NULL DEFAULT 'other',
    file_name VARCHAR(255) NOT NULL,
    file_ext VARCHAR(16) NOT NULL DEFAULT '',
    size_bytes BIGINT NOT NULL DEFAULT 0,
    content_sha256 VARCHAR(64) NOT NULL,
    stored_name VARCHAR(255) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    parsed_json JSON,
    source VARCHAR(16) NOT NULL DEFAULT 'upload',
    created_by VARCHAR(128) NOT NULL DEFAULT '',
    voided_at TIMESTAMP,
    void_reason VARCHAR(255) NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_attachment_document_type CHECK (document_type IN ({doc_types})),
    CONSTRAINT ck_attachment_status CHECK (status IN ('active','void')),
    CONSTRAINT uq_attachment_company_sha256 UNIQUE (company_id, content_sha256)
"""

_NEW_EXTRA_COLUMNS = """
    bank_statement_id VARCHAR(128),
    journal_entry_id VARCHAR(128) REFERENCES journal_entries(id) ON DELETE SET NULL,
    period VARCHAR(16),
    parent_attachment_id VARCHAR(128),
"""

_CORE_COLS = (
    "id",
    "company_id",
    "invoice_id",
    "document_type",
    "file_name",
    "file_ext",
    "size_bytes",
    "content_sha256",
    "stored_name",
    "status",
    "parsed_json",
    "source",
    "created_by",
    "voided_at",
    "void_reason",
    "created_at",
    "updated_at",
)


def _is_postgres(bind) -> bool:
    return bind.dialect.name == "postgresql"


def _column_names(bind, table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(bind).get_columns(table)}


def _index_names(bind, table: str) -> set[str]:
    return {idx["name"] for idx in sa.inspect(bind).get_indexes(table)}


def _table_sql(bind) -> str:
    return str(
        bind.execute(
            sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"),
            {"name": _TABLE},
        ).scalar()
        or ""
    )


def _sqlite_rebuild_upgraded(bind) -> None:
    tmp = f"{_TABLE}_rebuild_tmp"
    existing_cols = _column_names(bind, _TABLE)
    bind.execute(
        sa.text(
            f"CREATE TABLE {tmp} ("
            + _BASE_COLUMNS.format(extra_columns=_NEW_EXTRA_COLUMNS, doc_types=_NEW_TYPES)
            + ")"
        )
    )
    select_parts = []
    target_cols = [
        "id",
        "company_id",
        "invoice_id",
        "bank_statement_id",
        "journal_entry_id",
        "period",
        "parent_attachment_id",
        *_CORE_COLS[3:],
    ]
    for col in target_cols:
        select_parts.append(col if col in existing_cols else f"NULL AS {col}")
    bind.execute(
        sa.text(f"INSERT INTO {tmp} ({', '.join(target_cols)}) SELECT {', '.join(select_parts)} FROM {_TABLE}")
    )
    bind.execute(sa.text(f"DROP TABLE {_TABLE}"))
    bind.execute(sa.text(f"ALTER TABLE {tmp} RENAME TO {_TABLE}"))
    for idx_name, cols in (
        ("ix_attachments_company_invoice", "company_id, invoice_id"),
        ("ix_attachments_company_statement", "company_id, bank_statement_id"),
        ("ix_attachments_company_entry", "company_id, journal_entry_id"),
        ("ix_attachments_company_period", "company_id, period"),
        ("ix_attachments_company_created", "company_id, created_at"),
    ):
        bind.execute(sa.text(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {_TABLE} ({cols})"))


def _sqlite_rebuild_downgraded(bind) -> None:
    tmp = f"{_TABLE}_rebuild_tmp"
    bind.execute(
        sa.text(
            f"CREATE TABLE {tmp} ("
            + _BASE_COLUMNS.format(extra_columns="", doc_types=_OLD_TYPES)
            + ")"
        )
    )
    cols = ", ".join(_CORE_COLS)
    bind.execute(sa.text(f"INSERT INTO {tmp} ({cols}) SELECT {cols} FROM {_TABLE}"))
    bind.execute(sa.text(f"DROP TABLE {_TABLE}"))
    bind.execute(sa.text(f"ALTER TABLE {tmp} RENAME TO {_TABLE}"))
    bind.execute(sa.text(f"CREATE INDEX IF NOT EXISTS ix_attachments_company_invoice ON {_TABLE} (company_id, invoice_id)"))
    bind.execute(sa.text(f"CREATE INDEX IF NOT EXISTS ix_attachments_company_created ON {_TABLE} (company_id, created_at)"))


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        return

    if _is_postgres(bind):
        existing = _column_names(bind, _TABLE)
        if "bank_statement_id" not in existing:
            op.add_column(_TABLE, sa.Column("bank_statement_id", sa.String(128), nullable=True))
        if "journal_entry_id" not in existing:
            op.add_column(
                _TABLE,
                sa.Column("journal_entry_id", sa.String(128), sa.ForeignKey("journal_entries.id", ondelete="SET NULL"), nullable=True),
            )
        if "period" not in existing:
            op.add_column(_TABLE, sa.Column("period", sa.String(16), nullable=True))
        if "parent_attachment_id" not in existing:
            op.add_column(
                _TABLE,
                sa.Column("parent_attachment_id", sa.String(128), sa.ForeignKey("attachments.id", ondelete="SET NULL"), nullable=True),
            )
        bind.execute(sa.text(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CONSTRAINT}"))
        bind.execute(sa.text(f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_CONSTRAINT} CHECK (document_type IN ({_NEW_TYPES}))"))
        existing_idx = _index_names(bind, _TABLE)
        for idx_name, cols in (
            ("ix_attachments_company_statement", ["company_id", "bank_statement_id"]),
            ("ix_attachments_company_entry", ["company_id", "journal_entry_id"]),
            ("ix_attachments_company_period", ["company_id", "period"]),
        ):
            if idx_name not in existing_idx:
                op.create_index(idx_name, _TABLE, cols)
        return

    existing = _column_names(bind, _TABLE)
    raw_sql = _table_sql(bind)
    needs_rebuild = (
        "'bank_receipt'" not in raw_sql
        or not {"bank_statement_id", "journal_entry_id", "period", "parent_attachment_id"} <= existing
    )
    if needs_rebuild:
        _sqlite_rebuild_upgraded(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        return
    if _is_postgres(bind):
        bind.execute(sa.text(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CONSTRAINT}"))
        bind.execute(sa.text(f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_CONSTRAINT} CHECK (document_type IN ({_OLD_TYPES}))"))
        existing_idx = _index_names(bind, _TABLE)
        for idx_name in ("ix_attachments_company_period", "ix_attachments_company_entry", "ix_attachments_company_statement"):
            if idx_name in existing_idx:
                op.drop_index(idx_name, table_name=_TABLE)
        existing = _column_names(bind, _TABLE)
        for col_name in ("parent_attachment_id", "period", "journal_entry_id", "bank_statement_id"):
            if col_name in existing:
                op.drop_column(_TABLE, col_name)
        return

    if {"bank_statement_id", "journal_entry_id", "period", "parent_attachment_id"} & _column_names(bind, _TABLE):
        _sqlite_rebuild_downgraded(bind)
