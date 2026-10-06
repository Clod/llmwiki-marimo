"""Rebuild the index from the files on disk, with no model call.

The durable layer of a wiki is the markdown under `wiki/` and the documents under
`sources/`; `index.db` is derived from them. `reindex_from_disk` writes a fresh
database from those files. It never modifies a markdown file or a source file.

The five steps:
  1. Apply the schema to a fresh database and re-create the `workspace` row.
  2. One `source_kind='source'` row per file of `sources/`: recompute the file
     hash, size and mtime, extract the pages again with the deterministic
     extractor, chunk them, and fill `document_pages` and `document_chunks`.
     This is the only step that reads an original file, and it uses no model.
  3. One `source_kind='wiki'` row per page of `wiki/`: title and tags from the
     front-matter, chunks from the markdown. The triggers of `chunks_fts` fill
     the search index. `index.md`, `overview.md` and `log.md` have no row, as
     in ingestion (`services.wiki.FIXED_PAGES`).
  4. `update_references` per wiki page rebuilds `document_references` from the
     citations and the links on the page.
  5. Each summary page gets its `source_document_id` back: the source whose
     `make_wiki_slug(filename)` equals the slug of the page.

Recovered: every `documents` row (by `relative_path`), `document_pages`,
`document_chunks`, `chunks_fts` and the reference graph. Not recoverable from the
files: `version`, `document_number` and the creation dates (reset), and
`stale_since`. A wiki page whose source file is gone is registered, but its
`cites` edge has no target.

The fresh database is built beside the old one and swapped in only when it is
complete, so a failure leaves the old index as it was. A rebuild in which the
extractor fails on every source (a machine with no working LibreOffice, say) is a
failure, not a rebuild. The old index is kept as
`index.db.bak`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from domain.datasets.frontmatter import parse_frontmatter, split_frontmatter
from domain.ingestion import extractor
from domain.ingestion.chunker import chunk_pages
from domain.ingestion.detector import compute_file_hash
from domain.ingestion.formats import sorted_extensions
from domain.ingestion.wiki_generator import make_wiki_slug
from domain.text.stemming import stem_text
from domain.tools.db import open_db
from domain.tools.references import update_references
from domain.wiki_settings import load_wiki_language

logger = logging.getLogger(__name__)

Progress = Callable[[str], None]

SOURCE_SUFFIXES = tuple(sorted_extensions())
# The pages ingestion writes as plain files, with no row in `documents`.
FIXED_PAGES = frozenset({"index.md", "overview.md", "log.md"})
_SQLITE_SIDE_FILES = ("-wal", "-shm")


class NothingExtracted(RuntimeError):
    """No source could be extracted. This points at the machine, not at the files, so
    the rebuild stops and the old index stays."""


@dataclass(frozen=True)
class ReindexReport:
    """What a rebuild wrote."""

    sources_indexed: int
    sources_failed: tuple[tuple[str, str], ...]   # (file name, reason)
    wiki_pages_indexed: int
    summaries_linked: int
    references: int
    duration_s: float


def _noop(_: str) -> None:
    pass


def _title_from_filename(filename: str) -> str:
    """The title ingestion gives a page or a source without a better one."""
    return Path(filename).stem.replace("-", " ").replace("_", " ").strip().title()


def _remove_database(path: Path) -> None:
    for suffix in ("", *_SQLITE_SIDE_FILES):
        with contextlib.suppress(FileNotFoundError):
            Path(f"{path}{suffix}").unlink()


def _old_workspace_row(db_path: Path) -> tuple | None:
    """The `workspace` row of the old index, or None when it cannot be read.
    A damaged index is the reason to rebuild, so any error means "no row"."""
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return conn.execute(
                "SELECT id, name, description, user_id, created_at FROM workspace LIMIT 1").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _next_number(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COALESCE(MAX(document_number), 0) + 1 FROM documents").fetchone()[0]


# ── Step 2: sources ───────────────────────────────────────────────────────────

def _index_sources(conn, workspace: Path, user_id: str, language: str, progress: Progress) -> tuple[int, list]:
    sources_dir = workspace / "sources"
    files = sorted(p for p in sources_dir.iterdir()
                   if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in SOURCE_SUFFIXES
                   ) if sources_dir.is_dir() else []
    indexed, failed = 0, []
    for path in files:
        progress(f"📄 Indexing source {path.name}")
        doc_id = str(uuid.uuid4())
        stat = path.stat()
        cache_dir = workspace / ".llmwiki" / "cache" / "local" / doc_id
        try:
            page_contents, parser = extractor.extract(path, cache_dir)
        except (extractor.JavaNotInstalledError, extractor.LibreOfficeNotInstalledError):
            # The machine cannot extract: a rebuild would register every source as
            # failed. Stop, and leave the old index as it is.
            raise
        except Exception as exc:  # noqa: BLE001 — one unreadable file must not stop the rest
            logger.warning("reindex: %s could not be extracted: %s", path.name, exc)
            progress(f"❌ {path.name} — {exc}")
            failed.append((path.name, str(exc)[:500]))
            conn.execute(
                "INSERT INTO documents (id, user_id, filename, title, path, relative_path, source_kind, "
                "file_type, file_size, status, error_message, document_number, content_hash, mtime_ns, "
                "last_indexed_at) VALUES (?,?,?,?,'sources/',?,'source',?,?,'failed',?,?,?,?,datetime('now'))",
                (doc_id, user_id, path.name, _title_from_filename(path.name), f"sources/{path.name}",
                 path.suffix.lstrip(".").lower(), stat.st_size, str(exc)[:500], _next_number(conn),
                 compute_file_hash(path), stat.st_mtime_ns))
            conn.commit()
            continue

        chunks = chunk_pages(page_contents)
        full_content = "\n\n---\n\n".join(md for _, md in page_contents)
        with conn:
            conn.execute(
                "INSERT INTO documents (id, user_id, filename, title, path, relative_path, source_kind, "
                "file_type, file_size, status, page_count, content, parser, document_number, content_hash, "
                "mtime_ns, last_indexed_at) VALUES (?,?,?,?,'sources/',?,'source',?,?,'ready',?,?,?,?,?,?,"
                "datetime('now'))",
                (doc_id, user_id, path.name, _title_from_filename(path.name), f"sources/{path.name}",
                 path.suffix.lstrip(".").lower(), stat.st_size, len(page_contents), full_content, parser,
                 _next_number(conn), compute_file_hash(path), stat.st_mtime_ns))
            conn.executemany(
                "INSERT INTO document_pages (id, document_id, page, content) VALUES (?,?,?,?)",
                [(str(uuid.uuid4()), doc_id, number, md) for number, md in page_contents])
            _insert_chunks(conn, doc_id, chunks, language)
        indexed += 1
    return indexed, failed


def _insert_chunks(conn, doc_id: str, chunks, language: str) -> None:
    conn.executemany(
        "INSERT INTO document_chunks (id, document_id, chunk_index, content, content_stemmed, page, "
        "start_char, token_count, header_breadcrumb) VALUES (?,?,?,?,?,?,?,?,?)",
        [(str(uuid.uuid4()), doc_id, c.index, c.content, stem_text(c.content, language), c.page,
          c.start_char, c.token_count, c.header_breadcrumb) for c in chunks])


# ── Step 3: wiki pages ────────────────────────────────────────────────────────

def _page_metadata(text: str, filename: str) -> tuple[str, list[str]]:
    """The title and the tags of a page: the front-matter, else the first heading,
    else the file name."""
    block, body = split_frontmatter(text)
    fields: dict = {}
    if block:
        try:
            fields = parse_frontmatter(block)
        except ValueError:
            logger.warning("reindex: invalid front-matter in %s", filename)
    title = fields.get("title")
    if not title:
        heading = re.search(r"^#\s+(.+)$", body, re.M)
        title = heading.group(1).strip() if heading else _title_from_filename(filename)
    tags = fields.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    return str(title), [str(t) for t in tags]


def _index_wiki_pages(conn, workspace: Path, user_id: str, language: str, progress: Progress) -> list[tuple[str, str, str]]:
    """Register every page of wiki/; return (document id, content, directory) per page."""
    wiki_dir = workspace / "wiki"
    pages = sorted(p for p in wiki_dir.rglob("*.md") if p.is_file()) if wiki_dir.is_dir() else []
    registered = []
    for path in pages:
        relative_to_wiki = path.relative_to(wiki_dir).as_posix()
        if relative_to_wiki in FIXED_PAGES:
            continue
        relative = path.relative_to(workspace).as_posix()
        dir_path = "/" + path.parent.relative_to(workspace).as_posix() + "/"
        text = path.read_text(encoding="utf-8")
        title, tags = _page_metadata(text, path.name)
        progress(f"📝 Indexing page {relative}")
        doc_id = str(uuid.uuid4())
        with conn:
            conn.execute(
                "INSERT INTO documents (id, user_id, filename, title, path, relative_path, source_kind, "
                "file_type, status, content, tags, document_number) VALUES (?,?,?,?,?,?,'wiki','md','ready',?,?,?)",
                (doc_id, user_id, path.name, title, dir_path, relative, text, json.dumps(tags), _next_number(conn)))
            _insert_chunks(conn, doc_id, chunk_pages([(1, text)]), language)
        registered.append((doc_id, text, dir_path))
    return registered


# ── Step 5: summary → source ──────────────────────────────────────────────────

def _link_summaries(conn) -> int:
    """Set `source_document_id` of each summary page to the source whose slug it carries."""
    by_slug: dict[str, str] = {}
    for row in conn.execute("SELECT id, filename FROM documents WHERE source_kind='source' "
                            "AND status='ready' ORDER BY filename"):
        by_slug.setdefault(make_wiki_slug(row["filename"]), row["id"])
    linked = 0
    with conn:
        for row in conn.execute(
                "SELECT id, filename FROM documents WHERE source_kind='wiki' AND path='/wiki/summaries/'").fetchall():
            source_id = by_slug.get(Path(row["filename"]).stem)
            if source_id:
                conn.execute("UPDATE documents SET source_document_id=? WHERE id=?", (source_id, row["id"]))
                linked += 1
    return linked


# ── Swap ──────────────────────────────────────────────────────────────────────

def _swap_in(fresh: Path, db_path: Path) -> None:
    """Replace `db_path` by `fresh`; keep the old index, with its side files, as `.bak`."""
    backup = Path(f"{db_path}.bak")
    if db_path.exists():
        _remove_database(backup)
        for suffix in ("", *_SQLITE_SIDE_FILES):
            old = Path(f"{db_path}{suffix}")
            if old.exists():
                os.replace(old, Path(f"{backup}{suffix}"))
    else:
        for suffix in _SQLITE_SIDE_FILES:   # side files of an index that is gone
            with contextlib.suppress(FileNotFoundError):
                Path(f"{db_path}{suffix}").unlink()
    os.replace(fresh, db_path)


def reindex_from_disk(workspace: Path, db_path: str, *, progress: Progress = _noop) -> ReindexReport:
    """Write a fresh `index.db` from `sources/` and `wiki/`; return what it holds.

    Raises `JavaNotInstalledError` or `LibreOfficeNotInstalledError` when the
    extractor cannot run, and `NothingExtracted` when it fails on every source; in
    both cases the old index is left as it was. A source the extractor cannot read,
    while others can be read, is registered as `failed` and listed in the report.
    """
    started = time.monotonic()
    workspace, db_file = Path(workspace), Path(db_path)
    language = load_wiki_language(workspace)
    fresh = Path(f"{db_file}.reindex")
    _remove_database(fresh)

    progress("🗄 Creating a fresh index")
    conn = open_db(str(fresh))
    try:
        # Step 1: schema (applied by open_db) and the workspace row.
        old = _old_workspace_row(db_file)
        ws = old or (str(uuid.uuid4()), workspace.name, "", None, None)
        ws_id, name, description, user_id, created_at = ws
        user_id = user_id or ws_id
        conn.execute("INSERT INTO workspace (id, name, description, user_id, created_at) "
                     "VALUES (?,?,?,?,COALESCE(?, datetime('now')))", (ws_id, name, description, user_id, created_at))
        conn.commit()

        indexed, failed = _index_sources(conn, workspace, user_id, language, progress)
        if failed and not indexed:
            raise NothingExtracted(
                f"None of the {len(failed)} source file(s) could be extracted; the index was not replaced. "
                f"First error: {failed[0][0]}: {failed[0][1]}")
        registered = _index_wiki_pages(conn, workspace, user_id, language, progress)
        conn.close()
        conn = None

        progress("🔗 Rebuilding the references")
        for doc_id, text, dir_path in registered:
            update_references(str(fresh), doc_id, text, dir_path)

        conn = open_db(str(fresh))
        linked = _link_summaries(conn)
        references = conn.execute("SELECT COUNT(*) FROM document_references").fetchone()[0]
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("the rebuilt index failed its integrity check")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except BaseException:
        if conn is not None:
            conn.close()
        _remove_database(fresh)
        raise
    conn.close()
    for suffix in _SQLITE_SIDE_FILES:
        with contextlib.suppress(FileNotFoundError):
            Path(f"{fresh}{suffix}").unlink()

    progress("♻️ Replacing the index; the old one stays as index.db.bak")
    _swap_in(fresh, db_file)
    return ReindexReport(
        sources_indexed=indexed, sources_failed=tuple(failed), wiki_pages_indexed=len(registered),
        summaries_linked=linked, references=references, duration_s=time.monotonic() - started)
