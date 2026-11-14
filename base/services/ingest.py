"""Ingestion and maintenance of a wiki, for any user interface.

`ingest_uploads` is the upload path of the ingest app: it ingests each file,
then reconciles the pages it touched (node I15) and refreshes their "See also"
cross-links (node I16), all under one `ingest` root span. The other functions
are the maintenance passes and the read-only views of the sources.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from domain import tracing
from domain.tools.db import get_connection
from domain.verify.verifier import Verifier
from services.wiki import Wiki

Progress = Callable[[str], None]

# `orphan`: a concept page this ingestion created may have no inbound link yet, and
# its repair would delete it. The two verification checks are report only.
_NOT_REPAIRED_ON_INGEST = {"orphan", "unsupported_sentences", "unverified_pages",
                           "image_without_description", "stale_image_description"}


def _noop(_: str) -> None:
    pass


class InvalidUpload(ValueError):
    """An uploaded file the ingestion refuses: ``name`` is its cleaned name."""

    def __init__(self, name: str, message: str):
        super().__init__(message)
        self.name = name


class InvalidUploadName(InvalidUpload):
    """The name is empty, ``.``, ``..`` or hidden (starts with a dot)."""


class UnsupportedUploadType(InvalidUpload):
    """The extension is not one the ingestion reads."""


def clean_upload_name(name: str) -> str:
    """The bare file name of an uploaded file, safe to write under ``sources/``.

    Keeps the last path component (either separator), then refuses an empty
    name, ``.``, ``..`` and any name that starts with ``.``, and any extension
    outside ``SUPPORTED_EXTENSIONS``. Raises ``InvalidUpload``.
    """
    from domain.ingestion.formats import SUPPORTED_EXTENSIONS

    clean = PurePosixPath((name or "").replace("\\", "/")).name
    if not clean or clean.startswith("."):
        raise InvalidUploadName(clean, f"invalid file name: {name!r}")
    if PurePosixPath(clean).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise UnsupportedUploadType(
            clean, f"unsupported file type; supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    return clean


@dataclass(frozen=True)
class Upload:
    """One uploaded file: its name and its bytes.

    The name is cleaned on construction (``clean_upload_name``), so an ``Upload``
    never names a path outside ``sources/``; a refused name raises ``InvalidUpload``.
    """

    name: str
    contents: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", clean_upload_name(self.name))


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
    full_repair: bool = False, verifier: Verifier | None = None, progress: Progress = _noop,
    vision=None,
) -> list:
    """Ingest uploaded files, then reconcile and cross-link the pages they touched.

    `full_repair` picks the reconciliation depth: False runs the deterministic
    lint and repair only (no model calls); True also runs the model checks and
    repairs. The pass is scoped to the pages this ingestion touched, and the
    `orphan` check is excluded: a concept page this ingestion created may have
    no inbound link yet, and its repair would delete it. With `verifier`, each
    page the ingestion wrote is verified against its sources at the end, after
    the cross-links, which rewrite pages and delete their verification. `vision` is the
    vision model that describes the images of the sources when the wiki sets
    `[images] describe = true` (`domain.ingestion.describe.Vision`).
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
                                       progress, language=wiki.language,
                                       **({"vision": vision} if vision is not None else {})))

        source_ids = [r.doc_id for r in results if r.status == "ingested" and getattr(r, "doc_id", None)]
        touched = related_pages(wiki.db_path, source_ids)
        repair_client = client if full_repair else None
        mode = "full LLM" if full_repair else "deterministic"
        progress(f"🩺 Running {mode} lint on {len(touched)} ingested page(s)…")
        with tracing.node("I15", tracing.WRITING, mode=mode, pages=len(touched)) as i15:
            report = lint_wiki(wiki.db_path, wiki.path, client=repair_client, model=model,
                               progress_cb=progress)
            # The image checks report a source by its path under sources/.
            scope = touched | _source_paths(wiki.db_path, source_ids)
            fixable = [i for i in report.issues
                       if i.check not in _NOT_REPAIRED_ON_INGEST and i.page in scope]
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
        if verifier is not None and source_ids:
            _verify_written(wiki, source_ids, verifier, progress)
    return results


def _source_paths(db_path: str, source_ids: list[str]) -> set[str]:
    """The paths under sources/ of the given source documents."""
    if not source_ids:
        return set()
    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT relative_path FROM documents WHERE id IN ({','.join('?' * len(source_ids))})",
            source_ids).fetchall()
    return {r["relative_path"] for r in rows}


