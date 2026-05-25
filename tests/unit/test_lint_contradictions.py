"""Tests for contradiction_check and data_gap_check — step 3.5."""

import uuid

from domain.tools.db import get_connection
from domain.tools.wiki_fs import create_page
from domain.lint.checks import contradiction_check, data_gap_check
from tests.helpers.fake_llm import FakeLLMClient
from tests.helpers.workspace import WorkspaceFixture

_CONTENT_A = (
    "# Federal Reserve\n\n"
    "The Federal Reserve raises interest rates to combat inflation. "
    "Higher rates reduce consumer spending and slow economic growth.\n\n"
    "[^1]: shared.pdf\n"
)

_CONTENT_B = (
    "# Quantitative Easing\n\n"
    "Quantitative easing lowers interest rates by purchasing bonds. "
    "This stimulates lending and investment during downturns.\n\n"
    "[^1]: shared.pdf\n"
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


def _setup_two_related_concepts(tmp_workspace: WorkspaceFixture):
    from domain.tools.references import update_references
    _insert_source(tmp_workspace.db_path, "shared.pdf")
    page_a = create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                         "/wiki/concepts/", "federal-reserve", "Federal Reserve",
                         _CONTENT_A, [])
    page_b = create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                         "/wiki/concepts/", "quantitative-easing", "Quantitative Easing",
                         _CONTENT_B, [])
    update_references(tmp_workspace.db_path, page_a["id"], _CONTENT_A, "/wiki/concepts/")
    update_references(tmp_workspace.db_path, page_b["id"], _CONTENT_B, "/wiki/concepts/")
    return page_a, page_b


# ── contradiction_check ───────────────────────────────────────────────────────

def test_contradiction_check_detects_planted_contradiction(
    tmp_workspace: WorkspaceFixture,
) -> None:
    _setup_two_related_concepts(tmp_workspace)
    llm = FakeLLMClient(
        response_content="CONTRADICTION: Page A says rates rise, Page B says rates fall"
    )
    issues = contradiction_check(
        tmp_workspace.db_path, tmp_workspace.workspace, llm, "fake"
    )
    assert any(i.check == "contradiction" and i.severity == "error" for i in issues)
    assert any("rates" in i.description.lower() for i in issues)


def test_contradiction_check_no_issue_on_no_contradiction(
    tmp_workspace: WorkspaceFixture,
) -> None:
    _setup_two_related_concepts(tmp_workspace)
    llm = FakeLLMClient(response_content="NO CONTRADICTION")
    issues = contradiction_check(
        tmp_workspace.db_path, tmp_workspace.workspace, llm, "fake"
    )
    assert not any(i.check == "contradiction" for i in issues)


def test_contradiction_check_skipped_when_no_shared_sources(
    tmp_workspace: WorkspaceFixture,
) -> None:
    # Two concept pages citing different sources — no pairs formed
    from domain.tools.references import update_references
    _insert_source(tmp_workspace.db_path, "source-a.pdf")
    _insert_source(tmp_workspace.db_path, "source-b.pdf")

    ca = "# A\n\nContent.\n\n[^1]: source-a.pdf\n"
    cb = "# B\n\nContent.\n\n[^1]: source-b.pdf\n"
    pa = create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                     "/wiki/concepts/", "concept-a", "A", ca, [])
    pb = create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                     "/wiki/concepts/", "concept-b", "B", cb, [])
    update_references(tmp_workspace.db_path, pa["id"], ca, "/wiki/concepts/")
    update_references(tmp_workspace.db_path, pb["id"], cb, "/wiki/concepts/")

    llm = FakeLLMClient(response_content="CONTRADICTION: this should not appear")
    issues = contradiction_check(
        tmp_workspace.db_path, tmp_workspace.workspace, llm, "fake"
    )
    assert not any(i.check == "contradiction" for i in issues)


# ── data_gap_check ────────────────────────────────────────────────────────────

def test_data_gap_check_returns_gaps(tmp_workspace: WorkspaceFixture) -> None:
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/concepts/", "federal-reserve", "Federal Reserve", _CONTENT_A, [])
    # Use topics that appear in _CONTENT_A so FTS finds a host page
    llm = FakeLLMClient(
        response_content=(
            "GAP: Inflation — Add a concept page explaining inflation dynamics\n"
            "GAP: Interest Rates — Add a concept page on interest rate policy"
        )
    )
    issues = data_gap_check(
        tmp_workspace.db_path, tmp_workspace.workspace, llm, "fake"
    )
    assert len(issues) >= 1  # at least one gap maps to a host page
    assert all(i.check == "data_gap" and i.severity == "info" for i in issues)
    assert any("Inflation" in i.description for i in issues)


def test_data_gap_check_no_issue_when_no_gaps(tmp_workspace: WorkspaceFixture) -> None:
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/concepts/", "federal-reserve", "Federal Reserve", _CONTENT_A, [])
    llm = FakeLLMClient(response_content="NO GAPS")
    issues = data_gap_check(
        tmp_workspace.db_path, tmp_workspace.workspace, llm, "fake"
    )
    assert not any(i.check == "data_gap" for i in issues)


def test_data_gap_check_skipped_when_no_concepts(tmp_workspace: WorkspaceFixture) -> None:
    llm = FakeLLMClient(response_content="GAP: Something — Do something")
    issues = data_gap_check(
        tmp_workspace.db_path, tmp_workspace.workspace, llm, "fake"
    )
    assert issues == []
