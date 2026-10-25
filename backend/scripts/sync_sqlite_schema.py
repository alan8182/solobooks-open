# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Sync the local SQLite test database with the current ORM metadata.

Test mode (ACCOUNTING_TEST_MODE=1) only runs create_all on startup, which
creates missing tables but never adds columns to existing ones. After pulling
alembic migrations that alter tables, run this script to close the gap:

    cd backend && ./.venv313/bin/python scripts/sync_sqlite_schema.py

Rules:
- Missing tables are created from the ORM metadata (with their indexes).
- Missing columns are added via ALTER TABLE ADD COLUMN; NOT NULL requires a
  server_default on the model (otherwise the column cannot be added by ALTER).
- Missing indexes are created; nothing is ever dropped or altered implicitly.
- Legacy stub tables (structure drifted away from the ORM: extra columns
  and/or NOT NULL columns that ALTER cannot add) are reported loudly. They are
  rebuilt only when you pass --rebuild-empty-legacy AND the table holds zero
  rows; the old DDL is archived under data/legacy_tables/ first. Tables that
  still contain rows are never touched.
- Use --dry-run to preview every planned DDL without executing it.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa

from db.models import Base

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "app.db"
LEGACY_DDL_DIR = Path(__file__).resolve().parents[1] / "data" / "legacy_tables"


def _default_literal(server_default: sa.DefaultClause, dialect: sa.engine.Dialect) -> str:
    """Render a server_default as a literal SQLite accepts in ADD COLUMN.

    SQLite only takes constants here, so function defaults such as func.now()
    must be compiled through the dialect (-> CURRENT_TIMESTAMP) instead of being
    emitted verbatim (-> now(), which SQLite rejects).
    """
    arg = server_default.arg
    if isinstance(arg, str):
        escaped = arg.replace("'", "''")
        return "DEFAULT '" + escaped + "'"
    return f"DEFAULT {arg.compile(dialect=dialect)}"


def _column_ddl(column: sa.Column, dialect: sa.engine.Dialect) -> str | None:
    """Build the ALTER TABLE ADD COLUMN fragment for one column.

    Returns None when the column is NOT NULL without a server_default: SQLite
    cannot add such a column to an existing table, so the caller must decide
    whether the whole table needs a rebuild.
    """
    parts = [column.type.compile(dialect=dialect)]
    if not column.nullable:
        if column.server_default is None:
            return None
        parts.append("NOT NULL")
    if column.server_default is not None:
        parts.append(_default_literal(column.server_default, dialect))
    return " ".join(parts)


def _diagnose(table: sa.Table, existing: set[str]) -> tuple[list[str], list[str], list[str]]:
    """Compare one ORM table against the live columns.

    Returns (missing, extra, unaddable):
    - missing: ORM columns absent from the database
    - extra: live columns the ORM no longer declares (drift signal)
    - unaddable: missing NOT NULL columns without a server_default
    """
    orm_columns = {column.name for column in table.columns}
    missing = [column.name for column in table.columns if column.name not in existing]
    extra = sorted(existing - orm_columns)
    unaddable = [
        column.name
        for column in table.columns
        if column.name in missing and not column.nullable and column.server_default is None
    ]
    return missing, extra, unaddable


def _create_table(conn: sa.Connection, engine: sa.Engine, table: sa.Table, dry_run: bool) -> None:
    ddl = str(sa.schema.CreateTable(table).compile(dialect=engine.dialect)).strip()
    if dry_run:
        print(f"[plan ] create table {table.name}")
        return
    conn.exec_driver_sql(ddl)
    print(f"[table] created {table.name}")
    _create_indexes(conn, engine, table, dry_run)


def _create_indexes(
    conn: sa.Connection, engine: sa.Engine, table: sa.Table, dry_run: bool
) -> None:
    """Create any ORM-declared index that is missing from the live database."""
    if not table.indexes:
        return
    try:
        live = {index["name"] for index in sa.inspect(conn).get_indexes(table.name)}
    except sa.exc.NoSuchTableError:
        live = set()
    for index in table.indexes:
        if index.name in live:
            continue
        if dry_run:
            print(f"[plan ] create index {table.name}.{index.name}")
            continue
        try:
            conn.exec_driver_sql(str(sa.schema.CreateIndex(index).compile(dialect=engine.dialect)))
            print(f"[index] {table.name}.{index.name} created")
        except sa.exc.SQLAlchemyError as exc:
            print(f"[warn ] {table.name}.{index.name} 建索引失败: {exc}")


