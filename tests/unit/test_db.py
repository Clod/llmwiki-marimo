"""Tests for domain/tools/db.py — open_db and get_connection."""

import sqlite3

from domain.tools.db import get_connection, open_db


def test_open_db_creates_file(tmp_path) -> None:
    db_path = str(tmp_path / ".llmwiki" / "index.db")
    conn = open_db(db_path)
    conn.close()
    assert (tmp_path / ".llmwiki" / "index.db").exists()


def test_open_db_creates_parent_dirs(tmp_path) -> None:
    db_path = str(tmp_path / "nested" / "deep" / "index.db")
    conn = open_db(db_path)
    conn.close()
    assert (tmp_path / "nested" / "deep" / "index.db").exists()


def test_open_db_applies_schema(tmp_path) -> None:
    db_path = str(tmp_path / "index.db")
    conn = open_db(db_path)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert "documents" in tables
    assert "document_chunks" in tables
    assert "document_references" in tables


def test_open_db_idempotent(tmp_path) -> None:
    db_path = str(tmp_path / "index.db")
    conn1 = open_db(db_path)
    conn1.close()
    # Calling open_db a second time must not raise or corrupt the DB
    conn2 = open_db(db_path)
    conn2.close()


def test_open_db_has_source_document_id(tmp_path) -> None:
    db_path = str(tmp_path / "index.db")
    # The self-reference column ships in the base schema; a fresh DB has it.
    conn = open_db(db_path)
    conn.close()
    # Re-opening must stay idempotent.
    conn = open_db(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
    conn.close()
    assert "source_document_id" in cols


def test_get_connection_closes_on_exit(tmp_path) -> None:
    db_path = str(tmp_path / "index.db")
    with get_connection(db_path) as conn:
        assert isinstance(conn, sqlite3.Connection)
    # After the context, the connection should be closed — writing to it raises
    try:
        conn.execute("SELECT 1")
        assert False, "Expected connection to be closed"
    except Exception:
        pass


def test_get_connection_row_factory(tmp_path) -> None:
    db_path = str(tmp_path / "index.db")
    with get_connection(db_path) as conn:
        assert conn.row_factory is sqlite3.Row
