"""Filesystem + DB operations for wiki pages.

All functions operate on disk AND keep the documents table + FTS5 chunks in sync.

PURPOSE FOR BEGINNERS:
When a user or AI interacts with our Wiki, they expect changes to happen both on
the actual hard drive (the physical Markdown `.md` files) AND in the database
(so full-text searching and document linkages remain accurate).

This module contains the core functions to create, read, append, and delete wiki pages
while ensuring disk and database are kept in perfect harmony.
"""

# Standard JSON encoder/decoder to pack and unpack tag arrays
import json
# Regular expression library to match and replace dead links
import re
# Standard SQLite database library
import sqlite3
# Generator for unique random IDs (Universally Unique Identifiers)
import uuid
# Cross-platform file path management library
from pathlib import Path

# Specialized connection opener function from our local database utilities
from domain.tools.db import open_db
# Frontmatter writer/parser shared with dataset files — see module docstring
from domain.datasets.frontmatter import parse_frontmatter, render_frontmatter, split_frontmatter


# ── Private helpers ───────────────────────────────────────────────────────────

def _get_user_id(conn: sqlite3.Connection) -> str:
    """Fetch the active user_id from the workspace database table.

    If the database is brand new and workspace table is empty, we raise a
    RuntimeError informing the developer to initialize the DB first.
    """
    row = conn.execute("SELECT user_id FROM workspace LIMIT 1").fetchone()
    if not row:
        raise RuntimeError("No workspace row found. Call open_db() on an initialized DB.")
    # Return the user_id string from the returned database row
    return row["user_id"]


def _next_doc_number(conn: sqlite3.Connection) -> int:
    """Calculate the next sequential document number.

    Finds the maximum document_number currently in the documents table,
    and adds 1. The COALESCE function handles empty tables: if MAX(document_number)
    is NULL (empty database), it defaults to 0 and adds 1, returning 1.
    """
    row = conn.execute(
        "SELECT COALESCE(MAX(document_number), 0) + 1 FROM documents"
    ).fetchone()
    return row[0]


def _normalize_dir_path(dir_path: str) -> str:
    """Ensure dir_path starts and ends with /  e.g. 'wiki/summaries' → '/wiki/summaries/'.

    This keeps database path formats highly consistent and prevents search indexing bugs.
    """
    p = dir_path.strip("/")
    return f"/{p}/" if p else "/"


def _insert_chunks(conn: sqlite3.Connection, doc_id: str, content: str) -> None:
    """Deconstructs page text into search chunks and inserts them into the database.

    Uses standard chunk size logic to slice up long pages so the AI search system
    can match small, relevant paragraphs rather than entire long files.
    """
    # Import chunker dynamically here to prevent Python circular import conflicts
    # (when file A imports file B, and file B imports file A)
    from domain.ingestion.chunker import chunk_pages

    # 1. Break down the content into a list of semantic chunks.
    #    We pass the text as page 1 (since standard wiki pages are single-page markdown).
    chunks = chunk_pages([(1, content)])

    # 2. Bulk insert the chunks into the database using 'executemany' for high speed.
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


