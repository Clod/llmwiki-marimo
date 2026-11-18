"""Ingestion pipeline orchestrator.

Three public entry points:
  ingest_file()          — single file, full pipeline
  scan_and_ingest()      — scan sources/ for new/modified files
  regenerate_wiki_pages() — regenerate wiki pages without re-extracting

All functions are synchronous.
"""

import asyncio
import concurrent.futures
import contextvars
import json
import re
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Literal

# The steps of the ingestion (`workflow.py`) read these names from this module, so a test can
# patch them here; they are not used in this file.
from .chunker import chunk_pages  # noqa: F401
from .detector import needs_ingestion  # noqa: F401
from .extractor import (
    extract,  # noqa: F401
    JavaNotInstalledError,
    LibreOfficeNotInstalledError,
    check_java,
    check_libreoffice,
)
from .describe import Vision, default_vision, describe_images, effective_config  # noqa: F401
from .images import load_figures
from .text_extract import referenced_images
from .formats import (
    IMAGE_EXTENSIONS, OFFICE_EXTENSIONS, SUPPORTED_EXTENSIONS, TEXT_EXTENSIONS, sorted_extensions,
    supported_extensions,
)
from .index_manager import update_index, remove_index_entry  # noqa: F401
from .wiki_generator import (
    make_wiki_slug,
    extract_structured, extract_dataset_aliases, build_summary_page,
    build_concept_page,  # noqa: F401
    update_overview,  # noqa: F401
    inject_see_also, strip_accents,
)
from .alias_generation import regenerate_dataset_aliases, update_generated_aliases  # noqa: F401
from domain import tracing
from domain.i18n import get_locale
from domain.wiki_settings import load_images_config
from domain.tools.db import get_connection, open_db
from domain.datasets.frontmatter import parse_frontmatter, split_frontmatter
from domain.tools.wiki_fs import create_page, read_page, append_to_page, delete_page, page_description  # noqa: F401
from domain.tools.references import update_references
from domain.tools.git_ops import init_wiki_repo, auto_commit  # noqa: F401
from domain.messages import M_
from domain.text.stemming import stem_text

logger = logging.getLogger(__name__)


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


def _init_wiki_workspace(workspace: Path, language: str = "en") -> None:
    """Create wiki directory structure and seed files on first run."""
    loc = get_locale(language)
    (workspace / "wiki" / "summaries").mkdir(parents=True, exist_ok=True)
    (workspace / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)

    index_file = workspace / "wiki" / "index.md"
    if not index_file.exists():
        index_file.write_text(
            f"# {loc.index_title}\n\n## {loc.index_section_summaries}\n\n## {loc.index_section_concepts}\n",
            encoding="utf-8",
        )

    overview_file = workspace / "wiki" / "overview.md"
    if not overview_file.exists():
        overview_file.write_text(
            f"# {loc.overview_title}\n\n{loc.overview_empty}\n", encoding="utf-8"
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
    language: str = "en",
    vision: Vision | None = None,
) -> IngestResult:
    """Run the full ingestion pipeline for a single file.

    `vision` is the vision model that describes the images of the source when the wiki
    sets `[images] describe = true`; without it the model of the environment
    (`VISION_*`) is used, and none means the images stay undescribed.

    With WIKI_TRACE=1 the run is one `document` span, inside the `ingest` root
    span of the batch or scan that called it (or a root of its own). Each step
    is a child span named by its node in the writing diagram (the I_ nodes of
    one document, W_CHUNK, W_INSERT_CHUNKS), and the end state is one of
    E_INVALID, E_UNCHANGED, E_ROLLED_BACK and E_INGESTED (domain/tracing.py).
    """
    with tracing.root("ingest", workspace), tracing.node(
        "document", tracing.WRITING, filename=Path(file_path).name, batch=_batch_mode,
    ) as doc:
        result = _ingest_file(
            file_path, db_path, workspace, llm_client, model,
            progress_cb=progress_cb, lint_after_ingest=lint_after_ingest,
            _batch_mode=_batch_mode, language=language, doc=doc, vision=vision,
        )
        doc.set(status=result.status, message=result.message)
        if result.status == "failed" and result.doc_id is None:
            tracing.mark("E_INVALID", tracing.WRITING, message=result.message)
        elif result.status == "skipped":
            tracing.mark("E_UNCHANGED", tracing.WRITING)
        elif result.status == "ingested":
            tracing.mark("E_INGESTED", tracing.WRITING)
        return result


