"""Regression tests seeded from the frozen golden corpus.

These assert LLM-variation-robust structural invariants of a known-good ingest
(4 fairy-tale PDFs, 1 individual + 3 batch). They guard the fixes from the MVP
review — most importantly the citation graph (H1) — against future regressions
in the surrounding workflows.

Skipped until the corpus is frozen:
    python scripts/build_golden_corpus.py build      # ingest (needs LLM keys)
    # inspect tests/fixtures/_golden_staging/wiki/
    python scripts/build_golden_corpus.py freeze     # snapshot -> golden_corpus/
    git add tests/fixtures/golden_corpus
"""

import pytest

from domain.tools.db import get_connection
from tests.helpers.golden import golden_available, restore_golden

pytestmark = pytest.mark.skipif(
    not golden_available(),
    reason="golden corpus not frozen — run scripts/build_golden_corpus.py build && freeze",
)


@pytest.fixture
def golden(tmp_path):
    return restore_golden(tmp_path)


def test_four_sources_all_ready(golden) -> None:
    db_path, _ = golden
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT status FROM documents WHERE source_kind='source'"
        ).fetchall()
    assert len(rows) == 4
    assert all(r["status"] == "ready" for r in rows)


def test_every_concept_page_has_a_cites_edge(golden) -> None:
    """H1 guard: a concept page with no cites edge means the Sources parser broke."""
    db_path, _ = golden
    with get_connection(db_path) as conn:
        bad = conn.execute(
            "SELECT d.filename FROM documents d "
            "WHERE d.source_kind='wiki' AND d.path='/wiki/concepts/' "
            "AND NOT EXISTS (SELECT 1 FROM document_references r "
            "  WHERE r.source_document_id=d.id AND r.reference_type='cites')"
        ).fetchall()
    assert [r["filename"] for r in bad] == []


def test_each_summary_cites_its_source(golden) -> None:
    db_path, _ = golden
    with get_connection(db_path) as conn:
        summaries = conn.execute(
            "SELECT id, source_document_id FROM documents "
            "WHERE source_kind='wiki' AND path='/wiki/summaries/'"
        ).fetchall()
        assert len(summaries) == 4
        for s in summaries:
            assert s["source_document_id"] is not None
            edge = conn.execute(
                "SELECT 1 FROM document_references "
                "WHERE source_document_id=? AND target_document_id=? AND reference_type='cites'",
                (s["id"], s["source_document_id"]),
            ).fetchone()
            assert edge is not None, "summary page does not cite its own source"


def test_concept_pages_exist(golden) -> None:
    db_path, _ = golden
    with get_connection(db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM documents "
            "WHERE source_kind='wiki' AND path='/wiki/concepts/'"
        ).fetchone()[0]
    assert n > 0


def test_lint_reports_no_errors(golden) -> None:
    """Deterministic lint (no LLM) should find no error-severity issues."""
    from domain.lint.runner import lint_wiki

    db_path, workspace = golden
    report = lint_wiki(db_path, workspace)
    assert report.errors == [], f"unexpected lint errors: {[i.page for i in report.errors]}"


def test_db_and_markdown_tree_agree(golden) -> None:
    """The snapshot is DB + files; every wiki row must have its markdown on disk."""
    db_path, workspace = golden
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT relative_path FROM documents WHERE source_kind='wiki'"
        ).fetchall()
    missing = [r["relative_path"] for r in rows if not (workspace / r["relative_path"]).exists()]
    assert missing == []
