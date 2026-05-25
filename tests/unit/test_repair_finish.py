"""TDD tests for the three new repair actions: missing_xref, contradiction, data_gap,
and the new gap_filled check+repair.

Implements PRD: .trellis/tasks/05-25-finish-repair-actions/prd.md
"""

import uuid

from domain.lint.report import LintIssue
from domain.lint.runner import lint_wiki
from domain.repair.actions import (
    repair_contradiction,
    repair_data_gap,
    repair_gap_filled,
    repair_missing_xref,
)
from domain.repair.runner import repair_wiki
from domain.lint.report import LintReport
from domain.tools.db import get_connection
from domain.tools.references import get_forward_refs
from domain.tools.wiki_fs import create_page
from tests.helpers.workspace import WorkspaceFixture

_LONG = "word " * 60  # exceed MIN_CHUNK_TOKENS


# ── helpers ───────────────────────────────────────────────────────────────────

def _workspace_id(db_path: str) -> str:
    with get_connection(db_path) as conn:
        return conn.execute("SELECT user_id FROM workspace LIMIT 1").fetchone()["user_id"]


def _insert_source(db_path: str, filename: str, content: str) -> str:
    uid = _workspace_id(db_path)
    doc_id = str(uuid.uuid4())
    with get_connection(db_path) as conn:
        with conn:
            conn.execute(
                "INSERT INTO documents "
                "(id, user_id, filename, title, path, relative_path, source_kind, "
                "file_type, status, content, page_count, document_number) "
                "VALUES (?,?,?,?,?,?,'source','pdf','ready',?,1,1)",
                (doc_id, uid, filename, filename, "/sources/",
                 f"sources/{filename}", content),
            )
            conn.execute(
                "INSERT INTO document_pages (id, document_id, page, content) "
                "VALUES (?,?,1,?)",
                (str(uuid.uuid4()), doc_id, content),
            )
    return doc_id


def _insert_chunk(db_path: str, doc_id: str, content: str, chunk_index: int = 99) -> None:
    """Insert a searchable chunk for a document directly."""
    with get_connection(db_path) as conn:
        with conn:
            conn.execute(
                "INSERT INTO document_chunks "
                "(id, document_id, chunk_index, content, token_count) "
                "VALUES (?,?,?,?,?)",
                (str(uuid.uuid4()), doc_id, chunk_index, content, len(content.split())),
            )
            # keep FTS in sync
            conn.execute(
                "INSERT INTO chunks_fts(rowid, content) "
                "SELECT rowid, content FROM document_chunks "
                "WHERE document_id=? ORDER BY chunk_index DESC LIMIT 1",
                (doc_id,),
            )


def _create_concept(ws: WorkspaceFixture, slug: str, title: str, content: str) -> dict:
    return create_page(
        ws.db_path, ws.workspace,
        "/wiki/concepts/", slug, title, content, [],
    )


def _get_doc_id(db_path: str, page_path: str) -> str | None:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM documents WHERE path||filename=?", (page_path,)
        ).fetchone()
    return row["id"] if row else None


# ══════════════════════════════════════════════════════════════════════════════
# INCREMENT 1 — repair_missing_xref
# ══════════════════════════════════════════════════════════════════════════════

