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
) -> dict:
    """Write a wiki page to disk and insert/update the DB record.

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

    # 3. Create any parent directories if they don't exist, and write file to disk
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")

    # 4. Open a database connection to synchronize the change
    conn = open_db(db_path)
    try:
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
                        (content, json.dumps(tags), title, source_document_id, doc_id),
                    )
                else:
                    conn.execute(
                        "UPDATE documents SET content=?, tags=?, title=?, "
                        "version=version+1, updated_at=datetime('now') WHERE id=?",
                        (content, json.dumps(tags), title, doc_id),
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
                     content, json.dumps(tags), doc_number, source_document_id),
                )

            # Reconstruct and insert search chunks to match the new text content
            _insert_chunks(conn, doc_id, content)
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


def append_to_page(
    db_path: str,
    workspace: Path,
    dir_path: str,
    slug: str,
    content: str,
) -> bool:
    """Append content to an existing wiki page (disk + DB). Returns True on success."""
    # 1. Locate the file
    dir_path = _normalize_dir_path(dir_path)
    filename = f"{slug}.md"
    relative_path = dir_path.lstrip("/") + filename
    file_path = workspace / relative_path

    # If the file isn't there, we cannot append to it!
    if not file_path.exists():
        return False

    # 2. Read the old text, format it cleanly with extra spacing, and append the new content
    existing_content = file_path.read_text(encoding="utf-8")
    new_content = existing_content.rstrip("\n") + "\n\n" + content

    # Write the combined string back to disk
    file_path.write_text(new_content, encoding="utf-8")

    # 3. Synchronize with the database
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
