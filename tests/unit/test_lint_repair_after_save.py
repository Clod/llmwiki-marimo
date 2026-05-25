"""Tests for _lint_and_repair_after_save helper (task 11.2)."""

import uuid
from unittest.mock import patch

from domain.chat.wiki_tools import _lint_and_repair_after_save, save_to_wiki
from domain.lint.report import LintIssue, LintReport
from domain.repair.report import RepairReport, RepairResult
from domain.tools.db import get_connection
from tests.helpers.fake_llm import FakeLLMClient
from tests.helpers.workspace import WorkspaceFixture


def _issue(check: str, page: str) -> LintIssue:
    return LintIssue(check=check, severity="error", page=page,
                     description="desc", suggestion="fix")


def _repair_report(*results: RepairResult) -> RepairReport:
    return RepairReport(results=list(results))


def _repair_result(check: str, page: str, action: str = "concept_created") -> RepairResult:
    return RepairResult(check=check, page=page, action=action, success=True, message="ok")


# ── 1. No issues → returns "" ─────────────────────────────────────────────────

def test_no_issues_returns_empty(tmp_workspace: WorkspaceFixture) -> None:
    empty_report = LintReport(issues=[])
    with patch("domain.lint.runner.lint_wiki", return_value=empty_report), \
         patch("domain.repair.runner.repair_wiki") as mock_repair:
        result = _lint_and_repair_after_save(
            tmp_workspace.db_path, tmp_workspace.workspace,
            "wiki/concepts/my-concept.md",
        )
    assert result == ""
    mock_repair.assert_not_called()


# ── 2. Issues for other pages are excluded ────────────────────────────────────

def test_issues_for_other_pages_excluded(tmp_workspace: WorkspaceFixture) -> None:
    other_issue = _issue("missing_concept", "/wiki/concepts/other.md")
    report = LintReport(issues=[other_issue])
    with patch("domain.lint.runner.lint_wiki", return_value=report), \
         patch("domain.repair.runner.repair_wiki") as mock_repair:
        result = _lint_and_repair_after_save(
            tmp_workspace.db_path, tmp_workspace.workspace,
            "wiki/concepts/my-concept.md",
        )
    assert result == ""
    mock_repair.assert_not_called()


# ── 3. Orphan issue for saved page is excluded ────────────────────────────────

def test_orphan_issue_for_saved_page_excluded(tmp_workspace: WorkspaceFixture) -> None:
    orphan_issue = _issue("orphan", "/wiki/concepts/my-concept.md")
    report = LintReport(issues=[orphan_issue])
    with patch("domain.lint.runner.lint_wiki", return_value=report), \
         patch("domain.repair.runner.repair_wiki") as mock_repair:
        result = _lint_and_repair_after_save(
            tmp_workspace.db_path, tmp_workspace.workspace,
            "wiki/concepts/my-concept.md",
        )
    assert result == ""
    mock_repair.assert_not_called()


# ── 4. missing_concept issue triggers repair ──────────────────────────────────

def test_missing_concept_triggers_repair(tmp_workspace: WorkspaceFixture) -> None:
    issue = _issue("missing_concept", "/wiki/concepts/my-concept.md")
    lint_report = LintReport(issues=[issue])
    repair = _repair_report(_repair_result("missing_concept", "/wiki/concepts/my-concept.md"))

    with patch("domain.lint.runner.lint_wiki", return_value=lint_report), \
         patch("domain.repair.runner.repair_wiki", return_value=repair) as mock_repair:
        result = _lint_and_repair_after_save(
            tmp_workspace.db_path, tmp_workspace.workspace,
            "wiki/concepts/my-concept.md",
        )

    assert result != ""
    assert "1 issue(s)" in result
    called_report = mock_repair.call_args[0][0]
    assert len(called_report.issues) == 1
    assert called_report.issues[0].check == "missing_concept"


# ── 5. Exception is swallowed ─────────────────────────────────────────────────

