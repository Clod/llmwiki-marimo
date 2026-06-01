"""Shared SQLite DB helpers for all domain/tools modules.

open_db() is the single entry point for opening the wiki index DB.
All other modules import from here — do NOT define open_db elsewhere.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_SCHEMA_PATH = Path(__file__).parent.parent.parent.parent / "database" / "sqlite_schema.sql"


def open_db(db_path: str) -> sqlite3.Connection:
    """Open the SQLite DB, applying the base schema on first run."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _apply_base_schema(conn)
    return conn


@contextmanager
def get_connection(db_path: str) -> Iterator[sqlite3.Connection]:
    """Context manager that opens a connection and closes it on exit."""
    conn = open_db(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _apply_base_schema(conn: sqlite3.Connection) -> None:
    if not _SCHEMA_PATH.exists():
        raise RuntimeError(f"Base schema not found: {_SCHEMA_PATH}")
    schema = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()
