# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""P1 WP-B：固定资产现代化——legacy payload-blob 模块搬进核心 ORM。

三张表一次建齐（FixedAsset 卡片 / DepreciationSchedule 折旧计划 /
FixedAssetDisposal 处置单），照无形资产迁移（547c64a1d455）同款结构。

存量表处置：
- legacy 裸表 fixed_assets（payload blob）与 depreciation_schedule 改名为
  legacy_fixed_assets / legacy_depreciation_schedule，本版本只读保留，
  由 services/fixed_asset_backfill_service 回填进新表后，下一版本退役删除；
- SQLite/PG 的 ALTER TABLE ... RENAME 均原生支持，索引随表走，无需重建；
- 新表 fixed_assets 与改名后的 legacy_fixed_assets 不冲突。

Revision ID: b5c2e8a4f6d1
Revises: e8a3c5f7b1d9
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b5c2e8a4f6d1"
down_revision = "e8a3c5f7b1d9"
branch_labels = None
depends_on = None

ASSET_STATUS_VALUES = (
    "'draft','pending_review','active','idle','fully_depreciated','disposing','disposed','scrapped'"
)


def _existing_tables() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def _rename_legacy(existing: set[str], old: str, new: str) -> None:
    if old in existing and new not in existing:
        op.execute(f"ALTER TABLE {old} RENAME TO {new}")