class TestRepairMissingXref:
    def test_adds_see_also_link(self, tmp_workspace: WorkspaceFixture) -> None:
        """Repair appends a ## See also section with a relative link."""
        _create_concept(tmp_workspace, "interest-rates", "Interest Rates",
                        f"# Interest Rates\n\n{_LONG}")
        _create_concept(tmp_workspace, "federal-reserve", "Federal Reserve",
                        f"# Federal Reserve\n\n{_LONG}")

        issue = LintIssue(
            check="missing_xref", severity="info",
            page="/wiki/concepts/interest-rates.md",
            description="'Interest Rates' and 'Federal Reserve' share cited sources",
            suggestion="Add a cross-reference link",
            related_page="/wiki/concepts/federal-reserve.md",
        )
        result = repair_missing_xref(issue, tmp_workspace.db_path, tmp_workspace.workspace)

        assert result.success
        assert result.action == "xref_added"
        content = (tmp_workspace.workspace / "wiki/concepts/interest-rates.md").read_text()
        assert "## See also" in content
        assert "federal-reserve.md" in content

    def test_creates_links_to_edge(self, tmp_workspace: WorkspaceFixture) -> None:
        """Repair creates a links_to DB edge after appending the link."""
        _create_concept(tmp_workspace, "page-a", "Page A", f"# Page A\n\n{_LONG}")
        _create_concept(tmp_workspace, "page-b", "Page B", f"# Page B\n\n{_LONG}")

        issue = LintIssue(
            check="missing_xref", severity="info",
            page="/wiki/concepts/page-a.md",
            description="A and B share sources",
            suggestion="",
            related_page="/wiki/concepts/page-b.md",
        )
        repair_missing_xref(issue, tmp_workspace.db_path, tmp_workspace.workspace)

        doc_a_id = _get_doc_id(tmp_workspace.db_path, "/wiki/concepts/page-a.md")
        assert doc_a_id is not None
        refs = get_forward_refs(tmp_workspace.db_path, doc_a_id)
        assert any(r["reference_type"] == "links_to" for r in refs)

    def test_idempotent_skips_if_already_linked(self, tmp_workspace: WorkspaceFixture) -> None:
        """Second repair call returns skipped — no duplicate section."""
        _create_concept(tmp_workspace, "page-a", "Page A", f"# Page A\n\n{_LONG}")
        _create_concept(tmp_workspace, "page-b", "Page B", f"# Page B\n\n{_LONG}")

        issue = LintIssue(
            check="missing_xref", severity="info",
            page="/wiki/concepts/page-a.md",
            description="A and B share sources",
            suggestion="",
            related_page="/wiki/concepts/page-b.md",
        )
        repair_missing_xref(issue, tmp_workspace.db_path, tmp_workspace.workspace)
        result2 = repair_missing_xref(issue, tmp_workspace.db_path, tmp_workspace.workspace)

        assert result2.action == "skipped"
        content = (tmp_workspace.workspace / "wiki/concepts/page-a.md").read_text()
        assert content.count("## See also") == 1

    def test_skips_if_missing_related_page(self, tmp_workspace: WorkspaceFixture) -> None:
        """Skips gracefully when related_page is empty."""
        issue = LintIssue(
            check="missing_xref", severity="info",
            page="/wiki/concepts/page-a.md",
            description="A and B share sources",
            suggestion="",
        )
        result = repair_missing_xref(issue, tmp_workspace.db_path, tmp_workspace.workspace)
        assert result.action == "skipped"

    def test_appends_bullet_to_existing_see_also(self, tmp_workspace: WorkspaceFixture) -> None:
        """If ## See also already exists, appends bullet without adding a second heading."""
        _create_concept(tmp_workspace, "page-a", "Page A",
                        f"# Page A\n\n{_LONG}\n\n## See also\n\n- [Existing](existing.md)\n")
        _create_concept(tmp_workspace, "page-b", "Page B", f"# Page B\n\n{_LONG}")

        issue = LintIssue(
            check="missing_xref", severity="info",
            page="/wiki/concepts/page-a.md",
            description="A and B share sources",
            suggestion="",
            related_page="/wiki/concepts/page-b.md",
        )
        result = repair_missing_xref(issue, tmp_workspace.db_path, tmp_workspace.workspace)
        assert result.success
        content = (tmp_workspace.workspace / "wiki/concepts/page-a.md").read_text()
        assert content.count("## See also") == 1
        assert "page-b.md" in content

    def test_end_to_end_lint_then_repair_clears_issue(self, tmp_workspace: WorkspaceFixture) -> None:
        """Full lint → repair → lint cycle: the pair stops being flagged."""
        src_id = _insert_source(tmp_workspace.db_path, "shared.pdf", f"Shared content {_LONG}")

        a = _create_concept(tmp_workspace, "alpha", "Alpha", f"# Alpha\n\n{_LONG}")
        b = _create_concept(tmp_workspace, "beta", "Beta", f"# Beta\n\n{_LONG}")

        # Directly insert cites edges so missing_xref_check fires
        with get_connection(tmp_workspace.db_path) as conn:
            with conn:
                conn.execute(
                    "INSERT INTO document_references "
                    "(source_document_id, target_document_id, reference_type) "
                    "VALUES (?,?,'cites')",
                    (a["id"], src_id),
                )
                conn.execute(
                    "INSERT INTO document_references "
                    "(source_document_id, target_document_id, reference_type) "
                    "VALUES (?,?,'cites')",
                    (b["id"], src_id),
                )

        # First lint — must find a missing_xref
        report1 = lint_wiki(tmp_workspace.db_path, tmp_workspace.workspace)
        xref_issues = [i for i in report1.issues if i.check == "missing_xref"]
        assert len(xref_issues) > 0

        repair_wiki(report1, tmp_workspace.db_path, tmp_workspace.workspace)

        # Second lint — xref should now be gone
        report2 = lint_wiki(tmp_workspace.db_path, tmp_workspace.workspace)
        xref_after = [i for i in report2.issues if i.check == "missing_xref"]
        assert len(xref_after) == 0


