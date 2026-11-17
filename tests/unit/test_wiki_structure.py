"""The structure check of a generated wiki: the defects the code produces.

A regeneration of the examples left the «See also» section of every summary page after
the footnote `[^1]: source.docx`; nothing checked a generated wiki for the defects of
form the code is responsible for. `check_structure` lists them; the text the model
writes is not checked."""

import uuid

from domain.lint.structure import check_structure
from domain.tools.db import get_connection
from domain.tools.wiki_fs import create_page
from tests.helpers.workspace import WorkspaceFixture

CONCEPT = "# Alpha\n\n## Definition\nAlpha is a thing.\n\n## See also\n\n- [Beta](beta.md)\n\n## Sources\n- a.pdf\n"
BETA = "# Beta\n\n## Definition\nBeta is a thing.\n\n## Sources\n- a.pdf\n"
SUMMARY = ("# A\n\n## Summary\nThe document.\n\n## Related concepts\n\n- [Alpha](../concepts/alpha.md)\n\n"
           "## See also\n\n- [Beta](../concepts/beta.md)\n\n[^1]: a.pdf\n")


def _wiki(ws: WorkspaceFixture, *, alpha=CONCEPT, summary=SUMMARY, description="A thing.", index=None):
    create_page(ws.db_path, ws.workspace, "/wiki/concepts/", "alpha", "Alpha", alpha, ["entity"],
                overwrite=True, sources=["a.pdf"], topics=["t"], description=description)
    create_page(ws.db_path, ws.workspace, "/wiki/concepts/", "beta", "Beta", BETA, ["entity"],
                overwrite=True, sources=["a.pdf"], topics=["t"], description="Another thing.")
    create_page(ws.db_path, ws.workspace, "/wiki/summaries/", "a", "A", summary, [],
                overwrite=True, sources=["a.pdf"], topics=["t"])
    (ws.workspace / "wiki" / "index.md").write_text(index if index is not None else (
        "# Index\n\n- [Alpha](concepts/alpha.md)\n- [Beta](concepts/beta.md)\n- [A](summaries/a.md)\n"),
        encoding="utf-8")


def _rules(ws, **kwargs):
    return sorted({p.rule for p in check_structure(ws.workspace, ws.db_path, "en", **kwargs)})


def test_a_correct_wiki_has_no_problem(tmp_workspace: WorkspaceFixture):
    _wiki(tmp_workspace)
    assert check_structure(tmp_workspace.workspace, tmp_workspace.db_path, "en") == []


def test_a_concept_page_without_description(tmp_workspace: WorkspaceFixture):
    _wiki(tmp_workspace, description=None)
    problems = check_structure(tmp_workspace.workspace, tmp_workspace.db_path, "en")
    assert [(p.page, p.rule) for p in problems] == [("concepts/alpha.md", "frontmatter")]
    assert "description" in problems[0].message


def test_a_broken_link(tmp_workspace: WorkspaceFixture):
    _wiki(tmp_workspace, alpha=CONCEPT.replace("beta.md", "gamma.md"))
    assert _rules(tmp_workspace) == ["link"]


def test_see_also_after_the_footnote(tmp_workspace: WorkspaceFixture):
    after = ("# A\n\n## Summary\nThe document.\n\n[^1]: a.pdf\n\n## See also\n\n- [Beta](../concepts/beta.md)\n")
    _wiki(tmp_workspace, summary=after)
    assert _rules(tmp_workspace) == ["sections"]


def test_see_also_after_the_sources(tmp_workspace: WorkspaceFixture):
    after = "# Alpha\n\n## Definition\nAlpha.\n\n## Sources\n- a.pdf\n\n## See also\n\n- [Beta](beta.md)\n"
    _wiki(tmp_workspace, alpha=after)
    assert _rules(tmp_workspace) == ["sections"]


def test_a_repeated_heading(tmp_workspace: WorkspaceFixture):
    _wiki(tmp_workspace, alpha=CONCEPT + "\n## Sources\n- b.pdf\n")
    assert _rules(tmp_workspace) == ["sections"]


def test_a_page_missing_from_the_index(tmp_workspace: WorkspaceFixture):
    _wiki(tmp_workspace, index="# Index\n\n- [Alpha](concepts/alpha.md)\n- [A](summaries/a.md)\n")
    problems = check_structure(tmp_workspace.workspace, tmp_workspace.db_path, "en")
    assert [(p.page, p.rule) for p in problems] == [("concepts/beta.md", "index")]


def test_a_source_whose_text_ends_in_a_heading(tmp_workspace: WorkspaceFixture):
    _wiki(tmp_workspace)
    with get_connection(tmp_workspace.db_path) as conn:
        user_id = conn.execute("SELECT user_id FROM workspace LIMIT 1").fetchone()["user_id"]
        with conn:
            conn.execute(
                "INSERT INTO documents (id, user_id, filename, title, path, relative_path, source_kind, "
                "file_type, status, content) VALUES (?,?,?,?,'sources/','sources/a.pdf','source','pdf','ready',?)",
                (str(uuid.uuid4()), user_id, "a.pdf", "a.pdf", "Text.\n\n## 📊 Summary table\n"))
    problems = check_structure(tmp_workspace.workspace, tmp_workspace.db_path, "en")
    assert [(p.page, p.rule) for p in problems] == [("sources/a.pdf", "extraction")]


def test_links_with_a_scheme_or_an_anchor_are_not_files(tmp_workspace: WorkspaceFixture):
    _wiki(tmp_workspace, alpha=CONCEPT.replace("Alpha is a thing.",
                                               "See [web](https://example.com), [top](#top) and `[x](y.md)`."))
    assert check_structure(tmp_workspace.workspace, tmp_workspace.db_path, "en") == []


# ── where «See also» goes in a summary page ───────────────────────────────────
SUMMARY_PAGE = ("# A\n\n## Summary\nThe document.\n\n## Source information\n\n- **File:** a.pdf\n\n"
                "[^1]: a.pdf\n")


def test_inject_see_also_puts_the_section_above_the_footnote_of_a_summary():
    from domain.ingestion.wiki_generator import inject_see_also

    page = inject_see_also(SUMMARY_PAGE + "Alpha is mentioned.\n",
                           [{"title": "Alpha", "rel_path": "../concepts/alpha.md"}], language="en")
    assert page.index("## See also") < page.index("[^1]: a.pdf")
    assert "- [Alpha](../concepts/alpha.md)" in page


def test_inject_see_also_still_goes_above_the_sources_heading():
    from domain.ingestion.wiki_generator import inject_see_also

    page = inject_see_also("# Alpha\n\n## Definition\nBeta here.\n\n## Sources\n- a.pdf\n",
                           [{"title": "Beta", "rel_path": "beta.md"}], language="en")
    assert page.index("## See also") < page.index("## Sources")


def test_the_missing_xref_repair_puts_the_section_above_the_footnote(tmp_workspace: WorkspaceFixture):
    from domain.lint.report import LintIssue
    from domain.repair.actions import repair_missing_xref

    _wiki(tmp_workspace, summary=SUMMARY_PAGE)
    issue = LintIssue(check="missing_xref", severity="info", page="/wiki/summaries/a.md",
                      description="", suggestion="", related_page="/wiki/concepts/beta.md")
    assert repair_missing_xref(issue, tmp_workspace.db_path, tmp_workspace.workspace, language="en").success
    page = (tmp_workspace.workspace / "wiki" / "summaries" / "a.md").read_text(encoding="utf-8")
    assert page.index("## See also") < page.index("[^1]: a.pdf")
    assert check_structure(tmp_workspace.workspace, tmp_workspace.db_path, "en") == []
