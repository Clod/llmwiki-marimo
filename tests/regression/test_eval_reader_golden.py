"""Regression tests for the eval-packet DB reader against the frozen corpus.

These exercise the real SQL (cites-edge joins, content lookups) with no LLM, so
they catch a broken query the moment the schema or the corpus shape drifts.
"""

import pytest

from domain.eval.reader import ingestion_items, resolve_citations
from tests.helpers.golden import golden_available, restore_golden

pytestmark = pytest.mark.skipif(
    not golden_available(),
    reason="golden corpus not frozen — run scripts/build_golden_corpus.py build && freeze",
)


@pytest.fixture
def golden_db(tmp_path) -> str:
    db_path, _workspace = restore_golden(tmp_path)
    return db_path


def test_ingestion_items_cover_all_four_sources(golden_db) -> None:
    items = ingestion_items(golden_db)
    names = {i.source_name for i in items}
    assert names == {
        "Cinderella.pdf",
        "Little Red Riding Hood.pdf",
        "Snow White and the Seven Dwarfs.pdf",
        "The Sleeping Beauty in the Wood.pdf",
    }


def test_each_item_has_source_text_summary_and_concepts(golden_db) -> None:
    items = ingestion_items(golden_db)
    for item in items:
        assert item.source_text.strip(), f"{item.source_name}: empty source text"
        assert item.summary.strip(), f"{item.source_name}: no summary page"
        assert item.concepts, f"{item.source_name}: no concept pages cite it"
        for concept in item.concepts:
            assert concept.label.startswith("wiki/concepts/")
            assert concept.content.strip()


def test_source_text_is_truncated_to_excerpt_cap(golden_db) -> None:
    # The Sleeping Beauty source is ~19.8k chars; a small cap must bite.
    items = {i.source_name: i for i in ingestion_items(golden_db, excerpt_chars=500)}
    sleeping = items["The Sleeping Beauty in the Wood.pdf"]
    assert "characters omitted" in sleeping.source_text
    assert len(sleeping.source_text) < 800


def test_resolve_citations_returns_real_page_content(golden_db) -> None:
    evidence = resolve_citations(golden_db, ["wiki/summaries/cinderella.md"])
    assert len(evidence) == 1
    assert evidence[0].label == "wiki/summaries/cinderella.md"
    assert evidence[0].content.strip()
    assert "not found" not in evidence[0].content


def test_resolve_citations_handles_pdf_and_missing_refs(golden_db) -> None:
    evidence = resolve_citations(
        golden_db,
        ["Cinderella.pdf, p. 3", "wiki/summaries/does-not-exist.md"],
    )
    by_label = {e.label: e.content for e in evidence}
    assert by_label["Cinderella.pdf, p. 3"].strip()
    assert "not found" not in by_label["Cinderella.pdf, p. 3"]
    assert "not found" in by_label["wiki/summaries/does-not-exist.md"]


def test_resolve_citations_empty_input(golden_db) -> None:
    assert resolve_citations(golden_db, []) == []
