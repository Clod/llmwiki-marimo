"""The topics a concept page declares in its front-matter (`topics: [...]`): read,
kept when the page is rewritten, and counted in the coverage roster like a title."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from domain import tracing
from domain.chat import preretrieval
from domain.chat.config import load_config
from domain.chat.preretrieval import PreRetrievalTurn
from domain.chat.refusal import roster_suggestions
from domain.chat.workflow import input_checks
from domain.datasets.frontmatter import split_frontmatter
from domain.datasets.source import LocalMarkdownSource
from domain.tools.db import get_connection
from domain.tools.wiki_fs import concept_page_names, create_page, page_topics, write_page_content
from tests.helpers.workspace import WorkspaceFixture

EXAMPLE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "wikis" / "finanzas-argentinas"
PAGE = "plazo-fijo"   # «Plazo fijo»: no word of QUESTION in its title
QUESTION = "¿La inflación me puede comer la ganancia?"


@pytest.fixture
def wiki(tmp_path, monkeypatch) -> Path:
    """A copy of the finance example with no `topics` on any page: each test
    declares the topics it measures."""
    monkeypatch.delenv("WIKI_AUTOCOMMIT", raising=False)
    folder = tmp_path / "finanzas"
    shutil.copytree(EXAMPLE, folder, ignore=shutil.ignore_patterns("cache", "traces"))
    for kind in ("concepts", "summaries"):
        for page in (folder / "wiki" / kind).glob("*.md"):
            if page_topics(page.read_text(encoding="utf-8")):
                _set_topics(folder, page.stem, None, kind)
    return folder


def _db(wiki: Path) -> str:
    return str(wiki / ".llmwiki" / "index.db")


def _set_topics(wiki: Path, slug: str, topics: list[str] | None, folder: str = "concepts") -> None:
    """Rewrite the `topics` line of a concept or summary page, on disk and in the index."""
    path = wiki / "wiki" / folder / f"{slug}.md"
    block, body = split_frontmatter(path.read_text(encoding="utf-8"))
    # A long `topics` list wraps onto indented lines: they go with it.
    lines, in_topics = [], False
    for line in block.splitlines():
        in_topics = line.startswith("topics:") or (in_topics and line.startswith(" "))
        if not in_topics:
            lines.append(line)
    if topics is not None:
        lines.append(f"topics: [{', '.join(topics)}]")
    text = "---\n" + "\n".join(lines) + "\n---\n" + body + "\n"
    assert write_page_content(_db(wiki), wiki, f"/wiki/{folder}/", slug, text, language="es")


def _input_node(wiki: Path, question: str) -> tuple[PreRetrievalTurn, list[str]]:
    """Run the input workflow on `question`; return the turn and its node ids."""
    turn = PreRetrievalTurn(
        question, load_config(wiki), _db(wiki), wiki, [], "es",
        stem_language="es", source=LocalMarkdownSource(wiki / "datasets"),
    )

    async def go():
        with tracing.collect() as path:
            await input_checks.graph.run(state=turn, inputs=turn)
        return [step["node"] for step in path]
    return turn, asyncio.run(go())


# ── reading ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("front_matter", "expected"), [
    ("topics: [inflación, poder adquisitivo]", ["inflación", "poder adquisitivo"]),
    ("topics: inflación", ["inflación"]),
    ("topics: [inflación, 3, '  ']", ["inflación"]),
    ("topics: 3", []),
    ("title: Sin temas", []),
    ("topics: [inflación", []),
])
def test_page_topics_reads_the_front_matter(front_matter, expected):
    assert page_topics(f"---\n{front_matter}\n---\n\n# Página\n") == expected


def test_a_page_without_front_matter_has_no_topics():
    assert page_topics("# Página\n") == []
    assert page_topics(None) == []


# ── kept on a rewrite ─────────────────────────────────────────────────────────

def test_create_page_keeps_the_topics_the_page_declares(tmp_workspace: WorkspaceFixture):
    ws = tmp_workspace
    create_page(ws.db_path, ws.workspace, "/wiki/concepts/", "riesgo", "Riesgo", "# Riesgo\n\nTexto.\n", ["theme"])
    path = ws.workspace / "wiki" / "concepts" / "riesgo.md"
    path.write_text(path.read_text().replace("tags: [theme]", "tags: [theme]\ntopics: [inflación]"))

    create_page(ws.db_path, ws.workspace, "/wiki/concepts/", "riesgo", "Riesgo",
                "# Riesgo\n\nOtro texto.\n", ["theme"], overwrite=True)

    assert page_topics(path.read_text()) == ["inflación"]
    with get_connection(ws.db_path) as conn:
        content = conn.execute("SELECT content FROM documents WHERE relative_path = 'wiki/concepts/riesgo.md'"
                               ).fetchone()["content"]
    assert page_topics(content) == ["inflación"]
    assert "Otro texto." in content


def test_create_page_takes_the_topics_of_the_content_when_the_page_is_new(tmp_workspace: WorkspaceFixture):
    """A rollback restores a page from the prior index: its content carries the front-matter."""
    ws = tmp_workspace
    content = "---\ntype: concept\ntitle: Riesgo\ntopics: [inflación]\n---\n\n# Riesgo\n\nTexto.\n"
    create_page(ws.db_path, ws.workspace, "/wiki/concepts/", "riesgo", "Riesgo", content, ["theme"])
    assert page_topics((ws.workspace / "wiki" / "concepts" / "riesgo.md").read_text()) == ["inflación"]


def test_a_rollback_restores_the_topics_of_the_snapshot(tmp_workspace: WorkspaceFixture):
    """`_rollback_wiki_pages` restores a page with replace_sources=True from the prior
    index: the topics the rolled-back run wrote to disk do not stay."""
    ws = tmp_workspace
    prior = "---\ntype: concept\ntitle: Riesgo\ntopics: [inflación]\n---\n\n# Riesgo\n\nTexto.\n"
    create_page(ws.db_path, ws.workspace, "/wiki/concepts/", "riesgo", "Riesgo", prior, ["theme"])
    path = ws.workspace / "wiki" / "concepts" / "riesgo.md"
    path.write_text(path.read_text().replace("topics: [inflación]", "topics: [inflación, colateral]"))
    create_page(ws.db_path, ws.workspace, "/wiki/concepts/", "riesgo", "Riesgo", prior, ["theme"],
                overwrite=True, replace_sources=True)
    assert page_topics(path.read_text()) == ["inflación"]


def test_a_page_without_topics_gets_no_topics_line(tmp_workspace: WorkspaceFixture):
    ws = tmp_workspace
    create_page(ws.db_path, ws.workspace, "/wiki/concepts/", "riesgo", "Riesgo", "# Riesgo\n\nTexto.\n", ["theme"])
    assert "topics" not in (ws.workspace / "wiki" / "concepts" / "riesgo.md").read_text()


# ── the coverage roster ───────────────────────────────────────────────────────

def test_a_declared_topic_lets_the_question_pass_the_roster(wiki):
    _set_topics(wiki, PAGE, None)
    turn, nodes = _input_node(wiki, QUESTION)
    assert not turn.in_roster and "R_NOT_IN_ROSTER" in nodes

    _set_topics(wiki, PAGE, ["inflación"])
    turn, nodes = _input_node(wiki, QUESTION)
    assert turn.in_roster and "inflación" in turn.matched
    assert "R_NOT_IN_ROSTER" not in nodes


def test_a_topic_of_two_words_matches_only_when_both_appear_in_a_row(wiki):
    _set_topics(wiki, PAGE, ["poder adquisitivo"])
    names = concept_page_names(_db(wiki))
    assert "poder adquisitivo" in names
    assert preretrieval.matched_terms("¿Pierdo poder adquisitivo?", names, language="es") == ["poder adquisitivo"]
    assert "poder adquisitivo" not in preretrieval.matched_terms(
        "¿Tengo poder para elegir lo adquirido?", names, language="es")


def test_the_suggestions_include_the_page_of_a_named_topic(wiki):
    _set_topics(wiki, PAGE, ["inflación"])
    pages = roster_suggestions(_db(wiki), QUESTION, set(), "es")
    assert ("Plazo fijo", f"/wiki/concepts/{PAGE}.md") in pages


def test_the_roster_tab_shows_a_topic_with_its_page(wiki):
    from services import vocabulary as vocab
    from services.wiki import open_wiki

    _set_topics(wiki, PAGE, ["inflación"])
    entries = {e.name: e for e in vocab.read_vocabulary(open_wiki(wiki)).roster}
    assert entries["inflación"].origin == "topic"
    assert entries["inflación"].page == f"concepts/{PAGE}"


# ── summary pages ─────────────────────────────────────────────────────────────

SUMMARY = "10-plazos-fijos"


def test_a_topic_of_a_summary_page_lets_the_question_pass_the_roster(wiki):
    question = "¿Qué pasa con la precancelación de un depósito?"
    assert not _input_node(wiki, question)[0].in_roster
    _set_topics(wiki, SUMMARY, ["precancelación"], "summaries")
    turn, _nodes = _input_node(wiki, question)
    assert turn.in_roster and "precancelación" in turn.matched
    pages = roster_suggestions(_db(wiki), question, set(), "es")
    assert ("10 Plazos Fijos", f"/wiki/summaries/{SUMMARY}.md") in pages


def test_the_title_of_a_summary_page_stays_out_of_the_roster(wiki):
    _set_topics(wiki, SUMMARY, ["precancelación"], "summaries")
    names = concept_page_names(_db(wiki))
    assert "precancelación" in names and "10 Plazos Fijos" not in names
    assert "Plazo fijo" in names


def test_the_roster_tab_links_a_summary_topic_to_its_page(wiki):
    from services import vocabulary as vocab
    from services.wiki import open_wiki

    _set_topics(wiki, SUMMARY, ["precancelación"], "summaries")
    entries = {e.name: e for e in vocab.read_vocabulary(open_wiki(wiki)).roster}
    assert entries["precancelación"].origin == "topic"
    assert entries["precancelación"].page == f"summaries/{SUMMARY}"
    assert "10 Plazos Fijos" not in entries


def test_a_topic_declared_by_two_pages_links_to_both(wiki):
    from services import vocabulary as vocab
    from services.wiki import open_wiki

    _set_topics(wiki, "cauciones-bursatiles", ["incumplimiento"])
    _set_topics(wiki, "aforo", ["incumplimiento"])
    entries = {e.name: e for e in vocab.read_vocabulary(open_wiki(wiki)).roster}
    assert entries["incumplimiento"].origin == "topic"
    assert {page for page, _title in entries["incumplimiento"].pages} == {
        "concepts/cauciones-bursatiles", "concepts/aforo"}