def test_exception_swallowed(tmp_workspace: WorkspaceFixture) -> None:
    with patch("domain.lint.runner.lint_wiki", side_effect=RuntimeError("db gone")):
        result = _lint_and_repair_after_save(
            tmp_workspace.db_path, tmp_workspace.workspace,
            "wiki/concepts/my-concept.md",
        )
    assert result == ""


# ── 6. page_path without leading slash normalizes correctly ───────────────────

def test_page_path_normalized(tmp_workspace: WorkspaceFixture) -> None:
    issue = _issue("missing_xref", "/wiki/concepts/my-concept.md")
    lint_report = LintReport(issues=[issue])
    repair = _repair_report(_repair_result("missing_xref", "/wiki/concepts/my-concept.md",
                                           action="skipped"))

    with patch("domain.lint.runner.lint_wiki", return_value=lint_report), \
         patch("domain.repair.runner.repair_wiki", return_value=repair):
        result = _lint_and_repair_after_save(
            tmp_workspace.db_path, tmp_workspace.workspace,
            "wiki/concepts/my-concept.md",  # no leading slash
        )

    assert "1 issue(s)" in result


# ── 7. End-to-end: saving a chat page auto-cross-links it (§6.8) ───────────────

def test_save_to_wiki_auto_cross_links_shared_source(tmp_workspace: WorkspaceFixture) -> None:
    """Proof that §6.8 closes the loop: when a saved chat page cites the same
    source as an existing page, the post-save hook runs the (now-implemented)
    repair_missing_xref and adds a ## See also link to the saved page.

    The existing page is given a max-value id so the freshly-saved page (random
    uuid4) sorts as `path_a` in missing_xref_check — the direction the post-save
    filter can act on. (The directional limitation is noted in manual §12.)
    """
    db = tmp_workspace.db_path
    ws = tmp_workspace.workspace
    with get_connection(db) as conn:
        uid = conn.execute("SELECT user_id FROM workspace LIMIT 1").fetchone()["user_id"]
        src_id = str(uuid.uuid4())
        # Existing wiki page with a guaranteed-highest id → saved page becomes path_a
        existing_id = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        with conn:
            conn.execute(
                "INSERT INTO documents "
                "(id, user_id, filename, title, path, relative_path, source_kind, "
                "file_type, status, content, page_count, document_number) "
                "VALUES (?,?,?,?,?,?,'source','pdf','ready','',1,1)",
                (src_id, uid, "shared.pdf", "shared.pdf", "/sources/", "sources/shared.pdf"),
            )
            conn.execute(
                "INSERT INTO documents "
                "(id, user_id, filename, title, path, relative_path, source_kind, "
                "file_type, status, content, document_number) "
                "VALUES (?,?,?,?,?,?,'wiki','md','ready',?,2)",
                (existing_id, uid, "federal-reserve.md", "Federal Reserve",
                 "/wiki/concepts/", "wiki/concepts/federal-reserve.md",
                 "# Federal Reserve\n\n[^1]: shared.pdf\n"),
            )
            # Existing page cites the shared source
            conn.execute(
                "INSERT INTO document_references "
                "(source_document_id, target_document_id, reference_type) "
                "VALUES (?,?,'cites')",
                (existing_id, src_id),
            )

    # FakeLLM returns structured markdown that cites the same source
    fake = FakeLLMClient(response_content=(
        "# Interest Rates\n\n"
        "Central banks adjust interest rates to steer the economy.\n\n"
        "[^1]: shared.pdf\n"
    ))

    msg = save_to_wiki(
        db, ws, "Interest Rates", "Raw chat content about rates.",
        category="concept", client=fake, model="fake",
    )

    saved = ws / "wiki" / "concepts" / "interest-rates.md"
    content = saved.read_text(encoding="utf-8")
    assert "## See also" in content
    assert "federal-reserve.md" in content
    assert "Post-save repair" in msg
