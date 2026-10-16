# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Reliable PostgreSQL accounting core.

Revision ID: f3a7c91d2b10
Revises: cab1ab2f70d7
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from db.base import Base
import db.models  # noqa: F401 - registers tables on Base.metadata


revision = "f3a7c91d2b10"
down_revision = "cab1ab2f70d7"
branch_labels = None
depends_on = None


CORE_TABLES = [
    "company_members",
    "chart_accounts",
    "accounting_periods",
    "voucher_sequences",
    "rule_versions",
    "source_transactions",
    "journal_entries",
    "journal_lines",
    "reconciliation_links",
    "period_close_runs",
    "audit_events",
    "legacy_import_records",
    "idempotency_records",
    "step_up_tokens",
]

AUXILIARY_TABLES = [
    "audit_log", "bank_statements", "bank_statement_matches", "categories",
    "depreciation_schedule", "opening_balances", "private_expenses", "employees",
    "salary_sheets", "salary_records", "recurring_templates",
]


def _column_names(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _create_auxiliary_tables(bind) -> None:
    metadata = sa.MetaData()
    money = sa.Numeric(20, 2, asdecimal=True)
    tables = [
        sa.Table("audit_log", metadata,
            sa.Column("id", sa.Text, primary_key=True), sa.Column("company_id", sa.Text, nullable=False),
            sa.Column("user_id", sa.Text, nullable=False), sa.Column("action", sa.Text, nullable=False),
            sa.Column("entity_type", sa.Text, nullable=False), sa.Column("entity_id", sa.Text),
            sa.Column("details", sa.Text), sa.Column("created_at", sa.Text, nullable=False)),
        sa.Table("bank_statements", metadata,
            sa.Column("id", sa.Text, primary_key=True), sa.Column("company_id", sa.Text, nullable=False),
            sa.Column("transaction_date", sa.Text, nullable=False), sa.Column("description", sa.Text, nullable=False),
            sa.Column("amount", money, nullable=False), sa.Column("balance", money, nullable=False, server_default="0"),
            sa.Column("statement_id", sa.Text, nullable=False), sa.Column("matched_entry_id", sa.Text),
            sa.Column("description_normalized", sa.Integer, nullable=False, server_default="0"),
            sa.Column("created_at", sa.Text, nullable=False)),
        sa.Table("bank_statement_matches", metadata,
            sa.Column("statement_id", sa.Text, primary_key=True), sa.Column("entry_id", sa.Text, primary_key=True),
            sa.Column("company_id", sa.Text, nullable=False), sa.Column("previous_billing_status", sa.Text),
            sa.Column("previous_payment_date", sa.Text), sa.Column("created_at", sa.Text, nullable=False)),
        sa.Table("categories", metadata,
            sa.Column("id", sa.Text, primary_key=True), sa.Column("company_id", sa.Text, nullable=False),
            sa.Column("name", sa.Text, nullable=False), sa.Column("direction", sa.Text, nullable=False),
            sa.Column("parent_id", sa.Text), sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
            sa.Column("is_system", sa.Integer, nullable=False, server_default="0"),
            sa.Column("is_active", sa.Integer, nullable=False, server_default="1"),
            sa.Column("created_at", sa.Text, nullable=False), sa.Column("updated_at", sa.Text, nullable=False)),
        sa.Table("depreciation_schedule", metadata,
            sa.Column("id", sa.Text, primary_key=True), sa.Column("company_id", sa.Text, nullable=False),
            sa.Column("asset_id", sa.Text, nullable=False), sa.Column("period", sa.Text, nullable=False),
            sa.Column("depreciation_amount", money, nullable=False, server_default="0"),
            sa.Column("accumulated_before", money, nullable=False, server_default="0"),
            sa.Column("accumulated_after", money, nullable=False, server_default="0"),
            sa.Column("voucher_ref", sa.Text), sa.Column("created_at", sa.Text, nullable=False),
            sa.UniqueConstraint("asset_id", "period", name="uq_depreciation_asset_period")),
        sa.Table("opening_balances", metadata,
            sa.Column("id", sa.Text, primary_key=True), sa.Column("company_id", sa.Text, nullable=False),
            sa.Column("account_code", sa.Text, nullable=False), sa.Column("debit_balance", money, nullable=False, server_default="0"),
            sa.Column("credit_balance", money, nullable=False, server_default="0"), sa.Column("fiscal_year", sa.Integer, nullable=False),
            sa.Column("created_at", sa.Text, nullable=False), sa.Column("updated_at", sa.Text, nullable=False),
            sa.UniqueConstraint("company_id", "account_code", "fiscal_year", name="uq_opening_company_account_year")),
        sa.Table("private_expenses", metadata,
            sa.Column("id", sa.Text, primary_key=True), sa.Column("company_id", sa.Text, nullable=False),
            sa.Column("user_id", sa.Text, nullable=False), sa.Column("title", sa.Text, nullable=False),
            sa.Column("amount", money, nullable=False), sa.Column("direction", sa.Text, server_default="expense"),
            sa.Column("category", sa.Text, server_default=""), sa.Column("occur_date", sa.Text, server_default=""),
            sa.Column("payer", sa.Text, server_default=""), sa.Column("owner_name", sa.Text, server_default="本人"),
            sa.Column("private_account", sa.Text, server_default=""), sa.Column("is_available", sa.Integer, server_default="1"),
            sa.Column("memo", sa.Text, server_default=""), sa.Column("invoice_status", sa.Text, server_default="no_invoice"),
            sa.Column("substitute_entry_id", sa.Text, server_default=""), sa.Column("substitute_memo", sa.Text, server_default=""),
            sa.Column("created_at", sa.Text, nullable=False), sa.Column("updated_at", sa.Text, nullable=False)),
        sa.Table("employees", metadata,
            sa.Column("id", sa.Text, primary_key=True), sa.Column("company_id", sa.Text, nullable=False),
            sa.Column("name", sa.Text, nullable=False), sa.Column("department", sa.Text, nullable=False),
            sa.Column("base_salary", money, nullable=False, server_default="0"),
            sa.Column("social_security_base", money, nullable=False, server_default="0"),
            sa.Column("is_active", sa.Integer, nullable=False, server_default="1"),
            sa.Column("created_at", sa.Text, nullable=False), sa.Column("updated_at", sa.Text, nullable=False)),
        sa.Table("salary_sheets", metadata,
            sa.Column("id", sa.Text, primary_key=True), sa.Column("company_id", sa.Text, nullable=False),
            sa.Column("month", sa.Text, nullable=False), sa.Column("status", sa.Text, nullable=False, server_default="draft"),
            sa.Column("accrual_voucher_id", sa.Text), sa.Column("payment_voucher_id", sa.Text),
            sa.Column("created_at", sa.Text, nullable=False), sa.Column("updated_at", sa.Text, nullable=False),
            sa.UniqueConstraint("company_id", "month", name="uq_salary_company_month")),
        sa.Table("salary_records", metadata,
            sa.Column("id", sa.Text, primary_key=True), sa.Column("sheet_id", sa.Text, nullable=False),
            sa.Column("employee_id", sa.Text, nullable=False), sa.Column("employee_name", sa.Text, nullable=False),
            *[sa.Column(name, money, nullable=False, server_default="0") for name in (
                "base_salary", "bonus", "allowance", "deduction", "personal_social_security",
                "personal_tax", "company_social_security", "net_salary", "total_cost")],
            sa.Column("created_at", sa.Text, nullable=False), sa.Column("updated_at", sa.Text, nullable=False)),
        sa.Table("recurring_templates", metadata,
            sa.Column("id", sa.Text, primary_key=True), sa.Column("company_id", sa.Text, nullable=False),
            sa.Column("name", sa.Text, nullable=False), sa.Column("template_data", sa.Text, nullable=False),
            sa.Column("frequency", sa.Text, nullable=False), sa.Column("next_due_date", sa.Text, nullable=False),
            sa.Column("is_active", sa.Integer, nullable=False, server_default="1"),
            sa.Column("created_at", sa.Text, nullable=False), sa.Column("updated_at", sa.Text, nullable=False)),
    ]
    for table in tables:
        table.create(bind=bind, checkfirst=True)


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    company_columns = _column_names(bind, "auth_companies")
    additions = {
        "base_currency": sa.Column("base_currency", sa.String(3), nullable=False, server_default="CNY"),
        "accounting_standard": sa.Column("accounting_standard", sa.String(64), nullable=False, server_default="small_enterprise_cn"),
        "posting_policy": sa.Column("posting_policy", sa.String(32), nullable=False, server_default="owner_self_post"),
        "timezone": sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"),
    }
    for name, column in additions.items():
        if name not in company_columns:
            op.add_column("auth_companies", column)

    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE auth_companies ALTER COLUMN payload TYPE JSONB "
            "USING CASE WHEN payload IS NULL OR payload = '' THEN '{}'::jsonb ELSE payload::jsonb END"
        )

    for table_name in CORE_TABLES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)
    _create_auxiliary_tables(bind)

    account_columns = _column_names(bind, "chart_accounts")
    if "normal_side" not in account_columns:
        op.add_column("chart_accounts", sa.Column("normal_side", sa.String(8), nullable=True))

    if dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION validate_journal_balance(entry_id text)
            RETURNS void AS $$
            DECLARE
                entry_status text;
                debit_total numeric(20,2);
                credit_total numeric(20,2);
                line_count integer;
            BEGIN
                SELECT status INTO entry_status FROM journal_entries WHERE id = entry_id;
                IF entry_status IS NULL OR entry_status NOT IN ('posted', 'reversed') THEN
                    RETURN;
                END IF;
                SELECT
                    COALESCE(SUM(amount) FILTER (WHERE direction = 'debit'), 0),
                    COALESCE(SUM(amount) FILTER (WHERE direction = 'credit'), 0),
                    COUNT(*)
                INTO debit_total, credit_total, line_count
                FROM journal_lines WHERE journal_entry_id = entry_id;
                IF line_count < 2 OR debit_total <> credit_total OR debit_total <= 0 THEN
                    RAISE EXCEPTION 'UNBALANCED_ENTRY:% debit=% credit=% lines=%', entry_id, debit_total, credit_total, line_count;
                END IF;
            END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION journal_balance_line_trigger()
            RETURNS trigger AS $$
            BEGIN
                PERFORM validate_journal_balance(COALESCE(NEW.journal_entry_id, OLD.journal_entry_id));
                RETURN COALESCE(NEW, OLD);
            END;
            $$ LANGUAGE plpgsql;

            CREATE CONSTRAINT TRIGGER trg_journal_line_balance
            AFTER INSERT OR UPDATE OR DELETE ON journal_lines
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION journal_balance_line_trigger();

            CREATE OR REPLACE FUNCTION journal_entry_balance_trigger()
            RETURNS trigger AS $$
            BEGIN
                PERFORM validate_journal_balance(NEW.id);
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE CONSTRAINT TRIGGER trg_journal_entry_balance
            AFTER INSERT OR UPDATE OF status ON journal_entries
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION journal_entry_balance_trigger();
            """
        )
        op.execute(
            """
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

            CREATE TRIGGER trg_journal_entry_immutable
            BEFORE UPDATE OR DELETE ON journal_entries
            FOR EACH ROW EXECUTE FUNCTION prevent_posted_journal_mutation();

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

            CREATE TRIGGER trg_journal_line_immutable
            BEFORE UPDATE OR DELETE ON journal_lines
            FOR EACH ROW EXECUTE FUNCTION prevent_posted_line_mutation();

            CREATE OR REPLACE FUNCTION prevent_audit_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'AUDIT_EVENT_IMMUTABLE:%', OLD.id;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_audit_event_immutable
            BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_audit_event_immutable ON audit_events")
        op.execute("DROP TRIGGER IF EXISTS trg_journal_line_immutable ON journal_lines")
        op.execute("DROP TRIGGER IF EXISTS trg_journal_entry_immutable ON journal_entries")
        op.execute("DROP TRIGGER IF EXISTS trg_journal_entry_balance ON journal_entries")
        op.execute("DROP TRIGGER IF EXISTS trg_journal_line_balance ON journal_lines")
        op.execute("DROP FUNCTION IF EXISTS prevent_audit_mutation()")
        op.execute("DROP FUNCTION IF EXISTS prevent_posted_line_mutation()")
        op.execute("DROP FUNCTION IF EXISTS prevent_posted_journal_mutation()")
        op.execute("DROP FUNCTION IF EXISTS journal_entry_balance_trigger()")
        op.execute("DROP FUNCTION IF EXISTS journal_balance_line_trigger()")
        op.execute("DROP FUNCTION IF EXISTS validate_journal_balance(text)")

    for table_name in reversed(CORE_TABLES):
        op.drop_table(table_name)

    for table_name in reversed(AUXILIARY_TABLES):
        op.drop_table(table_name)

    for column in ("timezone", "posting_policy", "accounting_standard", "base_currency"):
        op.drop_column("auth_companies", column)
