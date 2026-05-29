"""Ingestion pipeline orchestrator.

Three public entry points:
  ingest_file()          — single file, full pipeline
  scan_and_ingest()      — scan sources/ for new/modified files
  regenerate_wiki_pages() — regenerate wiki pages without re-extracting

All functions are synchronous — safe to call directly from Marimo cells.
The migration (source_document_id column) is applied automatically on open_db().
"""

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from . import trace
from .chunker import chunk_pages
from .detector import needs_ingestion
from .extractor import extract, LibreOfficeNotInstalledError, check_libreoffice
from .index_manager import update_index, remove_index_entry
from .wiki_generator import (
    build_wiki_page, make_wiki_slug,
    extract_structured, build_summary_page, build_concept_page, update_overview,
)
from domain.tools.db import open_db
from domain.tools.wiki_fs import create_page, read_page, append_to_page, delete_page
from domain.tools.references import update_references
from domain.tools.git_ops import init_wiki_repo, auto_commit

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = frozenset({".pdf", ".docx"})


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class IngestResult:
    file_path: Path
    status: Literal["ingested", "skipped", "failed"]
    message: str
    doc_id: str | None = None


# ── Workspace & DB helpers ────────────────────────────────────────────────────

def _get_user_id(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT user_id FROM workspace LIMIT 1").fetchone()
    if not row:
        raise RuntimeError(
            "No workspace found. Run the app once to initialise the database."
        )
    return row["user_id"]


def _next_doc_number(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(document_number), 0) + 1 FROM documents"
    ).fetchone()
    return row[0]


def _init_wiki_workspace(workspace: Path) -> None:
    """Create wiki directory structure and seed files on first run."""
    (workspace / "wiki" / "summaries").mkdir(parents=True, exist_ok=True)
    (workspace / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)

    index_file = workspace / "wiki" / "index.md"
    if not index_file.exists():
        index_file.write_text("# Wiki Index\n\n## Summaries\n\n## Concepts\n", encoding="utf-8")

    overview_file = workspace / "wiki" / "overview.md"
    if not overview_file.exists():
        overview_file.write_text(
            "# Knowledge Base Overview\n\n_No documents ingested yet._\n", encoding="utf-8"
        )

    log_file = workspace / "wiki" / "log.md"
    if not log_file.exists():
        log_file.write_text("# Ingest Log\n\n", encoding="utf-8")


# ── Public API ────────────────────────────────────────────────────────────────

