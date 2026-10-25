# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")
MONEY = Numeric(20, 2, asdecimal=True)
RATE = Numeric(12, 6, asdecimal=True)
# 数量金额式的数量列：4 位小数足够覆盖"0.0001 吨"级精度，且与金额乘积校验的
# 容差（0.01 元）不冲突。
QUANTITY = Numeric(18, 4, asdecimal=True)


def new_id() -> str:
    return str(uuid4())


class AuthUser(Base):
    __tablename__ = "auth_users"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    openid: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    phone_verified_at: Mapped[str] = mapped_column(Text, nullable=False, default="")
    wechat_openid_bound_at: Mapped[str] = mapped_column(Text, nullable=False, default="")
    current_company_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    nickname: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    avatar: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 密码登录：PBKDF2 自描述串（pbkdf2_sha256$iter$salt$hash），空串表示未设置密码。
    # server_default 必须给：create_all 出的 SQLite 测试表靠它容忍省列的裸 SQL 插入
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    password_updated_at: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # 平台级标记（管理后台）：管理员校验每次查库，不进 JWT claim，避免禁用后的旧 token 窗口期
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    is_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_login_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default="")


class Company(Base):
    __tablename__ = "auth_companies"

    company_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(128), ForeignKey("auth_users.user_id"), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False, default=dict)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    accounting_standard: Mapped[str] = mapped_column(String(64), nullable=False, default="small_enterprise_cn")
    posting_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="owner_self_post")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        CheckConstraint("base_currency = 'CNY'", name="ck_company_currency_cny"),
        CheckConstraint("posting_policy IN ('owner_self_post','maker_checker')", name="ck_company_posting_policy"),
    )


class CompanyMember(Base):
    __tablename__ = "company_members"

    company_id: Mapped[str] = mapped_column(String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), ForeignKey("auth_users.user_id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("role IN ('owner','accountant','preparer','viewer')", name="ck_member_role"),
        CheckConstraint("status IN ('active','invited','disabled')", name="ck_member_status"),
    )


class ChartAccount(Base):
    __tablename__ = "chart_accounts"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_code: Mapped[str | None] = mapped_column(String(32))
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    category: Mapped[str | None] = mapped_column(String(64))
    type: Mapped[str | None] = mapped_column(String(64))
    normal_side: Mapped[str | None] = mapped_column(String(8))
    is_leaf: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 数量金额式核算（P1 WP-D）：科目勾选后凭证行可带数量/单价，账表出数量结存。
    # 预置 1403/1405/1408 由 seed 与迁移默认开启；unit 是"个/件/箱"这类展示单位。
    quantity_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    unit: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_chart_account_company_code"),
        CheckConstraint("normal_side IS NULL OR normal_side IN ('debit','credit')", name="ck_account_normal_side"),
    )


class RdProject(Base):
    __tablename__ = "rd_projects"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    manager: Mapped[str | None] = mapped_column(String(255))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    initiation_document_status: Mapped[str] = mapped_column(String(24), nullable=False, default="missing")
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_rd_project_company_code"),
        UniqueConstraint("company_id", "id", name="uq_rd_project_company_id"),
        CheckConstraint("status IN ('draft','active','completed','archived')", name="ck_rd_project_status"),
        CheckConstraint("initiation_document_status IN ('missing','partial','complete')", name="ck_rd_project_document_status"),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_rd_project_dates"),
        Index("ix_rd_project_company_status", "company_id", "status"),
    )


class LegacyEntryProjection(Base):
    """Compatibility read model for clients and legacy reconciliation code.

    It is written only from the authoritative journal transaction and is never
    used for formal reports.
    """

    __tablename__ = "entries"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    company_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("idx_entries_company_updated", "company_id", "updated_at"),
        Index("idx_entries_user_company", "user_id", "company_id"),
    )


class AccountingPeriod(Base):
    __tablename__ = "accounting_periods"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    closed_by: Mapped[str | None] = mapped_column(String(128))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_by: Mapped[str | None] = mapped_column(String(128))
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint("company_id", "period_start", name="uq_accounting_period_company_month"),
        CheckConstraint("status IN ('open','closing','closed','reopened')", name="ck_accounting_period_status"),
        Index("ix_accounting_period_company_status", "company_id", "status"),
    )


class VoucherSequence(Base):
    __tablename__ = "voucher_sequences"

    company_id: Mapped[str] = mapped_column(String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), primary_key=True)
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    next_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class RuleVersion(Base):
    __tablename__ = "rule_versions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON_VALUE, nullable=False, default=dict)

    __table_args__ = (UniqueConstraint("rule_type", "version", name="uq_rule_type_version"),)


class SourceTransaction(Base):
    __tablename__ = "source_transactions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    external_key: Mapped[str | None] = mapped_column(String(255))
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    original_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    exchange_rate: Mapped[Decimal | None] = mapped_column(RATE)
    raw_payload: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="candidate")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "source_type", "external_key", name="uq_source_external_key"),
        CheckConstraint("amount >= 0", name="ck_source_amount_nonnegative"),
        CheckConstraint("currency = 'CNY'", name="ck_source_currency_cny"),
        CheckConstraint("status IN ('candidate','confirmed','reconciled','void')", name="ck_source_status"),
    )


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False)
    source_transaction_id: Mapped[str | None] = mapped_column(String(128), ForeignKey("source_transactions.id"))
    voucher_no: Mapped[str | None] = mapped_column(String(64))
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    entry_type: Mapped[str] = mapped_column(String(24), nullable=False, default="standard")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(128))
    posted_by: Mapped[str | None] = mapped_column(String(128))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversal_of_id: Mapped[str | None] = mapped_column(String(128), ForeignKey("journal_entries.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False, default="account-mapping-v2")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON_VALUE, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "voucher_no", name="uq_journal_company_voucher"),
        UniqueConstraint("company_id", "reversal_of_id", name="uq_single_reversal"),
        CheckConstraint("status IN ('draft','pending_review','posted','reversed')", name="ck_journal_status"),
        # accrual：暂估入账（月末票未到、业务已发生的成本费用），次月红冲后转正式凭证
        CheckConstraint("entry_type IN ('standard','opening','closing','reversal','accrual')", name="ck_journal_entry_type"),
        CheckConstraint("currency = 'CNY'", name="ck_journal_currency_cny"),
        Index("ix_journal_company_period_status", "company_id", "period_start", "status"),
    )


class JournalLine(Base):
    __tablename__ = "journal_lines"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    journal_entry_id: Mapped[str] = mapped_column(String(128), ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False)
    company_id: Mapped[str] = mapped_column(String(128), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), ForeignKey("chart_accounts.id"), nullable=False)
    account_code: Mapped[str] = mapped_column(String(32), nullable=False)
    account_name: Mapped[str] = mapped_column(String(255), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    # 数量金额式（P1 WP-D）：只随数量科目行走；数量不参与借贷平衡校验，
    # amount 仍是唯一权威值（估价入账允许只填数量不填单价）。
    quantity: Mapped[Decimal | None] = mapped_column(QUANTITY)
    unit_price: Mapped[Decimal | None] = mapped_column(RATE)
    unit: Mapped[str | None] = mapped_column(String(16))
    auxiliary: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("journal_entry_id", "line_no", name="uq_journal_line_number"),
        CheckConstraint("direction IN ('debit','credit')", name="ck_journal_line_direction"),
        CheckConstraint("amount > 0", name="ck_journal_line_positive"),
        ForeignKeyConstraint(
            ["company_id", "account_code"],
            ["chart_accounts.company_id", "chart_accounts.code"],
            name="fk_journal_line_company_account",
        ),
        Index("ix_journal_line_account", "company_id", "account_code"),
    )


