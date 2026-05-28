"""Phase 3.6 — Full lint integration test.

Sets up a wiki with all known issue types and verifies lint_wiki() detects all of them.
Also verifies the pipeline lint_after_ingest flag appends results to log.md.
"""

import json
import shutil
import uuid
from pathlib import Path

from domain.lint.runner import lint_wiki
from domain.tools.db import get_connection
from domain.tools.references import update_references
from domain.tools.wiki_fs import create_page
from tests.helpers.fake_llm import FakeLLMClient
from tests.helpers.workspace import WorkspaceFixture

_FIXTURES = Path(__file__).parent.parent / "fixtures"

_BASE = (
    "This concept covers financial theory related to market efficiency "
    "and portfolio diversification in global markets.\n"
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


def _set_updated_at(db_path: str, doc_id: str, ts: str) -> None:
    with get_connection(db_path) as conn:
        with conn:
            conn.execute("UPDATE documents SET updated_at=? WHERE id=?", (ts, doc_id))


def _setup_all_issues(tmp_workspace: WorkspaceFixture) -> None:
    db = tmp_workspace.db_path
    ws = tmp_workspace.workspace

    # ── Orphan: concept-orphan has no inbound links ───────────────────────────
    create_page(db, ws, "/wiki/concepts/", "orphan-concept", "Orphan Concept",
                f"# Orphan\n\n{_BASE}", [])

    # ── Stale: concept-stale cites a source that is newer than the page ───────
    source_id = _insert_source(db, "stale-source.pdf")
    stale_content = f"# Stale Concept\n\n{_BASE}\n\n[^1]: stale-source.pdf\n"
    stale_page = create_page(db, ws, "/wiki/concepts/", "stale-concept", "Stale Concept",
                             stale_content, [])
    update_references(db, stale_page["id"], stale_content, "/wiki/concepts/")
    _set_updated_at(db, source_id, "2099-01-01 00:00:00")  # source is "in the future"

    # ── Missing xref: concept-a and concept-b share a source but don't link ───
    _insert_source(db, "shared.pdf")
    content_a = f"# Concept A\n\n{_BASE}\n\n[^1]: shared.pdf\n"
    content_b = f"# Concept B\n\n{_BASE}\n\n[^1]: shared.pdf\n"
    page_a = create_page(db, ws, "/wiki/concepts/", "concept-a", "Concept A", content_a, [])
    page_b = create_page(db, ws, "/wiki/concepts/", "concept-b", "Concept B", content_b, [])
    update_references(db, page_a["id"], content_a, "/wiki/concepts/")
    update_references(db, page_b["id"], content_b, "/wiki/concepts/")

    # ── Missing concept: summary links to nonexistent concept page ────────────
    broken_content = (
        f"# My Summary\n\n{_BASE}\n\n"
        "See [Missing Thing](../concepts/missing-thing.md).\n"
    )
    create_page(db, ws, "/wiki/summaries/", "my-summary", "My Summary", broken_content, [])


def test_lint_wiki_detects_all_issue_types(tmp_workspace: WorkspaceFixture) -> None:
    _setup_all_issues(tmp_workspace)

    llm = FakeLLMClient(responses=[
        "CONTRADICTION: conflicting claims about rates",  # contradiction check
        "GAP: Portfolio Diversification — Add concept on diversification",  # data_gap: matches _BASE
    ])

    report = lint_wiki(tmp_workspace.db_path, tmp_workspace.workspace, llm, "fake")

    check_types = {i.check for i in report.issues}
    assert "orphan" in check_types
    assert "stale" in check_types
    assert "missing_xref" in check_types
    assert "missing_concept" in check_types
    assert "contradiction" in check_types
    assert "data_gap" in check_types


def test_lint_report_summary_counts_correctly(tmp_workspace: WorkspaceFixture) -> None:
    _setup_all_issues(tmp_workspace)
    llm = FakeLLMClient(response_content="NO CONTRADICTION")
    report = lint_wiki(tmp_workspace.db_path, tmp_workspace.workspace, llm, "fake")
    assert report.issues
    summary = report.summary()
    assert "issue" in summary
    assert report.checked_at


def test_lint_without_llm_runs_deterministic_checks(tmp_workspace: WorkspaceFixture) -> None:
    _setup_all_issues(tmp_workspace)
    # No client passed → LLM checks skipped
    report = lint_wiki(tmp_workspace.db_path, tmp_workspace.workspace)
    check_types = {i.check for i in report.issues}
    assert "orphan" in check_types
    assert "stale" in check_types
    assert "missing_xref" in check_types
    assert "missing_concept" in check_types
    assert "contradiction" not in check_types
    assert "data_gap" not in check_types


def test_lint_after_ingest_appends_to_log(tmp_workspace: WorkspaceFixture) -> None:
    """pipeline.ingest_file with lint_after_ingest=True appends lint summary to log.md."""
    from domain.ingestion.pipeline import ingest_file

    pdf = _FIXTURES / "pdfs" / "Snow White and the Seven Dwarfs.pdf"
    dest = tmp_workspace.workspace / "sources" / pdf.name
    shutil.copy(pdf, dest)

    extraction_json = json.dumps({
        "document_summary": "A fairy tale about Snow White.",
        "concepts": [{"name": "Snow White", "category": "entity",
                      "insight": "The protagonist princess"}],
    })
    tmp_workspace.llm.responses = [
        extraction_json,                      # extract_structured
        f"# Snow White\n\n{_BASE}",           # build_concept_page
        "# Overview\n\nUpdated narrative.\n", # update_overview
        "NO CONTRADICTION",                   # lint contradiction check
    ]

    # Pre-create an orphan so the lint has something to report
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/concepts/", "pre-existing-orphan", "Pre Existing Orphan",
                f"# Pre Existing Orphan\n\n{_BASE}", [])

    ingest_file(dest, tmp_workspace.db_path, tmp_workspace.workspace,
                tmp_workspace.llm, "fake", lint_after_ingest=True)

    log = (tmp_workspace.workspace / "wiki" / "log.md").read_text()
    assert "Lint" in log
    assert "orphan" in log.lower()
