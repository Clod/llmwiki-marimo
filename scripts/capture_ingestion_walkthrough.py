#!/usr/bin/env python3
"""Replay the ingestion story end to end and capture what it produces.

Plain English: this ingests two documents into an empty wiki, then edits one and
deletes the other, taking an inventory of **every artifact** after each act —
files on disk, rows in SQLite, edges in the citation graph, generated aliases,
git commits. It exists so `docs/ingestion_walkthrough.md` can be *regenerated*
from a real run instead of hand-maintained (hand-written example output rots the
moment the pipeline changes).

It mirrors what the ingest app's runner actually does — `ingest_file`, then a
lint+repair pass scoped to the pages that ingest touched, then the cross-link
pass — so the captured numbers match the product, not a simplified path.

    uv run python scripts/capture_ingestion_walkthrough.py            # write the appendix
    uv run python scripts/capture_ingestion_walkthrough.py --out X.md

Needs a live LLM (the wiki pages are model-written) and a few minutes. The
workspace is temporary; only the markdown appendix is kept.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "base"))

FIXTURES = _PROJECT_ROOT / "tests" / "fixtures" / "pdfs"
DOC_A = "Cinderella.pdf"
DOC_B = "Little Red Riding Hood.pdf"
# Stands in for "someone edited DOC_A": the detector only ever sees that the
# file's hash changed, so swapping the bytes exercises the real re-ingest path.
DOC_A_REVISED = "The Sleeping Beauty in the Wood.pdf"


# ── Inventory ─────────────────────────────────────────────────────────────────

@dataclass
class Snapshot:
    """Everything a reader should be able to point at after an act."""

    act: str
    log: list[str] = field(default_factory=list)
    wiki_files: list[str] = field(default_factory=list)
    llmwiki_files: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    refs: dict[str, int] = field(default_factory=dict)
    aliases: str = ""
    commits: list[str] = field(default_factory=list)


def _rel_tree(root: Path, base: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(
        str(p.relative_to(base)) for p in root.rglob("*")
        if p.is_file() and "cache" not in p.parts
    )


def _db_counts(db_path: str) -> tuple[dict[str, int], dict[str, int]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        counts = {
            "documents (source)": conn.execute(
                "SELECT COUNT(*) FROM documents WHERE source_kind='source'").fetchone()[0],
            "documents (wiki)": conn.execute(
                "SELECT COUNT(*) FROM documents WHERE source_kind='wiki'").fetchone()[0],
            "document_pages": conn.execute("SELECT COUNT(*) FROM document_pages").fetchone()[0],
            "document_chunks": conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0],
            "chunks_fts": conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0],
        }
        refs = {
            row["reference_type"]: row["n"]
            for row in conn.execute(
                "SELECT reference_type, COUNT(*) AS n FROM document_references "
                "GROUP BY reference_type ORDER BY reference_type"
            ).fetchall()
        }
    finally:
        conn.close()
    return counts, refs


def _git_log(workspace: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "log", "--oneline"], cwd=str(workspace),
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip().splitlines() if out.returncode == 0 else []
    except Exception:  # noqa: BLE001 — the appendix must survive a git-less workspace
        return []


def snapshot(act: str, workspace: Path, db_path: str, log: list[str]) -> Snapshot:
    counts, refs = _db_counts(db_path)
    aliases_file = workspace / ".llmwiki" / "aliases.generated.toml"
    return Snapshot(
        act=act,
        log=log,
        wiki_files=_rel_tree(workspace / "wiki", workspace),
        llmwiki_files=_rel_tree(workspace / ".llmwiki", workspace),
        counts=counts,
        refs=refs,
        aliases=aliases_file.read_text(encoding="utf-8") if aliases_file.exists() else "",
        commits=_git_log(workspace),
    )


# ── The acts ──────────────────────────────────────────────────────────────────

def _ingest_like_the_app(fp: Path, workspace: Path, db_path: str, client, model,
                         cb) -> list[str]:
    """One ingest exactly as `ingest_app.py:ingest_runner` performs it.

    ingest_file alone is NOT what the product does: the runner follows it with a
    lint+repair pass **scoped to the pages this ingest touched** (orphan excluded,
    deterministic — no LLM) and then the cross-link pass. Act 2's cross-references
    only appear because of that tail, so the capture must include it.
    """
    from domain.ingestion import crosslink_wiki_pages, ingest_file
    from domain.lint.report import LintReport
    from domain.lint.runner import lint_wiki
    from domain.repair.runner import repair_wiki
    from domain.tools.db import get_connection

    result = ingest_file(fp, db_path, workspace, client, model, cb, language="en")
    if result.status != "ingested" or not result.doc_id:
        cb(f"↩︎ {fp.name} — {result.status}: {result.message}")
        return []

    src_ids = [result.doc_id]
    placeholders = ",".join("?" * len(src_ids))
    related: set[str] = set()
    with get_connection(db_path) as conn:
        for row in conn.execute(
            f"SELECT path || filename AS p FROM documents "
            f"WHERE source_kind='wiki' AND source_document_id IN ({placeholders})", src_ids,
        ).fetchall():
            related.add(row["p"])
        for row in conn.execute(
            f"SELECT d.path || d.filename AS p FROM document_references dr "
            f"JOIN documents d ON dr.source_document_id = d.id "
            f"WHERE dr.target_document_id IN ({placeholders}) "
            f"AND dr.reference_type='cites' AND d.source_kind='wiki'", src_ids,
        ).fetchall():
            related.add(row["p"])

    report = lint_wiki(db_path, workspace, client=None, model=model, progress_cb=cb)
    fixable = [i for i in report.issues if i.check != "orphan" and i.page in related]
    if fixable:
        cb(f"🔧 {len(fixable)} issue(s) on ingested pages — repairing (deterministic)…")
        repair_wiki(LintReport(issues=fixable, checked_at=report.checked_at),
                    db_path, workspace, llm_client=None, model=model,
                    progress_cb=cb, language="en")
    else:
        cb("✅ Ingested pages consistent — no repairs needed.")

    n = crosslink_wiki_pages(workspace, db_path, language="en", progress_cb=cb)
    if n:
        cb(f"🔗 Cross-linked {n} page(s)")
    return [result.doc_id]


def run_story(workspace: Path) -> list[Snapshot]:
    from openai import OpenAI
    from config import require_llm_config, settings
    from domain.tools.db import open_db, seed_workspace_row
    from domain.tools.deletion import delete_source

    base_url = settings.WIKI_LLM_BASE_URL or settings.LLM_BASE_URL
    api_key = settings.WIKI_LLM_API_KEY or settings.LLM_API_KEY
    model = settings.WIKI_LLM_MODEL or settings.LLM_MODEL
    require_llm_config(base_url, api_key, model, purpose="the ingestion walkthrough")
    client = OpenAI(base_url=base_url, api_key=api_key)

    sources = workspace / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    db_path = str(workspace / ".llmwiki" / "index.db")
    open_db(db_path).close()
    seed_workspace_row(db_path, workspace.name)

    snaps: list[Snapshot] = []

    def act(title: str) -> tuple[list[str], callable]:
        lines: list[str] = []

        def cb(msg: str) -> None:
            lines.append(msg)
            print(f"  {msg}")
        print(f"\n=== {title} ===")
        return lines, cb

    # ── Act 1 — the first document lands in an empty wiki ─────────────────────
    lines, cb = act("ACT 1 · first document")
    shutil.copy(FIXTURES / DOC_A, sources / DOC_A)
    _ingest_like_the_app(sources / DOC_A, workspace, db_path, client, model, cb)
    snaps.append(snapshot("Act 1 — first document", workspace, db_path, lines))

    # ── Act 2 — a second document meets a non-empty wiki ──────────────────────
    lines, cb = act("ACT 2 · second document")
    shutil.copy(FIXTURES / DOC_B, sources / DOC_B)
    _ingest_like_the_app(sources / DOC_B, workspace, db_path, client, model, cb)
    snaps.append(snapshot("Act 2 — second document", workspace, db_path, lines))

    # ── Act 3a — re-ingesting an UNCHANGED source is a no-op ──────────────────
    lines, cb = act("ACT 3a · re-ingest unchanged")
    _ingest_like_the_app(sources / DOC_B, workspace, db_path, client, model, cb)
    snaps.append(snapshot("Act 3a — re-ingest, unchanged", workspace, db_path, lines))

    # ── Act 3b — the source changed on disk ───────────────────────────────────
    lines, cb = act("ACT 3b · edited source")
    shutil.copy(FIXTURES / DOC_A_REVISED, sources / DOC_A)   # same name, new bytes
    _ingest_like_the_app(sources / DOC_A, workspace, db_path, client, model, cb)
    snaps.append(snapshot("Act 3b — edited source re-ingested", workspace, db_path, lines))

    # ── Act 3c — deleting a source cascades ───────────────────────────────────
    lines, cb = act("ACT 3c · delete a source")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id FROM documents WHERE filename=? AND source_kind='source'", (DOC_B,),
        ).fetchone()
    if row:
        res = delete_source(db_path, workspace, row["id"], also_delete_file=True)
        cb(f"🗑 {res.action}: {res.message}")
    snaps.append(snapshot("Act 3c — source deleted", workspace, db_path, lines))
    return snaps


# ── Rendering ─────────────────────────────────────────────────────────────────

def _delta(now: dict[str, int], before: dict[str, int]) -> str:
    parts = []
    for key, value in now.items():
        d = value - before.get(key, 0)
        parts.append(f"| `{key}` | {value} | {d:+d} |")
    return "\n".join(parts)


def render(snaps: list[Snapshot]) -> str:
    out = [
        "<!-- GENERATED by scripts/capture_ingestion_walkthrough.py — do not edit by hand. -->",
        "# Appendix — artifact inventory, captured from a real run",
        "",
        "Every number below comes from actually ingesting the two bundled fairy-tale",
        "PDFs and then editing and deleting them. Regenerate with:",
        "",
        "```bash",
        "uv run python scripts/capture_ingestion_walkthrough.py",
        "```",
        "",
    ]
    prev_counts: dict[str, int] = {}
    prev_files: set[str] = set()
    for s in snaps:
        out += [f"## {s.act}", "", "**Activity log**", "", "```text"]
        out += s.log or ["(no output)"]
        out += ["```", "", "**Database**", "", "| table | rows | Δ |", "|---|---|---|",
                _delta(s.counts, prev_counts), ""]
        if s.refs:
            out += ["**Citation graph** (`document_references`)", "",
                    "| reference_type | edges |", "|---|---|"]
            out += [f"| `{k}` | {v} |" for k, v in s.refs.items()]
            out += [""]
        files = set(s.wiki_files) | set(s.llmwiki_files)
        added = sorted(files - prev_files)
        removed = sorted(prev_files - files)
        out += ["**Files**", ""]
        out += [f"- `+` `{f}`" for f in added] or ["- (no new files)"]
        out += [f"- `−` `{f}`" for f in removed]
        out += [""]
        if s.aliases.strip():
            out += ["**`.llmwiki/aliases.generated.toml`**", "", "```toml",
                    s.aliases.strip(), "```", ""]
        if s.commits:
            out += ["**Wiki git history**", "", "```text"] + s.commits + ["```", ""]
        prev_counts, prev_files = s.counts, files
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path,
                        default=_PROJECT_ROOT / "docs" / "ingestion_walkthrough_appendix.md")
    parser.add_argument("--keep", action="store_true",
                        help="keep the temporary workspace for inspection")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")

    tmp = Path(tempfile.mkdtemp(prefix="walkthrough-"))
    print(f"workspace: {tmp}")
    try:
        snaps = run_story(tmp)
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(snaps), encoding="utf-8")
    print(f"\n[OK] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