class RdJournalLineDetail(Base):
    __tablename__ = "rd_journal_line_details"

    journal_line_id: Mapped[str] = mapped_column(String(128), ForeignKey("journal_lines.id", ondelete="CASCADE"), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    expense_type: Mapped[str] = mapped_column(String(32), nullable=False)
    original_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    rd_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    allocation_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False, default=Decimal("1"))
    allocation_method: Mapped[str] = mapped_column(String(64), nullable=False, default="direct")
    allocation_basis: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["rd_projects.company_id", "rd_projects.id"],
            name="fk_rd_line_project_company",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "expense_type IN ('personnel','direct_input','depreciation_amortization','design','testing','outsourced','other')",
            name="ck_rd_line_expense_type",
        ),
        CheckConstraint("original_amount > 0", name="ck_rd_line_original_positive"),
        CheckConstraint("rd_amount > 0 AND rd_amount <= original_amount", name="ck_rd_line_amount_range"),
        CheckConstraint("allocation_rate > 0 AND allocation_rate <= 1", name="ck_rd_line_allocation_rate"),
        CheckConstraint(
            "allocation_method IN ('direct','timesheet','usage','area','quantity','other')",
            name="ck_rd_line_allocation_method",
        ),
        Index("ix_rd_line_company_project_type", "company_id", "project_id", "expense_type"),
    )


class RdSalaryAllocation(Base):
    __tablename__ = "rd_salary_allocations"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), nullable=False)
    month: Mapped[str] = mapped_column(String(7), nullable=False)
    employee_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    allocation_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    allocation_basis: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["rd_projects.company_id", "rd_projects.id"],
            name="fk_rd_salary_project_company",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("company_id", "month", "employee_id", "project_id", name="uq_rd_salary_employee_project_month"),
        CheckConstraint("allocation_rate > 0 AND allocation_rate <= 1", name="ck_rd_salary_rate"),
        CheckConstraint("length(month) = 7", name="ck_rd_salary_month"),
        Index("ix_rd_salary_company_month_employee", "company_id", "month", "employee_id"),
    )


class RdEvidenceDocument(Base):
    __tablename__ = "rd_evidence_documents"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    journal_entry_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("journal_entries.id", ondelete="RESTRICT")
    )
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_no: Mapped[str | None] = mapped_column(String(255))
    file_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="available")
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["rd_projects.company_id", "rd_projects.id"],
            name="fk_rd_evidence_project_company",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "document_type IN ('initiation','invoice','receipt','contract','timesheet','material_issue','usage_log','acceptance','other')",
            name="ck_rd_evidence_document_type",
        ),
        CheckConstraint("status IN ('available','pending_review','missing')", name="ck_rd_evidence_status"),
        CheckConstraint("reference_no IS NOT NULL OR file_url IS NOT NULL", name="ck_rd_evidence_reference"),
        Index("ix_rd_evidence_company_project_entry", "company_id", "project_id", "journal_entry_id"),
    )


class ReconciliationLink(Base):
    __tablename__ = "reconciliation_links"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False)
    source_transaction_id: Mapped[str] = mapped_column(String(128), ForeignKey("source_transactions.id"), nullable=False)
    journal_entry_id: Mapped[str] = mapped_column(String(128), ForeignKey("journal_entries.id"), nullable=False)
    reconciliation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="confirmed")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "source_transaction_id", "journal_entry_id", name="uq_reconciliation_link"),
        CheckConstraint("amount > 0", name="ck_reconciliation_amount_positive"),
        CheckConstraint("status IN ('suggested','confirmed','void')", name="ck_reconciliation_status"),
    )


class PeriodCloseRun(Base):
    __tablename__ = "period_close_runs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    checks: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_versions: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False, default=dict)
    ledger_watermark: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (CheckConstraint("status IN ('passed','blocked','closed','reopened')", name="ck_close_run_status"),)


class FiscalYearClose(Base):
    """年度结账：账套跨年连续性的锚点。

    年度结账不生成下一年期初凭证（总账是全历史累计，再生成会双计），
    它做三件事：锁年（结账后禁止再入账）、留快照与哈希（审计证据）、
    作为报表跨年期初的核对基准（期初余额从历史分录派生）。
    """

    __tablename__ = "fiscal_year_closes"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="closed")
    snapshot: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_versions: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False, default=dict)
    ledger_watermark: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    closed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reopened_by: Mapped[str | None] = mapped_column(String(128))
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("company_id", "fiscal_year", name="uq_fiscal_year_close_company_year"),
        CheckConstraint("status IN ('closed','reopened')", name="ck_fiscal_year_close_status"),
        CheckConstraint("fiscal_year >= 2000 AND fiscal_year <= 2999", name="ck_fiscal_year_close_year"),
    )


class PeriodBackfill(Base):
    """跨期补记台账：业务日期所属期间已关账时，凭证改记到后续未关账期间的留痕。

    会计准则依据：《小企业会计准则》第 88 条未来适用法——会计差错在"发现当期"
    更正，不追溯调整。本表不改变任何金额，只回答"这笔业务原本属于哪个月、
    为什么被记到了这个月"，供结账核对、备查簿与税务沟通使用。
    """

    __tablename__ = "period_backfills"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False)
    journal_entry_id: Mapped[str] = mapped_column(String(128), ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    original_period: Mapped[date] = mapped_column(Date, nullable=False)
    posting_period: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "journal_entry_id", name="uq_backfill_company_entry"),
        CheckConstraint("status IN ('active','reversed')", name="ck_backfill_status"),
        CheckConstraint("posting_period >= original_period", name="ck_backfill_period_order"),
        Index("ix_backfill_company_posting", "company_id", "posting_period"),
        Index("ix_backfill_company_original", "company_id", "original_period"),
        Index("ix_backfill_company_status", "company_id", "status"),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    sequence_no: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(128))
    entity_version: Mapped[int | None] = mapped_column(Integer)
    before: Mapped[dict | None] = mapped_column(JSON_VALUE)
    after: Mapped[dict | None] = mapped_column(JSON_VALUE)
    reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_audit_event_company_created", "company_id", "created_at"),)