def _validation_error(file_path: Path, *, images: bool = True) -> str | None:
    """The reason `file_path` cannot be ingested, or None (node I_VALIDATE).

    With `images` false (`[images] ignore = true`), an image file is refused.
    """
    if not file_path.is_file():
        return f"File not found: {file_path}"

    ext = file_path.suffix.lower()
    if not images and ext in IMAGE_EXTENSIONS:
        return (f"Unsupported file type '{ext}': this wiki ignores images "
                "([images] ignore = true in wiki_config.toml). "
                f"Supported: {', '.join(sorted_extensions(supported_extensions(images)))}")
    if ext not in SUPPORTED_EXTENSIONS:
        return f"Unsupported file type '{ext}'. Supported: {', '.join(sorted_extensions())}"

    if ext in TEXT_EXTENSIONS or ext in IMAGE_EXTENSIONS:
        return None  # read directly: no Java, no LibreOffice

    if ext in OFFICE_EXTENSIONS and not check_libreoffice():
        return LibreOfficeNotInstalledError(file_path.name).args[0]

    # A PDF and an office file end up in opendataloader-pdf, which runs a .jar:
    # the office file is converted to PDF first. Without a Java runtime the
    # extractor raises deep in a subprocess call, so the check happens here,
    # next to the LibreOffice one.
    if not check_java():
        return JavaNotInstalledError(file_path.name).args[0]
    return None