def _strip_dead_links(
    conn: sqlite3.Connection,
    workspace: Path,
    doc_id: str,
    relative_path: str,
) -> None:
    """Remove markdown links pointing at relative_path from all wiki pages that reference it.

    If we delete a page (e.g. 'wiki/concepts/old.md'), we don't want other pages
    in our wiki containing broken links like '[Old Stuff](wiki/concepts/old.md)'.

    This function finds every page that links to the deleted page, strips the link
    brackets and URLs, leaving just the plain text (e.g., 'Old Stuff'), and saves
    the updated pages both to disk and the database chunks.
    """
    # 1. Find all documents that reference our about-to-be-deleted doc_id
    refs = conn.execute(
        "SELECT d.id, d.relative_path, d.content "
        "FROM document_references dr "
        "JOIN documents d ON dr.source_document_id = d.id "
        "WHERE dr.target_document_id = ? AND d.source_kind = 'wiki'",
        (doc_id,),
    ).fetchall()

    # If no other files link to this page, we can safely exit!
    if not refs:
        return

    # 2. Create a Regular Expression pattern that matches markdown links
    #    pointing to our specific path (both relative e.g. 'wiki/x.md' and absolute '/wiki/x.md')
    escaped = re.escape(relative_path)
    if relative_path.startswith("wiki/"):
        path_pat = f"(?:/{escaped}|{escaped})"
    else:
        path_pat = escaped

    # Matches: [Link Text](relative_path) or [Link Text](/relative_path)
    dead_link = re.compile(r"\[([^\]]+)\]\(" + path_pat + r"\)")

    # 3. Iterate through each referencing document, clean the text, and save
    for ref in refs:
        content = ref["content"] or ""
        # Replace the link markdown with just the inner text (represented by group '\1')
        new_content = dead_link.sub(r"\1", content)

        # If no replacement was actually made (already clean), skip updating
        if new_content == content:
            continue

        # Write the updated text to the physical file on disk
        file_path = workspace / ref["relative_path"]
        if file_path.exists():
            file_path.write_text(new_content, encoding="utf-8")

        # Run a transaction to update database records and re-generate search chunks
        with conn:
            conn.execute(
                "UPDATE documents SET content=?, version=version+1, "
                "updated_at=datetime('now') WHERE id=?",
                (new_content, ref["id"]),
            )
            # Remove old search chunks
            conn.execute("DELETE FROM document_chunks WHERE document_id=?", (ref["id"],))
            # Insert brand new search chunks for the cleaned content
            _insert_chunks(conn, ref["id"], new_content)


# ── Public API ────────────────────────────────────────────────────────────────

_PAGE_TYPE_BY_DIR = {
    "/wiki/concepts/": "concept",
    "/wiki/summaries/": "summary",
}


def _resource_for_raw_source(raw: str) -> str:
    """Map a raw source identifier — a bare filename, or the "chat" sentinel —
    to an OKF `resource` value.

    Every call site (pipeline.py's ingested-file sources, wiki_tools.py's
    chat-saved sources) passes a bare identifier with no path, since that's
    what this module always accepted before OKF's mapping shape existed.
    "chat" is the stable identifier for a non-file chat-synthesis origin, so it
    is left as-is; anything else is an ingested document's filename, resolved
    relative to the workspace root as `sources/<filename>`.
    """
    if raw == "chat" or raw.startswith("sources/"):
        return raw
    return f"sources/{raw}"


def _normalize_source_entry(entry: object) -> dict[str, str] | None:
    """Normalize one `sources` entry to this module's mapping shape {"resource": ...}.

    Accepts both the legacy bare-string shape (every page written before OKF's
    provenance-list shape existed — that's every page under examples/, and
    every page any earlier build of create_page wrote) and the current mapping
    shape (passed through, keeping only `resource` — the other OKF provenance
    keys are optional and this module never writes them). Returns None for
    anything a resource can't be recovered from, so callers can drop it.
    """
    if isinstance(entry, str):
        if not entry:
            return None
        return {"resource": _resource_for_raw_source(entry)}
    if isinstance(entry, dict):
        resource = entry.get("resource")
        if isinstance(resource, str) and resource:
            return {"resource": resource}
        return None
    return None


def _sources_from_fm_block(fm_block: str | None) -> list[dict[str, str]]:
    """Parse and normalize the `sources` list out of a frontmatter block.

    Migrates the legacy bare-string shape to the current mapping shape (see
    `_normalize_source_entry`) so a read never re-introduces a mixed-type
    list. Deduplicates on `resource`, keeping the first occurrence. Returns []
    for anything not a list, or a wholly malformed frontmatter block.
    """
    if fm_block is None:
        return []
    try:
        fields = parse_frontmatter(fm_block)
    except ValueError:
        return []
    raw = fields.get("sources")
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in raw:
        norm = _normalize_source_entry(entry)
        if norm is None or norm["resource"] in seen:
            continue
        seen.add(norm["resource"])
        normalized.append(norm)
    return normalized


