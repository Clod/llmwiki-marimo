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
    Derived wiki pages (summaries generated from this source) are deleted
    outright — there is no source left to regenerate them from.

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

        # Collect derived wiki pages BEFORE the cascade removes document_references.
        # Use both the direct source_document_id link (set during ingestion) and the
        # references graph so nothing is missed.
        with get_connection(db_path) as conn:
            by_ref = [
                r["source_document_id"]
                for r in conn.execute(
                    "SELECT DISTINCT source_document_id FROM document_references"
                    " WHERE target_document_id=?",
                    (doc_id,),
                ).fetchall()
            ]
            by_col = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM documents"
                    " WHERE source_document_id=? AND source_kind='wiki'",
                    (doc_id,),
                ).fetchall()
            ]
            wiki_ids = list({*by_ref, *by_col})

            wiki_paths: list[tuple[str, str]] = []  # (dir_path, slug) pairs
            if wiki_ids:
                placeholders = ",".join("?" * len(wiki_ids))
                wiki_paths = [
                    (r["path"], r["filename"].removesuffix(".md"))
                    for r in conn.execute(
                        f"SELECT path, filename FROM documents"
                        f" WHERE id IN ({placeholders}) AND source_kind='wiki'",
                        wiki_ids,
                    ).fetchall()
                ]

        # Delete derived wiki pages from disk and DB before the source cascade
        deleted_wiki: list[str] = []
        for dir_path, slug in wiki_paths:
            if delete_page(db_path, workspace, dir_path, slug):
                deleted_wiki.append(f"{dir_path}{slug}.md")
                logger.info("Deleted derived wiki page: %s%s.md", dir_path, slug)

        with get_connection(db_path) as conn:
            with conn:
                conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))

        if also_delete_file:
            physical = workspace / relative_path
            if physical.exists():
                physical.unlink()
                logger.info("Deleted physical file: %s", physical)

        wiki_note = f"; deleted {len(deleted_wiki)} derived wiki page(s)" if deleted_wiki else ""
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
