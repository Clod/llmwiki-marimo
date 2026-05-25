"""Tests for staleness_check — step 3.2."""

import uuid

from domain.tools.db import get_connection
from domain.tools.wiki_fs import create_page
from domain.lint.checks import staleness_check
from tests.helpers.workspace import WorkspaceFixture

_CONTENT = (
    "# Federal Reserve\n\n"
    "The Federal Reserve is responsible for US monetary policy and financial stability. "
    "It uses tools like interest rate adjustments and quantitative easing programs.\n\n"
    "[^1]: paper.pdf\n"
)


def _insert_source(db_path: str, filename: str) -> str:
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
                "file_type, status, content, document_number) "
                "VALUES (?,?,?,?,'sources/',?,'source','pdf','ready','',?)",
                (doc_id, user_id, filename, filename, f"sources/{filename}", doc_number),
            )
    return doc_id


def _create_cite_edge(db_path: str, wiki_id: str, source_id: str) -> None:
    with get_connection(db_path) as conn:
        with conn:
            conn.execute(
                "INSERT INTO document_references "
                "(source_document_id, target_document_id, reference_type) "
                "VALUES (?,?,'cites')",
                (wiki_id, source_id),
            )


def _set_updated_at(db_path: str, doc_id: str, ts: str) -> None:
    with get_connection(db_path) as conn:
        with conn:
            conn.execute("UPDATE documents SET updated_at=? WHERE id=?", (ts, doc_id))


def test_staleness_check_flags_when_source_newer(tmp_workspace: WorkspaceFixture) -> None:
    concept = create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                          "/wiki/concepts/", "federal-reserve", "Federal Reserve", _CONTENT, [])
    source_id = _insert_source(tmp_workspace.db_path, "paper.pdf")
    _create_cite_edge(tmp_workspace.db_path, concept["id"], source_id)
    # Make source appear much newer than the concept page
    _set_updated_at(tmp_workspace.db_path, source_id, "2099-01-01 00:00:00")

    issues = staleness_check(tmp_workspace.db_path)
    assert any(i.check == "stale" and "federal-reserve" in i.page for i in issues)


def test_staleness_check_no_flag_when_source_older(tmp_workspace: WorkspaceFixture) -> None:
    concept = create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                          "/wiki/concepts/", "federal-reserve", "Federal Reserve", _CONTENT, [])
    source_id = _insert_source(tmp_workspace.db_path, "paper.pdf")
    _create_cite_edge(tmp_workspace.db_path, concept["id"], source_id)
    # Make source appear much older than the concept page
    _set_updated_at(tmp_workspace.db_path, source_id, "2000-01-01 00:00:00")

    issues = staleness_check(tmp_workspace.db_path)
    assert not any(i.check == "stale" for i in issues)


def test_staleness_check_no_flag_when_no_citations(tmp_workspace: WorkspaceFixture) -> None:
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/concepts/", "federal-reserve", "Federal Reserve", _CONTENT, [])
    # No citation edges — no sources to compare against
    issues = staleness_check(tmp_workspace.db_path)
    assert not any(i.check == "stale" for i in issues)