def _existing_sources(file_path: Path) -> list[dict[str, str]]:
    """Read the (normalized) `sources` list from a page already on disk, or []
    if absent/malformed.

    Used so re-saving a page accumulates sources in code rather than asking the
    LLM to remember and re-transcribe them.
    """
    if not file_path.exists():
        return []
    try:
        old_text = file_path.read_text(encoding="utf-8")
    except OSError:
        return []
    fm_block, _ = split_frontmatter(old_text)
    return _sources_from_fm_block(fm_block)


def _lookup_source_document_filename(conn: sqlite3.Connection, source_document_id: str) -> str | None:
    """Look up a document's filename by id, for resolving the default `sources` value."""
    row = conn.execute(
        "SELECT filename FROM documents WHERE id = ?", (source_document_id,)
    ).fetchone()
    return row["filename"] if row else None


def create_page(
    db_path: str,
    workspace: Path,
    dir_path: str,
    slug: str,
    title: str,
    content: str,
    tags: list[str],
    overwrite: bool = False,
    source_document_id: str | None = None,
    sources: list[str] | None = None,
    replace_sources: bool = False,
) -> dict:
    """Write a wiki page to disk and insert/update the DB record.

    Frontmatter (`type`, `title`, `tags`, `sources`) is rendered here in code —
    never trust the model to transcribe it. Any frontmatter already present in
    `content` (e.g. leftover from an older prompt) is discarded, not duplicated.

    `sources` follows OKF's provenance shape: a list of mappings, each with a
    `resource` key (e.g. `{"resource": "sources/report.pdf"}`), never a bare
    string. Callers still pass plain identifiers (an ingested filename, or the
    "chat" sentinel) via the `sources=` param — `_resource_for_raw_source`
    resolves those to the mapping shape. Reads of any existing `sources` (on
    disk, or in `content`'s own frontmatter) accept both this mapping shape and
    the legacy bare-string shape every page written before it existed — that's
    every page under examples/, and every page an earlier build of this
    function wrote — normalizing the legacy form so a save never produces a
    mixed-type list.

    `sources` accumulates across saves by default: an existing on-disk `sources`
    list is unioned with the incoming one (existing first, then new entries not
    already present, deduplicated on `resource`). When `sources` is not passed
    and `source_document_id` is given, it resolves to that document's filename.

    `replace_sources=True` makes the resolved `sources` authoritative instead of
    unioned with disk. Resolution order in that mode: the explicit `sources=`
    argument if given; otherwise the `sources` already recorded in `content`'s
    own frontmatter; otherwise the key is omitted. This is what lets ingestion
    rollback (`_rollback_wiki_pages`, pipeline.py) restore a page's prior
    sources exactly, instead of inheriting the source the now-rolled-back run
    wrote to disk — the prior DB snapshot it restores from already carries the
    prior frontmatter, `sources` included (possibly in the legacy shape, which
    this function migrates just like any other read).

    Returns {"id": doc_id, "path": relative_path}.
    Raises FileExistsError if the page exists and overwrite=False.
    """
    # 1. Standardize path formats
    dir_path = _normalize_dir_path(dir_path)
    filename = f"{slug}.md"
    relative_path = dir_path.lstrip("/") + filename
    file_path = workspace / relative_path

    # 2. Prevent accidental overwrites if overwrite flag is False
    if file_path.exists() and not overwrite:
        raise FileExistsError(f"Page already exists: {relative_path}")

    # 3. Open a database connection early — needed both for the source-filename
    #    lookup below and for the DB sync later.
    conn = open_db(db_path)
    try:
        # 4. Strip any frontmatter the caller (typically an LLM transcription,
        #    or — for a rollback restore — the prior DB snapshot) already put
        #    in `content`; it gets replaced wholesale, never merged. Keep the
        #    stripped-off block too: replace_sources reads its `sources`.
        #    render_frontmatter's own block already ends in a blank line, so
        #    drop any leading blank line the body carries — otherwise
        #    re-saving an already-fronted page would double it up.
        incoming_fm_block, body = split_frontmatter(content)
        body = body.lstrip("\n")

        # 5/6. Resolve `sources` (list of {"resource": ...} mappings — see the
        # docstring for the legacy-string migration and replace_sources rules).
        if replace_sources:
            # Authoritative — never unioned with what's on disk.
            if sources is not None:
                merged_sources = []
                seen_resources: set[str] = set()
                for raw in sources:
                    resource = _resource_for_raw_source(raw)
                    if resource not in seen_resources:
                        seen_resources.add(resource)
                        merged_sources.append({"resource": resource})
            else:
                merged_sources = _sources_from_fm_block(incoming_fm_block)
        else:
            # Accumulate: whatever is already on disk (already normalized —
            # legacy bare strings included), plus any new ones not already
            # present, existing entries first (order-stable), deduped on
            # `resource`.
            resolved_sources = sources
            if resolved_sources is None and source_document_id is not None:
                looked_up = _lookup_source_document_filename(conn, source_document_id)
                resolved_sources = [looked_up] if looked_up else None
            merged_sources = _existing_sources(file_path)
            seen_resources = {s["resource"] for s in merged_sources}
            for raw in (resolved_sources or []):
                resource = _resource_for_raw_source(raw)
                if resource not in seen_resources:
                    seen_resources.add(resource)
                    merged_sources.append({"resource": resource})

        # 7. Derive the OKF `type` field from the target directory.
        page_type = _PAGE_TYPE_BY_DIR.get(dir_path, "page")

        # 8. Render the frontmatter block in code and prepend it to the body.
        frontmatter_block = render_frontmatter({
            "type": page_type, "title": title, "tags": tags, "sources": merged_sources,
        })
        final_content = frontmatter_block + body

        # 9. Create any parent directories if they don't exist, and write file to disk
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(final_content, encoding="utf-8")

        user_id = _get_user_id(conn)
        # Check if the document already exists in our database
        existing = conn.execute(
            "SELECT id FROM documents WHERE relative_path = ?", (relative_path,)
        ).fetchone()

        # Execute inside a transaction to ensure all database operations succeed together
        with conn:
            if existing:
                # UPDATE: Modify the existing page metadata and content
                doc_id = existing["id"]
                if source_document_id is not None:
                    conn.execute(
                        "UPDATE documents SET content=?, tags=?, title=?, source_document_id=?, "
                        "version=version+1, updated_at=datetime('now') WHERE id=?",
                        (final_content, json.dumps(tags), title, source_document_id, doc_id),
                    )
                else:
                    conn.execute(
                        "UPDATE documents SET content=?, tags=?, title=?, "
                        "version=version+1, updated_at=datetime('now') WHERE id=?",
                        (final_content, json.dumps(tags), title, doc_id),
                    )
                # Remove the document's outdated search chunks before re-inserting
                conn.execute("DELETE FROM document_chunks WHERE document_id=?", (doc_id,))
            else:
                # INSERT: Create a fresh new document entry
                doc_id = str(uuid.uuid4())
                doc_number = _next_doc_number(conn)
                conn.execute(
                    "INSERT INTO documents "
                    "(id, user_id, filename, title, path, relative_path, source_kind, "
                    "file_type, status, content, tags, document_number, source_document_id) "
                    "VALUES (?,?,?,?,?,?,'wiki','md','ready',?,?,?,?)",
                    (doc_id, user_id, filename, title, dir_path, relative_path,
                     final_content, json.dumps(tags), doc_number, source_document_id),
                )

            # Reconstruct and insert search chunks to match the new text content
            _insert_chunks(conn, doc_id, final_content)
    finally:
        # Always close connection to prevent database locks or memory leaks
        conn.close()

    return {"id": doc_id, "path": relative_path}


