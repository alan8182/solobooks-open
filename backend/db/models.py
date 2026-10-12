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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")
MONEY = Numeric(20, 2, asdecimal=True)
RATE = Numeric(12, 6, asdecimal=True)


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
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False, default="account-mapping-v1")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON_VALUE, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "voucher_no", name="uq_journal_company_voucher"),
        UniqueConstraint("company_id", "reversal_of_id", name="uq_single_reversal"),
        CheckConstraint("status IN ('draft','pending_review','posted','reversed')", name="ck_journal_status"),
        CheckConstraint("entry_type IN ('standard','opening','closing','reversal')", name="ck_journal_entry_type"),
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
