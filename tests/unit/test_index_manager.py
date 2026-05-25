"""Tests for index_manager.update_index — step 2.3."""

from pathlib import Path


from domain.ingestion.index_manager import update_index
from tests.helpers.workspace import WorkspaceFixture

_INITIAL = "# Wiki Index\n\n## Summaries\n\n## Concepts\n"


def test_add_summary_entry(tmp_workspace: WorkspaceFixture) -> None:
    update_index(tmp_workspace.workspace, "summaries/my-doc.md", "About finance", "summaries")
    text = (tmp_workspace.workspace / "wiki" / "index.md").read_text()
    assert "[My Doc](summaries/my-doc.md)" in text
    assert "About finance" in text


def test_add_concept_entry(tmp_workspace: WorkspaceFixture) -> None:
    update_index(tmp_workspace.workspace, "concepts/federal-reserve.md", "US central bank", "concepts")
    text = (tmp_workspace.workspace / "wiki" / "index.md").read_text()
    assert "[Federal Reserve](concepts/federal-reserve.md)" in text
    assert "## Concepts" in text


def test_summary_goes_under_summaries_section(tmp_workspace: WorkspaceFixture) -> None:
    update_index(tmp_workspace.workspace, "summaries/doc.md", "A summary", "summaries")
    text = (tmp_workspace.workspace / "wiki" / "index.md").read_text()
    summaries_pos = text.index("## Summaries")
    concepts_pos = text.index("## Concepts")
    entry_pos = text.index("[Doc]")
    assert summaries_pos < entry_pos < concepts_pos


def test_concept_goes_under_concepts_section(tmp_workspace: WorkspaceFixture) -> None:
    update_index(tmp_workspace.workspace, "concepts/inflation.md", "Price rises", "concepts")
    text = (tmp_workspace.workspace / "wiki" / "index.md").read_text()
    concepts_pos = text.index("## Concepts")
    entry_pos = text.index("[Inflation]")
    assert entry_pos > concepts_pos


def test_update_existing_entry_no_duplicate(tmp_workspace: WorkspaceFixture) -> None:
    update_index(tmp_workspace.workspace, "summaries/doc.md", "First summary", "summaries")
    update_index(tmp_workspace.workspace, "summaries/doc.md", "Updated summary", "summaries")
    text = (tmp_workspace.workspace / "wiki" / "index.md").read_text()
    assert text.count("[Doc](summaries/doc.md)") == 1
    assert "Updated summary" in text
    assert "First summary" not in text


def test_multiple_entries_all_present(tmp_workspace: WorkspaceFixture) -> None:
    update_index(tmp_workspace.workspace, "summaries/doc-a.md", "Doc A", "summaries")
    update_index(tmp_workspace.workspace, "summaries/doc-b.md", "Doc B", "summaries")
    update_index(tmp_workspace.workspace, "concepts/inflation.md", "Inflation", "concepts")
    text = (tmp_workspace.workspace / "wiki" / "index.md").read_text()
    assert "[Doc A]" in text
    assert "[Doc B]" in text
    assert "[Inflation]" in text


def test_creates_index_if_missing(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "wiki").mkdir(parents=True)
    update_index(workspace, "summaries/doc.md", "A doc", "summaries")
    assert (workspace / "wiki" / "index.md").exists()
