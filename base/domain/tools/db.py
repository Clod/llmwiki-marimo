"""Shared SQLite DB helpers for all domain/tools modules.

open_db() is the single entry point for opening the wiki index DB.
All other modules import from here — do NOT define open_db elsewhere.
"""

import sqlite3
import uuid
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator

_SCHEMA_PATH = Path(__file__).parent.parent.parent.parent / "database" / "sqlite_schema.sql"


class SchemaMismatchError(RuntimeError):
    """An existing database was built with an older, incompatible schema.

    The schema in ``database/sqlite_schema.sql`` is applied with
    ``CREATE TABLE IF NOT EXISTS`` and there is no migration step, so a table
    that already exists keeps the columns it was created with. Without this
    check the mismatch surfaced later as a raw ``sqlite3.OperationalError``
    ("no such column: ...") from whatever query happened to run first.
    """

    def __init__(self, db_path: str, missing: dict[str, list[str]]):
        detail = "; ".join(
            f"{table} (missing: {', '.join(columns)})"
            for table, columns in sorted(missing.items())
        )
        msg = (
            f"The index at '{db_path}' was built with an older schema and cannot "
            f"be opened: {detail}. This database predates a schema change and "
            "there is no migration step. The wiki pages themselves are markdown "
            "under git and are not affected: delete the index file and re-ingest "
            "the documents in sources/ to rebuild it."
        )
        super().__init__(msg)


def open_db(db_path: str) -> sqlite3.Connection:
    """Open the SQLite DB, applying the base schema on first run.

    Raises SchemaMismatchError when the file already holds tables that lack
    columns the current schema declares.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _verify_schema(conn, db_path)
    _apply_base_schema(conn)
    return conn


def _user_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info('{table}')")}


@lru_cache(maxsize=1)
def _reference_columns(schema_text: str) -> dict[str, frozenset[str]]:
    """Columns the current schema declares, per table.

    The reference is built by applying the schema to an in-memory database
    rather than by parsing the SQL, so the comparison stays exact as the schema
    evolves. The result is cached per schema text: the cost is paid once per
    process, not on every open_db call.
    """
    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript(schema_text)
        return {
            table: frozenset(_table_columns(reference, table))
            for table in _user_tables(reference)
        }
    finally:
        reference.close()


def _verify_schema(conn: sqlite3.Connection, db_path: str) -> None:
    """Fail early when an existing table lacks a column the schema declares.

    A table the schema declares and the database does not have yet is not a
    mismatch: _apply_base_schema creates that table. Only a table that exists
    with fewer columns than the schema declares is incompatible, because no
    ALTER TABLE ever runs.
    """
    existing = _user_tables(conn)
    if not existing:
        return
    missing: dict[str, list[str]] = {}
    for table, expected in _reference_columns(_schema_text()).items():
        if table not in existing:
            continue
        gap = sorted(expected - _table_columns(conn, table))
        if gap:
            missing[table] = gap
    if missing:
        conn.close()
        raise SchemaMismatchError(db_path, missing)


@contextmanager
def get_connection(db_path: str) -> Iterator[sqlite3.Connection]:
    """Context manager that opens a connection and closes it on exit."""
    conn = open_db(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _schema_text() -> str:
    if not _SCHEMA_PATH.exists():
        raise RuntimeError(f"Base schema not found: {_SCHEMA_PATH}")
    return _SCHEMA_PATH.read_text(encoding="utf-8")


def _apply_base_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_schema_text())
    conn.commit()


def seed_workspace_row(db_path: str, name: str) -> None:
    """Insert the single workspace row the pipeline's ``_get_user_id`` requires.

    Idempotent: does nothing if a workspace row already exists. A freshly created
    DB needs exactly one workspace row before any ingestion runs — both the golden
    corpus builder and the eval-packet builder rely on this.
    """
    conn = open_db(db_path)
    try:
        if conn.execute("SELECT 1 FROM workspace LIMIT 1").fetchone() is None:
            ws_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO workspace (id, name, description, user_id) VALUES (?,?,?,?)",
                (ws_id, name, "", ws_id),
            )
            conn.commit()
    finally:
        conn.close()
