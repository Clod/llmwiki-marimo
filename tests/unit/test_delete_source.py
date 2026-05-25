"""Tests for delete_source() (task 11.3)."""

import uuid
from pathlib import Path
from unittest.mock import patch


from domain.tools.deletion import delete_source
from domain.tools.db import get_connection
from tests.helpers.workspace import WorkspaceFixture


# ── helpers ───────────────────────────────────────────────────────────────────

def _insert_source(db_path: str, workspace: Path, filename: str) -> str:
    """Insert a minimal source document row; return its doc_id."""
    doc_id = str(uuid.uuid4())
    relative_path = f"sources/{filename}"
    (workspace / relative_path).write_bytes(b"%PDF-1.4 fake content")
    with get_connection(db_path) as conn:
        user_id = conn.execute("SELECT user_id FROM workspace LIMIT 1").fetchone()["user_id"]
        with conn:
            conn.execute(
                "INSERT INTO documents "
                "(id, user_id, filename, title, path, relative_path, source_kind, file_type, status) "
                "VALUES (?,?,?,?,'sources/',?,?,'pdf','ready')",
                (doc_id, user_id, filename, filename, relative_path, "source"),
            )
            conn.execute(
                "INSERT INTO document_chunks "
                "(id, document_id, chunk_index, content, page, start_char, token_count) "
                "VALUES (?,?,0,'chunk text',1,0,10)",
                (str(uuid.uuid4()), doc_id),
            )
    return doc_id


def _insert_wiki_page(
    db_path: str,
    workspace: Path,
    filename: str,
    source_doc_id: str,
) -> str:
    """Insert a wiki summary page, its file, and a reference to source_doc_id."""
    wiki_id = str(uuid.uuid4())
    relative_path = f"wiki/summaries/{filename}"
    (workspace / relative_path).write_text("# Summary\n", encoding="utf-8")
    with get_connection(db_path) as conn:
        user_id = conn.execute("SELECT user_id FROM workspace LIMIT 1").fetchone()["user_id"]
        with conn:
            conn.execute(
                "INSERT INTO documents "
                "(id, user_id, filename, title, path, relative_path, source_kind, file_type,"
                " status, source_document_id) "
                "VALUES (?,?,?,?,'wiki/summaries/',?,'wiki','md','ready',?)",
                (wiki_id, user_id, filename, filename, relative_path, source_doc_id),
            )
            conn.execute(
                "INSERT INTO document_references "
                "(id, source_document_id, target_document_id, reference_type) "
                "VALUES (?,?,?,'cites')",
                (str(uuid.uuid4()), wiki_id, source_doc_id),
            )
    return wiki_id


# ── tests ─────────────────────────────────────────────────────────────────────

def test_delete_source_removes_db_row(tmp_workspace: WorkspaceFixture) -> None:
    doc_id = _insert_source(tmp_workspace.db_path, tmp_workspace.workspace, "paper.pdf")
    with patch("domain.tools.git_ops.auto_commit"):
        result = delete_source(tmp_workspace.db_path, tmp_workspace.workspace, doc_id)
    assert result.success
    assert result.action == "deleted"
    with get_connection(tmp_workspace.db_path) as conn:
        row = conn.execute("SELECT id FROM documents WHERE id=?", (doc_id,)).fetchone()
    assert row is None


def test_delete_source_cascades_chunks(tmp_workspace: WorkspaceFixture) -> None:
    doc_id = _insert_source(tmp_workspace.db_path, tmp_workspace.workspace, "paper.pdf")
    with patch("domain.tools.git_ops.auto_commit"):
        delete_source(tmp_workspace.db_path, tmp_workspace.workspace, doc_id)
    with get_connection(tmp_workspace.db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE document_id=?", (doc_id,)
        ).fetchone()[0]
    assert count == 0


def test_delete_source_deletes_derived_wiki_page(tmp_workspace: WorkspaceFixture) -> None:
    doc_id = _insert_source(tmp_workspace.db_path, tmp_workspace.workspace, "paper.pdf")
    _insert_wiki_page(tmp_workspace.db_path, tmp_workspace.workspace, "paper-summary.md", doc_id)
    wiki_file = tmp_workspace.workspace / "wiki" / "summaries" / "paper-summary.md"
    assert wiki_file.exists()
    with patch("domain.tools.git_ops.auto_commit"):
        result = delete_source(tmp_workspace.db_path, tmp_workspace.workspace, doc_id)
    assert result.success
    assert "1 derived wiki page(s)" in result.message
    assert not wiki_file.exists()
    with get_connection(tmp_workspace.db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE source_kind='wiki'"
        ).fetchone()[0]
    assert count == 0


def test_delete_source_not_found(tmp_workspace: WorkspaceFixture) -> None:
    result = delete_source(tmp_workspace.db_path, tmp_workspace.workspace, "nonexistent-id")
    assert not result.success
    assert result.action == "failed"
    assert "not found" in result.message


def test_delete_source_wrong_kind(tmp_workspace: WorkspaceFixture) -> None:
    wiki_id = str(uuid.uuid4())
    with get_connection(tmp_workspace.db_path) as conn:
        user_id = conn.execute("SELECT user_id FROM workspace LIMIT 1").fetchone()["user_id"]
        with conn:
            conn.execute(
                "INSERT INTO documents "
                "(id, user_id, filename, title, path, relative_path, source_kind, file_type, status) "
                "VALUES (?,?,?,?,'wiki/concepts/','wiki/concepts/foo.md','wiki','md','ready')",
                (wiki_id, user_id, "foo.md", "Foo"),
            )
    result = delete_source(tmp_workspace.db_path, tmp_workspace.workspace, wiki_id)
    assert not result.success
    assert result.action == "failed"
    assert "not a source" in result.message


def test_delete_source_also_removes_file(tmp_workspace: WorkspaceFixture) -> None:
    doc_id = _insert_source(tmp_workspace.db_path, tmp_workspace.workspace, "report.pdf")
    file_path = tmp_workspace.workspace / "sources" / "report.pdf"
    assert file_path.exists()
    with patch("domain.tools.git_ops.auto_commit"):
        result = delete_source(
            tmp_workspace.db_path, tmp_workspace.workspace, doc_id, also_delete_file=True
        )
    assert result.success
    assert not file_path.exists()
