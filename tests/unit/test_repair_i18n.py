"""Localization of the repair actions (phase: repair i18n).

The repair pass runs after every ingest, every batch, and every chat-save. In a
non-English wiki it must regenerate content and structural headers in the wiki
language — otherwise an es wiki accumulates English headers (and, for See-also,
a duplicate section because the English check never matches the es header).

Backward-compat guard: every assertion has an English-default counterpart that
must stay byte-identical to the pre-feature behavior.
"""

from domain.i18n import get_locale
from domain.lint.report import LintIssue, LintReport
from domain.repair.actions import repair_missing_xref
from domain.repair.runner import repair_wiki
from domain.tools.references import update_references
from domain.tools.wiki_fs import create_page, read_page
from tests.helpers.workspace import WorkspaceFixture

_ES_SEE_ALSO = f"## {get_locale('es').h_see_also}"   # "## Véase también"
_EN_SEE_ALSO = f"## {get_locale('en').h_see_also}"   # "## See also"


def _make_pages(ws: WorkspaceFixture, a_extra: str = "") -> None:
    """Create two linkable concept pages; page A optionally carries a_extra."""
    content_a = f"# Concept A\n\nContent about monetary policy.\n{a_extra}"
    content_b = "# Concept B\n\nContent about fiscal policy.\n"
    a = create_page(ws.db_path, ws.workspace, "/wiki/concepts/", "concept-a", "Concept A", content_a, [])
    b = create_page(ws.db_path, ws.workspace, "/wiki/concepts/", "concept-b", "Concept B", content_b, [])
    update_references(ws.db_path, a["id"], content_a, "/wiki/concepts/")
    update_references(ws.db_path, b["id"], content_b, "/wiki/concepts/")


def _xref_issue() -> LintIssue:
    return LintIssue(
        check="missing_xref", severity="warning",
        page="/wiki/concepts/concept-a.md",
        description="Concept A and Concept B share a source but are not linked",
        suggestion="link them",
        related_page="/wiki/concepts/concept-b.md",
    )


# ── repair_missing_xref: header is localized ──────────────────────────────────

def test_xref_creates_localized_section_for_es(tmp_workspace: WorkspaceFixture) -> None:
    _make_pages(tmp_workspace)
    result = repair_missing_xref(_xref_issue(), tmp_workspace.db_path, tmp_workspace.workspace, language="es")
    assert result.success
    page = read_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/concepts/", "concept-a")
    assert _ES_SEE_ALSO in page
    assert _EN_SEE_ALSO not in page          # no English header leaked into an es page
    assert "concept-b.md" in page


def test_xref_appends_under_existing_es_section_no_duplicate(tmp_workspace: WorkspaceFixture) -> None:
    # Page A already has a localized See-also section → the bullet must be appended
    # under it, NOT trigger a second (English) section.
    _make_pages(tmp_workspace, a_extra=f"\n{_ES_SEE_ALSO}\n\n- [Otro](otro.md)\n")
    result = repair_missing_xref(_xref_issue(), tmp_workspace.db_path, tmp_workspace.workspace, language="es")
    assert result.success
    page = read_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/concepts/", "concept-a")
    assert page.count(_ES_SEE_ALSO) == 1     # still exactly one section
    assert _EN_SEE_ALSO not in page
    assert "concept-b.md" in page


def test_xref_english_default_unchanged(tmp_workspace: WorkspaceFixture) -> None:
    # Regression guard: the default path still emits the English header.
    _make_pages(tmp_workspace)
    result = repair_missing_xref(_xref_issue(), tmp_workspace.db_path, tmp_workspace.workspace)
    assert result.success
    page = read_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/concepts/", "concept-a")
    assert _EN_SEE_ALSO in page
    assert _ES_SEE_ALSO not in page


# ── repair_wiki: language is forwarded only to language-aware handlers ─────────

def test_repair_wiki_forwards_language(tmp_workspace: WorkspaceFixture) -> None:
    _make_pages(tmp_workspace)
    report = LintReport(issues=[_xref_issue()], checked_at="now")
    repair_wiki(report, tmp_workspace.db_path, tmp_workspace.workspace, language="es")
    page = read_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/concepts/", "concept-a")
    assert _ES_SEE_ALSO in page
    assert _EN_SEE_ALSO not in page


def test_repair_wiki_english_default(tmp_workspace: WorkspaceFixture) -> None:
    _make_pages(tmp_workspace)
    report = LintReport(issues=[_xref_issue()], checked_at="now")
    repair_wiki(report, tmp_workspace.db_path, tmp_workspace.workspace)
    page = read_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/concepts/", "concept-a")
    assert _EN_SEE_ALSO in page
    assert _ES_SEE_ALSO not in page