def ingest_file(
    file_path: Path,
    db_path: str,
    workspace: Path,
    llm_client,
    model: str,
    progress_cb: Callable[[str], None] | None = None,
    lint_after_ingest: bool = False,
    _batch_mode: bool = False,
) -> IngestResult:
    """Run the full ingestion pipeline for a single file.

    Returns IngestResult with status 'ingested', 'skipped', or 'failed'.
    On failure, the source document record is set to status='failed' in the DB.

    _batch_mode=True suppresses steps 10-13 (overview, log, git commit, lint)
    so batch_ingest() can run them once for the whole batch.
    """
    def _cb(msg: str) -> None:
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    # Tracing is opt-in (WIKI_TRACE=1); these no-op defaults make the early-return
    # and skip paths safe before a real tracer is started (after step 3).
    tracer: object = trace.NULL
    _created = False
    ok = False

    _cb(f"🔍 Validating {file_path.name}")

    # ── Step 1: Validate ──────────────────────────────────────────────────────
    if not file_path.is_file():
        return IngestResult(file_path, "failed", f"File not found: {file_path}")

    ext = file_path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return IngestResult(
            file_path, "failed",
            f"Unsupported file type '{ext}'. Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
        )

    if ext == ".docx" and not check_libreoffice():
        msg = LibreOfficeNotInstalledError(file_path.name).args[0]
        return IngestResult(file_path, "failed", msg)

    _init_wiki_workspace(workspace)

    conn = open_db(db_path)
    try:
        user_id = _get_user_id(conn)
        relative = f"sources/{file_path.name}"

        # ── Step 2: Detect changes ────────────────────────────────────────────
        should_process, current_hash = needs_ingestion(file_path, conn)
        if not should_process:
            _cb(f"⏭ {file_path.name} — already up to date")
            return IngestResult(file_path, "skipped", "already up to date")

        # ── Step 3: Upsert source document record (status=processing) ─────────
        existing = conn.execute(
            "SELECT id FROM documents WHERE relative_path = ?", (relative,)
        ).fetchone()
        stat = file_path.stat()

        if existing:
            doc_id = existing["id"]
            conn.execute(
                "UPDATE documents SET status='processing', content_hash=?, mtime_ns=?, "
                "last_indexed_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
                (current_hash, stat.st_mtime_ns, doc_id),
            )
        else:
            doc_id = str(uuid.uuid4())
            doc_number = _next_doc_number(conn)
            title = file_path.stem.replace("-", " ").replace("_", " ").title()
            conn.execute(
                "INSERT INTO documents "
                "(id, user_id, filename, title, path, relative_path, source_kind, "
                "file_type, file_size, status, document_number, content_hash, mtime_ns, "
                "last_indexed_at) "
                "VALUES (?,?,?,?,'sources/',?,?,?,?,?,?,?,?,datetime('now'))",
                (doc_id, user_id, file_path.name, title, relative,
                 "source", ext.lstrip("."), stat.st_size,
                 "processing", doc_number, current_hash, stat.st_mtime_ns),
            )
        conn.commit()

        # ── Trace: join or start a run, wrap the client, open the document ─────
        tracer, _created = trace.active_or_start(workspace, db_path)
        llm_client = tracer.wrap(llm_client)
        tracer.document_start(doc_id, file_path.name, relative)

        _cb(f"⚙️ Extracting text from {file_path.name}")

        # Wiki pages created/overwritten in steps 8-9, for rollback on failure.
        wiki_compensations: list[dict] = []
        try:
            # ── Step 4: Extract text ──────────────────────────────────────────
            cache_dir = workspace / ".llmwiki" / "cache" / "local" / doc_id
            with tracer.stage("extract"):
                page_contents, parser = extract(file_path, cache_dir)
                full_content = "\n\n---\n\n".join(md for _, md in page_contents)
                # Trace intermediate data so the path can be followed end to end.
                tracer.artifact(
                    "extracted_text", "extracted_text", full_content,
                    page_count=len(page_contents), parser=parser,
                )
            _cb(f"✅ Extracted {len(page_contents)} pages")

            # ── Step 5: Chunk source document ─────────────────────────────────
            with tracer.stage("chunk"):
                chunks = chunk_pages(page_contents)
                tracer.artifact(
                    "chunks", "chunks",
                    json.dumps(
                        [{"chunk_index": c.index, "page": c.page,
                          "token_count": c.token_count, "start_char": c.start_char,
                          "content": c.content} for c in chunks],
                        ensure_ascii=False, indent=2,
                    ),
                    ext="json", count=len(chunks),
                )
            _cb(f"✂️ Chunked into {len(chunks)} chunks")

            # ── Step 6: Atomic source document DB write ────────────────────────
            with conn:
                conn.execute(
                    "UPDATE documents SET status='ready', content=?, page_count=?, "
                    "parser=?, mtime_ns=?, updated_at=datetime('now') WHERE id=?",
                    (full_content, len(page_contents), parser, stat.st_mtime_ns, doc_id),
                )
                conn.execute("DELETE FROM document_pages WHERE document_id=?", (doc_id,))
                conn.executemany(
                    "INSERT INTO document_pages (id, document_id, page, content) "
                    "VALUES (?,?,?,?)",
                    [(str(uuid.uuid4()), doc_id, pn, md) for pn, md in page_contents],
                )
                conn.execute("DELETE FROM document_chunks WHERE document_id=?", (doc_id,))
                conn.executemany(
                    "INSERT INTO document_chunks "
                    "(id, document_id, chunk_index, content, page, start_char, "
                    "token_count, header_breadcrumb) VALUES (?,?,?,?,?,?,?,?)",
                    [
                        (str(uuid.uuid4()), doc_id, c.index, c.content, c.page,
                         c.start_char, c.token_count, c.header_breadcrumb)
                        for c in chunks
                    ],
                )
            # Source doc now committed — subsequent tools can open their own connections.
            conn.close()
            conn = None

            doc_meta = {
                "filename": file_path.name,
                "file_type": ext.lstrip("."),
                "page_count": len(page_contents),
                "parser": parser,
            }

            # ── Step 7: Structured extraction ─────────────────────────────────
            _cb("🤖 Extracting knowledge structure...")
            with tracer.stage("structured_extraction"):
                extraction = extract_structured(doc_meta, page_contents, llm_client, model)
            _cb(f"📋 Found {len(extraction.concepts)} concept(s)")

            # ── Step 8: Create/update concept pages ───────────────────────────
            concept_slugs: list[str] = []
            with tracer.stage("concepts", concept_count=len(extraction.concepts)):
                for concept in extraction.concepts:
                    slug = make_wiki_slug(concept.name)
                    concept_slugs.append(slug)
                    existing_content = read_page(db_path, workspace, "/wiki/concepts/", slug)
                    _cb(f"💡 {'Updating' if existing_content else 'Creating'} concept: {concept.name}")
                    concept_md = build_concept_page(
                        concept, file_path.name, existing_content, llm_client, model
                    )
                    prior = _snapshot_wiki_page(db_path, f"wiki/concepts/{slug}.md")
                    concept_result = create_page(
                        db_path, workspace, "/wiki/concepts/", slug,
                        concept.name, concept_md, [concept.category], overwrite=True,
                    )
                    wiki_compensations.append({
                        "dir_path": "/wiki/concepts/", "slug": slug,
                        "category": "concepts", "prior": prior,
                    })
                    update_references(
                        db_path, concept_result["id"], concept_md, "/wiki/concepts/",
                    )
                    tracer.artifact(
                        "markdown", f"concept:{slug}", concept_md, ext="md",
                        relative_path=f"wiki/concepts/{slug}.md",
                        concept_name=concept.name, category=concept.category,
                    )
                    one_liner = concept.insight[:80] if concept.insight else concept.name
                    update_index(workspace, f"concepts/{slug}.md", one_liner, "concepts")

            # ── Step 9: Create summary page ───────────────────────────────────
            _cb("📄 Writing summary page...")
            with tracer.stage("summary"):
                summary_md = build_summary_page(doc_meta, extraction)
                wiki_slug = make_wiki_slug(file_path.name)
                prior_summary = _snapshot_wiki_page(db_path, f"wiki/summaries/{wiki_slug}.md")
                summary_result = create_page(
                    db_path, workspace, "/wiki/summaries/", wiki_slug,
                    _title_from_filename(file_path.name), summary_md, [],
                    overwrite=True, source_document_id=doc_id,
                )
                wiki_compensations.append({
                    "dir_path": "/wiki/summaries/", "slug": wiki_slug,
                    "category": "summaries", "prior": prior_summary,
                })
                update_references(
                    db_path, summary_result["id"], summary_md, "/wiki/summaries/",
                )
                tracer.artifact(
                    "markdown", f"summary:{wiki_slug}", summary_md, ext="md",
                    relative_path=f"wiki/summaries/{wiki_slug}.md",
                    source_document_id=doc_id,
                )
                update_index(
                    workspace,
                    f"summaries/{wiki_slug}.md",
                    extraction.document_summary[:80] if extraction.document_summary else file_path.name,
                    "summaries",
                )

            if not _batch_mode:
                # ── Step 10: Rewrite overview ─────────────────────────────────
                _cb("🌐 Updating overview...")
                with tracer.stage("overview"):
                    current_overview = (workspace / "wiki" / "overview.md").read_text(encoding="utf-8")
                    all_concept_names = _all_concept_names(db_path)
                    new_overview = update_overview(
                        current_overview, extraction.document_summary,
                        all_concept_names, llm_client, model,
                    )
                    (workspace / "wiki" / "overview.md").write_text(new_overview, encoding="utf-8")
                    tracer.artifact(
                        "markdown", "overview", new_overview, ext="md",
                        relative_path="wiki/overview.md",
                    )

                # ── Step 11: Append to log ────────────────────────────────────
                timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                log_entry = (
                    f"## [{timestamp}] Ingested | {file_path.name}\n\n"
                    f"{extraction.document_summary[:200]}\n\n"
                    f"Concepts: {', '.join(c.name for c in extraction.concepts) or 'none'}\n"
                )
                append_to_page(db_path, workspace, "/wiki/", "log", log_entry)

                # ── Step 12: Git commit ───────────────────────────────────────
                init_wiki_repo(workspace)
                auto_commit(workspace, f"ingest: {file_path.name}")

            # ── Step 13 (optional): Lint ──────────────────────────────────────
            if lint_after_ingest and not _batch_mode:
                from domain.lint.runner import lint_wiki
                _cb("🩺 Running lint checks...")
                lint_report = lint_wiki(db_path, workspace)
                if lint_report.issues:
                    lint_lines = "\n".join(
                        f"- [{i.severity.upper()}] {i.check}: {i.description}"
                        for i in lint_report.issues
                    )
                    append_to_page(
                        db_path, workspace, "/wiki/", "log",
                        f"### Lint: {lint_report.summary()}\n\n{lint_lines}\n",
                    )
                _cb(f"🩺 Lint: {lint_report.summary()}")

            ok = True
            _cb(f"✅ Done: {file_path.name} ({len(page_contents)} pages, {len(chunks)} chunks)")
            return IngestResult(file_path, "ingested", f"{len(page_contents)} pages", doc_id)

        except Exception as exc:
            logger.exception("Ingestion failed for %s", file_path.name)
            # Roll back wiki pages this run created/overwrote so a failed ingest
            # leaves no orphaned or half-merged derived pages behind.
            rolled_back = _rollback_wiki_pages(db_path, workspace, wiki_compensations)
            if rolled_back:
                _cb(f"↩️ Rolled back {rolled_back} partial wiki page(s)")
            if conn is None:
                conn = open_db(db_path)
            conn.execute(
                "UPDATE documents SET status='failed', error_message=?, "
                "updated_at=datetime('now') WHERE id=?",
                (str(exc)[:500], doc_id),
            )
            conn.commit()
            _cb(f"❌ Failed: {file_path.name} — {exc}")
            return IngestResult(file_path, "failed", str(exc), doc_id)

    finally:
        tracer.document_end(status="ok" if ok else "error")
        if _created:
            tracer.finish()
        if conn is not None:
            conn.close()


