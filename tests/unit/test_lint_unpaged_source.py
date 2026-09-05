"""Tests for unpaged_source_check — a source stored with no wiki page written from it.

Ingestion commits the source row as ``status='ready'`` at step 6, before the model
writes anything. A model failure in steps 7-9 leaves the source indexed with no
page, and nothing else reports it: ``needs_ingestion`` compares mtime and hash, so
every later scan calls the file up to date, and ``thin_page_check`` skips a source
that has no citing page at all. No LLM.
"""

import uuid

from domain.lint.checks import unpaged_source_check
from domain.tools.db import get_connection
from tests.helpers.workspace import WorkspaceFixture


def _insert_source(db_path: str, filename: str, status: str = "ready") -> str:
    doc_id = str(uuid.uuid4())
    with get_connection(db_path) as conn:
        user_id = conn.execute("SELECT user_id FROM workspace LIMIT 1").fetchone()["user_id"]
        doc_number = conn.execute(
            "SELECT COALESCE(MAX(document_number), 0) + 1 FROM documents"
        ).fetchone()[0]
        with conn:
            conn.execute(
                "INSERT INTO documents "
                "(id, user_id, filename, title, path, relative_path, source_kind, "
                "file_type, status, content, page_count, document_number) "
                "VALUES (?,?,?,?,'sources/',?,'source','pdf',?,'',1,?)",
                (doc_id, user_id, filename, filename, f"sources/{filename}",
                 status, doc_number),
            )
    return doc_id


def _insert_wiki_page_citing(db_path: str, slug: str, source_id: str) -> str:
    page_id = str(uuid.uuid4())
    with get_connection(db_path) as conn:
        user_id = conn.execute("SELECT user_id FROM workspace LIMIT 1").fetchone()["user_id"]
        doc_number = conn.execute(
            "SELECT COALESCE(MAX(document_number), 0) + 1 FROM documents"
        ).fetchone()[0]
        with conn:
            conn.execute(
                "INSERT INTO documents "
                "(id, user_id, filename, title, path, relative_path, source_kind, "
                "file_type, status, content, page_count, document_number) "
                "VALUES (?,?,?,?,'/wiki/summaries/',?,'wiki','md','ready','',1,?)",
                (page_id, user_id, f"{slug}.md", slug, f"wiki/summaries/{slug}.md",
                 doc_number),
            )
            conn.execute(
                "INSERT INTO document_references "
                "(id, source_document_id, target_document_id, reference_type) "
                "VALUES (?,?,?,'cites')",
                (str(uuid.uuid4()), page_id, source_id),
            )
    return page_id


def test_a_source_with_no_page_is_reported(tmp_workspace: WorkspaceFixture) -> None:
    ws = tmp_workspace
    _insert_source(ws.db_path, "Cinderella.pdf")

    issues = unpaged_source_check(ws.db_path)

    assert len(issues) == 1
    assert issues[0].check == "unpaged_source"
    assert "Cinderella.pdf" in issues[0].description
    assert "ingest it again" in issues[0].suggestion


def test_a_source_a_page_cites_is_not_reported(tmp_workspace: WorkspaceFixture) -> None:
    ws = tmp_workspace
    source_id = _insert_source(ws.db_path, "Cinderella.pdf")
    _insert_wiki_page_citing(ws.db_path, "cinderella", source_id)

    assert unpaged_source_check(ws.db_path) == []


def test_a_failed_source_is_not_reported(tmp_workspace: WorkspaceFixture) -> None:
    """status='failed' means ingestion is known to have gone wrong and said so.

    The check exists for the silent case: a source the pipeline considers
    complete that produced nothing.
    """
    ws = tmp_workspace
    _insert_source(ws.db_path, "Broken.pdf", status="failed")

    assert unpaged_source_check(ws.db_path) == []


def test_wiki_pages_are_not_mistaken_for_sources(tmp_workspace: WorkspaceFixture) -> None:
    """A wiki page cites nothing and must not be reported as an unpaged source."""
    ws = tmp_workspace
    source_id = _insert_source(ws.db_path, "Cinderella.pdf")
    _insert_wiki_page_citing(ws.db_path, "cinderella", source_id)

    reported = {i.page for i in unpaged_source_check(ws.db_path)}
    assert reported == set()