class LegacyImportRecord(Base):
    __tablename__ = "legacy_import_records"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), nullable=False)
    legacy_table: Mapped[str] = mapped_column(String(64), nullable=False)
    legacy_id: Mapped[str] = mapped_column(String(255), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("legacy_table", "legacy_id", name="uq_legacy_record_source"),
        CheckConstraint("status IN ('imported','quarantined','ignored')", name="ck_legacy_import_status"),
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | None] = mapped_column(JSON_VALUE)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("company_id", "idempotency_key", "operation", name="uq_idempotency_operation"),)


class StepUpToken(Base):
    __tablename__ = "step_up_tokens"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    company_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AgentAction(Base):
    """Agent 产生的"待确认动作"——agent 写操作不直接落库，人确认后才执行。"""

    __tablename__ = "agent_actions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(128))
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    payload: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON_VALUE)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        CheckConstraint("status IN ('pending','approved','rejected','expired')", name="ck_agent_action_status"),
        CheckConstraint(
            "action_type IN ('propose_voucher','propose_vouchers','propose_match','propose_accrual')",
            name="ck_agent_action_type",
        ),
        Index("ix_agent_actions_company_status", "company_id", "status"),
    )


class CompanyProfile(Base):
    """公司画像：agent 的长期记忆（行业、科目习惯、往来名单、修正历史）。"""

    __tablename__ = "company_profiles"

    company_id: Mapped[str] = mapped_column(String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), primary_key=True)
    profile: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LlmUsage(Base):
    """LLM 调用用量明细（管理后台统计用，M1 埋点）。

    每行一次逻辑调用（含重试后的最终结果）；OCR/ASR 无 token 数记 0。
    写入路径 fire-and-forget，失败只打日志，绝不影响业务请求。
    """

    __tablename__ = "llm_usage"

    # BigInteger PK 在 SQLite（测试模式）下不会成为 rowid 别名，用 Integer 变体
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(128))
    company_id: Mapped[str | None] = mapped_column(String(128))
    scene: Mapped[str] = mapped_column(String(64), nullable=False, default="other")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    error_type: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('ok','error','circuit_open')", name="ck_llm_usage_status"),
        Index("ix_llm_usage_created_at", "created_at"),
        Index("ix_llm_usage_company_created", "company_id", "created_at"),
        Index("ix_llm_usage_user_created", "user_id", "created_at"),
    )


class AdminAuditLog(Base):
    """管理后台操作留痕（M2）：谁在何时对什么目标做了什么。"""

    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True)
    operator_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_admin_audit_created", "created_at"),)


# ── 通用归集（管理口径）───────────────────────────────────────────────────────
# 研发费用归集是「重写凭证行」模型（见 RdJournalLineDetail），因为加计扣除
# 申报要科目级证据；管理口径归集不动凭证行，分配记录独立成表、可重算。
# 两者物理隔离，服务层 union，不在同一张表里用 policy 字段混着写。


class AccountingDimension(Base):
    """归集维度：项目 / 部门 / 客户 等分析口径的定义。

    刻意不设"必填"开关：维度强制填写会把凭证录入成本翻倍到最后没人填，
    数据覆盖率归零，报表形同虚设。
    """

    __tablename__ = "accounting_dimensions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="custom")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_agg_dimension_company_code"),
        UniqueConstraint("company_id", "id", name="uq_agg_dimension_company_id"),
        CheckConstraint(
            "kind IN ('project','department','customer','supplier','custom')",
            name="ck_agg_dimension_kind",
        ),
        CheckConstraint("status IN ('active','archived')", name="ck_agg_dimension_status"),
        Index("ix_agg_dimension_company_status", "company_id", "status"),
    )


class AccountingDimensionMember(Base):
    """归集对象：某维度下的一个具体归属对象（复用 RdProject 的字段形状）。"""

    __tablename__ = "accounting_dimension_members"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dimension_id: Mapped[str] = mapped_column(String(128), ForeignKey("accounting_dimensions.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON_VALUE, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "dimension_id"],
            ["accounting_dimensions.company_id", "accounting_dimensions.id"],
            name="fk_agg_member_company_dimension",
            ondelete="CASCADE",
        ),
        UniqueConstraint("company_id", "dimension_id", "code", name="uq_agg_member_company_dimension_code"),
        UniqueConstraint("company_id", "id", name="uq_agg_member_company_id"),
        CheckConstraint(
            "status IN ('draft','active','completed','archived')",
            name="ck_agg_member_status",
        ),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_agg_member_dates"),
        Index("ix_agg_member_company_dimension_status", "company_id", "dimension_id", "status"),
    )


class JournalLineAllocation(Base):
    """凭证行的管理口径分配记录（凭证行本身不动）。

    amount 是唯一权威值：RATE = Numeric(12,6)，按比例分摊必然产生尾差
    （100 × 0.333333 × 3 = 99.99990），所以接口只收金额，allocation_rate
    由后端按 amount / 行金额回填，仅作留痕，不参与任何金额校验。
    """

    __tablename__ = "journal_line_allocations"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), nullable=False)
    journal_line_id: Mapped[str] = mapped_column(String(128), ForeignKey("journal_lines.id", ondelete="CASCADE"), nullable=False)
    dimension_id: Mapped[str] = mapped_column(String(128), nullable=False)
    member_id: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    allocation_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False, default=Decimal("1"))
    allocation_method: Mapped[str] = mapped_column(String(24), nullable=False, default="direct")
    allocation_basis: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    rule_id: Mapped[str | None] = mapped_column(String(128))
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "dimension_id"],
            ["accounting_dimensions.company_id", "accounting_dimensions.id"],
            name="fk_agg_alloc_company_dimension",
        ),
        ForeignKeyConstraint(
            ["company_id", "member_id"],
            ["accounting_dimension_members.company_id", "accounting_dimension_members.id"],
            name="fk_agg_alloc_company_member",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("journal_line_id", "dimension_id", "member_id", name="uq_agg_alloc_line_dimension_member"),
        CheckConstraint("amount > 0", name="ck_agg_alloc_amount_positive"),
        CheckConstraint("allocation_rate > 0 AND allocation_rate <= 1", name="ck_agg_alloc_rate"),
        CheckConstraint(
            "allocation_method IN ('direct','timesheet','usage','area','quantity','revenue','headcount','other')",
            name="ck_agg_alloc_method",
        ),
        CheckConstraint("source IN ('manual','rule')", name="ck_agg_alloc_source"),
        Index("ix_agg_alloc_company_member", "company_id", "member_id"),
        Index("ix_agg_alloc_company_dimension", "company_id", "dimension_id"),
        Index("ix_agg_alloc_line", "journal_line_id"),
    )


