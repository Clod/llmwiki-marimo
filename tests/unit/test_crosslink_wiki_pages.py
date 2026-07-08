"""Tests for crosslink_wiki_pages — the final ingestion pass that injects a
localized "See also" section into concept/summary pages.

Regression: pipeline-generated concept pages previously never linked to one
another (inject_see_also was only wired into the chat "Save to wiki" path).
"""

from domain.ingestion.pipeline import crosslink_wiki_pages
from domain.i18n import get_locale
from domain.tools.wiki_fs import create_page, read_page
from tests.helpers.workspace import WorkspaceFixture


def _concept(db_path: str, workspace, slug: str, title: str, body: str) -> None:
    content = f"# {title}\n\n## Definition\n{body}\n\n## Sources\n- src.pdf\n"
    create_page(db_path, workspace, "/wiki/concepts/", slug, title, content, ["entity"])


def test_crosslink_adds_see_also_between_concepts(tmp_workspace: WorkspaceFixture) -> None:
    # Cinderella's prose mentions the Glass Slipper concept by name, but has no link.
    _concept(tmp_workspace.db_path, tmp_workspace.workspace, "cinderella", "Cinderella",
             "Cinderella loses her glass slipper at the ball.")
    _concept(tmp_workspace.db_path, tmp_workspace.workspace, "glass-slipper", "Glass Slipper",
             "A slipper made of glass, central to the tale.")

    changed = crosslink_wiki_pages(tmp_workspace.workspace, tmp_workspace.db_path, language="en")

    assert changed >= 1
    page = read_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/concepts/", "cinderella")
    assert "## See also" in page
    assert "[Glass Slipper](glass-slipper.md)" in page


def test_crosslink_is_localized_for_spanish(tmp_workspace: WorkspaceFixture) -> None:
    _concept(tmp_workspace.db_path, tmp_workspace.workspace, "cenicienta", "Cenicienta",
             "Cenicienta pierde su zapatilla de cristal en el baile.")
    _concept(tmp_workspace.db_path, tmp_workspace.workspace, "zapatilla-de-cristal",
             "Zapatilla de Cristal", "Una zapatilla de cristal, central en el cuento.")

    changed = crosslink_wiki_pages(tmp_workspace.workspace, tmp_workspace.db_path, language="es")

    assert changed >= 1
    page = read_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/concepts/", "cenicienta")
    assert f"## {get_locale('es').h_see_also}" in page
    assert "[Zapatilla de Cristal](zapatilla-de-cristal.md)" in page
    # English header must NOT appear on a Spanish page.
    assert "## See also" not in page


def test_crosslink_is_idempotent(tmp_workspace: WorkspaceFixture) -> None:
    _concept(tmp_workspace.db_path, tmp_workspace.workspace, "cinderella", "Cinderella",
             "Cinderella loses her glass slipper at the ball.")
    _concept(tmp_workspace.db_path, tmp_workspace.workspace, "glass-slipper", "Glass Slipper",
             "A slipper made of glass.")

    crosslink_wiki_pages(tmp_workspace.workspace, tmp_workspace.db_path, language="en")
    second = crosslink_wiki_pages(tmp_workspace.workspace, tmp_workspace.db_path, language="en")

    assert second == 0  # nothing new to link on a second run
    page = read_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/concepts/", "cinderella")
    assert page.count("## See also") == 1  # no duplicate section


def test_crosslink_no_mentions_leaves_page_untouched(tmp_workspace: WorkspaceFixture) -> None:
    _concept(tmp_workspace.db_path, tmp_workspace.workspace, "alpha", "Alpha",
             "This page talks about nothing related.")
    _concept(tmp_workspace.db_path, tmp_workspace.workspace, "beta", "Beta",
             "An unrelated topic entirely.")

    changed = crosslink_wiki_pages(tmp_workspace.workspace, tmp_workspace.db_path, language="en")

    assert changed == 0
    page = read_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/concepts/", "alpha")
    assert "## See also" not in page
