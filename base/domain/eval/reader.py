"""Read-only evidence gathering from a wiki's SQLite index.

These queries pull the ground truth a judge needs — the text of cited pages and,
per source, the extracted source text plus the pages the engine generated from
it. No LLM and no writes; deterministic given a frozen DB, so they are covered by
a golden-corpus regression test.
"""

from __future__ import annotations

import sqlite3

from domain.eval.packet import DEFAULT_EXCERPT_CHARS, Evidence, IngestionItem, truncate
from domain.tools.db import get_connection


def _resolve_one(conn: sqlite3.Connection, ref: str, excerpt_chars: int) -> Evidence:
    """Resolve a single citation reference to its page/source text."""
    ref_l = ref.lower()
    if ref_l.endswith(".md") or "wiki/" in ref_l:
        row = conn.execute(
            "SELECT content FROM documents WHERE relative_path=? LIMIT 1", (ref,)
        ).fetchone()
        content = row["content"] if row else None
        return Evidence(ref, content or "(cited wiki page not found in this wiki)")
    if ".pdf" in ref_l:
        filename = ref.split(",")[0].strip()
        row = conn.execute(
            "SELECT content FROM documents WHERE source_kind='source' AND filename=? LIMIT 1",
            (filename,),
        ).fetchone()
        if row and row["content"]:
            return Evidence(ref, truncate(row["content"], excerpt_chars))
        return Evidence(ref, "(cited source not found in this wiki)")
    return Evidence(ref, "(unrecognized citation format)")


def resolve_citations(
    db_path: str, refs: list[str], excerpt_chars: int = DEFAULT_EXCERPT_CHARS
) -> list[Evidence]:
    """Resolve citation references to evidence, opening the DB once."""
    if not refs:
        return []
    with get_connection(db_path) as conn:
        return [_resolve_one(conn, ref, excerpt_chars) for ref in refs]


def ingestion_items(
    db_path: str, excerpt_chars: int = DEFAULT_EXCERPT_CHARS
) -> list[IngestionItem]:
    """Per source document: its extracted text + the pages that cite it.

    A page "belongs" to a source when it has a ``cites`` edge to it — that is how
    the pipeline records provenance — so the summary and concept pages are found
    by following ``document_references`` rather than by name matching.
    """
    with get_connection(db_path) as conn:
        sources = conn.execute(
            "SELECT id, filename, content FROM documents "
            "WHERE source_kind='source' ORDER BY filename"
        ).fetchall()

        items: list[IngestionItem] = []
        for src in sources:
            summary_row = conn.execute(
                "SELECT d.content FROM documents d "
                "JOIN document_references r "
                "  ON r.source_document_id=d.id AND r.reference_type='cites' "
                "WHERE d.path='/wiki/summaries/' AND r.target_document_id=? LIMIT 1",
                (src["id"],),
            ).fetchone()

            concept_rows = conn.execute(
                "SELECT d.relative_path, d.content FROM documents d "
                "JOIN document_references r "
                "  ON r.source_document_id=d.id AND r.reference_type='cites' "
                "WHERE d.path='/wiki/concepts/' AND r.target_document_id=? "
                "ORDER BY d.relative_path",
                (src["id"],),
            ).fetchall()

            items.append(
                IngestionItem(
                    source_name=src["filename"],
                    source_text=truncate(src["content"] or "", excerpt_chars),
                    summary=(summary_row["content"] if summary_row else "") or "",
                    concepts=tuple(
                        Evidence(c["relative_path"], c["content"] or "")
                        for c in concept_rows
                    ),
                )
            )
        return items