def _title_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    return stem.replace("-", " ").replace("_", " ").strip().title()


def _snapshot_wiki_page(db_path: str, relative_path: str) -> dict | None:
    """Capture a wiki page's restorable state, or None if it doesn't exist yet.

    Used before steps 8-9 overwrite a page so a failed ingest can restore the
    prior version instead of leaving merged-but-orphaned content behind.
    """
    import json
    from domain.tools.db import get_connection
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT id, title, content, tags, source_document_id FROM documents"
            " WHERE relative_path=? AND source_kind='wiki'",
            (relative_path,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"] or "",
        "tags": json.loads(row["tags"]) if row["tags"] else [],
        "source_document_id": row["source_document_id"],
    }


def _rollback_wiki_pages(db_path: str, workspace: Path, compensations: list[dict]) -> int:
    """Undo wiki pages created/overwritten during a failed ingest.

    Pages this run newly created are deleted (and their index entry removed);
    pages it overwrote are restored to their prior content. Returns the number
    of pages acted on. Best-effort: a rollback error is logged, never raised, so
    it cannot mask the original ingestion failure.
    """
    acted = 0
    for comp in reversed(compensations):
        dir_path, slug, category, prior = (
            comp["dir_path"], comp["slug"], comp["category"], comp["prior"],
        )
        try:
            if prior is None:
                if delete_page(db_path, workspace, dir_path, slug):
                    remove_index_entry(workspace, f"{category}/{slug}.md", category)
                    acted += 1
            else:
                create_page(
                    db_path, workspace, dir_path, slug,
                    prior["title"], prior["content"], prior["tags"],
                    overwrite=True, source_document_id=prior["source_document_id"],
                )
                update_references(db_path, prior["id"], prior["content"], dir_path)
                acted += 1
        except Exception:
            logger.exception("Rollback failed for %s%s.md", dir_path, slug)
    return acted


