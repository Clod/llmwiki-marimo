"""Tests for missing_concept_check — step 3.4."""

from domain.tools.wiki_fs import create_page
from domain.lint.checks import missing_concept_check
from tests.helpers.workspace import WorkspaceFixture

_BASE = (
    "This concept covers important financial theory related to market efficiency "
    "and portfolio diversification in global markets.\n"
)


def test_missing_concept_flagged_for_broken_link(tmp_workspace: WorkspaceFixture) -> None:
    content = (
        f"# Existing Concept\n\n{_BASE}\n\n"
        "See also [Quantitative Easing](../concepts/quantitative-easing.md).\n"
    )
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/summaries/", "my-doc", "My Doc", content, [])
    # quantitative-easing.md does NOT exist on disk

    issues = missing_concept_check(tmp_workspace.db_path, tmp_workspace.workspace)
    assert any(i.check == "missing_concept" and "quantitative-easing.md" in i.description
               for i in issues)


def test_missing_concept_not_flagged_when_file_exists(tmp_workspace: WorkspaceFixture) -> None:
    # Create the target concept page first
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/concepts/", "quantitative-easing", "Quantitative Easing", _BASE, [])

    content = (
        f"# My Doc\n\n{_BASE}\n\n"
        "See also [Quantitative Easing](../concepts/quantitative-easing.md).\n"
    )
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/summaries/", "my-doc", "My Doc", content, [])

    issues = missing_concept_check(tmp_workspace.db_path, tmp_workspace.workspace)
    assert not any(i.check == "missing_concept" for i in issues)


def test_missing_concept_detected_in_concept_page(tmp_workspace: WorkspaceFixture) -> None:
    # A concept page linking to another missing concept
    content = (
        f"# Federal Reserve\n\n{_BASE}\n\n"
        "Related: [Missing Topic](concepts/missing-topic.md).\n"
    )
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/concepts/", "federal-reserve", "Federal Reserve", content, [])

    issues = missing_concept_check(tmp_workspace.db_path, tmp_workspace.workspace)
    assert any(i.check == "missing_concept" and "missing-topic.md" in i.description
               for i in issues)


def test_no_issues_when_no_concept_links(tmp_workspace: WorkspaceFixture) -> None:
    content = f"# My Doc\n\n{_BASE}\n\nNo links here.\n"
    create_page(tmp_workspace.db_path, tmp_workspace.workspace,
                "/wiki/summaries/", "my-doc", "My Doc", content, [])

    issues = missing_concept_check(tmp_workspace.db_path, tmp_workspace.workspace)
    assert not any(i.check == "missing_concept" for i in issues)