# ══════════════════════════════════════════════════════════════════════════════
# INCREMENT 2 — repair_contradiction
# ══════════════════════════════════════════════════════════════════════════════

class TestRepairContradiction:
    def test_appends_warning_callout(self, tmp_workspace: WorkspaceFixture) -> None:
        """Repair appends a ⚠️ contradiction callout to page A."""
        _create_concept(tmp_workspace, "page-a", "Page A", f"# Page A\n\n{_LONG}")
        _create_concept(tmp_workspace, "page-b", "Page B", f"# Page B\n\n{_LONG}")

        issue = LintIssue(
            check="contradiction", severity="error",
            page="/wiki/concepts/page-a.md",
            description="Conflicting claims about interest rates",
            suggestion="Review both pages",
            related_page="/wiki/concepts/page-b.md",
        )
        result = repair_contradiction(issue, tmp_workspace.db_path, tmp_workspace.workspace)

        assert result.success
        assert result.action == "contradiction_flagged"
        content = (tmp_workspace.workspace / "wiki/concepts/page-a.md").read_text()
        assert "<!-- CONTRADICTION:" in content
        assert "⚠️" in content

    def test_idempotent_skips_on_second_call(self, tmp_workspace: WorkspaceFixture) -> None:
        """Second repair returns skipped — no duplicate marker."""
        _create_concept(tmp_workspace, "page-a", "Page A", f"# Page A\n\n{_LONG}")
        _create_concept(tmp_workspace, "page-b", "Page B", f"# Page B\n\n{_LONG}")

        issue = LintIssue(
            check="contradiction", severity="error",
            page="/wiki/concepts/page-a.md",
            description="Conflicting claims",
            suggestion="",
            related_page="/wiki/concepts/page-b.md",
        )
        repair_contradiction(issue, tmp_workspace.db_path, tmp_workspace.workspace)
        result2 = repair_contradiction(issue, tmp_workspace.db_path, tmp_workspace.workspace)

        assert result2.action == "skipped"
        content = (tmp_workspace.workspace / "wiki/concepts/page-a.md").read_text()
        assert content.count("<!-- CONTRADICTION:") == 1

    def test_failed_if_page_not_found(self, tmp_workspace: WorkspaceFixture) -> None:
        """Returns failed when page A does not exist."""
        issue = LintIssue(
            check="contradiction", severity="error",
            page="/wiki/concepts/ghost.md",
            description="Conflict",
            suggestion="",
            related_page="/wiki/concepts/page-b.md",
        )
        result = repair_contradiction(issue, tmp_workspace.db_path, tmp_workspace.workspace)
        assert not result.success
        assert result.action == "failed"


