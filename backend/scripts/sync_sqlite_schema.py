"""Sync the local SQLite test database with the current ORM metadata.

Test mode (ACCOUNTING_TEST_MODE=1) only runs create_all on startup, which
creates missing tables but never adds columns to existing ones. After pulling
alembic migrations that alter tables, run this script to close the gap:

    cd backend && ./.venv313/bin/python scripts/sync_sqlite_schema.py

Rules:
- Missing tables are created from the ORM metadata.
- Missing columns are added via ALTER TABLE ADD COLUMN; NOT NULL requires a
  server_default on the model (otherwise the column is skipped with a hint).
- Existing columns are never touched; this script never drops anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa

from db.models import Base

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "app.db"


def _default_literal(server_default: sa.DefaultClause) -> str:
    arg = server_default.arg
    if isinstance(arg, str):
        return f"DEFAULT '{arg.replace("'", "''")}'"
    return f"DEFAULT {arg}"


def main() -> None:
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    if not db_path.exists():
        raise SystemExit(f"database not found: {db_path}")

    engine = sa.create_engine(f"sqlite:///{db_path}")
    inspector = sa.inspect(engine)

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                ddl = str(sa.schema.CreateTable(table).compile(dialect=engine.dialect)).strip()
                conn.exec_driver_sql(ddl)
                print(f"[table] created {table.name}")
                continue

            existing = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                parts = [column.type.compile(dialect=engine.dialect)]
                if not column.nullable:
                    if column.server_default is None:
                        print(
                            f"[skip ] {table.name}.{column.name} is NOT NULL without"
                            " server_default; add it manually"
                        )
                        continue
                    parts.append("NOT NULL")
                if column.server_default is not None:
                    parts.append(_default_literal(column.server_default))
                conn.exec_driver_sql(
                    f"ALTER TABLE {table.name} ADD COLUMN {column.name} {' '.join(parts)}"
                )
                print(f"[col  ] {table.name}.{column.name} added")

    engine.dispose()
    print("done.")


if __name__ == "__main__":
    main()