def _verify_written(wiki: Wiki, source_ids, verifier: Verifier, progress: Progress,
                    *, summaries_only: bool = False) -> None:
    from services.verification import verify_pages, written_pages

    pages = written_pages(wiki, source_ids, summaries_only=summaries_only)
    progress(f"🔎 Verifying {len(pages)} page(s) against their sources…")
    verify_pages(wiki, pages, verifier, progress=progress)


def scan_sources(wiki: Wiki, client, model: str, *, verifier: Verifier | None = None,
                 progress: Progress = _noop, vision=None) -> list:
    """Ingest the files of the sources folder that are new or changed since their
    last ingestion; return one result per file. Each ingested file commits the wiki.
    With `verifier`, the pages written are verified afterwards. `vision` describes
    the images (see `ingest_uploads`)."""
    from domain.ingestion import scan_and_ingest

    extra = {"vision": vision} if vision is not None else {}
    results = scan_and_ingest(wiki.path, wiki.db_path, client, model, progress, language=wiki.language,
                              **extra)
    source_ids = [r.doc_id for r in results if r.status == "ingested" and getattr(r, "doc_id", None)]
    if verifier is not None and source_ids:
        _verify_written(wiki, source_ids, verifier, progress)
    return results


def regenerate_summaries(wiki: Wiki, client, model: str, *, verifier: Verifier | None = None,
                         progress: Progress = _noop) -> list:
    """Regenerate the summary page of every ready source from its stored text,
    without extracting the text again; return one result per source.

    Only the summary pages are regenerated; the concept pages stay as they are.
    The generation is not localized yet (domain/ingestion/pipeline.py,
    `regenerate_wiki_pages`). The regenerated pages are committed to the wiki.
    With `verifier`, the regenerated summary pages are verified afterwards.
    """
    from domain.ingestion import regenerate_wiki_pages
    from domain.tools.git_ops import auto_commit, init_wiki_repo

    results = regenerate_wiki_pages(wiki.path, wiki.db_path, client, model, progress,
                                    language=wiki.language)
    regenerated = sum(r.status == "ingested" for r in results)
    if regenerated:
        init_wiki_repo(wiki.path)
        auto_commit(wiki.path, f"regenerate summaries: {regenerated}")
    source_ids = [r.doc_id for r in results if r.status == "ingested" and getattr(r, "doc_id", None)]
    if verifier is not None and source_ids:
        _verify_written(wiki, source_ids, verifier, progress, summaries_only=True)
    return results


def lint_and_repair(wiki: Wiki, client, model: str, *, verifier: Verifier | None = None,
                    progress: Progress = _noop) -> int:
    """Wiki-wide lint, then repair of every issue found; return the issue count.

    The repairs are committed to the wiki, so each one can be undone from its
    git history. With `verifier`, each page a repair wrote with the model (a
    regenerated summary, a created concept) is verified against its sources."""
    from domain.lint.runner import lint_wiki
    from domain.repair.runner import repair_wiki

    report = lint_wiki(wiki.db_path, wiki.path, client=client, model=model, progress_cb=progress)
    if not report.issues:
        progress("✅ No issues found.")
        return 0
    progress(f"🔧 Found {len(report.issues)} issue(s) — running repairs…")
    repairs = repair_wiki(report, wiki.db_path, wiki.path, llm_client=client, model=model,
                          progress_cb=progress, language=wiki.language)
    from domain.tools.git_ops import auto_commit, init_wiki_repo
    init_wiki_repo(wiki.path)
    auto_commit(wiki.path, f"lint & repair: {len(report.issues)} issue(s)")
    written = [r.written for r in repairs.results if r.success and r.written]
    if verifier is not None and written:
        from services.verification import verify_pages

        progress(f"🔎 Verifying {len(written)} page(s) the repairs wrote…")
        verify_pages(wiki, written, verifier, progress=progress)
    return len(report.issues)


def images_ignored(wiki: Wiki) -> bool:
    """True when the wiki sets `[images] ignore = true`, the default: the ingestion keeps
    no image and takes no image file as a source."""
    from domain.wiki_settings import load_images_config

    return load_images_config(wiki.path).ignore


def image_counts(wiki: Wiki):
    """The images of the wiki for the Maintain screen: kept, described, skipped by
    reason, without description, stale, and the calls "Describe the images" would make
    (`domain.ingestion.describe_pass.ImageCounts`)."""
    from domain.ingestion.describe_pass import image_counts as _image_counts

    return _image_counts(wiki.path, wiki.db_path)


