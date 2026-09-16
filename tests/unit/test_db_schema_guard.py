"""Tests for the schema guard in open_db.

The schema in ``database/sqlite_schema.sql`` is applied with
``CREATE TABLE IF NOT EXISTS`` and no ALTER TABLE ever runs, so a table created
by an older version keeps the columns that version declared. Before the guard,
the mismatch surfaced as a raw ``sqlite3.OperationalError`` from whatever query
ran first. No LLM.
"""

import sqlite3

import pytest

from domain.tools.db import SchemaMismatchError, open_db


def test_fresh_database_opens(tmp_path):
    """A database the schema created itself carries every declared column."""
    conn = open_db(str(tmp_path / "index.db"))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        conn.close()
    assert "documents" in tables


def test_reopening_a_current_database_is_not_a_mismatch(tmp_path):
    """The guard runs on every open, so a current database must pass twice."""
    db_path = str(tmp_path / "index.db")
    open_db(db_path).close()
    open_db(db_path).close()


def test_table_missing_a_declared_column_is_rejected(tmp_path):
    """An older `documents` table is reported instead of failing mid-query."""
    db_path = str(tmp_path / "vieja.db")
    legacy = sqlite3.connect(db_path)
    legacy.executescript("CREATE TABLE documents (id TEXT PRIMARY KEY, filename TEXT);")
    legacy.commit()
    legacy.close()

    with pytest.raises(SchemaMismatchError) as excinfo:
        open_db(db_path)

    message = str(excinfo.value)
    assert "documents" in message
    assert "relative_path" in message
    assert db_path in message
    assert "re-ingest" in message


def test_unrelated_table_does_not_trigger_the_guard(tmp_path):
    """A table the schema never declares is somebody else's data, not a mismatch."""
    db_path = str(tmp_path / "ajena.db")
    other = sqlite3.connect(db_path)
    other.executescript("CREATE TABLE notas (id INTEGER PRIMARY KEY, texto TEXT);")
    other.commit()
    other.close()

    open_db(db_path).close()