class AggregationRule(Base):
    """自动归集规则：把凭证行按规则打上维度标签，不靠人工逐行填。

    冲突约定（见设计文档 §4.4）：规则分配绝不覆盖 source='manual' 的记录，
    命中有手工分配的行整行跳过；同一行只由 priority 最小的第一条规则分配。
    """

    __tablename__ = "aggregation_rules"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dimension_id: Mapped[str] = mapped_column(String(128), nullable=False)
    member_id: Mapped[str] = mapped_column(String(128), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    account_prefix: Mapped[str | None] = mapped_column(String(32))
    direction: Mapped[str | None] = mapped_column(String(8))
    keyword: Mapped[str | None] = mapped_column(String(255))
    counterparty: Mapped[str | None] = mapped_column(String(255))
    rate: Mapped[Decimal] = mapped_column(RATE, nullable=False, default=Decimal("1"))
    method: Mapped[str] = mapped_column(String(24), nullable=False, default="direct")
    basis: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "dimension_id"],
            ["accounting_dimensions.company_id", "accounting_dimensions.id"],
            name="fk_agg_rule_company_dimension",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "member_id"],
            ["accounting_dimension_members.company_id", "accounting_dimension_members.id"],
            name="fk_agg_rule_company_member",
            ondelete="CASCADE",
        ),
        CheckConstraint("direction IS NULL OR direction IN ('debit','credit')", name="ck_agg_rule_direction"),
        CheckConstraint("rate > 0 AND rate <= 1", name="ck_agg_rule_rate"),
        CheckConstraint(
            "method IN ('direct','timesheet','usage','area','quantity','revenue','headcount','other')",
            name="ck_agg_rule_method",
        ),
        CheckConstraint("status IN ('active','archived')", name="ck_agg_rule_status"),
        Index("ix_agg_rule_company_dimension_priority", "company_id", "dimension_id", "priority"),
    )


class ReportLineRule(Base):
    """报表项映射规则：把科目余额按声明的口径归集到报表行。

    设计说明（与设计文档 §4.5 的差异，实现时按代码事实修正）
    ----------------------------------------------------------
    文档原稿是「一行一个 match_mode + account_prefix」。但对照现有硬编码口径
    （``tax_filing_export_service._profit_map`` / ``_build_balance_rows``），
    真实口径里存在**加减组合**，单列表达不了：

    - ``cost``            = prefix(5401) + prefix(5402)
    - ``research_expense``= prefix(4301) − prefix(4301002)   ← 减项
    - ``management_expense`` = prefix(5602) + research_expense
    - ``cash``            = exact(1001,1002,1012001,1012)     ← 多科目精确

    因此改为**声明式 pattern 数组**（``patterns`` JSON），每个 pattern 自带
    ``mode`` / ``value`` / ``sign``(+1/-1)。一个报表行 = 一组 pattern 的带符号和，
    既能表达前缀、精确、区间，也能表达减项与多科目组合，且**没有任何表达式求值**，
    不存在注入面。

    **前缀粒度硬约束**（来自本项目历史事故）：``match_mode='prefix'`` 时
    ``value`` 不得短于 4 位。``_sum_prefix(balances, "4")`` 会把 4301 连同所有
    4xxx 一并计入。另有一处反向事故：应付职工薪酬曾用 ``_sum_codes(["2211"])``
    精确码求和而非 ``_sum_prefix("2211")``，导致 221102 等子级余额被漏掉、
    应付职工薪酬静默归零。两者都说明粒度必须由配置显式承担，不能靠约定。

    **双轨校验**：本表**不直接驱动**对外报表。规则算出的口径要与现有硬编码口径
    逐行对比、零差异并连续 N 期稳定后才允许切换（见 report_line_rule_service）。
    """

    __tablename__ = "report_line_rules"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), nullable=False)
    report: Mapped[str] = mapped_column(String(32), nullable=False)  # profit | balance
    line_code: Mapped[str] = mapped_column(String(64), nullable=False)
    line_name: Mapped[str] = mapped_column(String(255), nullable=False)
    patterns: Mapped[list] = mapped_column(JSON_VALUE, nullable=False)
    sign: Mapped[str] = mapped_column(String(8), nullable=False, default="net")  # debit|credit|net
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id"],
            ["auth_companies.company_id"],
            name="fk_report_line_rule_company",
            ondelete="CASCADE",
        ),
        UniqueConstraint("company_id", "report", "line_code", name="uq_report_line_rule_line"),
        CheckConstraint("report IN ('profit','balance')", name="ck_report_line_rule_report"),
        CheckConstraint("sign IN ('debit','credit','net')", name="ck_report_line_rule_sign"),
        CheckConstraint("status IN ('active','archived')", name="ck_report_line_rule_status"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_report_line_rule_effective",
        ),
        Index("ix_report_line_rule_company_report", "company_id", "report", "status"),
    )


class FilingDeadline(Base):
    """征期日历：每个公司/税种/期间的申报截止与状态。"""

    __tablename__ = "filing_deadlines"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False)
    tax_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filed_evidence: Mapped[dict | None] = mapped_column(JSON_VALUE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "tax_kind", "period", name="uq_filing_deadline"),
        CheckConstraint(
            "status IN ('pending','filed','waived')",
            name="ck_filing_deadline_status",
        ),
        Index("ix_filing_deadlines_company_due", "company_id", "due_date"),
    )


