"""FTS5 full-text search over wiki and source document chunks."""

import logging

from domain.tools.db import get_connection

logger = logging.getLogger(__name__)


def search_chunks(
    db_path: str,
    query: str,
    limit: int = 10,
    scope: str = "all",
) -> list[dict]:
    """FTS5 full-text search over document chunks.

    scope: "all" | "wiki" | "sources"
    Returns list of dicts with keys: content, page, filename, title, path,
    file_type, header_breadcrumb, chunk_index, score.
    Returns [] for empty queries or no matches.
    """
    if not query or not query.strip():
        return []

    sql = (
        "SELECT dc.content, dc.page, dc.header_breadcrumb, dc.chunk_index, "
        "d.filename, d.title, d.path, d.file_type, "
        "rank AS score "
        "FROM document_chunks dc "
        "JOIN chunks_fts fts ON dc.rowid = fts.rowid "
        "JOIN documents d ON dc.document_id = d.id "
        "WHERE chunks_fts MATCH ? AND d.status != 'failed'"
    )
    params: list = [query]

    if scope == "wiki":
        sql += " AND d.source_kind = 'wiki'"
    elif scope == "sources":
        sql += " AND d.source_kind = 'source'"

    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)

    with get_connection(db_path) as conn:
        try:
            rows = conn.execute(sql, params).fetchall()
        except Exception:
            logger.warning("FTS5 search failed for query %r", query, exc_info=True)
            return []

    return [dict(row) for row in rows]