def read_page(
    db_path: str,
    workspace: Path,
    dir_path: str,
    slug: str,
) -> str | None:
    """Read a wiki page from disk. Returns markdown content or None if not found."""
    # Resolve the proper formatted path
    dir_path = _normalize_dir_path(dir_path)
    file_path = workspace / dir_path.lstrip("/") / f"{slug}.md"

    # If the file doesn't exist, safely return None instead of crashing
    if not file_path.exists():
        return None
    return file_path.read_text(encoding="utf-8")


def write_page_content(
    db_path: str,
    workspace: Path,
    dir_path: str,
    slug: str,
    new_content: str,
) -> bool:
    """Replace an existing wiki page's text (disk + DB). Returns True on success.

    For edits that have to land somewhere other than the end of the file — adding
    a link inside a section, for instance. `append_to_page` is the special case of
    this that concatenates.
    """
    dir_path = _normalize_dir_path(dir_path)
    filename = f"{slug}.md"
    relative_path = dir_path.lstrip("/") + filename
    file_path = workspace / relative_path

    if not file_path.exists():
        return False

    file_path.write_text(new_content, encoding="utf-8")

    # Synchronize with the database
    conn = open_db(db_path)
    try:
        with conn:
            # Update the parent document's text content, incrementing the version counter
            conn.execute(
                "UPDATE documents SET content=?, version=version+1, "
                "updated_at=datetime('now') WHERE relative_path=?",
                (new_content, relative_path),
            )
            # Find the ID of the document to refresh its search chunks
            row = conn.execute(
                "SELECT id FROM documents WHERE relative_path=?", (relative_path,)
            ).fetchone()
            if row:
                doc_id = row["id"]
                # Delete old search chunks and insert fresh ones matching the expanded content
                conn.execute(
                    "DELETE FROM document_chunks WHERE document_id=?", (doc_id,)
                )
                _insert_chunks(conn, doc_id, new_content)
    finally:
        conn.close()

    return True