def describe_images(wiki: Wiki, vision, *, progress: Progress = _noop):
    """Describe the images that have no valid description (the action "Describe the images"
    of the Maintain screen); return the `ImageCounts` afterwards. The summary pages and the
    stored text change, so the wiki is committed. The caller holds the wiki's lock."""
    from domain.ingestion.describe_pass import describe_wiki_images
    from domain.tools.git_ops import auto_commit, init_wiki_repo

    changed = describe_wiki_images(wiki.path, wiki.db_path, vision, progress)
    if changed:
        init_wiki_repo(wiki.path)
        auto_commit(wiki.path, f"describe images: {changed} source(s)")
    return image_counts(wiki)


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
    if deleted:
        from domain.tools.git_ops import auto_commit, init_wiki_repo
        init_wiki_repo(wiki.path)
        auto_commit(wiki.path, f"delete stale pages: {deleted}")
    return deleted, lines


def list_stale_pages(wiki: Wiki) -> list[dict]:
    """The pages marked stale: a source they cite was deleted, and they were not
    regenerated since. Each row has `id`, `filename`, `title`, `path` and
    `stale_since`."""
    from domain.tools.references import find_stale_pages

    return find_stale_pages(wiki.db_path)


def delete_source(wiki: Wiki, doc_id: str, *, also_delete_file: bool = False) -> tuple[bool, str]:
    """Delete a source document from the index; return (success, message).

    Its summary page is deleted, and the pages that cite it are kept and marked
    stale (domain/tools/deletion.py). With `also_delete_file` the file leaves
    the sources folder too. The deletion commits the wiki.
    """
    from domain.tools.deletion import delete_source as _delete_source

    result = _delete_source(wiki.db_path, wiki.path, doc_id, also_delete_file=also_delete_file)
    return result.success, result.message


def remove_source(wiki: Wiki, doc_id: str, *, also_delete_file: bool = False):
    """Delete a source as `delete_source` does, and return the `SourceDeletion`:
    the summary pages deleted, the pages marked stale, whether the file left
    sources/. The caller holds the wiki's lock."""
    from domain.tools.deletion import delete_source as _delete_source

    return _delete_source(wiki.db_path, wiki.path, doc_id, also_delete_file=also_delete_file)


def tool_status(language: str = "en") -> dict[str, object]:
    """The path of LibreOffice and of the Java runtime, or None when missing.

    Every ingestion needs Java: the text extractor runs a .jar. A DOCX also
    needs LibreOffice, which converts it to PDF. ``libreoffice`` is the path
    only when the converter is usable: it converted a probe document (the first
    call runs it, the process caches the result). An executable that cannot
    convert shows as ``libreoffice`` None with ``libreoffice_found`` set to the
    path, so a screen can tell "not installed" from "installed, does not convert".

    ``ocr`` is the OCR engine in force (``ocr.engine_status``): its ``label``,
    ``usable`` and, when not usable, ``problem``. `language` is the wiki
    language, which selects the Tesseract language pack to look for.
    """
    from domain.ingestion import extractor, ocr

    found = extractor.check_libreoffice()
    usable = extractor.libreoffice_can_convert() if found else False
    return {
        "libreoffice": found if usable else None,
        "libreoffice_found": found,
        "java": extractor.check_java(),
        "ocr": ocr.engine_status(language),
    }


def list_sources(wiki: Wiki) -> list[dict]:
    """The indexed source documents, by filename."""
    with get_connection(wiki.db_path) as conn:
        rows = conn.execute(
            "SELECT id, filename, status, page_count, parser, error_message, updated_at "
            "FROM documents WHERE source_kind='source' ORDER BY filename"
        ).fetchall()
    return [dict(row) for row in rows]


def peek_stats(db_path: str | Path) -> dict[str, int] | None:
    """The counts of `wiki_stats`, read without writing: the index is opened read only
    (`mode=ro`), so an index older than the schema is not migrated nor rebuilt. None
    when the index cannot be read as it is."""
    import sqlite3

    try:
        conn = sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        sources = conn.execute("SELECT COUNT(*) FROM documents WHERE source_kind='source'").fetchone()[0]
        pages = conn.execute("SELECT COUNT(*) FROM documents WHERE source_kind='wiki'").fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return {"sources": sources, "pages": pages}


def wiki_stats(wiki: Wiki) -> dict[str, int]:
    """The number of source documents and of wiki pages in the index."""
    with get_connection(wiki.db_path) as conn:
        sources = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE source_kind='source'").fetchone()[0]
        pages = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE source_kind='wiki'").fetchone()[0]
    return {"sources": sources, "pages": pages}
