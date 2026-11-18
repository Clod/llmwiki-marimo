"""The workflow of the ingestion of one file: `_ingest_file` as a graph.

The steps are the blocks of the ingestion, in order, each one under the node of the
writing diagram that it records (`docs/ingestion_nodes.md`): I_VALIDATE, I_IS_CHANGED,
I_DOCUMENT_ROW, I_EXTRACT, W_CHUNK, W_STEM, W_INSERT_CHUNKS, I_CONCEPTS, I_CONCEPT_PAGES,
I_ALIASES, I_SUMMARY_PAGE, I_IS_BATCH, I_OVERVIEW, I_LOG, I_COMMIT and I_LINT. I_DONE
reports the end of a run; it records no node.

The reply of the extraction (I_CONCEPTS) is checked: when it is not valid JSON, the graph
asks the model again for the JSON alone (I_CONCEPTS_RETRY). When the retry is not valid
JSON either, I_EXTRACTION_FAILED raises `ExtractionError`, and the run fails with no page
written: before this decision, the raw reply was written as the summary page. The decisions record nothing in the trace:
the trace of an ingestion is the one it was before the graph.

A step that raises leaves the graph. `pipeline._ingest_file` catches the exception after
I_DOCUMENT_ROW, rolls back the wiki pages that the run wrote, and marks the document
`status='failed'` (E_ROLLED_BACK). The graph is built with `auto_instrument=False`.
The ingestion is synchronous: `_ingest_file` runs the graph with `asyncio.run`.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic_graph import GraphBuilder, StepContext

from domain import tracing
from domain.i18n import get_locale
from domain.messages import M_
from domain.text.stemming import stem_text
from domain.tools.db import open_db

logger = logging.getLogger("domain.ingestion.pipeline")
W = tracing.WRITING


@dataclass
class IngestFile:
    """The state of the ingestion of one file: the arguments of `_ingest_file` and the
    values that its steps share. `result` is the `IngestResult` of the run, set by the
    step that ends it."""

    file_path: Path
    db_path: str
    workspace: Path
    llm_client: Any
    model: str
    cb: Callable[[str], None]
    lint_after_ingest: bool
    batch_mode: bool
    language: str
    doc: Any
    vision: Any = None
    error: str | None = None
    ext: str = ""
    conn: Any = None
    user_id: str = ""
    relative: str = ""
    should_process: bool = False
    current_hash: str = ""
    stat: Any = None
    doc_id: str | None = None
    # True from I_DOCUMENT_ROW on: an exception then rolls back the run.
    started: bool = False
    images_config: Any = None
    page_contents: list = field(default_factory=list)
    parser: str = ""
    image_pass: Any = None
    full_content: str = ""
    chunks: list = field(default_factory=list)
    stemmed: list = field(default_factory=list)
    doc_meta: dict = field(default_factory=dict)
    extraction: Any = None
    concept_slugs: list = field(default_factory=list)
    new_topics: list = field(default_factory=list)
    # Wiki pages created or overwritten by I_CONCEPT_PAGES and I_SUMMARY_PAGE, for the rollback.
    wiki_compensations: list = field(default_factory=list)
    result: Any = None


Ctx = StepContext[IngestFile, None, IngestFile]

g = GraphBuilder(name="ingest_file", state_type=IngestFile, input_type=IngestFile,
                 output_type=IngestFile, auto_instrument=False)


def _pipeline():
    # The helpers live in `pipeline`; imported here, at run time, to avoid an import cycle.
    from domain.ingestion import pipeline
    return pipeline


async def validate(ctx: Ctx) -> IngestFile:
    p, s = _pipeline(), ctx.state
    s.cb(M_("🔍 Validating %(file)s", file=s.file_path.name))
    s.error = p._validation_error(s.file_path, images=not p.load_images_config(s.workspace).ignore)
    tracing.mark("I_VALIDATE", W, answer=s.error is None, error=s.error)
    if s.error is not None:
        s.result = p.IngestResult(s.file_path, "failed", s.error)
    s.ext = s.file_path.suffix.lower()
    return s


async def detect_changes(ctx: Ctx) -> IngestFile:
    p, s = _pipeline(), ctx.state
    p._init_wiki_workspace(s.workspace, s.language)
    s.conn = open_db(s.db_path)
    s.user_id = p._get_user_id(s.conn)
    s.relative = f"sources/{s.file_path.name}"
    s.should_process, s.current_hash = p.needs_ingestion(s.file_path, s.conn)
    tracing.mark("I_IS_CHANGED", W, answer=s.should_process, content_hash=s.current_hash)
    if not s.should_process:
        s.cb(M_("⏭ %(file)s — already up to date", file=s.file_path.name))
        s.result = p.IngestResult(s.file_path, "skipped", "already up to date")
    return s


async def document_row(ctx: Ctx) -> IngestFile:
    """Upsert the source document record, with status 'processing'."""
    p, s = _pipeline(), ctx.state
    conn, file_path = s.conn, s.file_path
    existing = conn.execute("SELECT id FROM documents WHERE relative_path = ?", (s.relative,)).fetchone()
    s.stat = stat = file_path.stat()
    if existing:
        s.doc_id = existing["id"]
        conn.execute(
            "UPDATE documents SET status='processing', content_hash=?, mtime_ns=?, "
            "last_indexed_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
            (s.current_hash, stat.st_mtime_ns, s.doc_id),
        )
    else:
        s.doc_id = str(uuid.uuid4())
        doc_number = p._next_doc_number(conn)
        title = file_path.stem.replace("-", " ").replace("_", " ").title()
        conn.execute(
            "INSERT INTO documents "
            "(id, user_id, filename, title, path, relative_path, source_kind, "
            "file_type, file_size, status, document_number, content_hash, mtime_ns, "
            "last_indexed_at) "
            "VALUES (?,?,?,?,'sources/',?,?,?,?,?,?,?,?,datetime('now'))",
            (s.doc_id, s.user_id, file_path.name, title, s.relative,
             "source", s.ext.lstrip("."), stat.st_size,
             "processing", doc_number, s.current_hash, stat.st_mtime_ns),
        )
    conn.commit()
    tracing.mark("I_DOCUMENT_ROW", W, document_id=s.doc_id, relative_path=s.relative)
    s.doc.set(document_id=s.doc_id, relative_path=s.relative)
    s.llm_client = tracing.wrap_openai(s.llm_client)
    s.cb(M_("⚙️ Extracting text from %(file)s", file=file_path.name))
    s.started = True
    return s


async def extract_text(ctx: Ctx) -> IngestFile:
    p, s = _pipeline(), ctx.state
    cache_dir = s.workspace / ".llmwiki" / "cache" / "local" / s.doc_id
    with tracing.node("I_EXTRACT", W) as i4:
        s.images_config = p.effective_config(p.load_images_config(s.workspace), s.file_path.name)
        s.page_contents, s.parser = p.extract(s.file_path, cache_dir, s.language, s.cb,
                                              keep_images=not s.images_config.ignore)
        if s.images_config.describe and s.vision is None:
            s.vision = p.default_vision()
            if s.vision is None:
                logger.warning("[images] describe is on, but no vision model is configured")
        s.image_pass = p.describe_images(
            s.page_contents, workspace=s.workspace, doc_id=s.doc_id, language=s.language,
            config=s.images_config, vision=s.vision)
        s.page_contents = s.image_pass.pages
        s.full_content = "\n\n---\n\n".join(md for _, md in s.page_contents)
        # Trace intermediate data so the path can be followed end to end.
        i4.payload("extracted_text", s.full_content, channel="extracted_text")
        i4.set(page_count=len(s.page_contents), parser=s.parser)
        if s.image_pass.images:
            i4.set(images=s.image_pass.images, images_described=s.image_pass.described)
    s.cb(M_("✅ Extracted %(n)s pages", n=len(s.page_contents)))
    return s


async def chunk(ctx: Ctx) -> IngestFile:
    p, s = _pipeline(), ctx.state
    with tracing.node("W_CHUNK", W, document_id=s.doc_id) as w1:
        s.chunks = p.chunk_pages(s.page_contents)
        w1.payload(
            "chunks",
            json.dumps(
                [{"chunk_index": c.index, "page": c.page,
                  "token_count": c.token_count, "start_char": c.start_char,
                  "content": c.content} for c in s.chunks],
                ensure_ascii=False, indent=2,
            ),
            channel="chunks", ext="json",
        )
        w1.set(fragments=len(s.chunks))
    s.cb(M_("✂️ Chunked into %(n)s chunks", n=len(s.chunks)))
    return s


async def stem(ctx: Ctx) -> IngestFile:
    """The search index keeps each fragment reduced to stems in the wiki language."""
    s = ctx.state
    with tracing.node("W_STEM", W, document_id=s.doc_id, fragments=len(s.chunks), language=s.language):
        s.stemmed = [stem_text(c.content, s.language) for c in s.chunks]
    return s


async def insert_chunks(ctx: Ctx) -> IngestFile:
    s = ctx.state
    conn, doc_id = s.conn, s.doc_id
    with conn:
        conn.execute(
            "UPDATE documents SET status='ready', content=?, page_count=?, "
            "parser=?, mtime_ns=?, updated_at=datetime('now') WHERE id=?",
            (s.full_content, len(s.page_contents), s.parser, s.stat.st_mtime_ns, doc_id),
        )
        conn.execute("DELETE FROM document_pages WHERE document_id=?", (doc_id,))
        conn.executemany(
            "INSERT INTO document_pages (id, document_id, page, content) VALUES (?,?,?,?)",
            [(str(uuid.uuid4()), doc_id, pn, md) for pn, md in s.page_contents],
        )
        conn.execute("DELETE FROM document_chunks WHERE document_id=?", (doc_id,))
        conn.executemany(
            "INSERT INTO document_chunks "
            "(id, document_id, chunk_index, content, content_stemmed, page, "
            "start_char, token_count, header_breadcrumb) VALUES (?,?,?,?,?,?,?,?,?)",
            [(str(uuid.uuid4()), doc_id, c.index, c.content, stemmed, c.page,
              c.start_char, c.token_count, c.header_breadcrumb)
             for c, stemmed in zip(s.chunks, s.stemmed)],
        )
    tracing.mark("W_INSERT_CHUNKS", W, document_id=doc_id, fragments=len(s.chunks))
    conn.close()
    s.conn = None
    s.doc_meta = {
        "filename": s.file_path.name,
        "file_type": s.ext.lstrip("."),
        "page_count": len(s.page_contents),
        "parser": s.parser,
    }
    return s


async def concepts(ctx: Ctx) -> IngestFile:
    p, s = _pipeline(), ctx.state
    s.cb(M_("🤖 Extracting knowledge structure..."))
    with tracing.node("I_CONCEPTS", W) as i5:
        known = p.known_concepts(s.workspace, s.language)
        s.extraction = p.extract_structured(s.doc_meta, s.page_contents, s.llm_client, s.model,
                                            language=s.language, known=known)
        i5.set(concepts=[c.name for c in s.extraction.concepts], known=len(known))
    s.cb(M_("📋 Found %(n)s concept(s)", n=len(s.extraction.concepts)))
    return s


class ExtractionError(Exception):
    """The reply of the extraction is not valid JSON, after the retry."""


async def concepts_retry(ctx: Ctx) -> IngestFile:
    """I_CONCEPTS_RETRY: the reply of I_CONCEPTS did not parse; the model receives its reply
    and an instruction to return only the JSON."""
    p, s = _pipeline(), ctx.state
    s.cb(M_("🔁 The extraction is not valid JSON: asking the model again"))
    with tracing.node("I_CONCEPTS_RETRY", W, first_reply=s.extraction.raw[:500]) as retry:
        s.extraction = p.extract_structured(s.doc_meta, s.page_contents, s.llm_client, s.model,
                                            language=s.language, known=p.known_concepts(s.workspace, s.language),
                                            previous=s.extraction.raw)
        retry.set(parsed=s.extraction.parsed, concepts=[c.name for c in s.extraction.concepts])
    if s.extraction.parsed:
        s.cb(M_("📋 Found %(n)s concept(s)", n=len(s.extraction.concepts)))
    return s


async def extraction_failed(ctx: Ctx) -> IngestFile:
    """The retry did not parse either: the run fails, with no page written."""
    raise ExtractionError("the reply of the extraction is not valid JSON, after one retry")


async def concept_pages(ctx: Ctx) -> IngestFile:
    p, s = _pipeline(), ctx.state
    db_path, workspace, file_path = s.db_path, s.workspace, s.file_path
    with tracing.node("I_CONCEPT_PAGES", W, concept_count=len(s.extraction.concepts)) as i6:
        for concept in s.extraction.concepts:
            slug = p.make_wiki_slug(concept.name)
            s.concept_slugs.append(slug)
            existing_content = p.read_page(db_path, workspace, "/wiki/concepts/", slug)
            s.cb(M_("💡 Updating concept: %(name)s", name=concept.name) if existing_content
                 else M_("💡 Creating concept: %(name)s", name=concept.name))
            concept_md = p.build_concept_page(
                concept, file_path.name, existing_content, s.llm_client, s.model, language=s.language,
                image_descriptions=s.image_pass.described > 0,
            )
            prior = p._snapshot_wiki_page(db_path, f"wiki/concepts/{slug}.md")
            concept_result = p.create_page(
                db_path, workspace, "/wiki/concepts/", slug,
                concept.name, concept_md, [concept.category], overwrite=True,
                clear_stale=True,
                sources=[file_path.name],
                topics=concept.topics,
                description=concept.description,
            )
            s.new_topics.extend((f"concepts/{slug}", t) for t in concept_result["new_topics"])
            s.wiki_compensations.append({
                "dir_path": "/wiki/concepts/", "slug": slug,
                "category": "concepts", "prior": prior,
            })
            written = p.read_page(db_path, workspace, "/wiki/concepts/", slug) or concept_md
            p.update_references(db_path, concept_result["id"], written, "/wiki/concepts/")
            i6.payload(f"concept.{slug}", concept_md, channel="markdown", ext="md")
            one_liner = concept.insight or concept.name
            p.update_index(workspace, f"concepts/{slug}.md", one_liner, "concepts", language=s.language)
        i6.set(pages=[f"wiki/concepts/{slug}.md" for slug in s.concept_slugs])
    return s


async def aliases(ctx: Ctx) -> IngestFile:
    p, s = _pipeline(), ctx.state
    with tracing.node("I_ALIASES", W) as i7:
        try:
            alias_result = p.update_generated_aliases(
                s.workspace, [(c.name, c.aliases) for c in s.extraction.concepts],
            )
            i7.set(new_aliases=len(alias_result.new))
            p._report_new_aliases(s.cb, alias_result.new)
        except Exception as exc:  # noqa: BLE001 — secondary artifact, must not fail ingest
            i7.set(error=str(exc))
            logger.warning("alias generation failed for %s: %s", s.file_path.name, exc)
    return s


async def summary_page(ctx: Ctx) -> IngestFile:
    p, s = _pipeline(), ctx.state
    db_path, workspace, file_path = s.db_path, s.workspace, s.file_path
    s.cb(M_("📄 Writing summary page..."))
    with tracing.node("I_SUMMARY_PAGE", W) as i8:
        summary_md = p.build_summary_page(
            s.doc_meta, s.extraction, language=s.language,
            figures=p.load_figures(workspace, s.doc_id, with_descriptions=s.images_config.describe),
            doc_id=s.doc_id,
        )
        wiki_slug = p.make_wiki_slug(file_path.name)
        prior_summary = p._snapshot_wiki_page(db_path, f"wiki/summaries/{wiki_slug}.md")
        summary_result = p.create_page(
            db_path, workspace, "/wiki/summaries/", wiki_slug,
            p._title_from_filename(file_path.name), summary_md, [],
            overwrite=True, source_document_id=s.doc_id, clear_stale=True,
            topics=s.extraction.document_topics,
        )
        s.new_topics.extend((f"summaries/{wiki_slug}", t) for t in summary_result["new_topics"])
        i8.set(new_topics=len(s.new_topics))
        p._report_new_topics(s.cb, s.new_topics, s.full_content, s.language)
        s.wiki_compensations.append({
            "dir_path": "/wiki/summaries/", "slug": wiki_slug,
            "category": "summaries", "prior": prior_summary,
        })
        p.update_references(db_path, summary_result["id"], summary_md, "/wiki/summaries/")
        i8.payload("summary", summary_md, channel="markdown", ext="md")
        i8.set(page=f"wiki/summaries/{wiki_slug}.md")
        p.update_index(
            workspace, f"summaries/{wiki_slug}.md",
            s.extraction.document_summary or file_path.name, "summaries", language=s.language,
        )
    return s


async def is_batch(ctx: Ctx) -> IngestFile:
    """A batch runs I_OVERVIEW, I_LOG, I_COMMIT and I_LINT once, for the whole batch."""
    s = ctx.state
    tracing.mark("I_IS_BATCH", W, answer=s.batch_mode)
    return s


async def overview(ctx: Ctx) -> IngestFile:
    p, s = _pipeline(), ctx.state
    s.cb(M_("🌐 Updating overview..."))
    with tracing.node("I_OVERVIEW", W) as i10:
        current_overview = (s.workspace / "wiki" / "overview.md").read_text(encoding="utf-8")
        all_concept_names = p._all_concept_names(s.db_path)
        new_overview = p.update_overview(
            current_overview, s.extraction.document_summary,
            all_concept_names, s.llm_client, s.model, language=s.language,
        )
        (s.workspace / "wiki" / "overview.md").write_text(new_overview, encoding="utf-8")
        i10.payload("overview", new_overview, channel="markdown", ext="md")
    return s


async def log(ctx: Ctx) -> IngestFile:
    p, s = _pipeline(), ctx.state
    image_pass = s.image_pass
    image_line = (get_locale(s.language).image_counts.format(
        images=image_pass.images, described=image_pass.described) + "\n"
        if image_pass.images else "")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    log_entry = (
        f"## [{timestamp}] Ingested | {s.file_path.name}\n\n"
        f"{s.extraction.document_summary[:200]}\n\n"
        f"Concepts: {', '.join(c.name for c in s.extraction.concepts) or 'none'}\n"
        f"{image_line}"
    )
    with tracing.node("I_LOG", W):
        p.append_to_page(s.db_path, s.workspace, "/wiki/", "log", log_entry)
    return s


async def commit(ctx: Ctx) -> IngestFile:
    p, s = _pipeline(), ctx.state
    with tracing.node("I_COMMIT", W):
        p.init_wiki_repo(s.workspace)
        p.auto_commit(s.workspace, f"ingest: {s.file_path.name}")
    return s


async def lint(ctx: Ctx) -> IngestFile:
    p, s = _pipeline(), ctx.state
    from domain.lint.runner import lint_wiki
    s.cb(M_("🩺 Running lint checks..."))
    with tracing.node("I_LINT", W) as i13:
        lint_report = lint_wiki(s.db_path, s.workspace)
        i13.set(issues=len(lint_report.issues))
    if lint_report.issues:
        lint_lines = "\n".join(
            f"- [{i.severity.upper()}] {i.check}: {i.description}" for i in lint_report.issues
        )
        p.append_to_page(
            s.db_path, s.workspace, "/wiki/", "log",
            f"### Lint: {lint_report.summary()}\n\n{lint_lines}\n",
        )
    s.cb(M_("🩺 Lint: %(summary)s", summary=lint_report.summary()))
    return s


async def done(ctx: Ctx) -> IngestFile:
    p, s = _pipeline(), ctx.state
    image_pass = s.image_pass
    counts = (", " + get_locale(s.language).image_counts.format(
        images=image_pass.images, described=image_pass.described) if image_pass.images else "")
    s.cb(M_("✅ Done: %(file)s (%(pages)s pages, %(chunks)s chunks%(counts)s)", file=s.file_path.name,
            pages=len(s.page_contents), chunks=len(s.chunks), counts=counts))
    s.result = p.IngestResult(s.file_path, "ingested", f"{len(s.page_contents)} pages", s.doc_id)
    return s


steps = {code: g.step(fn, node_id=code) for code, fn in (
    ("I_VALIDATE", validate), ("I_IS_CHANGED", detect_changes), ("I_DOCUMENT_ROW", document_row),
    ("I_EXTRACT", extract_text), ("W_CHUNK", chunk), ("W_STEM", stem), ("W_INSERT_CHUNKS", insert_chunks),
    ("I_CONCEPTS", concepts), ("I_CONCEPTS_RETRY", concepts_retry), ("I_EXTRACTION_FAILED", extraction_failed),
    ("I_CONCEPT_PAGES", concept_pages), ("I_ALIASES", aliases),
    ("I_SUMMARY_PAGE", summary_page), ("I_IS_BATCH", is_batch), ("I_OVERVIEW", overview),
    ("I_LOG", log), ("I_COMMIT", commit), ("I_LINT", lint), ("I_DONE", done),
)}
STEPS = tuple(steps)

lint_after = (g.decision(node_id="after_ingest", note="Does the run lint the wiki?")
              .branch(g.match(IngestFile, matches=lambda s: s.lint_after_ingest and not s.batch_mode)
                      .label("lint after the ingestion").to(steps["I_LINT"]))
              .branch(g.match(IngestFile).label("no lint").to(steps["I_DONE"])))

g.add(
    g.edge_from(g.start_node).to(steps["I_VALIDATE"]),
    g.edge_from(steps["I_VALIDATE"]).to(
        g.decision(node_id="after_I_VALIDATE", note="Is the file valid?")
        .branch(g.match(IngestFile, matches=lambda s: s.error is not None).label("invalid file").to(g.end_node))
        .branch(g.match(IngestFile).label("valid file").to(steps["I_IS_CHANGED"]))
    ),
    g.edge_from(steps["I_IS_CHANGED"]).to(
        g.decision(node_id="after_I_IS_CHANGED", note="Did the file change since its last ingestion?")
        .branch(g.match(IngestFile, matches=lambda s: not s.should_process).label("unchanged").to(g.end_node))
        .branch(g.match(IngestFile).label("new or changed").to(steps["I_DOCUMENT_ROW"]))
    ),
    *(g.edge_from(steps[a]).to(steps[b]) for a, b in (
        ("I_DOCUMENT_ROW", "I_EXTRACT"), ("I_EXTRACT", "W_CHUNK"), ("W_CHUNK", "W_STEM"),
        ("W_STEM", "W_INSERT_CHUNKS"), ("W_INSERT_CHUNKS", "I_CONCEPTS"),
        ("I_CONCEPT_PAGES", "I_ALIASES"), ("I_ALIASES", "I_SUMMARY_PAGE"), ("I_SUMMARY_PAGE", "I_IS_BATCH"),
        ("I_OVERVIEW", "I_LOG"), ("I_LOG", "I_COMMIT"), ("I_LINT", "I_DONE"),
    )),
    g.edge_from(steps["I_IS_BATCH"]).to(
        g.decision(node_id="after_I_IS_BATCH", note="Is the file part of a batch?")
        .branch(g.match(IngestFile, matches=lambda s: s.batch_mode).label("part of a batch").to(lint_after))
        .branch(g.match(IngestFile).label("a single file").to(steps["I_OVERVIEW"]))
    ),
    g.edge_from(steps["I_CONCEPTS"]).to(
        g.decision(node_id="after_I_CONCEPTS", note="Is the reply of the extraction valid JSON?")
        .branch(g.match(IngestFile, matches=lambda s: s.extraction.parsed).label("valid JSON")
                .to(steps["I_CONCEPT_PAGES"]))
        .branch(g.match(IngestFile).label("not valid JSON").to(steps["I_CONCEPTS_RETRY"]))
    ),
    g.edge_from(steps["I_CONCEPTS_RETRY"]).to(
        g.decision(node_id="after_I_CONCEPTS_RETRY", note="Is the reply of the retry valid JSON?")
        .branch(g.match(IngestFile, matches=lambda s: s.extraction.parsed).label("valid JSON")
                .to(steps["I_CONCEPT_PAGES"]))
        .branch(g.match(IngestFile).label("not valid JSON").to(steps["I_EXTRACTION_FAILED"]))
    ),
    g.edge_from(steps["I_EXTRACTION_FAILED"]).to(g.end_node),
    g.edge_from(steps["I_COMMIT"]).to(lint_after),
    g.edge_from(steps["I_DONE"]).to(g.end_node),
)

graph = g.build()
