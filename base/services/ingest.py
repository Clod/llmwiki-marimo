"""Ingestion and maintenance of a wiki, for any user interface.

`ingest_uploads` is the upload path of the ingest app: it ingests each file,
then reconciles the pages it touched (node I15) and refreshes their "See also"
cross-links (node I16), all under one `ingest` root span. The other functions
are the maintenance passes and the read-only views of the sources.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from domain import tracing
from domain.tools.db import get_connection
from services.wiki import Wiki

Progress = Callable[[str], None]


def _noop(_: str) -> None:
    pass


@dataclass(frozen=True)
class Upload:
    """One uploaded file: its name and its bytes."""

    name: str
    contents: bytes


def related_pages(db_path: str, source_ids: Iterable[str]) -> set[str]:
    """The wiki pages an ingestion touched: the summaries of the ingested
    sources plus every wiki page that cites them, as `/wiki/.../x.md`."""
    ids = list(source_ids)
    if not ids:
        return set()
    placeholders = ",".join("?" * len(ids))
    paths: set[str] = set()
    with get_connection(db_path) as conn:
        for row in conn.execute(
            f"SELECT path || filename AS p FROM documents "
            f"WHERE source_kind='wiki' AND source_document_id IN ({placeholders})",
            ids,
        ).fetchall():
            paths.add(row["p"])
        for row in conn.execute(
            f"SELECT d.path || d.filename AS p FROM document_references dr "
            f"JOIN documents d ON dr.source_document_id = d.id "
            f"WHERE dr.target_document_id IN ({placeholders}) "
            f"AND dr.reference_type = 'cites' AND d.source_kind = 'wiki'",
            ids,
        ).fetchall():
            paths.add(row["p"])
    return paths


def ingest_uploads(
    wiki: Wiki, uploads: Iterable[Upload], client, model: str, *,
    full_repair: bool = False, progress: Progress = _noop,
) -> list:
    """Ingest uploaded files, then reconcile and cross-link the pages they touched.

    `full_repair` picks the reconciliation depth: False runs the deterministic
    lint and repair only (no model calls); True also runs the model checks and
    repairs. The pass is scoped to the pages this ingestion touched, and the
    `orphan` check is excluded: a concept page this ingestion created may have
    no inbound link yet, and its repair would delete it.
    """
    from domain.ingestion import crosslink_wiki_pages, ingest_file
    from domain.lint.report import LintReport
    from domain.lint.runner import lint_wiki
    from domain.repair.runner import repair_wiki

    uploads = list(uploads)
    results: list = []
    with tracing.root("ingest", wiki.path, kind="upload", files=len(uploads)):
        for upload in uploads:
            path = wiki.sources_dir / upload.name
            if not path.exists():
                path.write_bytes(upload.contents)
            results.append(ingest_file(path, wiki.db_path, wiki.path, client, model,
                                       progress, language=wiki.language))

        source_ids = [r.doc_id for r in results if r.status == "ingested" and r.doc_id]
        touched = related_pages(wiki.db_path, source_ids)
        repair_client = client if full_repair else None
        mode = "full LLM" if full_repair else "deterministic"
        progress(f"🩺 Running {mode} lint on {len(touched)} ingested page(s)…")
        with tracing.node("I15", tracing.WRITING, mode=mode, pages=len(touched)) as i15:
            report = lint_wiki(wiki.db_path, wiki.path, client=repair_client, model=model,
                               progress_cb=progress)
            fixable = [i for i in report.issues if i.check != "orphan" and i.page in touched]
            i15.set(issues=len(report.issues), fixable=len(fixable))
            if fixable:
                progress(f"🔧 {len(fixable)} issue(s) on ingested pages — repairing ({mode})…")
                repair_wiki(LintReport(issues=fixable, checked_at=report.checked_at),
                            wiki.db_path, wiki.path, llm_client=repair_client, model=model,
                            progress_cb=progress, language=wiki.language)
            else:
                progress("✅ Ingested pages consistent — no repairs needed.")

        # Refresh "See also" cross-links: an older page may reference a newly
        # added concept. Scoped to the touched pages; the wiki-wide sweep is
        # quadratic in the page count and belongs to the wiki-wide button.
        if source_ids:
            with tracing.node("I16", tracing.WRITING, touched=len(touched)) as i16:
                linked = crosslink_wiki_pages(wiki.path, wiki.db_path, language=wiki.language,
                                              progress_cb=progress, touched=touched)
                i16.set(linked=linked)
            if linked:
                progress(f"🔗 Cross-linked {linked} page(s)")
    return results


def lint_and_repair(wiki: Wiki, client, model: str, *, progress: Progress = _noop) -> int:
    """Wiki-wide lint, then repair of every issue found; return the issue count."""
    from domain.lint.runner import lint_wiki
    from domain.repair.runner import repair_wiki

    report = lint_wiki(wiki.db_path, wiki.path, client=client, model=model, progress_cb=progress)
    if not report.issues:
        progress("✅ No issues found.")
        return 0
    progress(f"🔧 Found {len(report.issues)} issue(s) — running repairs…")
    repair_wiki(report, wiki.db_path, wiki.path, llm_client=client, model=model,
                progress_cb=progress, language=wiki.language)
    return len(report.issues)


def delete_stale_pages(wiki: Wiki) -> tuple[int, list[str]]:
    """Delete every page marked stale; return (deleted count, one line per page).

    `delete_page` also strips the inbound links other pages carry to the page
    and drops its entry from index.md, so no lint pass is needed afterwards.
    One failing page does not stop the rest.
    """
    from domain.tools.references import find_stale_pages
    from domain.tools.wiki_fs import delete_page

    lines: list[str] = []
    deleted = 0
    for page in find_stale_pages(wiki.db_path):
        slug = page["filename"].removesuffix(".md")
        try:
            gone = delete_page(wiki.db_path, wiki.path, page["path"], slug)
        except Exception as exc:  # noqa: BLE001 — one bad page must not stop the rest
            lines.append(f"❌ {page['filename']} — {exc}")
            continue
        deleted += int(gone)
        lines.append(f"{'🗑' if gone else '⚠️'} {page['path']}{page['filename']}"
                     f"{'' if gone else ' — not found on disk'}")
    return deleted, lines


def list_sources(wiki: Wiki) -> list[dict]:
    """The indexed source documents, by filename."""
    with get_connection(wiki.db_path) as conn:
        rows = conn.execute(
            "SELECT id, filename, status, page_count, parser, error_message, updated_at "
            "FROM documents WHERE source_kind='source' ORDER BY filename"
        ).fetchall()
    return [dict(row) for row in rows]


def wiki_stats(wiki: Wiki) -> dict[str, int]:
    """The number of source documents and of wiki pages in the index."""
    with get_connection(wiki.db_path) as conn:
        sources = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE source_kind='source'").fetchone()[0]
        pages = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE source_kind='wiki'").fetchone()[0]
    return {"sources": sources, "pages": pages}
