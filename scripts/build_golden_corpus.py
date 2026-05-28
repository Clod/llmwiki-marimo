#!/usr/bin/env python3
"""Build and freeze the golden regression corpus.

Ingestion is non-deterministic (LLM output varies run to run), so it can't be
strict-diffed. Instead we ingest a fixed set of public-domain fairy-tale PDFs
ONCE, a human eyeballs the result, and we freeze the whole workspace
(sources/ + wiki/ + a SQL dump of index.db) into a tracked fixture. That frozen
"picture" is then a deterministic starting point for regression tests of every
OTHER workflow (delete, lint/repair, chat retrieval, regenerate).

Usage:
    # 1. Ingest the 4 docs (1 individually + 3 as a batch) into a staging
    #    workspace and print a verification report. Needs LLM keys in .env.
    python scripts/build_golden_corpus.py build

    # 2. Manually inspect tests/fixtures/_golden_staging/wiki/ — confirm the
    #    summaries, concepts, cross-links and citation graph look right.

    # 3. Freeze the verified staging workspace into the tracked golden fixture.
    python scripts/build_golden_corpus.py freeze

    # (re-print the report for the current staging workspace)
    python scripts/build_golden_corpus.py verify

The corpus (public-domain English fairy tales): Cinderella is ingested
individually; Little Red Riding Hood, The Sleeping Beauty in the Wood and Snow
White and the Seven Dwarfs are ingested as a batch. The three Perrault tales
(Cinderella, Red Riding Hood, Sleeping Beauty) share an author and Snow White
adds Grimm + overlapping concepts (magic mirror, poisoned apple, dwarfs), so the
batch exercises multi-source concept pages, cross-linking (missing_xref) and the
citation graph. (The Perrault tales come from The Blue Fairy Book, ed. Andrew
Lang, 1889 — Project Gutenberg #503.)
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BASE = str(_PROJECT_ROOT / "base")
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

# Force base/config.py to win over any other config module on the path.
sys.modules.pop("config", None)

_PDFS_DIR = _PROJECT_ROOT / "tests" / "fixtures" / "pdfs"
_STAGING = _PROJECT_ROOT / "tests" / "fixtures" / "_golden_staging"   # gitignored
_GOLDEN = _PROJECT_ROOT / "tests" / "fixtures" / "golden_corpus"      # tracked

# Cinderella goes through the single-file path; the rest through the batch path.
_INDIVIDUAL = "Cinderella.pdf"
_BATCH = [
    "Little Red Riding Hood.pdf",
    "The Sleeping Beauty in the Wood.pdf",
    "Snow White and the Seven Dwarfs.pdf",
]


def _cb(msg: str) -> None:
    print(f"  {msg}")


def _seed_workspace_row(db_path: str, name: str) -> None:
    """Insert the single workspace row the pipeline's _get_user_id requires."""
    from domain.tools.db import open_db

    conn = open_db(db_path)
    try:
        if conn.execute("SELECT 1 FROM workspace LIMIT 1").fetchone() is None:
            ws_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO workspace (id, name, description, user_id) VALUES (?,?,?,?)",
                (ws_id, name, "", ws_id),
            )
            conn.commit()
    finally:
        conn.close()


def _llm():
    """Build an OpenAI-compatible client + model from settings (WIKI_LLM_* → LLM_*)."""
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()
    from config import settings

    base_url = settings.WIKI_LLM_BASE_URL or settings.LLM_BASE_URL
    api_key = settings.WIKI_LLM_API_KEY or settings.LLM_API_KEY
    model = settings.WIKI_LLM_MODEL or settings.LLM_MODEL
    if not api_key:
        sys.exit("No LLM API key configured. Set WIKI_LLM_API_KEY or LLM_API_KEY in .env.")
    return OpenAI(base_url=base_url, api_key=api_key), model


