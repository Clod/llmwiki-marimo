"""Wiki-aware tools for the chat agent.

These tools give the agent direct read/write access to the structured wiki
(summaries/, concepts/, index.md) produced by Phase 2 ingestion.

Dependency contract: ctx.deps is the SQLite DB path (str).
Workspace is derived as Path(db_path).parent.parent — always workspace/.llmwiki/index.db.
"""

# Import the standard logging library to log warnings and errors
import logging
# Import Path from pathlib to easily handle file system paths in a cross-platform way
from pathlib import Path

# Import RunContext from pydantic_ai to access runtime context and dependencies passed to agent tools
from pydantic_ai import RunContext

# Initialize a logger for this module to assist with debugging and monitoring
logger = logging.getLogger(__name__)


def _workspace(db_path: str) -> Path:
    """Helper function to calculate the workspace root directory.

    Given the SQLite database path (which lives at workspace/.llmwiki/index.db),
    moving up two directory levels (.parent.parent) gives the workspace root path.
    """
    return Path(db_path).parent.parent


# ── 4.1: read_wiki_page ───────────────────────────────────────────────────────

def read_wiki_page(ctx: RunContext[str], path: str) -> str:
    """Read a wiki page by its path relative to the workspace root.

    Args:
        ctx: The runtime context containing system dependencies (in this case, the database path).
        path: Path relative to workspace, e.g. "wiki/index.md",
              "wiki/summaries/blancanieves.md", "wiki/concepts/federal-reserve.md".

    Returns:
        Full markdown content of the page, or a 'not found' message.
    """
    # 1. Retrieve the SQLite database path from the agent's context dependencies
    db_path = ctx.deps

    # 2. Resolve the requested path and confine it to the wiki/ directory.
    #    read_wiki_page is LLM-callable and ingested content can carry prompt
    #    injection, so a crafted path like "wiki/../../.env" must not escape the
    #    wiki tree. lstrip("/") keeps the join relative; resolve() normalises any
    #    ".." segments (and symlinks) so is_relative_to can reject escapes.
    ws_root = _workspace(db_path).resolve()
    wiki_root = (ws_root / "wiki").resolve()
    file = (ws_root / path.lstrip("/")).resolve()
    if not file.is_relative_to(wiki_root):
        logger.warning("read_wiki_page rejected out-of-bounds path: %s", path)
        return f"Invalid path: {path}"

    # 3. If the file does not exist, return a friendly user-facing error message
    if not file.exists():
        return f"Page not found: {path}"

    try:
        # 4. Read the page, prefixed with a canonical attribution header so the
        #    agent always has a citation anchor inline with the content it uses.
        #    Without this the model has to recall the path it requested, and
        #    wiki-first answers routinely arrive uncited. The search tools already
        #    carry their own path/page attribution; this gives read_wiki_page parity.
        content = file.read_text(encoding="utf-8")
        rel = file.relative_to(ws_root).as_posix()
        return f"[wiki page: {rel}]\n\n{content}"
    except Exception as exc:
        # 5. Catch any unexpected errors (e.g. permission issues), log it, and return a clean error message
        logger.warning("read_wiki_page failed for %s: %s", path, exc)
        return f"Error reading {path}: {exc}"


# ── 4.1: search_wiki_fts ─────────────────────────────────────────────────────

