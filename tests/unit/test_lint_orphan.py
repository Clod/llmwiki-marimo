"""Tests for orphan_check — step 3.1."""

from domain.tools.references import update_references
from domain.tools.wiki_fs import create_page
from domain.lint.checks import orphan_check
from tests.helpers.workspace import WorkspaceFixture

_CONTENT = (
    "# Concept\n\n"
    "This concept covers important financial theory related to market efficiency, "
    "portfolio diversification, and risk-adjusted returns in emerging economies.\n"
)


def test_orphan_check_flags_unreferenced_concept(tmp_workspace: WorkspaceFixture) -> None:
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/concepts/", "orphan-concept", "Orphan Concept", _CONTENT, [])
    issues = orphan_check(tmp_workspace.db_path)
    assert any(i.check == "orphan" and "orphan-concept" in i.page for i in issues)


def test_orphan_check_skips_referenced_concept(tmp_workspace: WorkspaceFixture) -> None:
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/concepts/", "linked-concept", "Linked Concept", _CONTENT, [])
    # Summary page links to the concept
    summary_content = _CONTENT + "\nSee [Linked Concept](/wiki/concepts/linked-concept.md).\n"
    summary = create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                          "/wiki/summaries/", "my-doc", "My Doc", summary_content, [])
    update_references(tmp_workspace.db_path, summary["id"], summary_content, "/wiki/summaries/")

    issues = orphan_check(tmp_workspace.db_path)
    assert not any("linked-concept" in i.page for i in issues)


def test_orphan_check_only_flags_concepts_not_summaries(tmp_workspace: WorkspaceFixture) -> None:
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/concepts/", "orphan-concept", "Orphan Concept", _CONTENT, [])
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/summaries/", "orphan-summary", "Orphan Summary", _CONTENT, [])
    issues = orphan_check(tmp_workspace.db_path)
    # Concept flagged, summary not flagged
    assert any("concepts/orphan-concept" in i.page for i in issues)
    assert not any("summaries/orphan-summary" in i.page for i in issues)


def test_orphan_check_three_pages_two_orphans(tmp_workspace: WorkspaceFixture) -> None:
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/concepts/", "concept-a", "Concept A", _CONTENT, [])
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/concepts/", "concept-b", "Concept B", _CONTENT, [])
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/concepts/", "concept-c", "Concept C", _CONTENT, [])
    # Link only to concept-a
    linker_content = _CONTENT + "\nSee [Concept A](/wiki/concepts/concept-a.md).\n"
    linker = create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                         "/wiki/summaries/", "linker", "Linker", linker_content, [])
    update_references(tmp_workspace.db_path, linker["id"], linker_content, "/wiki/summaries/")

    issues = orphan_check(tmp_workspace.db_path)
    orphan_pages = {i.page for i in issues if i.check == "orphan"}
    assert not any("concept-a" in p for p in orphan_pages)
    assert any("concept-b" in p for p in orphan_pages)
    assert any("concept-c" in p for p in orphan_pages)