def _all_concept_names(db_path: str) -> list[str]:
    """Return names of all concept wiki pages currently in the DB."""
    from domain.tools.db import get_connection
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT title FROM documents "
            "WHERE source_kind='wiki' AND path='/wiki/concepts/' AND status='ready'"
        ).fetchall()
    return [r["title"] for r in rows]


def scan_and_ingest(
    workspace: Path,
    db_path: str,
    llm_client,
    model: str,
    progress_cb: Callable[[str], None] | None = None,
) -> list[IngestResult]:
    """Scan workspace/sources/ and ingest new or modified files."""
    def _cb(msg: str) -> None:
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    sources_dir = workspace / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    _cb("🔎 Scanning sources/...")

    candidates = [
        p for p in sorted(sources_dir.rglob("*"))
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_EXTENSIONS
        # Only consider the path *below* sources/ when skipping hidden entries —
        # otherwise a workspace under a dot-directory (e.g. ~/.local/share/ws)
        # would make every source look hidden and be skipped.
        and not any(part.startswith(".") for part in p.relative_to(sources_dir).parts)
    ]
    _cb(f"🔎 Found {len(candidates)} candidate file(s)")

    if not candidates:
        return []

    # One trace run for the whole scan (no-op when WIKI_TRACE is unset).
    with trace.run_scope(workspace, db_path) as tracer:
        llm_client = tracer.wrap(llm_client)
        results: list[IngestResult] = []
        for fp in candidates:
            results.append(ingest_file(fp, db_path, workspace, llm_client, model, progress_cb))

    ingested = sum(1 for r in results if r.status == "ingested")
    skipped  = sum(1 for r in results if r.status == "skipped")
    failed   = sum(1 for r in results if r.status == "failed")
    _cb(f"🏁 Scan complete — ingested: {ingested}, skipped: {skipped}, failed: {failed}")
    return results