def _run_graph(coroutine) -> None:
    """Run the graph of the ingestion to its end. The ingestion is synchronous: with no event
    loop in this thread, the graph runs in a new one; inside a running loop (an async caller),
    it runs in a thread of its own, with the context of this one, so the trace still
    collects its nodes."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coroutine)
        return
    context = contextvars.copy_context()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(context.run, asyncio.run, coroutine).result()


def _ingest_file(
    file_path: Path,
    db_path: str,
    workspace: Path,
    llm_client,
    model: str,
    progress_cb: Callable[[str], None] | None = None,
    lint_after_ingest: bool = False,
    _batch_mode: bool = False,
    language: str = "en",
    *,
    doc: tracing.Node,
    vision: Vision | None = None,
) -> IngestResult:
    """The body of `ingest_file`, inside the document's span `doc`.

    Returns IngestResult with status 'ingested', 'skipped', or 'failed'.
    On failure, the source document record is set to status='failed' in the DB.

    _batch_mode=True suppresses steps 10-13 (overview, log, git commit, lint)
    so batch_ingest() can run them once for the whole batch.

    language: ISO 639-1 code controlling the language of generated content
    (summaries, concept pages, overview) and the index/overview scaffold.
    """
    def _cb(msg: str) -> None:
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    from domain.ingestion import workflow

    run = workflow.IngestFile(
        file_path, db_path, workspace, llm_client, model, _cb, lint_after_ingest, _batch_mode,
        language, doc, vision=vision,
    )
    try:
        # The steps of the ingestion are the steps of the graph (`domain/ingestion/workflow.py`).
        try:
            _run_graph(workflow.graph.run(state=run, inputs=run))
            return run.result
        except Exception as exc:
            if not run.started:
                raise
            logger.exception("Ingestion failed for %s", file_path.name)
            rolled_back = _rollback_wiki_pages(db_path, workspace, run.wiki_compensations, language=language)
            tracing.mark("E_ROLLED_BACK", tracing.WRITING, error=str(exc)[:500], rolled_back=rolled_back)
            if rolled_back:
                _cb(M_("↩️ Rolled back %(n)s partial wiki page(s)", n=rolled_back))
            if run.conn is None:
                run.conn = open_db(db_path)
            run.conn.execute(
                "UPDATE documents SET status='failed', error_message=?, "
                "updated_at=datetime('now') WHERE id=?",
                (str(exc)[:500], run.doc_id),
            )
            run.conn.commit()
            _cb(M_("❌ Failed: %(file)s — %(error)s", file=file_path.name, error=exc))
            return IngestResult(file_path, "failed", str(exc), run.doc_id)
    finally:
        if run.conn is not None:
            run.conn.close()


def _title_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    return stem.replace("-", " ").replace("_", " ").strip().title()


def _snapshot_wiki_page(db_path: str, relative_path: str) -> dict | None:
    """Capture a wiki page's restorable state, or None if it doesn't exist yet.

    Used before steps 8-9 overwrite a page so a failed ingest can restore the
    prior version instead of leaving merged-but-orphaned content behind.
    """
    import json
    from domain.verify.store import load_verification
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT id, title, content, tags, source_document_id FROM documents"
            " WHERE relative_path=? AND source_kind='wiki'",
            (relative_path,),
        ).fetchone()
        if row is None:
            return None
        # The overwrite deletes the page's sentence verification; a rollback puts it back.
        verification = load_verification(conn, row["id"])
    return {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"] or "",
        "tags": json.loads(row["tags"]) if row["tags"] else [],
        "source_document_id": row["source_document_id"],
        "verification": verification,
    }


def _rollback_wiki_pages(
    db_path: str, workspace: Path, compensations: list[dict], language: str = "en"
) -> int:
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
                    remove_index_entry(workspace, f"{category}/{slug}.md", category, language=language)
                    acted += 1
            else:
                # replace_sources=True: prior["content"] is the pre-overwrite DB
                # snapshot and already carries the prior frontmatter (sources
                # included) — restore it authoritatively rather than unioning
                # with whatever the now-rolled-back run wrote to disk. Without
                # this, a failed ingest's filename would permanently stick to
                # the page's `sources` even though its content was restored.
                create_page(
                    db_path, workspace, dir_path, slug,
                    prior["title"], prior["content"], prior["tags"],
                    overwrite=True, source_document_id=prior["source_document_id"],
                    replace_sources=True,
                )
                update_references(db_path, prior["id"], prior["content"], dir_path)
                if prior.get("verification") is not None:
                    from domain.tools.db import get_connection
                    from domain.verify.store import restore_verification
                    with get_connection(db_path) as conn:
                        restore_verification(conn, prior["verification"])
                acted += 1
        except Exception:
            logger.exception("Rollback failed for %s%s.md", dir_path, slug)
    return acted


def _report_new_aliases(cb: Callable[[str], None], new: Iterable[tuple[str, str]]) -> None:
    """Name each alias the ingestion just generated, for a curator to review in the
    vocabulary screen: only a curator can tell a false alias («UVA» is not another
    name of «Plazo fijo UVA»)."""
    pairs = "; ".join(f"«{alias}» → «{canonical}»" for canonical, alias in new)
    if pairs:
        cb(M_("⚠️ New aliases to review in the vocabulary screen: %(pairs)s", pairs=pairs))


def _report_new_topics(cb: Callable[[str], None], new: Iterable[tuple[str, str]],
                       source_text: str = "", language: str = "en") -> None:
    """Name each topic the ingestion just added to a page, for a curator to review in
    the editor of the page. A topic the curator removed can come back with a later
    ingestion: this line names it again.

    The extraction prompt asks for topics in the words of the document, and nothing
    enforces it: on the Spanish tale example, 31 of 39 topics were not in the text,
    among them the generic ones that let a question about another work pass the
    coverage rule («cuento popular»). A second line names the new topics that are not
    in `source_text`, compared by stem as the coverage rule compares. The topics are
    kept: they are marked for review, not removed."""
    new = list(new)
    pairs = "; ".join(f"«{topic}» → {page}" for page, topic in new)
    if not pairs:
        return
    cb(M_("⚠️ New topics to review in the editor of each page: %(pairs)s", pairs=pairs))
    if not source_text:
        return
    text = f" {stem_text(source_text, language)} "
    absent = list(dict.fromkeys(topic for _, topic in new
                                if f" {stem_text(topic, language)} " not in text))
    if absent:
        cb(M_("⚠️ Of those, not in the text of the document (review them first): %(topics)s",
              topics=", ".join(f"«{t}»" for t in absent)))


def _all_concept_names(db_path: str) -> list[str]:
    """Return names of all concept wiki pages currently in the DB."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT title FROM documents "
            "WHERE source_kind='wiki' AND path='/wiki/concepts/' AND status='ready'"
        ).fetchall()
    return [r["title"] for r in rows]


