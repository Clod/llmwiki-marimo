"""FTS5 full-text search over wiki and source document chunks."""

import logging

from domain.text.stemming import stem_fts_query
from domain.tools.db import get_connection
from domain.wiki_settings import language_for_db

logger = logging.getLogger(__name__)


def search_chunks(
    db_path: str,
    query: str,
    limit: int = 10,
    scope: str = "all",
    language: str | None = None,
) -> list[dict]:
    """FTS5 full-text search over document chunks.

    The index holds stemmed text, so the query is stemmed here, in the wiki's
    language, before MATCH: no caller can forget to. `language` defaults to the
    language of the wiki that owns `db_path`.

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
    params: list = [stem_fts_query(query, language or language_for_db(db_path))]

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