# ── 发票主数据 ────────────────────────────────────────────────────────────────
# 发票此前只是 entries.payload 里的"影子数据"，去重要 json_extract 全表扫。
# 提升为一等实体后：进销项台账可直接查、去重走索引、后续一键取票/价税自动化有落点。
# entry_id 允许为空：历史回填时可能找不到对应凭证（凭证被删/改），主数据不能因此丢失。


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False
    )
    entry_id: Mapped[str | None] = mapped_column(String(128), ForeignKey("journal_entries.id", ondelete="SET NULL"))
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="expense", server_default="expense")
    invoice_number: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    invoice_code: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    invoice_type: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    invoice_status: Mapped[str] = mapped_column(String(32), nullable=False, default="normal", server_default="normal")
    invoice_date: Mapped[date | None] = mapped_column(Date)
    buyer_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    buyer_tax_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    seller_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    seller_tax_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    amount_excl_tax: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"), server_default="0")
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"), server_default="0")
    amount_incl_tax: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"), server_default="0")
    tax_rate: Mapped[str] = mapped_column(String(16), nullable=False, default="", server_default="")
    item_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    remark: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    partner_id: Mapped[str | None] = mapped_column(String(128))
    # 去重指纹：发票号存在时等于发票号，缺失时退化为 日期+对方+金额+方向
    dedup_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="invoice_import", server_default="invoice_import")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("direction IN ('income','expense')", name="ck_invoice_direction"),
        CheckConstraint("invoice_status IN ('normal','red','reversed')", name="ck_invoice_status"),
        UniqueConstraint("company_id", "dedup_fingerprint", name="uq_invoice_company_fingerprint"),
        Index("ix_invoice_company_number", "company_id", "invoice_number"),
        Index("ix_invoice_company_date", "company_id", "invoice_date"),
        Index("ix_invoice_company_direction_date", "company_id", "direction", "invoice_date"),
    )


# ── 辅助核算主数据 ────────────────────────────────────────────────────────────
# 行级辅助核算继续存 journal_lines.auxiliary JSON（管道已全通），这里只做维度主数据。
# id 引用主数据 + name 冗余显示名（对齐 account_name 冗余惯例）：主数据改名/停用后
# 历史凭证的显示不断裂。客户与供应商合一（type=customer/supplier/both）：
# 小微场景一个单位既客户又供应商极常见，分表会让档案分裂。


class Partner(Base):
    """往来单位档案（客户/供应商合一）。"""

    __tablename__ = "partners"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=lambda: f"pt-{uuid4().hex[:16]}")
    company_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="both", server_default="both")
    tax_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    contact: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    phone: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    address: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    mnemonic: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_partner_company_code"),
        CheckConstraint("type IN ('customer','supplier','both')", name="ck_partner_type"),
        Index("ix_partner_company_name", "company_id", "name"),
        Index("ix_partner_company_active", "company_id", "is_active"),
    )


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=lambda: f"dp-{uuid4().hex[:16]}")
    company_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_department_company_code"),
        Index("ix_department_company_name", "company_id", "name"),
    )


class Project(Base):
    """管理口径项目（部门/项目利润、费用归集）。研发项目继续走 rd_projects，互不合并。"""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=lambda: f"pj-{uuid4().hex[:16]}")
    company_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_project_company_code"),
        Index("ix_project_company_name", "company_id", "name"),
    )


class BackupRecord(Base):
    """归档导出留痕：谁在什么时候导了哪个期间、多大、指纹是什么。"""

    __tablename__ = "backup_records"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    period_from: Mapped[str] = mapped_column(String(7), nullable=False, default="", server_default="")
    period_to: Mapped[str] = mapped_column(String(7), nullable=False, default="", server_default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    voucher_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    data_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_backup_records_company_created", "company_id", "created_at"),)


class MCPAccessToken(Base):
    """MCP 个人访问令牌（PAT）：外部 AI 客户端以 Bearer sbk_* 访问 /mcp。

    库里只存 sha256(token_hash)；明文仅签发时返回一次。
    token 绑定单一公司作用域；scopes 控制 read / write:draft（M0 仅 read）。
    """

    __tablename__ = "mcp_tokens"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    company_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scopes: Mapped[str] = mapped_column(String(255), nullable=False, default="read", server_default="read")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_mcp_tokens_user", "user_id"),
    )


class MCPCallLog(Base):
    """MCP 工具调用留痕（M1）：每次外部客户端工具执行记一行，便于审计与排查。"""

    __tablename__ = "mcp_call_logs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    token_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    company_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tool: Mapped[str] = mapped_column(String(128), nullable=False)
    arguments_digest: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_mcp_call_logs_token_created", "token_id", "created_at"),
        Index("ix_mcp_call_logs_company_created", "company_id", "created_at"),
    )


# ── 无形资产 ──────────────────────────────────────────────────────────────
# 设计方案：根目录《无形资产模块设计方案.md》第 3 章。口径：小企业会计准则
# （不计提减值准备）、年限平均法、可供使用当月起摊、处置当月停摊、残值默认 0。


