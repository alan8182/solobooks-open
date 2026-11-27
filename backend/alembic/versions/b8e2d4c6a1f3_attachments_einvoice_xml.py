# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""发票电子原件归档：attachments 表 + invoices 查验字段。

Phase 1 单据整理（数电票 XML，回应财会〔2020〕6号 / 财会〔2025〕9号）：
- ``attachments``：原始凭证附件表，挂 ``invoice_id``（SET NULL）。原件不可变，
  只允许作废标记（status='void'）+ 审计留痕，文件目录只增不减（A2）。
- ``invoices.verify_status`` / ``verified_at``：用户在电子税务局查验后的人工标记，
  回应 6 号文第三条「查验合法真实」。

回滚方式：downgrade drop 表与两列；磁盘附件目录不动（只增不减），
对凭证与账务零影响——本阶段不写任何凭证路径代码。

Revision ID: b8e2d4c6a1f3
Revises: e7c1a4d9f2b8
Create Date: 2026-11-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "b8e2d4c6a1f3"
down_revision = "e7c1a4d9f2b8"
branch_labels = None
depends_on = None

_ATTACHMENTS = "attachments"
_INVOICES = "invoices"
_VERIFY_COLUMNS = ("verify_status", "verified_at")


def _column_names(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_ATTACHMENTS):
        op.create_table(
            _ATTACHMENTS,
            sa.Column("id", sa.String(128), primary_key=True),
            sa.Column(
                "company_id",
                sa.String(128),
                sa.ForeignKey("auth_companies.company_id", ondelete="CASCADE"),
                nullable=False,
            ),
            # 挂发票主数据而非凭证：冲销重记时 invoices.entry_id 被迁移
            # （_rebind_invoice_after_repost），附件随主数据走，不产生指向
            # reversed 凭证的悬空引用（A3）。SET NULL：凭证删除会级联删发票行，
            # 原件证据必须存活为「未匹配」等待重新匹配。
            sa.Column(
                "invoice_id",
                sa.String(128),
                sa.ForeignKey("invoices.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("document_type", sa.String(32), nullable=False, server_default="other"),
            sa.Column("file_name", sa.String(255), nullable=False),
            sa.Column("file_ext", sa.String(16), nullable=False, server_default=""),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("content_sha256", sa.String(64), nullable=False),
            # 落盘文件名（相对 ATTACHMENTS_DIR/{company_id[:12]}，内部命名 id.ext）
            sa.Column("stored_name", sa.String(255), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            # 数电票 XML 的完整解析结果（含明细行）；版式文件为空
            sa.Column("parsed_json", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=True),
            sa.Column("source", sa.String(16), nullable=False, server_default="upload"),
            sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
            sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("void_reason", sa.String(255), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint(
                "document_type IN ('einvoice_xml','einvoice_pdf','einvoice_ofd','other')",
                name="ck_attachment_document_type",
            ),
            sa.CheckConstraint("status IN ('active','void')", name="ck_attachment_status"),
            sa.UniqueConstraint("company_id", "content_sha256", name="uq_attachment_company_sha256"),
        )
        op.create_index("ix_attachments_company_invoice", _ATTACHMENTS, ["company_id", "invoice_id"])
        op.create_index("ix_attachments_company_created", _ATTACHMENTS, ["company_id", "created_at"])

    if sa.inspect(bind).has_table(_INVOICES):
        existing = _column_names(bind, _INVOICES)
        if "verify_status" not in existing:
            op.add_column(
                _INVOICES,
                sa.Column("verify_status", sa.String(16), nullable=False, server_default="unverified"),
            )
        if "verified_at" not in existing:
            op.add_column(_INVOICES, sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_INVOICES):
        existing = _column_names(bind, _INVOICES)
        missing = [name for name in _VERIFY_COLUMNS if name in existing]
        if missing:
            # SQLite 不支持一次删多列，走 batch 重建表；PostgreSQL 同样兼容
            with op.batch_alter_table(_INVOICES) as batch:
                for name in missing:
                    batch.drop_column(name)
    if sa.inspect(bind).has_table(_ATTACHMENTS):
        op.drop_index("ix_attachments_company_created", table_name=_ATTACHMENTS)
        op.drop_index("ix_attachments_company_invoice", table_name=_ATTACHMENTS)
        op.drop_table(_ATTACHMENTS)
