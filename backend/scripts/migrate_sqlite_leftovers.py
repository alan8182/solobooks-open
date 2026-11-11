# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""SQLite → PostgreSQL 补充迁移：官方 migrate_sqlite_to_postgres.py 未覆盖的表。

官方脚本以核心账务为主线（identities/companies/accounts/entries/bank/audit），
invoices、tax_registrations、filing_deadlines、mcp_tokens、llm_usage 等其之后
新增的功能表不在 COPY_TABLES 内。本脚本基于 ORM 元数据做类型感知的整表拷贝
（JSON/Numeric/Boolean 按 SQLAlchemy 类型转换），全部 INSERT ... ON CONFLICT
DO NOTHING，可与官方脚本任意顺序重跑。

用法（先跑 alembic upgrade head 与官方脚本，再执行本脚本）：

    python scripts/migrate_sqlite_leftovers.py \
        --source data/app.db \
        --database-url "postgresql+psycopg://solobooks_owner:...@localhost:5432/solobooks"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.base import Base  # noqa: E402
import db.models  # noqa: F401,E402  注册全部表到 metadata
from db.session import session_scope  # noqa: E402

# 官方脚本未覆盖、但本地库存在数据的功能表
LEFTOVER_TABLES = (
    "company_profiles",
    "invoices",
    "tax_registrations",
    "filing_deadlines",
    "agent_actions",
    "source_transactions",
    "accounting_periods",
    "voucher_sequences",
    "period_close_runs",
    "idempotency_records",
    "mcp_tokens",
    "mcp_call_logs",
    "llm_usage",
    "admin_audit_logs",
    "backup_records",
    "user_quota_counters",
    "audit_events",
    "legacy_import_records",
)

BATCH = 200


def _parse_datetime(value: str):
    text = value.strip().replace(" ", "T", 1).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _coerce(value, column_type):
    if value is None:
        return None
    type_name = type(column_type).__name__
    if type_name in ("JSON", "JSONB", "MutableDict") or "JSON" in type_name.upper():
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        return value
    if type_name == "Boolean":
        return bool(value)
    if type_name in ("DateTime", "TIMESTAMP", "DateTimeWithTZ"):
        if isinstance(value, str):
            return _parse_datetime(value)
        return value
    if type_name in ("Date",):
        if isinstance(value, str):
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return value
        return value
    if type_name in ("Numeric", "NUMERIC", "Decimal"):
        if isinstance(value, str):
            try:
                return Decimal(value)
            except InvalidOperation:
                return value
        return value
    return value


async def _sync_sequences(session, table_names) -> None:
    """显式 id 整表拷贝不会推进 PG 自增序列，插入新行会撞主键
    （admin_audit_logs 真实踩过：删除主体写审计留痕时 UniqueViolation）。
    拷贝完成后把每个自增列的序列拨到 MAX(id)。"""
    from sqlalchemy import text

    for table_name in table_names:
        table = Base.metadata.tables.get(table_name)
        if table is None:
            continue
        for column in table.primary_key.columns:
            seq = (await session.execute(
                text("SELECT pg_get_serial_sequence(:t, :c)"),
                {"t": table_name, "c": column.name},
            )).scalar()
            if not seq:
                continue
            await session.execute(text(
                f"SELECT setval('{seq}', COALESCE((SELECT MAX(\"{column.name}\") FROM \"{table_name}\"), 1))"
            ))


async def migrate(source_path: Path, database_url: str) -> dict[str, int]:
    source = sqlite3.connect(f"file:{source_path.resolve().as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    counts: dict[str, int] = {}
    try:
        async with session_scope() as session:
            for table_name in LEFTOVER_TABLES:
                if not source.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,),
                ).fetchone():
                    continue
                table = Base.metadata.tables.get(table_name)
                if table is None:
                    print(f"[skip] ORM 中无表 {table_name}")
                    continue
                rows = [dict(r) for r in source.execute(f'SELECT * FROM "{table_name}"')]
                if not rows:
                    continue
                columns = {c.name: c for c in table.columns}
                inserted = 0
                for start in range(0, len(rows), BATCH):
                    batch = []
                    for raw in rows[start : start + BATCH]:
                        record = {
                            col: _coerce(raw.get(col), columns[col].type)
                            for col in columns
                            if col in raw
                        }
                        batch.append(record)
                    stmt = pg_insert(table).on_conflict_do_nothing()
                    await session.execute(stmt, batch)
                    inserted += len(batch)
                counts[table_name] = inserted
                print(f"[ok] {table_name}: {inserted} 行")
            await _sync_sequences(session, [t for t in LEFTOVER_TABLES if t in counts])
            await session.commit()
    finally:
        source.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="data/app.db")
    parser.add_argument("--database-url", default="")
    args = parser.parse_args()
    if args.database_url:
        import os

        os.environ["DATABASE_URL"] = args.database_url
    counts = asyncio.run(migrate(Path(args.source), args.database_url))
    print(json.dumps({"copied": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
