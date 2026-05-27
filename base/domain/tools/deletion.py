"""Source deletion — removes a source document and cascades to DB children."""

import logging
from pathlib import Path

from domain.repair.report import RepairResult

logger = logging.getLogger(__name__)


def delete_source(
    db_path: str,
    workspace: Path,
    doc_id: str,
    *,
    also_delete_file: bool = False,
) -> RepairResult:
    """Remove a source document from the DB and optionally from disk.

    FK ON DELETE CASCADE handles document_pages, document_chunks, chunks_fts
    (via triggers), and document_references automatically.

    Dependent wiki pages are handled by relationship:
      - 1-to-1 summary pages (source_document_id == doc_id) are deleted outright —
        there is no source left to regenerate them from.
      - Pages that merely cite the source (e.g. multi-source concept pages) are kept
        and marked stale (stale_since), since they may draw on other surviving
        sources; deleting them would destroy that synthesis.

    Returns a RepairResult describing the outcome.
    """
    from domain.tools.db import get_connection
    from domain.tools.git_ops import auto_commit
    from domain.tools.wiki_fs import delete_page

    try:
        with get_connection(db_path) as conn:
            row = conn.execute(
                "SELECT id, filename, relative_path, source_kind FROM documents WHERE id=?",
                (doc_id,),
            ).fetchone()

        if row is None:
            return RepairResult(
                check="delete_source",
                page=doc_id,
                action="failed",
                success=False,
                message=f"Document not found: {doc_id}",
            )

        if row["source_kind"] != "source":
            return RepairResult(
                check="delete_source",
                page=row["relative_path"],
                action="failed",
                success=False,
                message=f"Document is not a source (kind={row['source_kind']}): {row['filename']}",
            )

        filename = row["filename"]
        relative_path = row["relative_path"]

        # Classify dependent wiki pages BEFORE the cascade removes document_references:
        #  - 1-to-1 summary pages (source_document_id == this source) are *deleted* —
        #    there is no source left to regenerate them from.
        #  - Pages that merely *cite* the source (e.g. multi-source concept pages) are
        #    *kept* and marked stale: they may still draw on other surviving sources,
        #    so deleting them would destroy that synthesis. They are surfaced by
        #    find_stale_pages for review/regeneration.
        with get_connection(db_path) as conn:
            delete_ids = {
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM documents"
                    " WHERE source_document_id=? AND source_kind='wiki'",
                    (doc_id,),
                ).fetchall()
            }
            citing_ids = {
                r["source_document_id"]
                for r in conn.execute(
                    "SELECT DISTINCT source_document_id FROM document_references"
                    " WHERE target_document_id=? AND reference_type='cites'",
                    (doc_id,),
                ).fetchall()
            }
            # Cited pages that are not 1-to-1 derivatives get marked stale, not deleted.
            stale_ids = citing_ids - delete_ids

            delete_paths: list[tuple[str, str]] = []  # (dir_path, slug) pairs
            if delete_ids:
                placeholders = ",".join("?" * len(delete_ids))
                delete_paths = [
                    (r["path"], r["filename"].removesuffix(".md"))
                    for r in conn.execute(
                        f"SELECT path, filename FROM documents"
                        f" WHERE id IN ({placeholders}) AND source_kind='wiki'",
                        list(delete_ids),
                    ).fetchall()
                ]

        # Delete the 1-to-1 derived pages from disk and DB before the source cascade.
        deleted_wiki: list[str] = []
        for dir_path, slug in delete_paths:
            if delete_page(db_path, workspace, dir_path, slug):
                deleted_wiki.append(f"{dir_path}{slug}.md")
                logger.info("Deleted derived wiki page: %s%s.md", dir_path, slug)

        # Mark citing (multi-source) pages stale instead of deleting them.
        marked_stale = 0
        if stale_ids:
            placeholders = ",".join("?" * len(stale_ids))
            with get_connection(db_path) as conn:
                with conn:
                    cur = conn.execute(
                        f"UPDATE documents SET stale_since=datetime('now')"
                        f" WHERE id IN ({placeholders}) AND source_kind='wiki'"
                        f" AND stale_since IS NULL",
                        list(stale_ids),
                    )
                    marked_stale = cur.rowcount
            if marked_stale:
                logger.info("Marked %d citing wiki page(s) stale", marked_stale)

        with get_connection(db_path) as conn:
            with conn:
                conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))

        if also_delete_file:
            physical = workspace / relative_path
            if physical.exists():
                physical.unlink()
                logger.info("Deleted physical file: %s", physical)

        notes: list[str] = []
        if deleted_wiki:
            notes.append(f"deleted {len(deleted_wiki)} derived wiki page(s)")
        if marked_stale:
            notes.append(f"marked {marked_stale} citing page(s) stale")
        wiki_note = f"; {'; '.join(notes)}" if notes else ""
        auto_commit(workspace, f"delete source: {filename}")

        return RepairResult(
            check="delete_source",
            page=relative_path,
            action="deleted",
            success=True,
            message=f"Deleted source '{filename}'{wiki_note}",
        )

    except Exception as exc:
        logger.error("delete_source failed for %s: %s", doc_id, exc)
        return RepairResult(
            check="delete_source",
            page=doc_id,
            action="failed",
            success=False,
            message=f"Deletion failed: {exc}",
        )
