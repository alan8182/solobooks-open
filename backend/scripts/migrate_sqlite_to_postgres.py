# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Offline SQLite -> PostgreSQL migration with cent-exact reconciliation.

Run Alembic first, then rehearse against a staging database:

    python scripts/migrate_sqlite_to_postgres.py --source data/app.db --dry-run

The command rolls back when any legacy entry is quarantined or any account
movement differs. A resolution JSON may explicitly provide voucher lines or
mark a legacy record archive-only; no accounting decision is guessed.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from sqlalchemy import Numeric, bindparam, select, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.errors import AccountingError
from core.money import ZERO, money, money_text
from db.models import (
    AuditEvent,
    AuthUser,
    ChartAccount,
    Company,
    CompanyMember,
    JournalEntry,
    JournalLine,
    LegacyImportRecord,
    ReconciliationLink,
    SourceTransaction,
)
from db.session import dispose_engine, get_session_factory
from services.accounting_core_service import (
    create_entry,
    ensure_tenant,
    post_journal,
    seed_accounts,
)


MONEY_COLUMNS = {
    "amount", "balance", "depreciation_amount", "accumulated_before", "accumulated_after",
    "debit_balance", "credit_balance", "base_salary", "social_security_base", "bonus",
    "allowance", "deduction", "personal_social_security", "personal_tax",
    "company_social_security", "net_salary", "total_cost",
}
COPY_TABLES = [
    "auth_consents", "todo_states", "categories", "private_expenses",
    "employees", "salary_sheets", "salary_records", "recurring_templates",
    # P1 WP-B：固定资产进核心 ORM；legacy blob 表（fixed_assets / depreciation_schedule）
    # 不再拷贝——生产 PG 由 alembic 建新表 + 回填服务从 legacy_fixed_assets 迁移。
]


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return money_text(value)
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


