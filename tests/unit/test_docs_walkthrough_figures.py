"""The FTS figures quoted in the ingestion walkthrough must match the demo.

The walkthrough shows real rowids, ranks and counts measured against the
shipped finanzas-argentinas demo. Those are exactly the kind of number that
rots silently: anything that re-chunks the corpus reassigns the rowids, and a
doc claiming to quote real output is worthless once it quotes stale output.

Rather than trusting whoever regenerates the demo to remember, this reads the
figures back out of the prose and checks them. A failure here is not a bug in
the pipeline — it means the demo moved and the document has to be updated.
"""

import re
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "ingestion_walkthrough.md"
DEMO_DB = REPO / "examples" / "finanzas-argentinas" / ".llmwiki" / "index.db"

pytestmark = pytest.mark.skipif(
    not DEMO_DB.exists(), reason="shipped demo database not present"
)


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def db():
    conn = sqlite3.connect(f"file:{DEMO_DB}?mode=ro", uri=True)
    yield conn
    conn.close()


def _fts_rowids(db, word: str) -> list[int]:
    """Rowids matching `word`, best first — what the doc's mapping block shows."""
    return [
        r[0] for r in db.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank",
            (f'"{word}"',),
        )
    ]


def test_the_word_to_rowid_mapping_is_current(doc):
    """The `caución →` line lists the live hits, in rank order."""
    line = re.search(r"^\s*caución\s+→\s+(.+)$", doc, re.M)
    assert line, "the mapping example is gone from the walkthrough"
    quoted = [int(n) for n in line.group(1).split(",") if n.strip().isdigit()]

    with sqlite3.connect(f"file:{DEMO_DB}?mode=ro", uri=True) as db:
        assert quoted == _fts_rowids(db, "caución")


def test_the_bm25_table_matches_what_the_index_returns(doc, db):
    """Every row of the ranking table — rowid, mentions, tokens, rank."""
    rows = re.findall(
        r"^\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*−([\d.]+)\s*\|\s*$",
        doc, re.M,
    )
    assert len(rows) == 8, f"expected the 8-row BM25 table, parsed {len(rows)}"

    measured = db.execute(
        "SELECT chunks_fts.rowid, "
        "  (length(lower(c.content)) - length(replace(lower(c.content),'cauci',''))) / 5, "
        "  c.token_count, round(chunks_fts.rank, 2) "
        "FROM chunks_fts JOIN document_chunks c ON c.rowid = chunks_fts.rowid "
        "WHERE chunks_fts MATCH '\"caución\"' ORDER BY chunks_fts.rank"
    ).fetchall()

    quoted = [(int(a), int(b), int(c), -float(d)) for a, b, c, d in rows]
    assert quoted == [tuple(r) for r in measured]


def test_every_rowid_named_in_the_prose_is_one_the_table_shows(doc, db):
    """Rowids cited in the surrounding sentences, not just in the table.

    The first version of this file checked the table and the mapping line and
    stopped there — so the paragraph reading those rows off by number kept
    citing rowids from two re-chunks earlier, and the document contradicted its
    own table with every check passing.
    """
    prose = re.search(
        r"Three behaviours are legible.+?rarer terms in the same query\.",
        doc, re.S,
    )
    assert prose, "the BM25 explanation is gone from the walkthrough"

    # Anchor on the shapes the prose uses to name rows — "(a → b)" for the two
    # saturation comparisons and "fragments a, b and c" for the length one.
    # Matching every number instead would sweep up token counts, and filtering
    # those out by membership would filter out the stale rowids as well: the
    # first cut of this test did exactly that and caught nothing.
    cited = {int(n) for pair in re.findall(r"\((\d+) → (\d+)\)", prose.group(0))
             for n in pair}
    cited |= {int(n) for n in
              re.search(r"fragments (\d+), (\d+) and (\d+)", prose.group(0)).groups()}

    # Six distinct rows across the three comparisons — one row carries two of them
    assert len(cited) == 6, f"expected 6 distinct rows named in the prose, got {sorted(cited)}"
    live = set(_fts_rowids(db, "caución"))
    assert cited <= live, (
        f"prose cites rowids the index no longer returns: {sorted(cited - live)}"
    )


def test_the_corpus_size_the_prose_quotes_is_current(doc, db):
    """"eight fragments out of fifty-three", spelled out in the prose."""
    assert "out of fifty-three" in doc
    assert db.execute("SELECT count(*) FROM document_chunks").fetchone()[0] == 53

    assert "whose 53 fragments come from six sources" in doc
    sources = db.execute(
        "SELECT count(*) FROM documents WHERE source_kind='source'"
    ).fetchone()[0]
    assert sources == 6


def test_the_accent_folding_claim_still_holds(db):
    """Eleven fragments spell *inflación* and never the bare form; all are found.

    The doc's point is that a LIKE scan would miss them, so the number matters
    less than the equality — but a zero here would mean the example is vacuous.
    """
    only_accented = (
        "SELECT count(*) FROM document_chunks "
        "WHERE content LIKE '%inflación%' AND content NOT LIKE '%inflacion%'"
    )
    total = db.execute(only_accented).fetchone()[0]
    found = db.execute(
        "SELECT count(*) FROM document_chunks c WHERE c.rowid IN "
        "(SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH '\"inflacion\"') "
        "AND c.content LIKE '%inflación%' AND c.content NOT LIKE '%inflacion%'"
    ).fetchone()[0]

    assert total > 0
    assert found == total


def test_the_breadcrumb_example_is_what_the_chunker_produces(doc):
    """The breadcrumb shown for document_chunks, re-derived from a shipped page.

    Checked against the chunker rather than against a stored database, because
    a database can be older than the code that would write it today — the
    fairy-tale demo's index.db is exactly that — while a markdown file plus the
    current chunker is the thing the pipeline would actually produce.
    """
    quoted = re.search(r"joined with ` > ` — `([^`]+)`", doc.replace("\n  ", " "))
    assert quoted, "the breadcrumb example is gone from the walkthrough"

    page = REPO / "examples" / "fairy-tales" / "wiki" / "concepts" / "cinderella.md"
    if not page.exists():
        pytest.skip("fairy-tale demo page not present")

    import sys
    sys.path.insert(0, str(REPO / "base"))
    from domain.ingestion.chunker import chunk_pages

    produced = {c.header_breadcrumb
                for c in chunk_pages([(1, page.read_text(encoding="utf-8"))])}
    assert quoted.group(1) in produced, (
        f"the walkthrough quotes {quoted.group(1)!r}, but chunking "
        f"{page.name} produces {sorted(produced)}"
    )
