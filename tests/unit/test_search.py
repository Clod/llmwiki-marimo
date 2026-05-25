"""Tests for domain/tools/search.py — search_chunks (step 1.3)."""

import uuid

from domain.tools.db import get_connection
from domain.tools.search import search_chunks
from domain.tools.wiki_fs import create_page
from tests.helpers.workspace import WorkspaceFixture

_WIKI_CONTENT = (
    "# Monetary Policy\n\n"
    "Monetary policy refers to the actions undertaken by a central bank to control "
    "the money supply and achieve macroeconomic goals such as controlling inflation, "
    "consumption, growth, and liquidity.\n"
)

_SOURCE_CONTENT = (
    "Quantitative easing is a monetary policy tool used by central banks to stimulate "
    "the economy by purchasing government securities or other financial assets. "
    "This increases the money supply and encourages lending and investment.\n"
)


def _insert_source_doc(db_path: str, content: str, term: str) -> str:
    """Insert a source-kind document with one chunk directly into the DB."""
    doc_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    with get_connection(db_path) as conn:
        user_id = conn.execute("SELECT user_id FROM workspace LIMIT 1").fetchone()["user_id"]
        doc_number = conn.execute(
            "SELECT COALESCE(MAX(document_number), 0) + 1 FROM documents"
        ).fetchone()[0]
        with conn:
            conn.execute(
                "INSERT INTO documents "
                "(id, user_id, filename, title, path, relative_path, source_kind, "
                "file_type, status, content, document_number) "
                "VALUES (?,?,?,?,'sources/','sources/test.pdf','source','pdf','ready',?,?)",
                (doc_id, user_id, "test.pdf", term, content, doc_number),
            )
            conn.execute(
                "INSERT INTO document_chunks "
                "(id, document_id, chunk_index, content, page, start_char, token_count) "
                "VALUES (?,?,0,?,1,0,?)",
                (chunk_id, doc_id, content, len(content) // 4),
            )
    return doc_id


def test_search_chunks_finds_wiki_content(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "monetary-policy", "Monetary Policy", _WIKI_CONTENT, [],
    )
    results = search_chunks(tmp_workspace.db_path, "inflation")
    assert len(results) > 0
    assert any("inflation" in r["content"].lower() for r in results)


def test_search_chunks_result_has_expected_keys(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "monetary-policy", "Monetary Policy", _WIKI_CONTENT, [],
    )
    results = search_chunks(tmp_workspace.db_path, "inflation")
    assert results
    r = results[0]
    for key in ("content", "page", "filename", "title", "path", "score"):
        assert key in r, f"Missing key: {key}"


def test_search_chunks_scope_wiki_only(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "monetary-policy", "Monetary Policy", _WIKI_CONTENT, [],
    )
    _insert_source_doc(tmp_workspace.db_path, _SOURCE_CONTENT, "QE Paper")

    results = search_chunks(tmp_workspace.db_path, "central bank", scope="wiki")
    assert results
    assert all(r["path"].startswith("/wiki/") for r in results)


def test_search_chunks_scope_sources_only(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "monetary-policy", "Monetary Policy", _WIKI_CONTENT, [],
    )
    _insert_source_doc(tmp_workspace.db_path, _SOURCE_CONTENT, "QE Paper")

    results = search_chunks(tmp_workspace.db_path, "quantitative easing", scope="sources")
    assert results
    assert all(r["path"] == "sources/" for r in results)


def test_search_chunks_no_results(tmp_workspace: WorkspaceFixture) -> None:
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/summaries/", "monetary-policy", "Monetary Policy", _WIKI_CONTENT, [],
    )
    results = search_chunks(tmp_workspace.db_path, "xyzzy_nonexistent_term_42")
    assert results == []


def test_search_chunks_empty_query_returns_empty(tmp_workspace: WorkspaceFixture) -> None:
    results = search_chunks(tmp_workspace.db_path, "")
    assert results == []


def test_search_chunks_respects_limit(tmp_workspace: WorkspaceFixture) -> None:
    for i in range(5):
        create_page(
            tmp_workspace.db_path, tmp_workspace.workspace,
            "/wiki/summaries/", f"page-{i}", f"Page {i}",
            _WIKI_CONTENT.replace("Monetary Policy", f"Topic {i}"), [],
        )
    results = search_chunks(tmp_workspace.db_path, "inflation", limit=2)
    assert len(results) <= 2