def upgrade() -> None:
    existing = _existing_tables()
    _rename_legacy(existing, "fixed_assets", "legacy_fixed_assets")
    _rename_legacy(existing, "depreciation_schedule", "legacy_depreciation_schedule")

    # ── 1. 固定资产卡片主表 ───────────────────────────────────────────
    op.create_table(
        "fixed_assets",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(128),
            sa.ForeignKey("auth_companies.company_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("acquisition_type", sa.String(24), nullable=False, server_default="purchase"),
        sa.Column("acquisition_date", sa.Date(), nullable=False),
        sa.Column("available_for_use_date", sa.Date(), nullable=False),
        sa.Column("original_value", sa.Numeric(20, 2), nullable=False),
        sa.Column("input_vat_amount", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("payment_method", sa.String(24), nullable=False, server_default="company_account"),
        sa.Column("residual_value", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("residual_rate", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("useful_life_months", sa.Integer(), nullable=False),
        sa.Column("depreciation_method", sa.String(24), nullable=False, server_default="straight_line"),
        sa.Column("accumulated_depreciation", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("last_depreciated_period", sa.String(7)),
        sa.Column("depreciation_end_period", sa.String(7)),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column(
            "source_entry_id",
            sa.String(128),
            sa.ForeignKey("journal_entries.id", ondelete="SET NULL"),
        ),
        sa.Column("department_id", sa.String(128), sa.ForeignKey("departments.id", ondelete="SET NULL")),
        sa.Column("custodian", sa.String(128)),
        sa.Column("location", sa.String(255)),
        sa.Column("rd_project_id", sa.String(128), sa.ForeignKey("rd_projects.id", ondelete="SET NULL")),
        sa.Column("rd_use_rate", sa.Numeric(12, 6)),
        sa.Column("rd_allocation_basis", sa.String(255)),
        sa.Column("tax_life_months", sa.Integer()),
        sa.Column("disposed_on", sa.Date()),
        sa.Column("disposal_type", sa.String(24)),
        sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("updated_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "category IN ('building','machine','vehicle','electronics','furniture','other')",
            name="ck_fixed_asset_category",
        ),
        sa.CheckConstraint(
            "acquisition_type IN ('purchase','self_built','investor_contribution','donation')",
            name="ck_fixed_asset_acq_type",
        ),
        sa.CheckConstraint(f"status IN ({ASSET_STATUS_VALUES})", name="ck_fixed_asset_status"),
        sa.CheckConstraint(
            "depreciation_method IN ('straight_line','one_time')", name="ck_fixed_asset_method"
        ),
        sa.CheckConstraint("original_value > 0", name="ck_fixed_asset_original"),
        sa.CheckConstraint(
            "residual_value >= 0 AND residual_value < original_value", name="ck_fixed_asset_residual"
        ),
        sa.CheckConstraint("useful_life_months > 0", name="ck_fixed_asset_life"),
        sa.CheckConstraint(
            "rd_use_rate IS NULL OR (rd_use_rate >= 0 AND rd_use_rate <= 1)",
            name="ck_fixed_asset_rd_rate",
        ),
    )
    op.create_index(
        "uq_fixed_asset_company_code",
        "fixed_assets",
        ["company_id", "code"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_fixed_asset_source_entry",
        "fixed_assets",
        ["company_id", "source_entry_id"],
        unique=True,
        sqlite_where=sa.text("source_entry_id IS NOT NULL AND deleted_at IS NULL"),
        postgresql_where=sa.text("source_entry_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index("ix_fixed_asset_company_status", "fixed_assets", ["company_id", "status"])
    op.create_index("ix_fixed_asset_company_category", "fixed_assets", ["company_id", "category"])
    op.create_index("ix_fixed_asset_company_available", "fixed_assets", ["company_id", "available_for_use_date"])

    # ── 2. 折旧计划表 ─────────────────────────────────────────────────
    op.create_table(
        "fixed_asset_depreciation_schedule",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(128),
            sa.ForeignKey("auth_companies.company_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            sa.String(128),
            sa.ForeignKey("fixed_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_start", sa.String(7), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(20, 2), nullable=False),
        sa.Column("accumulated_before", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("accumulated_after", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="planned"),
        sa.Column(
            "journal_entry_id",
            sa.String(128),
            sa.ForeignKey("journal_entries.id", ondelete="SET NULL"),
        ),
        sa.Column("plan_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('planned','posted','reversed','skipped')", name="ck_fixed_asset_dep_status"
        ),
        sa.CheckConstraint("amount > 0", name="ck_fixed_asset_dep_amount"),
        sa.CheckConstraint(
            # SQLite 以 REAL 存 NUMERIC，等式比较必须按两位舍入，避免二进制浮点漂移
            "ROUND(accumulated_after, 2) = ROUND(accumulated_before + amount, 2)",
            name="ck_fixed_asset_dep_cumulative",
        ),
        sa.UniqueConstraint("company_id", "asset_id", "period_start", name="uq_fixed_asset_dep_company_asset_period"),
        sa.UniqueConstraint("company_id", "asset_id", "sequence_no", name="uq_fixed_asset_dep_company_asset_seq"),
    )
    op.create_index(
        "ix_fixed_asset_dep_company_period_status",
        "fixed_asset_depreciation_schedule",
        ["company_id", "period_start", "status"],
    )

    # ── 3. 处置单 ─────────────────────────────────────────────────────
    op.create_table(
        "fixed_asset_disposals",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(128),
            sa.ForeignKey("auth_companies.company_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            sa.String(128),
            sa.ForeignKey("fixed_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("disposal_type", sa.String(24), nullable=False),
        sa.Column("disposal_date", sa.Date(), nullable=False),
        sa.Column("proceeds_amount", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("output_vat_amount", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("accumulated_depreciation_snapshot", sa.Numeric(20, 2), nullable=False),
        sa.Column("net_book_value_snapshot", sa.Numeric(20, 2), nullable=False),
        sa.Column("gain_loss_amount", sa.Numeric(20, 2), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("reason", sa.String(500)),
        sa.Column(
            "journal_entry_id",
            sa.String(128),
            sa.ForeignKey("journal_entries.id", ondelete="SET NULL"),
        ),
        sa.Column("initiated_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("approved_by", sa.String(128)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "disposal_type IN ('sale','scrap','donate','transfer_out')",
            name="ck_fixed_asset_disposal_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft','pending_review','approved','rejected','posted','cancelled')",
            name="ck_fixed_asset_disposal_status",
        ),
        sa.CheckConstraint("net_book_value_snapshot >= 0", name="ck_fixed_asset_disposal_nbv"),
    )
    # 一张在途资产同时只允许一张有效处置单
    op.create_index(
        "uq_fixed_asset_disposal_active",
        "fixed_asset_disposals",
        ["company_id", "asset_id"],
        unique=True,
        sqlite_where=sa.text("status NOT IN ('cancelled','rejected')"),
        postgresql_where=sa.text("status NOT IN ('cancelled','rejected')"),
    )
    op.create_index("ix_fixed_asset_disposal_company_status", "fixed_asset_disposals", ["company_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_fixed_asset_disposal_company_status", table_name="fixed_asset_disposals")
    op.drop_index("uq_fixed_asset_disposal_active", table_name="fixed_asset_disposals")
    op.drop_table("fixed_asset_disposals")
    op.drop_index(
        "ix_fixed_asset_dep_company_period_status", table_name="fixed_asset_depreciation_schedule"
    )
    op.drop_table("fixed_asset_depreciation_schedule")
    op.drop_index("ix_fixed_asset_company_available", table_name="fixed_assets")
    op.drop_index("ix_fixed_asset_company_category", table_name="fixed_assets")
    op.drop_index("ix_fixed_asset_company_status", table_name="fixed_assets")
    op.drop_index("uq_fixed_asset_source_entry", table_name="fixed_assets")
    op.drop_index("uq_fixed_asset_company_code", table_name="fixed_assets")
    op.drop_table("fixed_assets")
    # legacy 裸表不回改名字：回滚即放弃迁移，legacy 数据仍在 legacy_* 表里
