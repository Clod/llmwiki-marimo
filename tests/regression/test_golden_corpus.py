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


def test_fts_rowcount_matches_chunks(golden) -> None:
    """§5: external-content FTS must stay row-aligned with document_chunks."""
    db_path, _ = golden
    with get_connection(db_path) as conn:
        chunks = conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0]
        fts = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
    assert chunks == fts, "FTS index drifted from document_chunks (a trigger didn't fire)"


def test_fts_search_returns_hits(golden) -> None:
    """§5: a keyword present across the corpus returns ranked hits (tokenizer ok)."""
    db_path, _ = golden
    with get_connection(db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'prince'"
        ).fetchone()[0]
    assert n > 0, "no FTS hits for 'prince' on a corpus full of princes"


def test_fts_integrity_check_passes(golden) -> None:
    """§5: the FTS5 integrity self-check must not raise (shadow tables intact)."""
    db_path, _ = golden
    with get_connection(db_path) as conn:
        # Raises sqlite3.DatabaseError if the FTS shadow tables are corrupt.
        conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('integrity-check')")


def test_deleting_a_source_cascades_and_drops_its_summary(golden) -> None:
    """§12: delete_source removes the source, cascades pages/chunks/refs, keeps the
    FTS aligned, and DELETES its 1-to-1 summary outright (it is not orphan-kept).
    Mutates only the restored temp copy."""
    from unittest.mock import patch

    from domain.tools.deletion import delete_source

    db_path, workspace = golden
    with get_connection(db_path) as conn:
        src = conn.execute(
            "SELECT id FROM documents WHERE source_kind='source' "
            "AND filename LIKE 'Little Red%'"
        ).fetchone()
        assert src is not None
        src_id = src["id"]
        summaries = [
            r["relative_path"]
            for r in conn.execute(
                "SELECT relative_path FROM documents "
                "WHERE source_kind='wiki' AND source_document_id=?",
                (src_id,),
            ).fetchall()
        ]
    assert summaries, "expected a 1-to-1 summary for the source"

    with patch("domain.tools.git_ops.auto_commit"):
        result = delete_source(db_path, workspace, src_id)
    assert result.success

    with get_connection(db_path) as conn:
        assert conn.execute("SELECT 1 FROM documents WHERE id=?", (src_id,)).fetchone() is None
        orphan_pages = conn.execute(
            "SELECT COUNT(*) FROM document_pages p "
            "LEFT JOIN documents d ON d.id=p.document_id WHERE d.id IS NULL"
        ).fetchone()[0]
        orphan_chunks = conn.execute(
            "SELECT COUNT(*) FROM document_chunks c "
            "LEFT JOIN documents d ON d.id=c.document_id WHERE d.id IS NULL"
        ).fetchone()[0]
        assert orphan_pages == 0 and orphan_chunks == 0, "cascade left orphan rows"
        chunks = conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0]
        fts = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        assert chunks == fts, "delete triggers left the FTS index misaligned"
        dangling = conn.execute(
            "SELECT COUNT(*) FROM document_references r "
            "LEFT JOIN documents s ON s.id=r.source_document_id "
            "LEFT JOIN documents t ON t.id=r.target_document_id "
            "WHERE s.id IS NULL OR t.id IS NULL"
        ).fetchone()[0]
        assert dangling == 0, "dangling reference edge after delete"

    # The 1-to-1 summary is deleted outright — on disk and in the DB.
    for rel in summaries:
        assert not (workspace / rel).exists(), f"{rel} should be deleted, not kept"
    with get_connection(db_path) as conn:
        for rel in summaries:
            assert conn.execute(
                "SELECT 1 FROM documents WHERE relative_path=?", (rel,)
            ).fetchone() is None