def _payload(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _open_source(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _has_table(source: sqlite3.Connection, table: str) -> bool:
    return source.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _read_resolutions(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("resolution file must be a JSON object keyed by legacy entry id")
    return data


def _legacy_lines(payload: dict) -> list[dict]:
    voucher = payload.get("voucherLines") or {}
    lines: list[dict] = []
    for direction in ("debit", "credit"):
        for line in voucher.get(direction, []) or []:
            amount_value = money(line.get("amount") or 0)
            canonical_direction = direction
            if amount_value < ZERO:
                canonical_direction = "credit" if direction == "debit" else "debit"
                amount_value = abs(amount_value)
            lines.append(
                {
                    "direction": canonical_direction,
                    "accountCode": str(line.get("accountCode") or ""),
                    "accountName": str(line.get("accountName") or ""),
                    "amount": amount_value,
                }
            )
    return lines


def _balanced(lines: list[dict]) -> bool:
    debit = sum((line["amount"] for line in lines if line["direction"] == "debit"), ZERO)
    credit = sum((line["amount"] for line in lines if line["direction"] == "credit"), ZERO)
    return len(lines) >= 2 and debit > ZERO and debit == credit


async def _migrate_identities(source: sqlite3.Connection, session) -> dict[str, str]:
    now = datetime.now(timezone.utc).isoformat()
    if _has_table(source, "auth_users"):
        for row in source.execute("SELECT * FROM auth_users"):
            values = dict(row)
            if await session.get(AuthUser, values["user_id"]) is None:
                session.add(AuthUser(
                    user_id=values["user_id"], openid=values.get("openid") or "",
                    phone=values.get("phone") or "", phone_verified_at=values.get("phone_verified_at") or "",
                    wechat_openid_bound_at=values.get("wechat_openid_bound_at") or "",
                    current_company_id=values.get("current_company_id") or "",
                    nickname=values.get("nickname") or "", avatar=values.get("avatar") or "",
                    created_at=values.get("created_at") or now, last_login_at=values.get("last_login_at") or now,
                    updated_at=values.get("updated_at") or "",
                ))
    await session.flush()

    owners: dict[str, str] = {}
    if _has_table(source, "auth_companies"):
        for row in source.execute("SELECT * FROM auth_companies"):
            values = dict(row)
            owner = values.get("owner_user_id") or "migration-owner"
            owners[values["company_id"]] = owner
            if await session.get(AuthUser, owner) is None:
                session.add(AuthUser(
                    user_id=owner, openid="", phone="", phone_verified_at="", wechat_openid_bound_at="",
                    current_company_id=values["company_id"], nickname="", avatar="", created_at=now,
                    last_login_at=now, updated_at=now,
                ))
                await session.flush()
            if await session.get(Company, values["company_id"]) is None:
                session.add(Company(
                    company_id=values["company_id"], owner_user_id=owner, payload=_payload(values.get("payload")),
                    base_currency="CNY", accounting_standard="small_enterprise_cn",
                    posting_policy="owner_self_post", timezone="Asia/Shanghai",
                    created_at=values.get("created_at") or now, updated_at=values.get("updated_at") or "",
                ))
            if await session.get(CompanyMember, {"company_id": values["company_id"], "user_id": owner}) is None:
                session.add(CompanyMember(company_id=values["company_id"], user_id=owner, role="owner", status="active"))
    await session.flush()
    return owners


async def _ensure_creator(session, company_id: str, user_id: str, owner_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    if await session.get(AuthUser, user_id) is None:
        session.add(AuthUser(
            user_id=user_id, openid="", phone="", phone_verified_at="", wechat_openid_bound_at="",
            current_company_id=company_id, nickname="", avatar="", created_at=now,
            last_login_at=now, updated_at=now,
        ))
        await session.flush()
    key = {"company_id": company_id, "user_id": user_id}
    if await session.get(CompanyMember, key) is None:
        session.add(CompanyMember(
            company_id=company_id,
            user_id=user_id,
            role="owner" if user_id == owner_id else "preparer",
            status="active",
        ))
        await session.flush()


async def _migrate_accounts(source: sqlite3.Connection, session, owners: dict[str, str]) -> None:
    for company_id in owners:
        await seed_accounts(session, company_id)
    if not _has_table(source, "chart_accounts"):
        return
    for row in source.execute("SELECT * FROM chart_accounts"):
        values = dict(row)
        account = (
            await session.execute(select(ChartAccount).where(
                ChartAccount.company_id == values["company_id"], ChartAccount.code == values["code"]
            ))
        ).scalar_one_or_none()
        if account:
            account.name = values.get("name") or account.name
            account.parent_code = values.get("parent_code")
            account.level = int(values.get("level") or 1)
            account.category = values.get("category")
            account.type = values.get("type")
            account.is_leaf = bool(values.get("is_leaf", 1))
            account.is_active = bool(values.get("is_active", 1))
        else:
            session.add(ChartAccount(
                id=values["id"], company_id=values["company_id"], code=values["code"],
                name=values["name"], parent_code=values.get("parent_code"), level=int(values.get("level") or 1),
                category=values.get("category"), type=values.get("type"), normal_side=None,
                is_leaf=bool(values.get("is_leaf", 1)), is_system=bool(values.get("is_system", 0)),
                is_active=bool(values.get("is_active", 1)), created_at=values.get("created_at") or "",
                updated_at=values.get("updated_at") or "",
            ))
    await session.flush()


async def _migrate_entries(
    source: sqlite3.Connection,
    session,
    owners: dict[str, str],
    resolutions: dict[str, dict],
) -> tuple[set[str], list[dict]]:
    archived: set[str] = set()
    exceptions: list[dict] = []
    if not _has_table(source, "entries"):
        return archived, exceptions
    for row in source.execute("SELECT * FROM entries ORDER BY created_at, id"):
        values = dict(row)
        payload = _payload(values.get("payload"))
        legacy_id = values["id"]
        company_id = values.get("company_id") or payload.get("company_id") or "c1"
        user_id = values.get("user_id") or payload.get("user_id") or owners.get(company_id) or "migration-owner"
        owner_id = owners.get(company_id) or user_id
        resolution = resolutions.get(legacy_id) or {}
        if resolution.get("action") == "archive":
            archived.add(legacy_id)
            session.add(LegacyImportRecord(
                company_id=company_id, legacy_table="entries", legacy_id=legacy_id,
                target_type=None, target_id=None, status="ignored", raw_payload=_json(payload),
                error_code="ARCHIVE_ONLY", error_message=str(resolution.get("reason") or "explicitly archived"),
            ))
            continue
        if resolution.get("voucherLines"):
            payload["voucherLines"] = resolution["voucherLines"]
        lines = _legacy_lines(payload)
        occurred_on = str(payload.get("occurDate") or payload.get("date") or "")
        problems: list[str] = []
        if not occurred_on:
            problems.append("MISSING_DATE")
        if not _balanced(lines):
            problems.append("MISSING_OR_UNBALANCED_LINES")
        if problems:
            exception = {"legacyId": legacy_id, "companyId": company_id, "problems": problems}
            exceptions.append(exception)
            session.add(LegacyImportRecord(
                company_id=company_id, legacy_table="entries", legacy_id=legacy_id,
                target_type="journal_entry", target_id=None, status="quarantined", raw_payload=_json(payload),
                error_code=problems[0], error_message=",".join(problems),
            ))
            continue
        await _ensure_creator(session, company_id, user_id, owner_id)
        original_fingerprint = payload.get("dedupFingerprint")
        if original_fingerprint:
            payload["legacyDedupFingerprint"] = original_fingerprint
        payload.update({
            "id": legacy_id,
            "occurDate": occurred_on,
            "dedupFingerprint": f"legacy-entry:{legacy_id}",
            "voucherLines": {
            "debit": [line for line in lines if line["direction"] == "debit"],
            "credit": [line for line in lines if line["direction"] == "credit"],
        }})
        try:
            entry = await create_entry(
                session, payload, company_id=company_id, user_id=user_id,
                request_id=f"legacy-create:{legacy_id}", confirm=False,
            )
            await post_journal(
                session, entry.id, company_id=company_id, user_id=owner_id,
                request_id=f"legacy-post:{legacy_id}",
            )
            session.add(LegacyImportRecord(
                company_id=company_id, legacy_table="entries", legacy_id=legacy_id,
                target_type="journal_entry", target_id=entry.id, status="imported", raw_payload=_json(payload),
            ))
        except AccountingError as exc:
            exceptions.append({"legacyId": legacy_id, "companyId": company_id, "problems": [exc.code]})
            session.add(LegacyImportRecord(
                company_id=company_id, legacy_table="entries", legacy_id=legacy_id,
                target_type="journal_entry", target_id=None, status="quarantined", raw_payload=_json(payload),
                error_code=exc.code, error_message=exc.message,
            ))
    await session.flush()
    return archived, exceptions


async def _copy_auxiliary(source: sqlite3.Connection, session) -> None:
    for table in COPY_TABLES:
        if not _has_table(source, table):
            continue
        rows = [dict(row) for row in source.execute(f'SELECT * FROM "{table}"')]
        if not rows:
            continue
        columns = list(rows[0])
        quoted_columns = ", ".join(f'"{column}"' for column in columns)
        placeholders = ", ".join(f":{column}" for column in columns)
        statement = text(
            f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'
        )
        statement = statement.bindparams(*[
            bindparam(column, type_=Numeric(20, 2))
            for column in columns
            if column in MONEY_COLUMNS
        ])
        payloads = []
        for row in rows:
            payloads.append({
                column: money(row[column]) if column in MONEY_COLUMNS and row[column] is not None else row[column]
                for column in columns
            })
        await session.execute(statement, payloads)


async def _migrate_opening_balances(
    source: sqlite3.Connection,
    session,
    owners: dict[str, str],
) -> list[dict]:
    if not _has_table(source, "opening_balances"):
        return []
    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in source.execute("SELECT * FROM opening_balances ORDER BY company_id, fiscal_year, account_code"):
        values = dict(row)
        grouped[(values["company_id"], int(values["fiscal_year"]))].append(values)

    exceptions: list[dict] = []
    for (company_id, fiscal_year), rows in grouped.items():
        debit_lines: list[dict] = []
        credit_lines: list[dict] = []
        total_debit = ZERO
        total_credit = ZERO
        for values in rows:
            debit = money(values.get("debit_balance") or 0)
            credit = money(values.get("credit_balance") or 0)
            account = (
                await session.execute(
                    select(ChartAccount).where(
                        ChartAccount.company_id == company_id,
                        ChartAccount.code == str(values["account_code"]),
                    )
                )
            ).scalar_one_or_none()
            base = {
                "accountCode": str(values["account_code"]),
                "accountName": account.name if account else str(values.get("account_name") or values["account_code"]),
            }
            if debit > ZERO:
                debit_lines.append({**base, "amount": debit})
                total_debit += debit
            if credit > ZERO:
                credit_lines.append({**base, "amount": credit})
                total_credit += credit

        if total_debit == ZERO and total_credit == ZERO:
            for values in rows:
                session.add(LegacyImportRecord(
                    company_id=company_id,
                    legacy_table="opening_balances",
                    legacy_id=values["id"],
                    target_type=None,
                    target_id=None,
                    status="ignored",
                    raw_payload=_json(values),
                    error_code="ZERO_OPENING_BALANCE",
                    error_message="zero opening balance does not create a journal",
                ))
            continue
        if total_debit != total_credit:
            exception = {
                "legacyId": f"opening:{company_id}:{fiscal_year}",
                "companyId": company_id,
                "problems": ["UNBALANCED_OPENING_BALANCE"],
            }
            exceptions.append(exception)
            for values in rows:
                session.add(LegacyImportRecord(
                    company_id=company_id,
                    legacy_table="opening_balances",
                    legacy_id=values["id"],
                    target_type="journal_entry",
                    target_id=None,
                    status="quarantined",
                    raw_payload=_json(values),
                    error_code="UNBALANCED_OPENING_BALANCE",
                    error_message=f"debit={money_text(total_debit)}, credit={money_text(total_credit)}",
                ))
            continue

        owner_id = owners.get(company_id) or "migration-owner"
        await _ensure_creator(session, company_id, owner_id, owner_id)
        entry_id = f"opening-{company_id}-{fiscal_year}"
        entry = await create_entry(
            session,
            {
                "id": entry_id,
                "direction": "owner_transfer",
                "amount": total_debit,
                "title": f"{fiscal_year} 年期初余额",
                "category": "期初余额",
                "occurDate": date(fiscal_year, 1, 1).isoformat(),
                "sourceType": "legacy_opening_balance",
                "dedupFingerprint": f"legacy-opening:{company_id}:{fiscal_year}",
                "entryType": "opening",
                "voucherAuthority": "legacy_import",
                "voucherLines": {"debit": debit_lines, "credit": credit_lines},
            },
            company_id=company_id,
            user_id=owner_id,
            request_id=f"legacy-opening-create:{company_id}:{fiscal_year}",
            confirm=False,
        )
        await post_journal(
            session,
            entry.id,
            company_id=company_id,
            user_id=owner_id,
            request_id=f"legacy-opening-post:{company_id}:{fiscal_year}",
        )
        for values in rows:
            session.add(LegacyImportRecord(
                company_id=company_id,
                legacy_table="opening_balances",
                legacy_id=values["id"],
                target_type="journal_entry",
                target_id=entry_id,
                status="imported",
                raw_payload=_json(values),
            ))
    await session.flush()
    return exceptions


async def _migrate_bank(source: sqlite3.Connection, session) -> None:
    if not _has_table(source, "bank_statements"):
        return
    match_rows = []
    if _has_table(source, "bank_statement_matches"):
        match_rows = [dict(row) for row in source.execute("SELECT * FROM bank_statement_matches")]
    matches_by_statement: dict[str, list[dict]] = defaultdict(list)
    for match in match_rows:
        matches_by_statement[match["statement_id"]].append(match)

    for row in source.execute("SELECT * FROM bank_statements"):
        values = dict(row)
        company_id = values["company_id"]
        amount_value = money(values.get("amount") or 0)
        source_transaction = SourceTransaction(
            company_id=company_id, source_type="bank_statement", external_key=values["id"],
            occurred_on=date.fromisoformat(str(values["transaction_date"])[:10]),
            description=values.get("description") or "银行流水", amount=abs(amount_value), currency="CNY",
            raw_payload=_json({**values, "signedAmount": money_text(amount_value)}), status="reconciled" if matches_by_statement.get(values["id"]) else "candidate",
            created_by="migration",
        )
        session.add(source_transaction)
        await session.flush()
        bank_insert = text(
            "INSERT INTO bank_statements "
            "(id, company_id, transaction_date, description, amount, balance, statement_id, matched_entry_id, description_normalized, created_at) "
            "VALUES (:id, :company_id, :transaction_date, :description, :amount, :balance, :statement_id, :matched_entry_id, :description_normalized, :created_at) "
            "ON CONFLICT DO NOTHING"
        ).bindparams(
            bindparam("amount", type_=Numeric(20, 2)),
            bindparam("balance", type_=Numeric(20, 2)),
        )
        await session.execute(bank_insert, {**values, "amount": amount_value, "balance": money(values.get("balance") or 0),
            "description_normalized": int(values.get("description_normalized") or 0)})
        for match in matches_by_statement.get(values["id"], []):
            target = await session.get(JournalEntry, match["entry_id"])
            if target:
                session.add(ReconciliationLink(
                    company_id=company_id, source_transaction_id=source_transaction.id,
                    journal_entry_id=target.id, reconciliation_type="bank_statement",
                    amount=abs(amount_value), status="confirmed", created_by="migration",
                ))
            await session.execute(text(
                "INSERT INTO bank_statement_matches "
                "(statement_id, company_id, entry_id, previous_billing_status, previous_payment_date, created_at) "
                "VALUES (:statement_id, :company_id, :entry_id, :previous_billing_status, :previous_payment_date, :created_at) "
                "ON CONFLICT DO NOTHING"
            ), match)


async def _migrate_audit(source: sqlite3.Connection, session) -> None:
    if not _has_table(source, "audit_log"):
        return
    for row in source.execute("SELECT * FROM audit_log ORDER BY created_at, id"):
        values = dict(row)
        details = _payload(values.get("details")) if values.get("details") else None
        session.add(AuditEvent(
            id=f"legacy-{values['id']}", company_id=values["company_id"],
            user_id=values.get("user_id") or "migration", action=f"legacy:{values.get('action') or 'unknown'}",
            entity_type=values.get("entity_type") or "legacy", entity_id=values.get("entity_id"),
            before=None, after=details, reason="migrated legacy audit event",
            request_id=f"legacy-audit:{values['id']}",
        ))


def _legacy_account_totals(source: sqlite3.Connection, archived: set[str]) -> dict[tuple[str, str, str], Decimal]:
    totals: dict[tuple[str, str, str], Decimal] = defaultdict(lambda: ZERO)
    if not _has_table(source, "entries"):
        return totals
    for row in source.execute("SELECT id, company_id, payload FROM entries"):
        if row["id"] in archived:
            continue
        payload = _payload(row["payload"])
        month = str(payload.get("occurDate") or payload.get("date") or "")[:7]
        for line in _legacy_lines(payload):
            key = (row["company_id"], month, f"{line['accountCode']}:{line['direction']}")
            totals[key] += line["amount"]
    if _has_table(source, "opening_balances"):
        for row in source.execute("SELECT company_id, fiscal_year, account_code, debit_balance, credit_balance FROM opening_balances"):
            month = f"{int(row['fiscal_year']):04d}-01"
            debit = money(row["debit_balance"] or 0)
            credit = money(row["credit_balance"] or 0)
            if debit:
                totals[(row["company_id"], month, f"{row['account_code']}:debit")] += debit
            if credit:
                totals[(row["company_id"], month, f"{row['account_code']}:credit")] += credit
    return totals


async def _target_account_totals(session) -> dict[tuple[str, str, str], Decimal]:
    rows = (
        await session.execute(
            select(
                JournalEntry.company_id,
                JournalEntry.period_start,
                JournalLine.account_code,
                JournalLine.direction,
                JournalLine.amount,
            ).join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
            .where(JournalEntry.status.in_(("posted", "reversed")))
        )
    ).all()
    totals: dict[tuple[str, str, str], Decimal] = defaultdict(lambda: ZERO)
    for company_id, period, code, direction, amount_value in rows:
        totals[(company_id, period.isoformat()[:7], f"{code}:{direction}")] += money(amount_value)
    return totals


async def migrate(args) -> tuple[dict, bool]:
    source_path = Path(args.source)
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    source = _open_source(source_path)
    resolutions = _read_resolutions(Path(args.resolutions) if args.resolutions else None)
    factory = get_session_factory()
    session = factory()
    transaction = await session.begin()
    committed = False
    try:
        owners = await _migrate_identities(source, session)
        await _migrate_accounts(source, session, owners)
        archived, exceptions = await _migrate_entries(source, session, owners, resolutions)
        exceptions.extend(await _migrate_opening_balances(source, session, owners))
        await _copy_auxiliary(source, session)
        await _migrate_bank(source, session)
        await _migrate_audit(source, session)
        await session.flush()

        legacy_totals = _legacy_account_totals(source, archived)
        target_totals = await _target_account_totals(session)
        keys = sorted(set(legacy_totals) | set(target_totals))
        differences = [
            {
                "companyId": key[0], "month": key[1], "accountDirection": key[2],
                "legacy": money_text(legacy_totals[key]), "target": money_text(target_totals[key]),
                "difference": money_text(target_totals[key] - legacy_totals[key]),
            }
            for key in keys if legacy_totals[key] != target_totals[key]
        ]
        report = {
            "source": str(source_path.resolve()),
            "sourceSha256": source_hash,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "dryRun": bool(args.dry_run),
            "legacyEntryCount": source.execute("SELECT COUNT(*) FROM entries").fetchone()[0] if _has_table(source, "entries") else 0,
            "importedJournalCount": int((await session.execute(select(JournalEntry))).scalars().unique().all().__len__()),
            "archivedLegacyIds": sorted(archived),
            "exceptions": exceptions,
            "accountDifferences": differences,
            "passed": not exceptions and not differences,
        }
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        if args.dry_run or not report["passed"]:
            await transaction.rollback()
        else:
            await transaction.commit()
            committed = True
        return report, committed
    finally:
        if transaction.is_active:
            await transaction.rollback()
        await session.close()
        source.close()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="data/app.db")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--resolutions", help="JSON mapping of legacy entry IDs to explicit archive/voucher decisions")
    parser.add_argument("--report", default="data/migration_reports/sqlite_to_postgres.json")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    if not args.database_url:
        print("DATABASE_URL or --database-url is required", file=sys.stderr)
        return 2
    os.environ["DATABASE_URL"] = args.database_url
    await dispose_engine()
    report, committed = await migrate(args)
    print(json.dumps({"passed": report["passed"], "committed": committed, "report": args.report}, ensure_ascii=False))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