class IntangibleAsset(Base):
    """无形资产卡片：取得 → 摊销 → 处置全生命周期的主档。"""

    __tablename__ = "intangible_assets"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    acquisition_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default="purchase", server_default="purchase"
    )
    acquisition_date: Mapped[date] = mapped_column(Date, nullable=False)
    available_for_use_date: Mapped[date] = mapped_column(Date, nullable=False)
    original_value: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    input_vat_amount: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=Decimal("0"), server_default="0"
    )
    payment_method: Mapped[str] = mapped_column(
        String(24), nullable=False, default="company_account", server_default="company_account"
    )
    residual_value: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"), server_default="0")
    residual_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False, default=Decimal("0"), server_default="0")
    useful_life_months: Mapped[int] = mapped_column(Integer, nullable=False)
    amortization_method: Mapped[str] = mapped_column(
        String(24), nullable=False, default="straight_line", server_default="straight_line"
    )
    accumulated_amortization: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=Decimal("0"), server_default="0"
    )
    last_amortized_period: Mapped[str | None] = mapped_column(String(7))
    amortization_end_period: Mapped[str | None] = mapped_column(String(7))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", server_default="draft")
    source_entry_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("journal_entries.id", ondelete="SET NULL")
    )
    legal_cert_no: Mapped[str | None] = mapped_column(String(128))
    grant_org: Mapped[str | None] = mapped_column(String(128))
    expiry_date: Mapped[date | None] = mapped_column(Date)
    department_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("departments.id", ondelete="SET NULL")
    )
    custodian: Mapped[str | None] = mapped_column(String(128))
    rd_project_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("rd_projects.id", ondelete="SET NULL")
    )
    rd_use_rate: Mapped[Decimal | None] = mapped_column(RATE)
    rd_allocation_basis: Mapped[str | None] = mapped_column(String(255))
    tax_life_months: Mapped[int | None] = mapped_column(Integer)
    disposed_on: Mapped[date | None] = mapped_column(Date)
    disposal_type: Mapped[str | None] = mapped_column(String(24))
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint(
            "category IN ('patent','trademark','copyright','land_use_right',"
            "'software','franchise','non_patent_technology','other')",
            name="ck_intangible_asset_category",
        ),
        CheckConstraint(
            "acquisition_type IN ('purchase','self_developed','investor_contribution','donation')",
            name="ck_intangible_asset_acq_type",
        ),
        CheckConstraint(
            "status IN ('draft','pending_review','active','idle','fully_amortized','disposing','disposed','scrapped')",
            name="ck_intangible_asset_status",
        ),
        CheckConstraint("amortization_method IN ('straight_line')", name="ck_intangible_asset_method"),
        CheckConstraint("original_value > 0", name="ck_intangible_asset_original"),
        CheckConstraint("residual_value >= 0 AND residual_value < original_value", name="ck_intangible_asset_residual"),
        CheckConstraint("useful_life_months > 0", name="ck_intangible_asset_life"),
        CheckConstraint("rd_use_rate IS NULL OR (rd_use_rate >= 0 AND rd_use_rate <= 1)", name="ck_intangible_asset_rd_rate"),
        # 软删除后编码可复用：部分唯一索引（PG 与 SQLite 双方言 where）
        Index(
            "uq_intangible_asset_company_code",
            "company_id",
            "code",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_intangible_asset_source_entry",
            "company_id",
            "source_entry_id",
            unique=True,
            sqlite_where=text("source_entry_id IS NOT NULL AND deleted_at IS NULL"),
            postgresql_where=text("source_entry_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index("ix_intangible_asset_company_status", "company_id", "status"),
        Index("ix_intangible_asset_company_category", "company_id", "category"),
        Index("ix_intangible_asset_company_expiry", "company_id", "expiry_date"),
        Index("ix_intangible_asset_company_available", "company_id", "available_for_use_date"),
    )


class IntangibleAmortizationSchedule(Base):
    """摊销计划：入账时预生成全周期 planned 行，月度摊销把到期行过账为 posted。"""

    __tablename__ = "intangible_amortization_schedule"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("intangible_assets.id", ondelete="CASCADE"), nullable=False
    )
    period_start: Mapped[str] = mapped_column(String(7), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    accumulated_before: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"), server_default="0")
    accumulated_after: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"), server_default="0")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="planned", server_default="planned")
    journal_entry_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("journal_entries.id", ondelete="SET NULL")
    )
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('planned','posted','reversed','skipped')",
            name="ck_intangible_amort_status",
        ),
        CheckConstraint("amount > 0", name="ck_intangible_amort_amount"),
        CheckConstraint(
            "ROUND(accumulated_after, 2) = ROUND(accumulated_before + amount, 2)",
            name="ck_intangible_amort_cumulative",
        ),
        UniqueConstraint("company_id", "asset_id", "period_start", name="uq_intangible_amort_company_asset_period"),
        UniqueConstraint("company_id", "asset_id", "sequence_no", name="uq_intangible_amort_company_asset_seq"),
        Index("ix_intangible_amort_company_period_status", "company_id", "period_start", "status"),
    )


class IntangibleDisposal(Base):
    """处置单：出售 / 报废清理 / 到期核销等，审批通过后生成处置凭证。"""

    __tablename__ = "intangible_disposals"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("intangible_assets.id", ondelete="CASCADE"), nullable=False
    )
    disposal_type: Mapped[str] = mapped_column(String(24), nullable=False)
    disposal_date: Mapped[date] = mapped_column(Date, nullable=False)
    proceeds_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"), server_default="0")
    output_vat_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"), server_default="0")
    accumulated_amortization_snapshot: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    net_book_value_snapshot: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    gain_loss_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", server_default="draft")
    reason: Mapped[str | None] = mapped_column(String(500))
    journal_entry_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("journal_entries.id", ondelete="SET NULL")
    )
    initiated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    approved_by: Mapped[str | None] = mapped_column(String(128))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint(
            "disposal_type IN ('sale','scrap','expire','donate','transfer_out')",
            name="ck_intangible_disposal_type",
        ),
        CheckConstraint(
            "status IN ('draft','pending_review','approved','rejected','posted','cancelled')",
            name="ck_intangible_disposal_status",
        ),
        CheckConstraint("net_book_value_snapshot >= 0", name="ck_intangible_disposal_nbv"),
        # 一张在途资产同时只允许一张有效处置单
        Index(
            "uq_intangible_disposal_active",
            "company_id",
            "asset_id",
            unique=True,
            sqlite_where=text("status NOT IN ('cancelled','rejected')"),
            postgresql_where=text("status NOT IN ('cancelled','rejected')"),
        ),
        Index("ix_intangible_disposal_company_status", "company_id", "status"),
    )


class IntangibleImpairmentTest(Base):
    """减值测试记录：小企业会计准则不计提减值准备（provision_amount 恒为 0），仅评估留痕。"""

    __tablename__ = "intangible_impairment_tests"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("intangible_assets.id", ondelete="CASCADE"), nullable=False
    )
    test_period: Mapped[str] = mapped_column(String(7), nullable=False)
    test_date: Mapped[date] = mapped_column(Date, nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    indicator_description: Mapped[str] = mapped_column(String(500), nullable=False)
    carrying_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    recoverable_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    conclusion: Mapped[str] = mapped_column(
        String(16), nullable=False, default="no_impairment", server_default="no_impairment"
    )
    provision_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"), server_default="0")
    evidence_ref: Mapped[str | None] = mapped_column(String(255))
    assessed_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    reviewed_by: Mapped[str | None] = mapped_column(String(128))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "trigger_type IN ('tech_obsolete','legal_invalid','market_decline','idle_long','other')",
            name="ck_intangible_impair_trigger",
        ),
        CheckConstraint("conclusion IN ('no_impairment','impairment_noted')", name="ck_intangible_impair_conclusion"),
        # 小企业会计准则：不计提减值准备
        CheckConstraint("provision_amount = 0", name="ck_intangible_impair_no_provision"),
        UniqueConstraint("company_id", "asset_id", "test_period", name="uq_intangible_impair_company_asset_period"),
    )


# ── 固定资产（P1 WP-B 现代化）─────────────────────────────────────────────
# 照无形资产模式：卡片-凭证外键强一致 + 全生命周期状态机 + 折旧计划预生成。
# 与无形资产的口径差异：
# - 折旧方法不止直线（one_time = 500 万以下设备器具一次性折旧，legacy 语义兼容）；
# - 次月起提：可供使用次月开始计提（无形资产是当月起摊）；
# - 处置当月停提（处置单创建时当月 planned 置 skipped，保证处置快照与 1602 一致）；
# - 处置凭证经 1606 固定资产清理中转。


