"""Tests for missing_xref_check — step 3.3."""

import uuid

from domain.tools.db import get_connection
from domain.tools.references import update_references
from domain.tools.wiki_fs import create_page
from domain.lint.checks import missing_xref_check
from tests.helpers.workspace import WorkspaceFixture

_BASE = (
    "This concept covers important financial theory related to market efficiency "
    "and portfolio diversification.\n"
)


def _page(content: str) -> str:
    return f"# Concept\n\n{content}\n\n[^1]: shared-source.pdf\n"


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


def test_missing_xref_flagged_when_shared_source_no_link(tmp_workspace: WorkspaceFixture) -> None:
    _insert_source(tmp_workspace.db_path, "shared-source.pdf")

    content_a = _page("Concept A content about monetary policy.")
    page_a = create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                         "/wiki/concepts/", "concept-a", "Concept A", content_a, [])
    update_references(tmp_workspace.db_path, page_a["id"], content_a, "/wiki/concepts/")

    content_b = _page("Concept B content about fiscal policy.")
    page_b = create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                         "/wiki/concepts/", "concept-b", "Concept B", content_b, [])
    update_references(tmp_workspace.db_path, page_b["id"], content_b, "/wiki/concepts/")

    issues = missing_xref_check(tmp_workspace.db_path)
    assert any(i.check == "missing_xref" for i in issues)
    # Both pages should appear in some description
    assert any("Concept A" in i.description or "Concept B" in i.description for i in issues)


def test_missing_xref_not_flagged_when_linked(tmp_workspace: WorkspaceFixture) -> None:
    _insert_source(tmp_workspace.db_path, "shared-source.pdf")

    content_a = _page("Concept A. See also [Concept B](concept-b.md).")
    page_a = create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                         "/wiki/concepts/", "concept-a", "Concept A", content_a, [])

    content_b = _page("Concept B content.")
    page_b = create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                         "/wiki/concepts/", "concept-b", "Concept B", content_b, [])

    update_references(tmp_workspace.db_path, page_a["id"], content_a, "/wiki/concepts/")
    update_references(tmp_workspace.db_path, page_b["id"], content_b, "/wiki/concepts/")

    issues = missing_xref_check(tmp_workspace.db_path)
    assert not any(i.check == "missing_xref" for i in issues)


def test_missing_xref_not_flagged_when_no_shared_source(tmp_workspace: WorkspaceFixture) -> None:
    _insert_source(tmp_workspace.db_path, "source-a.pdf")
    _insert_source(tmp_workspace.db_path, "source-b.pdf")

    content_a = "# Concept A\n\nContent.\n\n[^1]: source-a.pdf\n"
    content_b = "# Concept B\n\nContent.\n\n[^1]: source-b.pdf\n"

    page_a = create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                         "/wiki/concepts/", "concept-a", "Concept A", content_a, [])
    page_b = create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                         "/wiki/concepts/", "concept-b", "Concept B", content_b, [])
    update_references(tmp_workspace.db_path, page_a["id"], content_a, "/wiki/concepts/")
    update_references(tmp_workspace.db_path, page_b["id"], content_b, "/wiki/concepts/")

    issues = missing_xref_check(tmp_workspace.db_path)
    assert not any(i.check == "missing_xref" for i in issues)