# ══════════════════════════════════════════════════════════════════════════════
# INCREMENT 3 — repair_data_gap
# ══════════════════════════════════════════════════════════════════════════════

class TestRepairDataGap:
    def test_inserts_todo_note(self, tmp_workspace: WorkspaceFixture) -> None:
        """Repair inserts a DATA_GAP TODO note into the host page."""
        host = _create_concept(tmp_workspace, "interest-rates", "Interest Rates",
                               f"# Interest Rates\n\nInflation and rates matter. {_LONG}")
        _insert_chunk(tmp_workspace.db_path, host["id"],
                      "interest rates inflation monetary policy")

        issue = LintIssue(
            check="data_gap", severity="info",
            page="/wiki/concepts/interest-rates.md",
            description="Missing or underdeveloped topic: Inflation",
            suggestion="Consider adding an inflation concept page",
            topic="inflation",
        )
        result = repair_data_gap(issue, tmp_workspace.db_path, tmp_workspace.workspace)

        assert result.success
        assert result.action == "gap_noted"
        content = (tmp_workspace.workspace / "wiki/concepts/interest-rates.md").read_text()
        assert "<!-- DATA_GAP: inflation -->" in content
        assert "🚧" in content

    def test_idempotent_skips_if_note_already_present(self, tmp_workspace: WorkspaceFixture) -> None:
        """Second repair call skips when marker already in content."""
        existing_content = (
            f"# Interest Rates\n\n{_LONG}\n\n"
            "<!-- DATA_GAP: inflation -->\n"
            "> 🚧 **Missing topic: Inflation.** Consider adding it.\n"
        )
        _create_concept(tmp_workspace, "interest-rates", "Interest Rates", existing_content)

        issue = LintIssue(
            check="data_gap", severity="info",
            page="/wiki/concepts/interest-rates.md",
            description="Missing topic: Inflation",
            suggestion="Consider adding it",
            topic="inflation",
        )
        result = repair_data_gap(issue, tmp_workspace.db_path, tmp_workspace.workspace)
        assert result.action == "skipped"

    def test_skips_if_topic_already_covered_by_source(self, tmp_workspace: WorkspaceFixture) -> None:
        """Skips when a source already covers the topic."""
        src_id = _insert_source(tmp_workspace.db_path, "inflation.pdf",
                                f"Inflation is a rise in prices. {_LONG}")
        _insert_chunk(tmp_workspace.db_path, src_id, "inflation monetary policy prices")

        _create_concept(tmp_workspace, "interest-rates", "Interest Rates",
                        f"# Interest Rates\n\n{_LONG}")

        issue = LintIssue(
            check="data_gap", severity="info",
            page="/wiki/concepts/interest-rates.md",
            description="Missing topic: Inflation",
            suggestion="",
            topic="inflation",
        )
        result = repair_data_gap(issue, tmp_workspace.db_path, tmp_workspace.workspace)
        assert result.action == "skipped"

    def test_skips_if_topic_empty(self, tmp_workspace: WorkspaceFixture) -> None:
        """Skips gracefully when topic is empty string."""
        issue = LintIssue(
            check="data_gap", severity="info",
            page="/wiki/concepts/interest-rates.md",
            description="Missing topic",
            suggestion="",
        )
        result = repair_data_gap(issue, tmp_workspace.db_path, tmp_workspace.workspace)
        assert result.action == "skipped"

    def test_failed_if_host_page_not_found(self, tmp_workspace: WorkspaceFixture) -> None:
        """Returns failed when the host page does not exist on disk."""
        issue = LintIssue(
            check="data_gap", severity="info",
            page="/wiki/concepts/ghost.md",
            description="Missing topic: Inflation",
            suggestion="",
            topic="inflation",
        )
        result = repair_data_gap(issue, tmp_workspace.db_path, tmp_workspace.workspace)
        assert not result.success
        assert result.action == "failed"


# ══════════════════════════════════════════════════════════════════════════════
# INCREMENT 4 — gap_filled_check + repair_gap_filled
# ══════════════════════════════════════════════════════════════════════════════

