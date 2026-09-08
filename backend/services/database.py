"""Database compatibility layer.

The accounting core uses SQLAlchemy sessions directly. Remaining operational
services keep their small ``aiosqlite``-style API while production statements
execute on the same PostgreSQL database. SQLite is available only in explicit
test mode and as a migration source.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import aiosqlite
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection

from db.session import database_url, get_engine
from services.sqlite_db import APP_DB_FILE, DATA_DIR


DB_FILE = APP_DB_FILE


def _sqlite_target(url: str) -> str:
    database = make_url(url).database
    if not database or database == ":memory:":
        return database or ":memory:"
    if re.match(r"^/[A-Za-z]:/", database):
        database = database[1:]
    target = Path(database)
    if not target.is_absolute():
        target = (Path(__file__).resolve().parent.parent / target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    return str(target)


class CompatRow(Mapping[str, Any]):
    def __init__(self, values: Mapping[str, Any]):
        self._values = dict(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._values.values())[key]
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


class CompatCursor:
    def __init__(self, rows: list[CompatRow] | None = None, *, rowcount: int = 0):
        self._rows = rows or []
        self.rowcount = rowcount

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


def _bind_params(sql: str, params: Iterable[Any] | Mapping[str, Any] | None) -> tuple[str, dict[str, Any]]:
    if params is None:
        return sql, {}
    if isinstance(params, Mapping):
        return sql, {key: _coerce_param(value) for key, value in params.items()}
    values = list(params)
    pieces = sql.split("?")
    if len(pieces) - 1 != len(values):
        raise ValueError(f"SQL placeholder count mismatch: expected {len(pieces) - 1}, got {len(values)}")
    output = [pieces[0]]
    bindings: dict[str, Any] = {}
    for index, value in enumerate(values):
        name = f"p{index}"
        output.extend((f":{name}", pieces[index + 1]))
        bindings[name] = _coerce_param(value)
    return "".join(output), bindings


def _coerce_param(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    return value


_JSON_EXTRACT = re.compile(r"json_extract\(payload,\s*'\$\.([A-Za-z0-9_]+)'\)", re.IGNORECASE)
_JSON_SET = re.compile(r"json_set\(payload,\s*([^)]*)\)", re.IGNORECASE)


def _translate_json_set(match: re.Match[str]) -> str:
    tokens = [token.strip() for token in re.split(r",\s*(?=(?:[^']*'[^']*')*[^']*$)", match.group(1))]
    if len(tokens) % 2:
        return match.group(0)
    expression = "payload::jsonb"
    for index in range(0, len(tokens), 2):
        path = tokens[index].strip("'").removeprefix("$.")
        value = tokens[index + 1]
        expression = f"jsonb_set({expression}, '{{{path}}}', to_jsonb(CAST({value} AS text)), true)"
    return f"({expression})::text"


def _translate_postgres(sql: str) -> str:
    statement = sql.strip()
    statement = _JSON_EXTRACT.sub(lambda match: f"jsonb_extract_path_text(payload::jsonb, '{match.group(1)}')", statement)
    statement = _JSON_SET.sub(_translate_json_set, statement)
    if re.match(r"^INSERT\s+OR\s+IGNORE\s+INTO\s+", statement, re.IGNORECASE):
        statement = re.sub(r"^INSERT\s+OR\s+IGNORE\s+INTO\s+", "INSERT INTO ", statement, flags=re.IGNORECASE)
        if " ON CONFLICT " not in statement.upper():
            statement = statement.rstrip("; ") + " ON CONFLICT DO NOTHING"
    return statement


class PostgresCompatConnection:
    def __init__(self, connection: AsyncConnection):
        self._connection = connection

    async def execute(self, sql: str, params: Iterable[Any] | Mapping[str, Any] | None = None) -> CompatCursor:
        stripped = sql.strip()
        upper = stripped.upper()
        if upper.startswith("PRAGMA TABLE_INFO("):
            table_name = stripped[stripped.find("(") + 1:stripped.rfind(")")].strip(" ' \"")
            result = await self._connection.execute(
                text(
                    "SELECT column_name AS name, data_type AS type "
                    "FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = :table_name "
                    "ORDER BY ordinal_position"
                ),
                {"table_name": table_name},
            )
            return CompatCursor([CompatRow(row._mapping) for row in result.fetchall()])
        if "FROM SQLITE_MASTER" in upper:
            table_match = re.search(r"name\s*=\s*'([^']+)'", stripped, re.IGNORECASE)
            table_name = table_match.group(1) if table_match else ""
            result = await self._connection.execute(
                text(
                    "SELECT 1 AS present FROM information_schema.tables "
                    "WHERE table_schema = current_schema() AND table_name = :table_name"
                ),
                {"table_name": table_name},
            )
            return CompatCursor([CompatRow(row._mapping) for row in result.fetchall()])
        if upper.startswith(("CREATE TABLE", "CREATE INDEX", "CREATE UNIQUE INDEX", "ALTER TABLE")):
            return CompatCursor(rowcount=0)

        translated = _translate_postgres(stripped)
        translated, bindings = _bind_params(translated, params)
        result = await self._connection.execute(text(translated), bindings)
        rows = [CompatRow(row._mapping) for row in result.fetchall()] if result.returns_rows else []
        return CompatCursor(rows, rowcount=result.rowcount or 0)

    async def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any] | Mapping[str, Any]]) -> CompatCursor:
        translated = _translate_postgres(sql.strip())
        batches = list(seq_of_params)
        if not batches:
            return CompatCursor()
        first_sql, _ = _bind_params(translated, batches[0])
        binding_rows = [_bind_params(translated, params)[1] for params in batches]
        result = await self._connection.execute(text(first_sql), binding_rows)
        return CompatCursor(rowcount=result.rowcount or 0)

    async def commit(self) -> None:
        await self._connection.commit()

    async def rollback(self) -> None:
        await self._connection.rollback()

    async def close(self) -> None:
        await self._connection.close()


async def get_conn():
    url = database_url()
    if url.startswith("sqlite+"):
        conn = await aiosqlite.connect(_sqlite_target(url), timeout=30.0)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=wal")
        return conn
    connection = await get_engine().connect()
    return PostgresCompatConnection(connection)
