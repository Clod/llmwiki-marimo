"""Tests for crosslink_wiki_pages — the final ingestion pass that injects a
localized "See also" section into concept/summary pages.

Regression: pipeline-generated concept pages previously never linked to one
another (inject_see_also was only wired into the chat "Save to wiki" path).
"""

from domain.ingestion.pipeline import crosslink_wiki_pages, pages_touched_by
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


def test_crosslink_touched_rewrites_the_touched_page_and_its_mentioners(
    tmp_workspace: WorkspaceFixture,
) -> None:
    """Scoped to one ingest: the page it wrote, plus the pages whose text
    mentions it, get their links; unrelated pages are not even read again.
    The full pass compared every page with every other after any scan that
    ingested one file (7.2 s at 601 pages)."""
    ws, db = tmp_workspace.workspace, tmp_workspace.db_path
    _concept(db, ws, "cinderella", "Cinderella", "Cinderella loses her glass slipper.")
    _concept(db, ws, "royal-ball", "Royal Ball", "The ball where the glass slipper is lost.")
    _concept(db, ws, "glass-slipper", "Glass Slipper", "A slipper made of glass.")
    _concept(db, ws, "alpha", "Alpha", "This page mentions the royal ball only.")

    changed = crosslink_wiki_pages(ws, db, language="en", touched={"wiki/concepts/glass-slipper.md"})

    # cinderella and royal-ball mention the touched page -> linked; alpha does not.
    assert changed == 2
    assert "[Glass Slipper](glass-slipper.md)" in read_page(db, ws, "/wiki/concepts/", "cinderella")
    assert "[Glass Slipper](glass-slipper.md)" in read_page(db, ws, "/wiki/concepts/", "royal-ball")
    assert "## See also" not in read_page(db, ws, "/wiki/concepts/", "alpha")

    # A later full sweep still finds alpha -> royal-ball: nothing was lost, only deferred.
    assert crosslink_wiki_pages(ws, db, language="en") == 1
    assert "[Royal Ball](royal-ball.md)" in read_page(db, ws, "/wiki/concepts/", "alpha")


def test_crosslink_touched_accepts_the_app_path_shape_and_empty_set(
    tmp_workspace: WorkspaceFixture,
) -> None:
    ws, db = tmp_workspace.workspace, tmp_workspace.db_path
    _concept(db, ws, "cinderella", "Cinderella", "Cinderella loses her glass slipper.")
    _concept(db, ws, "glass-slipper", "Glass Slipper", "A slipper made of glass.")

    assert crosslink_wiki_pages(ws, db, language="en", touched=set()) == 0
    # The ingest app passes "/wiki/concepts/x.md" (path || filename); tolerated.
    assert crosslink_wiki_pages(ws, db, language="en", touched={"/wiki/concepts/glass-slipper.md"}) == 1


def test_pages_touched_by_returns_summary_and_citing_pages(tmp_workspace: WorkspaceFixture) -> None:
    import uuid
    from domain.tools.db import get_connection
    from domain.tools.references import update_references

    ws, db = tmp_workspace.workspace, tmp_workspace.db_path
    src_id = str(uuid.uuid4())
    with get_connection(db) as conn:
        user_id = conn.execute("SELECT user_id FROM workspace LIMIT 1").fetchone()["user_id"]
        with conn:
            conn.execute(
                "INSERT INTO documents (id, user_id, filename, title, path, relative_path, "
                "source_kind, file_type, status) VALUES (?,?,?,?,'sources/',?,'source','pdf','ready')",
                (src_id, user_id, "src.pdf", "Src", "sources/src.pdf"),
            )
    summary = create_page(db, ws, "/wiki/summaries/", "src", "Src", "# Src\n", [],
                          source_document_id=src_id)
    concept = create_page(db, ws, "/wiki/concepts/", "cinderella", "Cinderella",
                          "# Cinderella\n\n## Sources\n- src.pdf\n", [])
    update_references(db, concept["id"], "# Cinderella\n\n## Sources\n- src.pdf\n", "/wiki/concepts/")
    _concept(db, ws, "unrelated", "Unrelated", "Nothing here.")

    assert pages_touched_by(db, [src_id]) == {summary["path"], concept["path"]}
    assert pages_touched_by(db, []) == set()


def test_crosslink_matches_an_accented_mention(tmp_workspace: WorkspaceFixture) -> None:
    """The prose writes the accent, the slug never carries one.

    `slugify` strips combining marks, so a page titled "Panel Líder" is filed as
    `panel-lider.md` and matched by the text "panel lider". A page whose prose
    spells "Panel Líder" therefore went unlinked until the haystack was stripped
    the same way. Measured on the bundled finanzas-argentinas wiki before the
    fix: thirty page pairs in that shape.
    """
    _concept(tmp_workspace.db_path, tmp_workspace.workspace, "acciones-locales",
             "Acciones locales",
             "Las acciones cotizan en el Panel Líder y en el panel general.")
    _concept(tmp_workspace.db_path, tmp_workspace.workspace, "panel-lider", "Panel Líder",
             "Las empresas de mayor capitalización y liquidez.")

    changed = crosslink_wiki_pages(tmp_workspace.workspace, tmp_workspace.db_path, language="es")

    assert changed >= 1
    page = read_page(
        tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/concepts/", "acciones-locales"
    )
    assert "[Panel Líder](panel-lider.md)" in page


def test_crosslink_touched_selects_a_page_that_mentions_it_with_accents(
    tmp_workspace: WorkspaceFixture,
) -> None:
    """The scoped pass must normalise exactly as the injection does.

    `_crosslink_candidates` is a superset of the pages that end up linked only
    while both halves compare the same string; a page left out of the candidate
    set is never offered a link at all.
    """
    _concept(tmp_workspace.db_path, tmp_workspace.workspace, "acciones-locales",
             "Acciones locales",
             "Las acciones cotizan en el Panel Líder.")
    _concept(tmp_workspace.db_path, tmp_workspace.workspace, "panel-lider", "Panel Líder",
             "Las empresas de mayor capitalización y liquidez.")

    changed = crosslink_wiki_pages(
        tmp_workspace.workspace, tmp_workspace.db_path, language="es",
        touched={"wiki/concepts/panel-lider.md"},
    )

    assert changed >= 1
    page = read_page(
        tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/concepts/", "acciones-locales"
    )
    assert "[Panel Líder](panel-lider.md)" in page