def search_wiki_fts(ctx: RunContext[str], query: str, limit: int = 10) -> str:
    """FTS5 full-text search scoped to wiki pages only (summaries and concepts).

    Use this to find relevant wiki pages before falling back to raw source search.

    Args:
        ctx: The runtime context containing system dependencies.
        query: Search terms — key words or phrases to search for.
        limit: Maximum number of results to return (default is 10).

    Returns:
        Formatted results with page path, section details, and a content snippet.
    """
    # Import the specialized search function dynamically to keep startup times fast
    from domain.tools.search import search_chunks

    # 1. Retrieve the SQLite database path from the context
    db_path = ctx.deps

    try:
        # 2. Run a full-text search (FTS) using the database, scoped specifically to "wiki" pages
        results = search_chunks(db_path, query, limit=limit, scope="wiki")
    except Exception as exc:
        # If the database query fails, log the warning and return a clean error message to the agent
        logger.warning("search_wiki_fts failed for %r: %s", query, exc)
        return f"Wiki search failed for '{query}': {exc}"

    # 3. If no matching pages or chunks were found, inform the agent
    if not results:
        return f"No wiki pages found for '{query}'."

    # 4. Format the search results into a clean, easy-to-read markdown string for the agent
    lines = [f"**{len(results)} wiki result(s)** for '{query}':\n"]
    for r in results:
        # If the result points to a specific page number, format it
        page_str = f" p.{r['page']}" if r.get("page") else ""

        # If the result resides within a specific heading section (breadcrumb), format it
        crumb_str = f" › {r['header_breadcrumb']}" if r.get("header_breadcrumb") else ""

        # Combine directory path and filename to build the relative path to the file
        path = r.get("path", "") + r.get("filename", "")

        # Extract a short snippet (first 400 characters) to show where the match occurred
        snippet = r["content"][:400].strip()
        if len(r["content"]) > 400:
            snippet += "..."

        # Append the formatted result header and the code-blocked snippet to our output lines
        lines.append(f"**{path}**{page_str}{crumb_str}")
        lines.append(f"```\n{snippet}\n```\n")

    # 5. Join all lines with newlines and return the completed report
    return "\n".join(lines)


# ── Related-page context helper ───────────────────────────────────────────────

def _related_pages_for(workspace: Path, exclude_slug: str, current_dir: str) -> list[dict]:
    """Scan wiki/concepts and wiki/summaries and return link context for the LLM.

    Each entry has 'title' and 'rel_path' (relative from current_dir).
    The page being created/updated (exclude_slug) is omitted.
    """
    pages = []
    in_concepts = "concepts" in current_dir
    for dir_name in ("concepts", "summaries"):
        dir_full = workspace / "wiki" / dir_name
        if not dir_full.exists():
            continue
        for p in sorted(dir_full.glob("*.md")):
            if p.stem == exclude_slug:
                continue
            title = p.stem.replace("-", " ").title()
            if in_concepts and dir_name == "concepts":
                rel = f"{p.stem}.md"
            elif in_concepts and dir_name == "summaries":
                rel = f"../summaries/{p.stem}.md"
            elif not in_concepts and dir_name == "concepts":
                rel = f"../concepts/{p.stem}.md"
            else:
                rel = f"{p.stem}.md"
            pages.append({"title": title, "rel_path": rel})
    return pages


# ── Post-save lint+repair helper ─────────────────────────────────────────────

def _lint_and_repair_after_save(
    db_path: str,
    workspace: Path,
    page_path: str,
    client=None,
    model: str = "",
    language: str = "en",
) -> str:
    """Deterministic lint+repair scoped to the just-saved page.

    Excluded: orphan check (new page has no inbound links yet).
    Returns a brief summary string, or "" if nothing to report.
    """
    try:
        # Import linting and auto-repair systems dynamically
        from domain.lint.runner import lint_wiki
        from domain.lint.report import LintReport
        from domain.repair.runner import repair_wiki

        # Normalize paths to have a leading slash (e.g. "/wiki/concepts/abc.md")
        normalized = "/" + page_path.lstrip("/")

        # 1. Run the entire suite of wiki validators to find any issues (broken links, bad headers, etc.)
        full_report = lint_wiki(db_path, workspace)

        # 2. Filter issues down to ONLY the page we just saved, ignoring "orphan" errors
        #    (since a newly created page naturally won't have other pages linking to it yet)
        focused_issues = [
            i for i in full_report.issues
            if i.page == normalized and i.check != "orphan"
        ]

        # If no formatting or structural issues are found, we're all done!
        if not focused_issues:
            return ""

        # 3. Package up the scoped issues into a focused LintReport
        focused_report = LintReport(issues=focused_issues, checked_at=full_report.checked_at)

        # 4. Execute the auto-repair engine, which uses the LLM to rewrite and fix the specific issues
        repair_report = repair_wiki(focused_report, db_path, workspace,
                                    llm_client=client, model=model, language=language)

        # 5. Return a summary of what was successfully repaired, if anything
        if not repair_report.results:
            return ""
        return repair_report.summary()
    except Exception as exc:
        # Log any linting or repair errors as warnings but don't crash the save process
        logger.warning("post-save lint+repair failed: %s", exc)
        return ""