def known_concepts(workspace: Path, language: str = "en") -> list[tuple[str, str]]:
    """(title, description) of each concept page of the wiki, sorted by title: what the
    extraction receives to reuse the title of a concept the wiki already has.

    The description is the `description` of the front-matter. A page without one,
    written before the field existed, gives the first sentence of its definition
    section (`## Definición` in Spanish); a page with neither is listed by its title
    alone, with an empty description."""
    loc = get_locale(language)
    concepts: list[tuple[str, str]] = []
    for path in sorted((workspace / "wiki" / "concepts").glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        title = _front_title(text) or path.stem.replace("-", " ")
        concepts.append((title, page_description(text) or _first_definition_sentence(text, loc.h_definition)))
    return sorted(concepts, key=lambda c: c[0].lower())


def _front_title(text: str) -> str:
    """The `title` of a page's front-matter, or ""."""
    block, _ = split_frontmatter(text)
    if block is None:
        return ""
    try:
        title = parse_frontmatter(block).get("title")
    except ValueError:
        return ""
    return title.strip() if isinstance(title, str) else ""


def _first_definition_sentence(text: str, heading: str) -> str:
    """The first sentence of the `## <heading>` section of a page, or ""."""
    match = re.search(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    body = " ".join(match.group(1).split())
    sentence = re.match(r"(.+?[.!?])(\s|$)", body)
    return (sentence.group(1) if sentence else body).strip()


def scan_candidates(workspace: Path) -> list[Path]:
    """The files of workspace/sources/ the scan considers, sorted: the types the wiki
    reads, no hidden entry below sources/, and no image a Markdown source references.
    An empty list when sources/ does not exist."""
    sources_dir = workspace / "sources"
    if not sources_dir.is_dir():
        return []
    # A wiki that ignores images (`[images] ignore = true`) takes no image file as a source.
    supported = supported_extensions(not load_images_config(workspace).ignore)
    candidates = [
        p for p in sorted(sources_dir.rglob("*"))
        if p.is_file()
        and p.suffix.lower() in supported
        # Only consider the path *below* sources/ when skipping hidden entries —
        # otherwise a workspace under a dot-directory (e.g. ~/.local/share/ws)
        # would make every source look hidden and be skipped.
        and not any(part.startswith(".") for part in p.relative_to(sources_dir).parts)
    ]
    # An image a Markdown source references is a figure of that source, not a source (D11).
    referenced = referenced_images([p for p in candidates if p.suffix.lower() == ".md"])
    return [p for p in candidates if p.resolve() not in referenced]


def scan_and_ingest(
    workspace: Path,
    db_path: str,
    llm_client,
    model: str,
    progress_cb: Callable[[str], None] | None = None,
    language: str = "en",
    vision: Vision | None = None,
) -> list[IngestResult]:
    """Scan workspace/sources/ and ingest new or modified files."""
    def _cb(msg: str) -> None:
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    (workspace / "sources").mkdir(parents=True, exist_ok=True)
    _cb(M_("🔎 Scanning sources/..."))
    candidates = scan_candidates(workspace)
    _cb(M_("🔎 Found %(n)s candidate file(s)", n=len(candidates)))

    if not candidates:
        return []

    # One trace for the whole scan (no-op when WIKI_TRACE is unset).
    with tracing.root("ingest", workspace, kind="scan", files=len(candidates)):
        llm_client = tracing.wrap_openai(llm_client)
        results: list[IngestResult] = []
        for fp in candidates:
            results.append(
                ingest_file(fp, db_path, workspace, llm_client, model, progress_cb, language=language,
                            vision=vision)
            )

        # ── Dataset-alias pass (Piece 4): aliases for the known data terms ────
        # A workspace-level regen (the dataset vocab doesn't change per-doc, so it
        # isn't part of ingest_file). Gated on a fingerprint of the vocabulary, so
        # the LLM runs only when the datasets/ actually changed. Best-effort — an
        # alias must never fail a scan.
        with tracing.node("I_DATASET_ALIASES", tracing.WRITING) as i14:
            try:
                _ds = regenerate_dataset_aliases(
                    workspace,
                    lambda terms: extract_dataset_aliases(terms, llm_client, model, language=language),
                )
                i14.set(regenerated=_ds is not None)
                if _ds:
                    _cb(M_("🔤 Generated aliases for %(n)s data term(s)", n=len(_ds.aliases)))
                    _report_new_aliases(_cb, _ds.new)
            except Exception as exc:  # noqa: BLE001 — secondary artifact, must not fail the scan
                i14.set(error=str(exc))
                logger.warning("dataset alias generation failed: %s", exc)

        ingested = sum(1 for r in results if r.status == "ingested")
        skipped  = sum(1 for r in results if r.status == "skipped")
        failed   = sum(1 for r in results if r.status == "failed")
        _cb(M_("🏁 Scan complete — ingested: %(ingested)s, skipped: %(skipped)s, failed: %(failed)s",
               ingested=ingested, skipped=skipped, failed=failed))

        # The "See also" cross-links and the lint and repair of the pages this scan
        # wrote run after it, in the final review of `services.ingest.scan_sources`,
        # the same review the upload ends with.

    return results


def crosslink_wiki_pages(
    workspace: Path,
    db_path: str,
    language: str = "en",
    progress_cb: Callable[[str], None] | None = None,
    touched: Iterable[str] | None = None,
) -> int:
    """Inject a localized "See also" section into concept/summary pages.

    Runs as a final ingestion pass, after all documents are ingested, so
    cross-links are complete regardless of ingest order (a page written early
    can still link to a concept extracted from a later document). Deterministic —
    no LLM. Idempotent: ``inject_see_also`` skips pages already linked, so
    re-running adds nothing. Returns the number of pages whose content changed.

    ``touched`` narrows the pass to one ingest. It is the set of wiki pages that
    ingest wrote (relative paths such as ``wiki/concepts/x.md``; a leading
    ``/`` is tolerated). The pages rewritten are then those pages plus every
    page whose text mentions one of them, found with one substring scan per
    page, so the pass costs O(touched × pages) instead of the O(pages²)
    regular-expression work of the full sweep: measured, the full sweep took
    0.1 s on 73 pages and 7.2 s on 601, and it ran after every scan that
    ingested a single file. ``None`` keeps the full sweep, which the ingest
    app's wiki-wide button still uses. Every candidate is still matched against
    the whole page list, so the links it gains are the same as in a full sweep.

    This is the ingestion counterpart of the chat "Save to wiki" cross-linking
    (``wiki_tools.save_to_wiki`` → ``inject_see_also``); without it, pipeline-
    generated concept pages never link to one another.
    """
    def _rel_path(cur_in_concepts: bool, other_rel: str) -> str:
        stem = Path(other_rel).stem
        other_in_concepts = "/concepts/" in other_rel
        if cur_in_concepts == other_in_concepts:
            return f"{stem}.md"
        return f"../summaries/{stem}.md" if cur_in_concepts else f"../concepts/{stem}.md"

    conn = open_db(db_path)
    try:
        rows = conn.execute(
            "SELECT relative_path, title, tags, content FROM documents "
            "WHERE source_kind='wiki' AND status='ready' "
            "AND (relative_path LIKE 'wiki/concepts/%' "
            "     OR relative_path LIKE 'wiki/summaries/%')"
        ).fetchall()
    finally:
        conn.close()

    pages = [dict(r) for r in rows]
    targets = pages if touched is None else _crosslink_candidates(pages, touched)
    updated = 0
    for page in targets:
        cur_in_concepts = "/concepts/" in page["relative_path"]
        related = [
            {"title": o["title"], "rel_path": _rel_path(cur_in_concepts, o["relative_path"])}
            for o in pages
            if o["relative_path"] != page["relative_path"]
        ]
        new_content = inject_see_also(page["content"], related, language=language)
        if new_content == page["content"]:
            continue
        slug = Path(page["relative_path"]).stem
        dir_path = "/wiki/concepts/" if cur_in_concepts else "/wiki/summaries/"
        tags = json.loads(page["tags"]) if page["tags"] else []
        create_page(
            db_path, workspace, dir_path, slug, page["title"], new_content, tags,
            overwrite=True,
        )
        updated += 1
        if progress_cb:
            progress_cb(M_("🔗 cross-linked %(page)s", page=page['relative_path']))
    return updated


def _crosslink_candidates(pages: list[dict], touched: Iterable[str]) -> list[dict]:
    """The pages an ingest can have changed the links of: the pages it wrote,
    plus every page whose text mentions one of them by slug (the same
    hyphens-to-spaces, accent-stripped text ``inject_see_also`` matches on).

    The normalisation has to stay the same as ``inject_see_also``'s: this set is
    a superset of the pages that end up linked only while both halves compare
    the same string, and a page left out here is never offered a link."""
    wanted = {t.lstrip("/") for t in touched}
    slug_texts = {Path(t).stem.replace("-", " ").lower() for t in wanted}
    if not wanted:
        return []
    candidates = []
    for page in pages:
        if page["relative_path"] in wanted:
            candidates.append(page)
            continue
        text = strip_accents((page["content"] or "").lower())
        if any(slug in text for slug in slug_texts):
            candidates.append(page)
    return candidates


def regenerate_wiki_pages(
    workspace: Path,
    db_path: str,
    llm_client,
    model: str,
    progress_cb: Callable[[str], None] | None = None,
    language: str = "en",
) -> list[IngestResult]:
    """Regenerate summary pages for all ready source documents without re-extracting.

    Each page is built on the path a new file's ingestion uses for its summary:
    ``extract_structured`` (the content-language directive in the prompt) then
    ``build_summary_page`` (localized headings and labels), so a regenerated page
    follows the wiki ``language``. Only concepts whose page already exists are
    linked: regeneration writes no concept pages, and a link to a missing one
    would dangle.
    """
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
        _cb(M_("Found %(n)s source document(s)", n=len(docs)))

        results: list[IngestResult] = []
        for doc in docs:
            doc_id = doc["id"]
            filename = doc["filename"]
            file_path = workspace / "sources" / filename
            _cb(M_("🤖 Regenerating wiki page for %(file)s...", file=filename))
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
                extraction = extract_structured(
                    doc_meta, page_contents, llm_client, model, language=language
                )
                extraction.concepts = [
                    c for c in extraction.concepts
                    if read_page(db_path, workspace, "/wiki/concepts/", make_wiki_slug(c.name))
                ]
                wiki_markdown = build_summary_page(
                    doc_meta, extraction, language=language,
                    figures=load_figures(workspace, doc_id, with_descriptions=effective_config(
                        load_images_config(workspace), filename).describe),
                    doc_id=doc_id,
                )
                wiki_slug = make_wiki_slug(filename)
                regenerated = create_page(
                    db_path, workspace, "/wiki/summaries/", wiki_slug,
                    _title_from_filename(filename), wiki_markdown, [], overwrite=True,
                    clear_stale=True,
                    source_document_id=doc_id,
                    topics=extraction.document_topics,
                )
                _report_new_topics(_cb, [(f"summaries/{wiki_slug}", t) for t in regenerated["new_topics"]],
                                   "\n\n".join(md for _, md in page_contents), language)
                _cb(f"  ✅ {filename} → wiki/summaries/{wiki_slug}.md")
                results.append(IngestResult(file_path, "ingested", "wiki page regenerated", doc_id))
            except Exception as exc:
                logger.exception("Wiki regen failed for %s", filename)
                _cb(f"  ❌ {filename} — {exc}")
                results.append(IngestResult(file_path, "failed", str(exc), doc_id))

        _cb(M_("🏁 Regeneration complete"))
        return results
    finally:
        conn.close()