def _archive_ddl(conn: sa.Connection, table_name: str) -> Path:
    row = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).first()
    ddl = (row[0] if row and row[0] else f"-- no DDL found for {table_name}") + "\n"
    LEGACY_DDL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = LEGACY_DDL_DIR / f"{table_name}-{stamp}.sql"
    path.write_text(ddl, encoding="utf-8")
    return path


def _rebuild_empty_legacy(
    conn: sa.Connection, engine: sa.Engine, table: sa.Table, dry_run: bool
) -> bool:
    """Drop and recreate a drifted table, but only when it is empty.

    Returns True when the caller should treat the table as rebuilt.
    """
    quoted = engine.dialect.identifier_preparer.quote(table.name)
    rows = conn.exec_driver_sql(f"SELECT COUNT(*) FROM {quoted}").scalar() or 0
    if rows:
        print(
            f"[keep ] {table.name} 结构漂移但仍有 {rows} 行数据，未改动；"
            "请人工写迁移脚本搬运数据"
        )
        return False
    if dry_run:
        print(f"[plan ] rebuild empty table {table.name} (archive DDL, drop, recreate)")
        return True
    path = _archive_ddl(conn, table.name)
    conn.exec_driver_sql(f"DROP TABLE {quoted}")
    print(f"[drop ] {table.name} 已删除（0 行），旧 DDL 存档 {path}")
    _create_table(conn, engine, table, dry_run=False)
    return True


def sync(db_path: Path, *, rebuild_empty_legacy: bool, dry_run: bool) -> int:
    if not db_path.exists():
        raise SystemExit(f"database not found: {db_path}")

    engine = sa.create_engine(f"sqlite:///{db_path}")
    inspector = sa.inspect(engine)
    pending: list[str] = []

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                _create_table(conn, engine, table, dry_run)
                inspector.clear_cache()
                continue

            existing = {column["name"] for column in inspector.get_columns(table.name)}
            missing, extra, unaddable = _diagnose(table, existing)

            drifted = bool(extra or unaddable)
            if drifted:
                # 漂移表不做半吊子补齐：既不 ALTER 补列也不建索引（缺列必然报错），
                # 留待人工迁移或 --rebuild-empty-legacy 处理。
                if rebuild_empty_legacy:
                    if _rebuild_empty_legacy(conn, engine, table, dry_run):
                        inspector.clear_cache()
                        continue
                    # 拒绝重建的原因已由 _rebuild_empty_legacy 打印，这里不再重复。
                    pending.append(table.name)
                else:
                    detail = f"多列={extra or '无'}，缺列且 ALTER 无法补={unaddable or '无'}"
                    print(f"[warn ] {table.name} 结构与 ORM 漂移（{detail}）")
                    pending.append(table.name)
                inspector.clear_cache()
                continue

            for column in table.columns:
                if column.name in existing:
                    continue
                fragment = _column_ddl(column, engine.dialect)
                if fragment is None:
                    print(
                        f"[skip ] {table.name}.{column.name} 为 NOT NULL 且无 server_default，"
                        "无法 ALTER 补列"
                    )
                    continue
                if dry_run:
                    print(f"[plan ] add column {table.name}.{column.name}")
                    continue
                conn.exec_driver_sql(
                    f"ALTER TABLE {table.name} ADD COLUMN {column.name} {fragment}"
                )
                print(f"[col  ] {table.name}.{column.name} added")

            _create_indexes(conn, engine, table, dry_run)
            inspector.clear_cache()

    engine.dispose()

    if pending:
        print(
            "\n发现 "
            + str(len(pending))
            + " 张漂移表："
            + ", ".join(pending)
            + "\n若确认这些表为空且可以按 ORM 重建，重跑并加 --rebuild-empty-legacy"
        )
    print("done." + (" (dry-run，未改动数据库)" if dry_run else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同步本地 SQLite 库与 ORM 元数据")
    parser.add_argument(
        "db_path",
        nargs="?",
        type=Path,
        default=DEFAULT_DB,
        help=f"目标数据库路径（默认 {DEFAULT_DB}）",
    )
    parser.add_argument(
        "--rebuild-empty-legacy",
        action="store_true",
        help="对结构漂移且为 0 行的 legacy 表：存档 DDL 后删除并按 ORM 重建",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要执行的 DDL，不改动数据库",
    )
    args = parser.parse_args(argv)
    return sync(
        args.db_path,
        rebuild_empty_legacy=args.rebuild_empty_legacy,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