def append_to_page(
    db_path: str,
    workspace: Path,
    dir_path: str,
    slug: str,
    content: str,
) -> bool:
    """Append content to an existing wiki page (disk + DB). Returns True on success."""
    file_path = workspace / (_normalize_dir_path(dir_path).lstrip("/") + f"{slug}.md")
    if not file_path.exists():
        return False
    existing_content = file_path.read_text(encoding="utf-8")
    new_content = existing_content.rstrip("\n") + "\n\n" + content
    return write_page_content(db_path, workspace, dir_path, slug, new_content)


def delete_page(
    db_path: str,
    workspace: Path,
    dir_path: str,
    slug: str,
) -> bool:
    """Delete a wiki page from disk and the DB. Returns True if it existed."""
    # 1. Formulate paths
    dir_path = _normalize_dir_path(dir_path)
    relative_path = dir_path.lstrip("/") + f"{slug}.md"
    file_path = workspace / relative_path

    # 2. Note whether the page exists (file or DB row decide the return value).
    existed = file_path.exists()

    # 3. Clean up the database FIRST. If a DB step raises (e.g. inside
    #    _strip_dead_links), the file is still on disk, so the page stays
    #    consistent rather than leaving an orphan DB row pointing at a deleted file.
    conn = open_db(db_path)
    try:
        # Retrieve the document ID to find its related child rows
        row = conn.execute(
            "SELECT id FROM documents WHERE relative_path=?", (relative_path,)
        ).fetchone()

        if row:
            doc_id = row["id"]

            # A. Find and strip out all inbound links pointing to this deleted page
            #    so other pages don't have broken/dead links pointing here.
            _strip_dead_links(conn, workspace, doc_id, relative_path)

            with conn:
                # B. Remove all search text chunks belonging to this document
                conn.execute("DELETE FROM document_chunks WHERE document_id=?", (doc_id,))

                # C. Remove all relational citation links (both where this was source or target)
                conn.execute(
                    "DELETE FROM document_references "
                    "WHERE source_document_id=? OR target_document_id=?",
                    (doc_id, doc_id),
                )

                # D. Remove the document record itself from the primary index table
                conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
                existed = True
    finally:
        conn.close()

    # 4. Remove the physical file LAST, once the DB is consistent.
    if file_path.exists():
        file_path.unlink()

    return existed


def concept_page_names(db_path: str) -> list[str]:
    """Titles of all ready concept pages — the concept half of the coverage roster.

    Shared by the pre-retrieval gate and the vocabulary linter so both judge
    "what the wiki covers" the same way.
    """
    from domain.tools.db import get_connection
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT title FROM documents "
            "WHERE source_kind='wiki' AND path='/wiki/concepts/' AND status='ready'"
        ).fetchall()
    return [r["title"] for r in rows if r["title"]]