def _report(db_path: str, workspace: Path) -> bool:
    """Print a verification report. Returns True if the corpus looks healthy."""
    from domain.lint.runner import lint_wiki

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sources = conn.execute(
        "SELECT filename, status, page_count FROM documents "
        "WHERE source_kind='source' ORDER BY filename"
    ).fetchall()
    concepts = conn.execute(
        "SELECT filename FROM documents WHERE source_kind='wiki' AND path='/wiki/concepts/' "
        "ORDER BY filename"
    ).fetchall()
    summaries = conn.execute(
        "SELECT filename FROM documents WHERE source_kind='wiki' AND path='/wiki/summaries/' "
        "ORDER BY filename"
    ).fetchall()
    n_cites = conn.execute(
        "SELECT COUNT(*) FROM document_references WHERE reference_type='cites'"
    ).fetchone()[0]
    n_links = conn.execute(
        "SELECT COUNT(*) FROM document_references WHERE reference_type='links_to'"
    ).fetchone()[0]
    # Concept pages with NO outgoing cites edge — the H1 failure signature.
    concepts_without_cites = conn.execute(
        "SELECT d.filename FROM documents d "
        "WHERE d.source_kind='wiki' AND d.path='/wiki/concepts/' "
        "AND NOT EXISTS (SELECT 1 FROM document_references r "
        "                WHERE r.source_document_id=d.id AND r.reference_type='cites') "
        "ORDER BY d.filename"
    ).fetchall()
    conn.close()

    print("\n" + "=" * 60)
    print("VERIFICATION REPORT")
    print("=" * 60)
    print(f"\nSources ({len(sources)}):")
    for r in sources:
        print(f"  - {r['filename']:<24} status={r['status']:<10} pages={r['page_count']}")
    print(f"\nSummary pages ({len(summaries)}):")
    for r in summaries:
        print(f"  - {r['filename']}")
    print(f"\nConcept pages ({len(concepts)}):")
    for r in concepts:
        print(f"  - {r['filename']}")
    print(f"\nCitation graph: {n_cites} cites edges, {n_links} links_to edges")

    lint_report = lint_wiki(db_path, workspace)  # deterministic checks only (no client)
    print(f"\nLint (deterministic): {lint_report.summary()}")
    for issue in lint_report.issues:
        print(f"  [{issue.severity}] {issue.check}: {issue.page}")

    healthy = True
    print("\n" + "-" * 60)
    if any(r["status"] != "ready" for r in sources):
        print("FAIL: not all sources reached status='ready'.")
        healthy = False
    if concepts_without_cites:
        print(f"FAIL (H1 signature): {len(concepts_without_cites)} concept page(s) have "
              f"no cites edge: {[r['filename'] for r in concepts_without_cites]}")
        healthy = False
    if n_cites == 0:
        print("FAIL: zero cites edges in the whole graph.")
        healthy = False
    if healthy:
        print("OK: sources ready, every concept page has a cites edge, graph populated.")
    print("=" * 60)
    return healthy


def build() -> None:
    from domain.ingestion import ingest_file
    from domain.ingestion.batch import batch_ingest

    if _STAGING.exists():
        shutil.rmtree(_STAGING)
    sources_dir = _STAGING / "sources"
    sources_dir.mkdir(parents=True)
    for name in [_INDIVIDUAL, *_BATCH]:
        src = _PDFS_DIR / name
        if not src.exists():
            sys.exit(f"Missing source PDF: {src}")
        shutil.copy(src, sources_dir / name)

    db_path = str(_STAGING / ".llmwiki" / "index.db")
    (_STAGING / ".llmwiki").mkdir(parents=True, exist_ok=True)
    _seed_workspace_row(db_path, "golden_corpus")

    client, model = _llm()
    print(f"LLM: {model}")

    print(f"\n[1/2] Ingesting individually: {_INDIVIDUAL}")
    ingest_file(sources_dir / _INDIVIDUAL, db_path, _STAGING, client, model, _cb)

    print(f"\n[2/2] Batch ingesting: {', '.join(_BATCH)}")
    batch_ingest([sources_dir / n for n in _BATCH], db_path, _STAGING, client, model, _cb)

    ok = _report(db_path, _STAGING)
    print(f"\nStaging workspace: {_STAGING}")
    print("Inspect wiki/ pages, then run:  python scripts/build_golden_corpus.py freeze")
    if not ok:
        print("\n[!] Report shows problems — investigate before freezing.")


def verify() -> None:
    db_path = str(_STAGING / ".llmwiki" / "index.db")
    if not Path(db_path).exists():
        sys.exit(f"No staging workspace at {_STAGING}. Run `build` first.")
    _report(db_path, _STAGING)


def freeze() -> None:
    db_path = _STAGING / ".llmwiki" / "index.db"
    if not db_path.exists():
        sys.exit(f"No staging workspace at {_STAGING}. Run `build` first.")

    if _GOLDEN.exists():
        shutil.rmtree(_GOLDEN)
    _GOLDEN.mkdir(parents=True)

    shutil.copytree(_STAGING / "sources", _GOLDEN / "sources")
    shutil.copytree(_STAGING / "wiki", _GOLDEN / "wiki")

    # Binary copy is the restore source (the FTS5 `chunks_fts` table + its triggers
    # don't round-trip reliably through a .dump/.read).
    shutil.copy(db_path, _GOLDEN / "index.db")
    # Text .dump is the human-auditable companion for PR review and diffs.
    dump = subprocess.run(
        ["sqlite3", str(db_path), ".dump"], capture_output=True, text=True, check=True
    ).stdout
    (_GOLDEN / "index.db.sql").write_text(dump, encoding="utf-8")

    print(f"Froze golden corpus → {_GOLDEN}")
    print("  - sources/        (the 4 PDFs)")
    print("  - wiki/           (generated markdown tree)")
    print("  - index.db        (binary SQLite — the restore source)")
    print("  - index.db.sql    (sqlite3 .dump — human-auditable companion)")
    print("\nReview, then commit:  git add tests/fixtures/golden_corpus")


_COMMANDS = {"build": build, "verify": verify, "freeze": freeze}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in _COMMANDS:
        sys.exit(f"Usage: python {sys.argv[0]} {{build|verify|freeze}}")
    _COMMANDS[sys.argv[1]]()