# ── Direct save (no RunContext) ───────────────────────────────────────────────

def save_to_wiki(
    db_path: str,
    workspace: Path,
    title: str,
    content: str,
    category: str = "concept",
    *,
    client=None,
    model: str | None = None,
    language: str = "en",
) -> str:
    """Save content as a wiki page without requiring a PydanticAI RunContext.

    Applies an LLM structuring pass before writing. Creates the page if it
    doesn't exist; merges into existing if it does.

    Args:
        db_path: Path to the SQLite database.
        workspace: Absolute path to the workspace root directory.
        title: Human-readable page title.
        content: Markdown body content to save.
        category: "concept" or "summary" (determines folder placement).
        client: Optional OpenAI-compatible client instance.
        model: Optional model identifier.
        language: ISO 639-1 code controlling the language of the generated page
            (structured content, See-also section, index entry). Defaults to
            English so existing call sites stay byte-identical.

    Returns a confirmation string e.g. "Created wiki/concepts/my-concept.md".
    """
    # Dynamic imports to keep loading lightweight
    from domain.ingestion.wiki_generator import make_wiki_slug, structure_chat_content, inject_see_also
    from domain.tools.wiki_fs import create_page, read_page
    from domain.tools.references import update_references
    from domain.ingestion.index_manager import update_index

    # 1. Fallback: If no client or model is explicitly provided, instantiate them from global settings
    if client is None or model is None:
        from openai import OpenAI
        from config import settings
        base_url = settings.WIKI_LLM_BASE_URL or settings.LLM_BASE_URL
        api_key = settings.WIKI_LLM_API_KEY or settings.LLM_API_KEY
        client = client or OpenAI(base_url=base_url, api_key=api_key)
        model = model or settings.WIKI_LLM_MODEL or settings.LLM_MODEL

    # 2. Determine target folder path and generate a safe filename (slug)
    dir_path = "/wiki/concepts/" if category == "concept" else "/wiki/summaries/"
    slug = make_wiki_slug(title)

    # 3. Read any existing file content to support merging
    existing = read_page(db_path, workspace, dir_path, slug)

    # 4. Use LLM to structure/merge the content beautifully (in the wiki language)
    structured = structure_chat_content(
        title, category, content, existing, client, model, language=language
    )
    related = _related_pages_for(workspace, slug, dir_path)
    structured = inject_see_also(structured, related, language=language)

    # 5. Write the file to disk, determining whether this is an Update or a fresh Creation
    if existing:
        result = create_page(
            db_path, workspace, dir_path, slug, title, structured, [category],
            overwrite=True, sources=["chat"],
        )
        page_path = result["path"]
        action = "Updated"
    else:
        result = create_page(
            db_path, workspace, dir_path, slug, title, structured, [category],
            sources=["chat"],
        )
        page_path = result["path"]
        action = "Created"

    # 6. Database citation tracking synchronization
    try:
        from domain.tools.db import get_connection
        with get_connection(db_path) as conn:
            # Look up unique document record identifier
            row = conn.execute(
                "SELECT id FROM documents WHERE relative_path=?", (page_path,)
            ).fetchone()
        if row:
            update_references(db_path, row["id"], structured, dir_path)
    except Exception:
        # Ignore DB sync errors so the critical file-saving step is not disrupted,
        # but log them so a broken references table is diagnosable.
        logger.warning("reference sync failed for %s", page_path, exc_info=True)

    # 7. Update the main table-of-contents index file (index.md)
    summary = structured.strip().splitlines()[0].lstrip("# ").strip()[:80]
    update_index(workspace, page_path, summary, category + "s", language=language)

    # 8. Run automatic post-save validator checks and fixes (lint + repair)
    #    page_path already begins with "wiki/" (e.g. "wiki/concepts/foo.md"), so it
    #    is used as-is — prefixing another "wiki/" would double the segment.
    msg = f"{action} {page_path}"
    repair_summary = _lint_and_repair_after_save(db_path, workspace, page_path,
                                                  client=client, model=model or "", language=language)
    if repair_summary:
        msg += f"\n🔧 Post-save repair: {repair_summary}"
    return msg
