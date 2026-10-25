# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""无形资产模块：资产主表 / 摊销计划 / 处置单 / 减值测试记录（P0+P1）。

四张表一次建齐；减值测试（impairment_tests）表结构先落地，服务端 P2 启用。
部分唯一索引（软删除 / 有效处置单唯一）使用 postgresql_where + sqlite_where
双方言 where，Alembic 1.15 的 op.create_index 直接透传这两个方言参数。

Revision ID: 547c64a1d455
Revises: c9e3a1f6d4b8
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "547c64a1d455"
down_revision = "c9e3a1f6d4b8"
branch_labels = None
depends_on = None


ASSET_STATUS_VALUES = (
    "'draft','pending_review','active','idle','fully_amortized','disposing','disposed','scrapped'"
)


def upgrade() -> None:
    # ── 1. 无形资产主表 ────────────────────────────────────────────────
    op.create_table(
        "intangible_assets",
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
        sa.Column("amortization_method", sa.String(24), nullable=False, server_default="straight_line"),
        sa.Column("accumulated_amortization", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("last_amortized_period", sa.String(7)),
        sa.Column("amortization_end_period", sa.String(7)),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column(
            "source_entry_id",
            sa.String(128),
            sa.ForeignKey("journal_entries.id", ondelete="SET NULL"),
        ),
        sa.Column("legal_cert_no", sa.String(128)),
        sa.Column("grant_org", sa.String(128)),
        sa.Column("expiry_date", sa.Date()),
        sa.Column("department_id", sa.String(128), sa.ForeignKey("departments.id", ondelete="SET NULL")),
        sa.Column("custodian", sa.String(128)),
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
            "category IN ('patent','trademark','copyright','land_use_right',"
            "'software','franchise','non_patent_technology','other')",
            name="ck_intangible_asset_category",
        ),
        sa.CheckConstraint(
            "acquisition_type IN ('purchase','self_developed','investor_contribution','donation')",
            name="ck_intangible_asset_acq_type",
        ),
        sa.CheckConstraint(f"status IN ({ASSET_STATUS_VALUES})", name="ck_intangible_asset_status"),
        sa.CheckConstraint("amortization_method IN ('straight_line')", name="ck_intangible_asset_method"),
        sa.CheckConstraint("original_value > 0", name="ck_intangible_asset_original"),
        sa.CheckConstraint(
            "residual_value >= 0 AND residual_value < original_value", name="ck_intangible_asset_residual"
        ),
        sa.CheckConstraint("useful_life_months > 0", name="ck_intangible_asset_life"),
        sa.CheckConstraint(
            "rd_use_rate IS NULL OR (rd_use_rate >= 0 AND rd_use_rate <= 1)",
            name="ck_intangible_asset_rd_rate",
        ),
    )
    # 软删除后编码可复用 / 一张取得凭证只挂一张资产卡（部分唯一索引）
    op.create_index(
        "uq_intangible_asset_company_code",
        "intangible_assets",
        ["company_id", "code"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_intangible_asset_source_entry",
        "intangible_assets",
        ["company_id", "source_entry_id"],
        unique=True,
        sqlite_where=sa.text("source_entry_id IS NOT NULL AND deleted_at IS NULL"),
        postgresql_where=sa.text("source_entry_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index("ix_intangible_asset_company_status", "intangible_assets", ["company_id", "status"])
    op.create_index("ix_intangible_asset_company_category", "intangible_assets", ["company_id", "category"])
    op.create_index("ix_intangible_asset_company_expiry", "intangible_assets", ["company_id", "expiry_date"])
    op.create_index(
        "ix_intangible_asset_company_available", "intangible_assets", ["company_id", "available_for_use_date"]
    )

    # ── 2. 摊销计划表 ─────────────────────────────────────────────────
    op.create_table(
        "intangible_amortization_schedule",
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
            sa.ForeignKey("intangible_assets.id", ondelete="CASCADE"),
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
            "status IN ('planned','posted','reversed','skipped')", name="ck_intangible_amort_status"
        ),
        sa.CheckConstraint("amount > 0", name="ck_intangible_amort_amount"),
        sa.CheckConstraint(
            # SQLite 以 REAL 存 NUMERIC，等式比较必须按两位舍入，避免二进制浮点漂移
            "ROUND(accumulated_after, 2) = ROUND(accumulated_before + amount, 2)",
            name="ck_intangible_amort_cumulative",
        ),
        sa.UniqueConstraint("company_id", "asset_id", "period_start", name="uq_intangible_amort_company_asset_period"),
        sa.UniqueConstraint("company_id", "asset_id", "sequence_no", name="uq_intangible_amort_company_asset_seq"),
    )
    op.create_index(
        "ix_intangible_amort_company_period_status",
        "intangible_amortization_schedule",
        ["company_id", "period_start", "status"],
    )

    # ── 3. 处置单 ─────────────────────────────────────────────────────
    op.create_table(
        "intangible_disposals",
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
            sa.ForeignKey("intangible_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("disposal_type", sa.String(24), nullable=False),
        sa.Column("disposal_date", sa.Date(), nullable=False),
        sa.Column("proceeds_amount", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("output_vat_amount", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("accumulated_amortization_snapshot", sa.Numeric(20, 2), nullable=False),
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
            "disposal_type IN ('sale','scrap','expire','donate','transfer_out')",
            name="ck_intangible_disposal_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft','pending_review','approved','rejected','posted','cancelled')",
            name="ck_intangible_disposal_status",
        ),
        sa.CheckConstraint("net_book_value_snapshot >= 0", name="ck_intangible_disposal_nbv"),
    )
    # 一张在途资产同时只允许一张有效处置单
    op.create_index(
        "uq_intangible_disposal_active",
        "intangible_disposals",
        ["company_id", "asset_id"],
        unique=True,
        sqlite_where=sa.text("status NOT IN ('cancelled','rejected')"),
        postgresql_where=sa.text("status NOT IN ('cancelled','rejected')"),
    )
    op.create_index("ix_intangible_disposal_company_status", "intangible_disposals", ["company_id", "status"])

    # ── 4. 减值测试记录（表先落地，服务 P2 启用）──────────────────────
    op.create_table(
        "intangible_impairment_tests",
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
            sa.ForeignKey("intangible_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("test_period", sa.String(7), nullable=False),
        sa.Column("test_date", sa.Date(), nullable=False),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column("indicator_description", sa.String(500), nullable=False),
        sa.Column("carrying_amount", sa.Numeric(20, 2), nullable=False),
        sa.Column("recoverable_amount", sa.Numeric(20, 2), nullable=False),
        sa.Column("conclusion", sa.String(16), nullable=False, server_default="no_impairment"),
        sa.Column("provision_amount", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("evidence_ref", sa.String(255)),
        sa.Column("assessed_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("reviewed_by", sa.String(128)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "trigger_type IN ('tech_obsolete','legal_invalid','market_decline','idle_long','other')",
            name="ck_intangible_impair_trigger",
        ),
        sa.CheckConstraint(
            "conclusion IN ('no_impairment','impairment_noted')", name="ck_intangible_impair_conclusion"
        ),
        # 小企业会计准则：不计提减值准备
        sa.CheckConstraint("provision_amount = 0", name="ck_intangible_impair_no_provision"),
        sa.UniqueConstraint(
            "company_id", "asset_id", "test_period", name="uq_intangible_impair_company_asset_period"
        ),
    )


def downgrade() -> None:
    op.drop_table("intangible_impairment_tests")
    op.drop_index("ix_intangible_disposal_company_status", table_name="intangible_disposals")
    op.drop_index("uq_intangible_disposal_active", table_name="intangible_disposals")
    op.drop_table("intangible_disposals")
    op.drop_index("ix_intangible_amort_company_period_status", table_name="intangible_amortization_schedule")
    op.drop_table("intangible_amortization_schedule")
    op.drop_index("ix_intangible_asset_company_available", table_name="intangible_assets")
    op.drop_index("ix_intangible_asset_company_expiry", table_name="intangible_assets")
    op.drop_index("ix_intangible_asset_company_category", table_name="intangible_assets")
    op.drop_index("ix_intangible_asset_company_status", table_name="intangible_assets")
    op.drop_index("uq_intangible_asset_source_entry", table_name="intangible_assets")
    op.drop_index("uq_intangible_asset_company_code", table_name="intangible_assets")
    op.drop_table("intangible_assets")