class FixedAsset(Base):
    """固定资产卡片：取得 → 折旧 → 处置全生命周期的主档。"""

    __tablename__ = "fixed_assets"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    acquisition_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default="purchase", server_default="purchase"
    )
    acquisition_date: Mapped[date] = mapped_column(Date, nullable=False)
    available_for_use_date: Mapped[date] = mapped_column(Date, nullable=False)
    original_value: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    input_vat_amount: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=Decimal("0"), server_default="0"
    )
    payment_method: Mapped[str] = mapped_column(
        String(24), nullable=False, default="company_account", server_default="company_account"
    )
    residual_value: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"), server_default="0")
    residual_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False, default=Decimal("0"), server_default="0")
    useful_life_months: Mapped[int] = mapped_column(Integer, nullable=False)
    depreciation_method: Mapped[str] = mapped_column(
        String(24), nullable=False, default="straight_line", server_default="straight_line"
    )
    accumulated_depreciation: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=Decimal("0"), server_default="0"
    )
    last_depreciated_period: Mapped[str | None] = mapped_column(String(7))
    depreciation_end_period: Mapped[str | None] = mapped_column(String(7))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", server_default="draft")
    source_entry_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("journal_entries.id", ondelete="SET NULL")
    )
    department_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("departments.id", ondelete="SET NULL")
    )
    custodian: Mapped[str | None] = mapped_column(String(128))
    location: Mapped[str | None] = mapped_column(String(255))
    rd_project_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("rd_projects.id", ondelete="SET NULL")
    )
    rd_use_rate: Mapped[Decimal | None] = mapped_column(RATE)
    rd_allocation_basis: Mapped[str | None] = mapped_column(String(255))
    tax_life_months: Mapped[int | None] = mapped_column(Integer)
    disposed_on: Mapped[date | None] = mapped_column(Date)
    disposal_type: Mapped[str | None] = mapped_column(String(24))
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint(
            "category IN ('building','machine','vehicle','electronics','furniture','other')",
            name="ck_fixed_asset_category",
        ),
        CheckConstraint(
            "acquisition_type IN ('purchase','self_built','investor_contribution','donation')",
            name="ck_fixed_asset_acq_type",
        ),
        CheckConstraint(
            "status IN ('draft','pending_review','active','idle','fully_depreciated','disposing','disposed','scrapped')",
            name="ck_fixed_asset_status",
        ),
        CheckConstraint(
            "depreciation_method IN ('straight_line','one_time')", name="ck_fixed_asset_method"
        ),
        CheckConstraint("original_value > 0", name="ck_fixed_asset_original"),
        CheckConstraint("residual_value >= 0 AND residual_value < original_value", name="ck_fixed_asset_residual"),
        CheckConstraint("useful_life_months > 0", name="ck_fixed_asset_life"),
        CheckConstraint(
            "rd_use_rate IS NULL OR (rd_use_rate >= 0 AND rd_use_rate <= 1)", name="ck_fixed_asset_rd_rate"
        ),
        # 软删除后编码可复用：部分唯一索引（PG 与 SQLite 双方言 where）
        Index(
            "uq_fixed_asset_company_code",
            "company_id",
            "code",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # 期初/取得幂等：一张取得（或期初）凭证只挂一张资产卡
        Index(
            "uq_fixed_asset_source_entry",
            "company_id",
            "source_entry_id",
            unique=True,
            sqlite_where=text("source_entry_id IS NOT NULL AND deleted_at IS NULL"),
            postgresql_where=text("source_entry_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index("ix_fixed_asset_company_status", "company_id", "status"),
        Index("ix_fixed_asset_company_category", "company_id", "category"),
        Index("ix_fixed_asset_company_available", "company_id", "available_for_use_date"),
    )


class FixedAssetDepreciationSchedule(Base):
    """折旧计划：入账时预生成全周期 planned 行，月度 run 把到期行过账为 posted。"""

    __tablename__ = "fixed_asset_depreciation_schedule"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("fixed_assets.id", ondelete="CASCADE"), nullable=False
    )
    period_start: Mapped[str] = mapped_column(String(7), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    accumulated_before: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"), server_default="0")
    accumulated_after: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"), server_default="0")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="planned", server_default="planned")
    journal_entry_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("journal_entries.id", ondelete="SET NULL")
    )
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('planned','posted','reversed','skipped')",
            name="ck_fixed_asset_dep_status",
        ),
        CheckConstraint("amount > 0", name="ck_fixed_asset_dep_amount"),
        CheckConstraint(
            "ROUND(accumulated_after, 2) = ROUND(accumulated_before + amount, 2)",
            name="ck_fixed_asset_dep_cumulative",
        ),
        UniqueConstraint("company_id", "asset_id", "period_start", name="uq_fixed_asset_dep_company_asset_period"),
        UniqueConstraint("company_id", "asset_id", "sequence_no", name="uq_fixed_asset_dep_company_asset_seq"),
        Index("ix_fixed_asset_dep_company_period_status", "company_id", "period_start", "status"),
    )


class FixedAssetDisposal(Base):
    """处置单：出售 / 报废清理 / 捐赠 / 对外投资转出，审批通过后生成处置凭证（1606 中转）。"""

    __tablename__ = "fixed_asset_disposals"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("fixed_assets.id", ondelete="CASCADE"), nullable=False
    )
    disposal_type: Mapped[str] = mapped_column(String(24), nullable=False)
    disposal_date: Mapped[date] = mapped_column(Date, nullable=False)
    proceeds_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"), server_default="0")
    output_vat_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"), server_default="0")
    accumulated_depreciation_snapshot: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    net_book_value_snapshot: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    gain_loss_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", server_default="draft")
    reason: Mapped[str | None] = mapped_column(String(500))
    journal_entry_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("journal_entries.id", ondelete="SET NULL")
    )
    initiated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    approved_by: Mapped[str | None] = mapped_column(String(128))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint(
            "disposal_type IN ('sale','scrap','donate','transfer_out')",
            name="ck_fixed_asset_disposal_type",
        ),
        CheckConstraint(
            "status IN ('draft','pending_review','approved','rejected','posted','cancelled')",
            name="ck_fixed_asset_disposal_status",
        ),
        CheckConstraint("net_book_value_snapshot >= 0", name="ck_fixed_asset_disposal_nbv"),
        # 一张在途资产同时只允许一张有效处置单
        Index(
            "uq_fixed_asset_disposal_active",
            "company_id",
            "asset_id",
            unique=True,
            sqlite_where=text("status NOT IN ('cancelled','rejected')"),
            postgresql_where=text("status NOT IN ('cancelled','rejected')"),
        ),
        Index("ix_fixed_asset_disposal_company_status", "company_id", "status"),
    )