class TestGapFilled:
    def test_gap_filled_check_detects_covered_topic(self, tmp_workspace: WorkspaceFixture) -> None:
        """gap_filled_check returns an issue when a DATA_GAP marker's topic is covered."""
        from domain.lint.checks import gap_filled_check

        existing_content = (
            f"# Interest Rates\n\n{_LONG}\n\n"
            "<!-- DATA_GAP: inflation -->\n"
            "> 🚧 **Missing topic: Inflation.** Consider adding it. Drop a source about this into `sources/` and re-ingest to fill it in.\n"
        )
        _create_concept(tmp_workspace, "interest-rates", "Interest Rates", existing_content)

        src_id = _insert_source(tmp_workspace.db_path, "inflation.pdf",
                                f"Inflation is a rise in prices. {_LONG}")
        _insert_chunk(tmp_workspace.db_path, src_id, "inflation monetary policy prices rise")

        issues = gap_filled_check(tmp_workspace.db_path)
        assert any(i.check == "gap_filled" and i.topic == "inflation" for i in issues)

    def test_gap_filled_check_no_issue_without_source(self, tmp_workspace: WorkspaceFixture) -> None:
        """gap_filled_check finds no issue if topic has no source coverage."""
        from domain.lint.checks import gap_filled_check

        existing_content = (
            f"# Interest Rates\n\n{_LONG}\n\n"
            "<!-- DATA_GAP: inflation -->\n"
            "> 🚧 **Missing topic: Inflation.** Consider adding it. Drop a source about this into `sources/` and re-ingest to fill it in.\n"
        )
        _create_concept(tmp_workspace, "interest-rates", "Interest Rates", existing_content)

        issues = gap_filled_check(tmp_workspace.db_path)
        assert not any(i.check == "gap_filled" for i in issues)

    def test_repair_gap_filled_replaces_note_with_link(self, tmp_workspace: WorkspaceFixture) -> None:
        """Repair replaces the DATA_GAP block with a See link to the inflation wiki page."""
        existing_content = (
            f"# Interest Rates\n\n{_LONG}\n\n"
            "<!-- DATA_GAP: inflation -->\n"
            "> 🚧 **Missing topic: Inflation.** Consider adding it. Drop a source about this into `sources/` and re-ingest to fill it in.\n"
        )
        _create_concept(tmp_workspace, "interest-rates", "Interest Rates", existing_content)

        src_id = _insert_source(tmp_workspace.db_path, "inflation.pdf",
                                f"Inflation is a rise in prices. {_LONG}")
        _insert_chunk(tmp_workspace.db_path, src_id, "inflation monetary policy prices rise")

        infl = _create_concept(tmp_workspace, "inflation", "Inflation",
                               f"# Inflation\n\nInflation is the rise in prices. {_LONG}")
        _insert_chunk(tmp_workspace.db_path, infl["id"],
                      "inflation monetary policy prices rise")

        issue = LintIssue(
            check="gap_filled", severity="info",
            page="/wiki/concepts/interest-rates.md",
            description="TODO topic 'inflation' is now covered by a source",
            suggestion="Replace the TODO note with a link to the new page",
            topic="inflation",
        )
        result = repair_gap_filled(issue, tmp_workspace.db_path, tmp_workspace.workspace)

        assert result.success
        assert result.action == "gap_resolved"
        content = (tmp_workspace.workspace / "wiki/concepts/interest-rates.md").read_text()
        assert "<!-- DATA_GAP: inflation -->" not in content
        assert "ℹ️" in content
        assert "inflation" in content.lower()

    def test_repair_gap_filled_creates_links_to_edge(self, tmp_workspace: WorkspaceFixture) -> None:
        """Repair creates a links_to edge after replacing the note."""
        existing_content = (
            f"# Interest Rates\n\n{_LONG}\n\n"
            "<!-- DATA_GAP: inflation -->\n"
            "> 🚧 **Missing topic: Inflation.** Consider adding it. Drop a source about this into `sources/` and re-ingest to fill it in.\n"
        )
        _create_concept(tmp_workspace, "interest-rates", "Interest Rates", existing_content)

        src_id = _insert_source(tmp_workspace.db_path, "inflation.pdf",
                                f"Inflation is a rise in prices. {_LONG}")
        _insert_chunk(tmp_workspace.db_path, src_id, "inflation monetary policy")

        infl = _create_concept(tmp_workspace, "inflation", "Inflation",
                               f"# Inflation\n\nInflation is the rise. {_LONG}")
        _insert_chunk(tmp_workspace.db_path, infl["id"], "inflation monetary policy")

        issue = LintIssue(
            check="gap_filled", severity="info",
            page="/wiki/concepts/interest-rates.md",
            description="Covered",
            suggestion="",
            topic="inflation",
        )
        repair_gap_filled(issue, tmp_workspace.db_path, tmp_workspace.workspace)

        rates_id = _get_doc_id(tmp_workspace.db_path, "/wiki/concepts/interest-rates.md")
        refs = get_forward_refs(tmp_workspace.db_path, rates_id)
        assert any(r["reference_type"] == "links_to" for r in refs)

    def test_repair_gap_filled_skips_if_no_wiki_page(self, tmp_workspace: WorkspaceFixture) -> None:
        """Skips when source covers the topic but no wiki page exists yet."""
        existing_content = (
            f"# Interest Rates\n\n{_LONG}\n\n"
            "<!-- DATA_GAP: inflation -->\n"
            "> 🚧 **Missing topic: Inflation.** Consider adding it. Drop a source about this into `sources/` and re-ingest to fill it in.\n"
        )
        _create_concept(tmp_workspace, "interest-rates", "Interest Rates", existing_content)

        src_id = _insert_source(tmp_workspace.db_path, "inflation.pdf",
                                f"Inflation {_LONG}")
        _insert_chunk(tmp_workspace.db_path, src_id, "inflation monetary policy")

        issue = LintIssue(
            check="gap_filled", severity="info",
            page="/wiki/concepts/interest-rates.md",
            description="Covered",
            suggestion="",
            topic="inflation",
        )
        result = repair_gap_filled(issue, tmp_workspace.db_path, tmp_workspace.workspace)
        assert result.action == "skipped"

    def test_full_lint_repair_cycle_gap_filled(self, tmp_workspace: WorkspaceFixture) -> None:
        """Full cycle: lint detects gap_filled issue, repair resolves it."""
        from domain.lint.runner import lint_wiki

        existing_content = (
            f"# Interest Rates\n\n{_LONG}\n\n"
            "<!-- DATA_GAP: inflation -->\n"
            "> 🚧 **Missing topic: Inflation.** Consider adding it. Drop a source about this into `sources/` and re-ingest to fill it in.\n"
        )
        _create_concept(tmp_workspace, "interest-rates", "Interest Rates", existing_content)

        src_id = _insert_source(tmp_workspace.db_path, "inflation.pdf",
                                f"Inflation {_LONG}")
        _insert_chunk(tmp_workspace.db_path, src_id, "inflation monetary policy prices")

        infl = _create_concept(tmp_workspace, "inflation", "Inflation",
                               f"# Inflation\n\nInflation is the rise. {_LONG}")
        _insert_chunk(tmp_workspace.db_path, infl["id"], "inflation monetary policy prices")

        report1 = lint_wiki(tmp_workspace.db_path, tmp_workspace.workspace)
        gap_filled_issues = [i for i in report1.issues if i.check == "gap_filled"]
        assert len(gap_filled_issues) > 0

        # Repair only gap_filled issues to avoid orphan repair deleting our test pages
        filtered = LintReport(issues=gap_filled_issues)
        repair_wiki(filtered, tmp_workspace.db_path, tmp_workspace.workspace)

        content = (tmp_workspace.workspace / "wiki/concepts/interest-rates.md").read_text()
        assert "<!-- DATA_GAP: inflation -->" not in content