def regenerate_wiki_pages(
    workspace: Path,
    db_path: str,
    llm_client,
    model: str,
    progress_cb: Callable[[str], None] | None = None,
) -> list[IngestResult]:
    """Regenerate summary pages for all ready source documents without re-extracting."""
    def _cb(msg: str) -> None:
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    conn = open_db(db_path)
    try:
        docs = conn.execute(
            "SELECT id, filename, file_type, page_count, parser "
            "FROM documents WHERE source_kind='source' AND status='ready'"
        ).fetchall()
        _cb(f"Found {len(docs)} source document(s)")

        results: list[IngestResult] = []
        for doc in docs:
            doc_id = doc["id"]
            filename = doc["filename"]
            file_path = workspace / "sources" / filename
            _cb(f"🤖 Regenerating wiki page for {filename}...")
            try:
                page_rows = conn.execute(
                    "SELECT page, content FROM document_pages "
                    "WHERE document_id=? ORDER BY page",
                    (doc_id,),
                ).fetchall()
                page_contents = [(r["page"], r["content"]) for r in page_rows]
                doc_meta = {
                    "filename": filename,
                    "file_type": doc["file_type"],
                    "page_count": doc["page_count"] or len(page_contents),
                    "parser": doc["parser"] or "unknown",
                }
                wiki_markdown = build_wiki_page(doc_meta, page_contents, llm_client, model)
                wiki_slug = make_wiki_slug(filename)
                create_page(
                    db_path, workspace, "/wiki/summaries/", wiki_slug,
                    _title_from_filename(filename), wiki_markdown, [], overwrite=True,
                    source_document_id=doc_id,
                )
                _cb(f"  ✅ {filename} → wiki/summaries/{wiki_slug}.md")
                results.append(IngestResult(file_path, "ingested", "wiki page regenerated", doc_id))
            except Exception as exc:
                logger.exception("Wiki regen failed for %s", filename)
                _cb(f"  ❌ {filename} — {exc}")
                results.append(IngestResult(file_path, "failed", str(exc), doc_id))

        _cb("🏁 Regeneration complete")
        return results
    finally:
        conn.close()