class TaxRegistration(Base):
    """税费种认定：电子税务局「税费种认定信息」的一行。

    **这是认定表的忠实副本，不是申报事项**——一行 = 税局页面上的一行，
    字段名与列一一对应（征收项目/品目/子目/纳税期限/申报期限/征收率/税率/
    主附税标志/征收代理方式）。多个认定行可能归属同一张申报表，通过
    `return_group` 归并（如城建税/教育费附加/地方教育附加 → vat），
    征期日历按 `return_group` 而不是按认定行物化，否则同一截止日会出现
    多条同表待办，用户会重复点「已申报」。

    `levy_category` / `levy_sub_item` 必须是 NOT NULL DEFAULT ''，不能用 NULL：
    PG 唯一索引把 NULL 视为互不相等，用 NULL 会让同一行重复导入悄悄翻倍。
    """

    __tablename__ = "tax_registrations"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # 征收项目 / 征收品目 / 征收子目
    levy_item: Mapped[str] = mapped_column(String(64), nullable=False)
    levy_category: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    levy_sub_item: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    # 纳税期限：month / quarter / year / other（按次、半年等归 other，不物化征期）
    filing_cycle: Mapped[str] = mapped_column(String(16), nullable=False)
    filing_cycle_text: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    # 申报期限（原文）+ 归一化规则
    deadline_text: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    deadline_rule: Mapped[str] = mapped_column(String(32), nullable=False, default="month_15")
    due_month_offset: Mapped[int | None] = mapped_column(Integer)
    due_day: Mapped[int | None] = mapped_column(Integer)
    # 征收率 / 税率或单位税额（税率非纯数字时原文进 rate_unit）
    levy_rate: Mapped[Decimal | None] = mapped_column(RATE)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 6, asdecimal=True))
    rate_unit: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    # 主附税标志：main / vat_attachment；征收代理方式：self / withholding
    main_flag: Mapped[str] = mapped_column(String(16), nullable=False, default="main", server_default="main")
    collection_method: Mapped[str] = mapped_column(String(16), nullable=False, default="self", server_default="self")
    # 归属申报表组（= filing_deadlines.tax_kind）
    return_group: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual", server_default="manual")
    note: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "levy_item",
            "levy_category",
            "levy_sub_item",
            "filing_cycle",
            name="uq_tax_registration_row",
        ),
        CheckConstraint(
            "filing_cycle IN ('month','quarter','year','other')",
            name="ck_tax_registration_cycle",
        ),
        CheckConstraint(
            "status IN ('active','inactive')", name="ck_tax_registration_status"
        ),
        CheckConstraint(
            "main_flag IN ('main','vat_attachment')", name="ck_tax_registration_main_flag"
        ),
        CheckConstraint(
            "collection_method IN ('self','withholding')",
            name="ck_tax_registration_collection",
        ),
        CheckConstraint(
            "deadline_rule IN ('month_15','quarter_15','year_15','next_year_0331',"
            "'next_year_0531','next_year_0630','next_year_0930','custom','none')",
            name="ck_tax_registration_deadline_rule",
        ),
        Index("ix_tax_registrations_company_status", "company_id", "status"),
    )


class LedgerBookSnapshot(Base):
    """已签发的电子账簿快照（一册一行）。

    为什么存完整 payload 而不只存 hash：账簿签发后如果反结账、补记，实时数据就
    变了，但当时打印装订的那一册必须能"重打与原版逐字一致"。只留 hash 只能证明
    "变了"，不能还原"当时是什么"。

    anchor 存签发时刻的期间状态与凭证最大号，用来判断"这册是否已过期"：
    过期不改旧册，只提示重新签发，旧册永远保留。
    """

    __tablename__ = "ledger_book_snapshots"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False
    )
    book_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="monthly")
    # 月度册 "2026-06"，年度册 "2026"
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    # 连续编号：2026-06-0001
    book_no: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="issued")
    payload: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    anchor: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False, default=dict)
    generated_by: Mapped[str | None] = mapped_column(String(128))
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text)
    voided_by: Mapped[str | None] = mapped_column(String(128))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("company_id", "book_kind", "period", "book_no", name="uq_book_snapshot_number"),
        CheckConstraint("book_kind IN ('monthly','yearly')", name="ck_book_snapshot_kind"),
        CheckConstraint("status IN ('issued','void')", name="ck_book_snapshot_status"),
        Index("ix_book_snapshot_company_period", "company_id", "period"),
    )


class AuditShare(Base):
    """Solo Audit Share 协审链接：月度体检快照 + 免注册只读访问。

    明文 token 仅在签发时返回一次，库里只存 sha256(token_hash)。
    snapshot 是生成时冻结的脱敏数据集——CPA 看到的与批注的永远是同一份，
    老板改账后需重新生成新链接，旧批注挂在旧快照上不漂移。
    """

    __tablename__ = "audit_shares"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_companies.company_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # 体检月份 "2026-09"
    month: Mapped[str] = mapped_column(String(7), nullable=False)
    anonymized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    snapshot: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False, default=dict)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_audit_shares_company_month", "company_id", "month"),
    )


class ShareAnnotation(Base):
    """协审批注：CPA 对快照内条目/检查项/报告区块的咨询意见。

    批注是协审链接唯一的写操作，且只写本表；采纳建议后的账务调整
    仍走草稿-确认流程，责任边界保持"CPA 提建议、老板确认"。
    """

    __tablename__ = "share_annotations"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    share_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("audit_shares.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # 锚点：overall=总体意见 / entry=快照内条目 / check_item=检查项 / report_section=报表区块
    anchor_type: Mapped[str] = mapped_column(String(24), nullable=False, default="entry")
    anchor_id: Mapped[str | None] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_name: Mapped[str] = mapped_column(String(64), nullable=False, default="协审会计师")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    resolved_by: Mapped[str | None] = mapped_column(String(128))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "anchor_type IN ('overall','entry','check_item','report_section')",
            name="ck_share_annotation_anchor",
        ),
        CheckConstraint(
            "status IN ('open','acknowledged','resolved','dismissed')",
            name="ck_share_annotation_status",
        ),
        Index("ix_share_annotations_share", "share_id", "created_at"),
        Index("ix_share_annotations_company", "company_id"),
    )
