"""FTS5 search tool for the wiki assistant agent.

PURPOSE FOR BEGINNERS:
When the AI assistant cannot find an answer within the highly-structured Wiki pages,
it falls back to searching through the raw source documents (like PDFs, reports,
or raw TXT files).

This file implements that "fallback search" tool. It uses `aiosqlite`, an asynchronous
SQLite library, which allows our application to run queries without freezing the entire
program (letting other users continue their actions while database searches are running).
"""

# Import standard logging library to record database search issues
import logging
# Import aiosqlite for asynchronous (non-blocking) SQLite operations
import aiosqlite
# Import RunContext from pydantic_ai to access shared dependencies (the database path)
from pydantic_ai import RunContext

# Set up logging for this module
logger = logging.getLogger(__name__)


async def search_source_chunks(ctx: RunContext[str], query: str, limit: int = 10) -> str:
    """Search source document chunks using FTS5 full-text search.

    Args:
        ctx: The runtime context containing system dependencies (contains db_path).
        query: Search terms — keywords or phrases extracted from the user's message.
        limit: Maximum results to return (default 10).

    Returns:
        Formatted markdown results containing document names, page numbers,
        hierarchical section breadcrumbs, and matching text snippets.
    """
    # 1. Retrieve the SQLite database path from the runtime context dependencies
    db_path = ctx.deps

    try:
        # 2. Open an asynchronous connection to the database.
        #    journal_mode=WAL is a persistent database-level setting already applied
        #    by open_db at creation, so this read path does not need to re-set it.
        async with aiosqlite.connect(db_path) as db:
            # 3. Perform a fast Full-Text Search (FTS5) join query:
            #    - We match the virtual FTS table (chunks_fts) on the unique 'rowid'.
            #    - We join with 'documents' to fetch the actual human-readable filename.
            #    - We filter for source files only (source_kind = 'source') and bypass broken files.
            #    - We order by 'rank' (FTS5's automatic relevancy score; smaller values mean a better match).
            cursor = await db.execute(
                """
                SELECT dc.content, dc.page, dc.header_breadcrumb,
                       d.filename, rank AS score
                FROM document_chunks dc
                JOIN chunks_fts fts ON dc.rowid = fts.rowid
                JOIN documents d ON dc.document_id = d.id
                WHERE chunks_fts MATCH ?
                  AND d.status != 'failed'
                  AND d.source_kind = 'source'
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit),
            )
            # 4. Fetch all matching records asynchronously
            rows = await cursor.fetchall()

    except Exception as exc:
        # If the database or query fails, log it and return a message guiding the AI
        logger.warning("FTS5 search failed for %r: %s", query, exc)
        return f"Search failed for '{query}': {exc}. Try simpler or different search terms."

    # 5. If no search matches were found, return a friendly notice
    if not rows:
        return f"No results found for '{query}'."

    # 6. Format the records into clean, legible markdown for the AI agent
    lines = [f"**{len(rows)} result(s)** for '{query}':\n"]
    for content, page, breadcrumb, filename, score in rows:
        # Format the page number if available (e.g. "p. 5")
        page_str = f" p.{page}" if page else ""

        # Format the section breadcrumb path if available (e.g. " › Introduction › Definition")
        crumb_str = f" › {breadcrumb}" if breadcrumb else ""

        # Extract a short, digestible preview snippet (first 400 characters)
        snippet = content[:400].strip()
        if len(content) > 400:
            snippet += "..."

        # Append the formatted result header and code-blocked preview to our output lines
        lines.append(f"**{filename}**{page_str}{crumb_str}")
        lines.append(f"```\n{snippet}\n```\n")

    # 7. Join lines with newlines and return the completed report
    return "\n".join(lines)
