"""Individual repair actions — one function per lint check type.

Each function accepts a LintIssue and the runtime context (db_path, workspace,
optional LLM client) and returns a RepairResult describing what was done.
"""

import json
import os
import re
import logging
from pathlib import Path

from domain.lint.report import LintIssue
from domain.lint.markers import (
    DATA_GAP_BLOCK_RE,
    DATA_GAP_NOTE,
    contradiction_marker,
    fts_safe,
)
from domain.tools.db import get_connection
from domain.tools.references import update_references
from domain.ingestion.index_manager import update_index
from .report import RepairResult

logger = logging.getLogger(__name__)


def _relative_link(from_page: str, to_page: str) -> str:
    """On-disk relative href from one /wiki/.../x.md page to another."""
    return os.path.relpath(to_page, start=os.path.dirname(from_page))


def _parse_page_path(page: str) -> tuple[str, str]:
    """Split '/wiki/concepts/snow-white.md' into ('/wiki/concepts/', 'snow-white')."""
    last_slash = page.rfind("/")
    dir_path = page[: last_slash + 1]
    slug = page[last_slash + 1 :].removesuffix(".md")
    return dir_path, slug


# ── Orphan ────────────────────────────────────────────────────────────────────

def repair_orphan(
    issue: LintIssue,
    db_path: str,
    workspace: Path,
) -> RepairResult:
    """Delete a concept page that nothing links to."""
    from domain.tools.wiki_fs import delete_page

    dir_path, slug = _parse_page_path(issue.page)
    deleted = delete_page(db_path, workspace, dir_path, slug)
    if deleted:
        return RepairResult(
            check="orphan", page=issue.page,
            action="deleted", success=True,
            message=f"Deleted orphan page: {issue.page}",
        )
    return RepairResult(
        check="orphan", page=issue.page,
        action="failed", success=False,
        message=f"Page not found on disk or in DB: {issue.page}",
    )


# ── Stale ─────────────────────────────────────────────────────────────────────

def repair_stale(
    issue: LintIssue,
    db_path: str,
    workspace: Path,
    llm_client,
    model: str,
) -> RepairResult:
    """Regenerate a summary page whose source document was updated after it."""
    from domain.tools.wiki_fs import create_page
    from domain.ingestion.wiki_generator import (
        extract_structured, build_summary_page, make_wiki_slug,
    )

    with get_connection(db_path) as conn:
        wiki_doc = conn.execute(
            "SELECT id, source_document_id FROM documents "
            "WHERE path || filename = ?",
            (issue.page,),
        ).fetchone()

        if not wiki_doc or not wiki_doc["source_document_id"]:
            return RepairResult(
                check="stale", page=issue.page,
                action="skipped", success=True,
                message="No source_document_id — only summary pages can be auto-regenerated",
            )

        src_id = wiki_doc["source_document_id"]
        src = conn.execute(
            "SELECT filename, file_type, page_count, parser FROM documents WHERE id=?",
            (src_id,),
        ).fetchone()
        if not src:
            return RepairResult(
                check="stale", page=issue.page,
                action="failed", success=False,
                message=f"Source document {src_id} not found in DB",
            )

        pages = conn.execute(
            "SELECT page, content FROM document_pages "
            "WHERE document_id=? ORDER BY page",
            (src_id,),
        ).fetchall()

    page_contents = [(r["page"], r["content"]) for r in pages]
    if not page_contents:
        return RepairResult(
            check="stale", page=issue.page,
            action="failed", success=False,
            message=f"No page text stored for source '{src['filename']}'",
        )

    try:
        doc_meta = {
            "filename": src["filename"],
            "file_type": src["file_type"],
            "page_count": src["page_count"] or len(page_contents),
            "parser": src["parser"] or "unknown",
        }
        extraction = extract_structured(doc_meta, page_contents, llm_client, model)
        summary_md = build_summary_page(doc_meta, extraction)
        wiki_slug = make_wiki_slug(src["filename"])
        name = src["filename"].rsplit(".", 1)[0].replace("-", " ").replace("_", " ").title()

        result = create_page(
            db_path, workspace, "/wiki/summaries/", wiki_slug,
            name, summary_md, [], overwrite=True, source_document_id=src_id,
        )
        update_references(db_path, result["id"], summary_md, "/wiki/summaries/")

        return RepairResult(
            check="stale", page=issue.page,
            action="regenerated", success=True,
            message=f"Regenerated from source: {src['filename']}",
        )
    except Exception as exc:
        logger.exception("repair_stale failed for %s", issue.page)
        return RepairResult(
            check="stale", page=issue.page,
            action="failed", success=False,
            message=str(exc),
        )


# ── Missing cross-reference ───────────────────────────────────────────────────

def repair_missing_xref(
    issue: LintIssue,
    db_path: str,
    workspace: Path,
) -> RepairResult:
    """Append a ## See also link from page A to page B, then rebuild references."""
    from domain.tools.wiki_fs import read_page, append_to_page

    b_path = issue.related_page
    if not b_path:
        return RepairResult(
            check="missing_xref", page=issue.page,
            action="skipped", success=True,
            message="missing related_page",
        )

    a_path = issue.page
    dir_a, slug_a = _parse_page_path(a_path)
    content_a = read_page(db_path, workspace, dir_a, slug_a)
    if content_a is None:
        return RepairResult(
            check="missing_xref", page=a_path,
            action="failed", success=False,
            message=f"Page not found: {a_path}",
        )

    rel = _relative_link(a_path, b_path)

    # Derive B's display title from the DB, fall back to slug
    slug_b = b_path.rsplit("/", 1)[-1].removesuffix(".md")
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT id, title FROM documents WHERE path||filename=?",
            (b_path,),
        ).fetchone()
    title_b = (row["title"] if row and row["title"] else slug_b.replace("-", " ").title())

    # Idempotency — already linked?
    if f"]({rel})" in content_a or "](#" in content_a:
        if rel in content_a:
            return RepairResult(
                check="missing_xref", page=a_path,
                action="skipped", success=True,
                message="already linked",
            )

    if "## See also" in content_a:
        bullet = f"- [{title_b}]({rel})"
        append_to_page(db_path, workspace, dir_a, slug_a, bullet)
    else:
        block = f"## See also\n\n- [{title_b}]({rel})"
        append_to_page(db_path, workspace, dir_a, slug_a, block)

    # Re-read and rebuild references so the links_to edge is recorded
    full_content = read_page(db_path, workspace, dir_a, slug_a)
    with get_connection(db_path) as conn:
        r = conn.execute(
            "SELECT id FROM documents WHERE path||filename=?", (a_path,)
        ).fetchone()
    if r:
        update_references(db_path, r["id"], full_content, dir_a)

    return RepairResult(
        check="missing_xref", page=a_path,
        action="xref_added", success=True,
        message=f"Linked → {b_path}",
    )


# ── Missing concept ───────────────────────────────────────────────────────────

def repair_missing_concept(
    issue: LintIssue,
    db_path: str,
    workspace: Path,
    llm_client,
    model: str,
) -> RepairResult:
    """Create a missing concept page that a wiki page links to but doesn't exist."""
    from domain.tools.wiki_fs import create_page
    from domain.tools.search import search_chunks

    # Parse concept filename from description:
    # "Links to 'concepts/snow-white.md' which doesn't exist on disk"
    m = re.search(r"concepts/([^']+\.md)", issue.description)
    if not m:
        return RepairResult(
            check="missing_concept", page=issue.page,
            action="failed", success=False,
            message=f"Could not parse concept filename from: {issue.description}",
        )

    concept_file = m.group(1)           # e.g. "snow-white.md"
    slug = concept_file.removesuffix(".md")
    title = slug.replace("-", " ").title()

    # Gather context from existing wiki/source content
    search_term = slug.replace("-", " ")
    results = search_chunks(db_path, search_term, limit=5, scope="all")
    context_blocks = [r["content"][:600] for r in results[:3]]
    context = "\n\n---\n\n".join(context_blocks) if context_blocks else ""

    prompt = (
        f'Write a wiki concept page for "{title}".\n\n'
        + (f"Context from related documents:\n\n{context}\n\n" if context else "")
        + "Format as markdown with a # heading, a brief definition paragraph, "
        "and key points as bullet list."
    )

    try:
        response = llm_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        content = response.choices[0].message.content.strip()

        result = create_page(
            db_path, workspace, "/wiki/concepts/", slug,
            title, content, [], overwrite=False,
        )
        update_references(db_path, result["id"], content, "/wiki/concepts/")
        update_index(workspace, f"concepts/{slug}.md", title, "concepts")

        return RepairResult(
            check="missing_concept", page=issue.page,
            action="concept_created", success=True,
            message=f"Created wiki/concepts/{concept_file}",
        )
    except FileExistsError:
        return RepairResult(
            check="missing_concept", page=issue.page,
            action="skipped", success=True,
            message=f"wiki/concepts/{concept_file} already exists",
        )
    except Exception as exc:
        logger.exception("repair_missing_concept failed for %s", issue.page)
        return RepairResult(
            check="missing_concept", page=issue.page,
            action="failed", success=False,
            message=str(exc),
        )


# ── Contradiction ─────────────────────────────────────────────────────────────

def repair_contradiction(
    issue: LintIssue,
    db_path: str,
    workspace: Path,
    llm_client=None,
    model: str = "",
) -> RepairResult:
    """Append an idempotent ⚠️ warning callout to page A flagging the contradiction."""
    from domain.tools.wiki_fs import read_page, append_to_page

    a_path = issue.page
    b_path = issue.related_page
    dir_a, slug_a = _parse_page_path(a_path)
    content_a = read_page(db_path, workspace, dir_a, slug_a)
    if content_a is None:
        return RepairResult(
            check="contradiction", page=a_path,
            action="failed", success=False,
            message=f"Page not found: {a_path}",
        )

    marker = contradiction_marker(b_path)
    if marker in content_a:
        return RepairResult(
            check="contradiction", page=a_path,
            action="skipped", success=True,
            message="already flagged",
        )

    rel = _relative_link(a_path, b_path) if b_path else b_path
    detail = issue.description
    note = (
        f"{marker}\n"
        f"> ⚠️ **Possible contradiction** with [{b_path}]({rel}): {detail}\n"
        f"> Review both pages and resolve the conflicting claims."
    )
    append_to_page(db_path, workspace, dir_a, slug_a, note)

    full_content = read_page(db_path, workspace, dir_a, slug_a)
    with get_connection(db_path) as conn:
        r = conn.execute(
            "SELECT id FROM documents WHERE path||filename=?", (a_path,)
        ).fetchone()
    if r:
        update_references(db_path, r["id"], full_content, dir_a)

    return RepairResult(
        check="contradiction", page=a_path,
        action="contradiction_flagged", success=True,
        message=f"Flagged contradiction with {b_path}",
    )


# ── Data gap ──────────────────────────────────────────────────────────────────

def repair_data_gap(
    issue: LintIssue,
    db_path: str,
    workspace: Path,
    llm_client=None,
    model: str = "",
) -> RepairResult:
    """Insert a DATA_GAP TODO note into the most-related existing wiki page."""
    from domain.tools.wiki_fs import read_page, append_to_page
    from domain.tools.search import search_chunks

    slug = issue.topic
    if not slug:
        return RepairResult(
            check="data_gap", page=issue.page,
            action="skipped", success=True,
            message="empty topic slug",
        )

    # Skip if the topic is already covered by a source
    query = fts_safe(slug.replace("-", " "))
    if query and search_chunks(db_path, query, limit=1, scope="sources"):
        return RepairResult(
            check="data_gap", page=issue.page,
            action="skipped", success=True,
            message=f"topic '{slug}' already covered by a source",
        )

    host_path = issue.page
    dir_h, slug_h = _parse_page_path(host_path)
    host_content = read_page(db_path, workspace, dir_h, slug_h)
    if host_content is None:
        return RepairResult(
            check="data_gap", page=host_path,
            action="failed", success=False,
            message=f"Host page not found: {host_path}",
        )

    if f"<!-- DATA_GAP: {slug} -->" in host_content:
        return RepairResult(
            check="data_gap", page=host_path,
            action="skipped", success=True,
            message=f"DATA_GAP marker for '{slug}' already present",
        )

    title = slug.replace("-", " ").title()
    note = DATA_GAP_NOTE.format(slug=slug, title=title, suggestion=issue.suggestion)
    append_to_page(db_path, workspace, dir_h, slug_h, note)

    return RepairResult(
        check="data_gap", page=host_path,
        action="gap_noted", success=True,
        message=f"TODO note for '{slug}' in {host_path}",
    )


# ── Gap filled ────────────────────────────────────────────────────────────────

def repair_gap_filled(
    issue: LintIssue,
    db_path: str,
    workspace: Path,
) -> RepairResult:
    """Replace a DATA_GAP TODO note with a link once the topic has a wiki page."""
    from domain.tools.wiki_fs import create_page
    from domain.tools.search import search_chunks

    slug = issue.topic
    dir_h, slug_h = _parse_page_path(issue.page)
    from domain.tools.wiki_fs import read_page as _read_page
    host_content = _read_page(db_path, workspace, dir_h, slug_h)
    if host_content is None:
        return RepairResult(
            check="gap_filled", page=issue.page,
            action="failed", success=False,
            message=f"Host page not found: {issue.page}",
        )

    # Find the wiki page that now covers this topic
    query = fts_safe(slug.replace("-", " "))
    hits = search_chunks(db_path, query, limit=5, scope="wiki") if query else []

    # Pick first hit that is not the host page itself; prefer concepts/ over summaries/
    target = None
    for hit in sorted(hits, key=lambda h: (0 if "/concepts/" in h.get("path", "") else 1)):
        candidate = hit["path"] + hit["filename"]
        if candidate != issue.page:
            target = hit
            break

    if target is None:
        return RepairResult(
            check="gap_filled", page=issue.page,
            action="skipped", success=True,
            message=f"topic '{slug}' covered by source but no wiki page found yet",
        )

    target_path = target["path"] + target["filename"]
    target_title = target.get("title") or slug.title()
    rel = _relative_link(issue.page, target_path)
    replacement = f"> ℹ️ See [{target_title}]({rel}).\n"

    def _replace_block(m: re.Match) -> str:
        return replacement if m.group("slug") == slug else m.group(0)

    new_content = DATA_GAP_BLOCK_RE.sub(_replace_block, host_content)
    if new_content == host_content:
        return RepairResult(
            check="gap_filled", page=issue.page,
            action="skipped", success=True,
            message="DATA_GAP marker not found in content",
        )

    # Rewrite the host page preserving title and tags
    with get_connection(db_path) as conn:
        r = conn.execute(
            "SELECT id, title, tags FROM documents WHERE path||filename=?",
            (issue.page,),
        ).fetchone()
    tags = json.loads(r["tags"]) if r and r["tags"] else []
    page_title = (r["title"] if r else slug_h.replace("-", " ").title())

    res = create_page(
        db_path, workspace, dir_h, slug_h, page_title, new_content, tags, overwrite=True
    )
    update_references(db_path, res["id"], new_content, dir_h)

    return RepairResult(
        check="gap_filled", page=issue.page,
        action="gap_resolved", success=True,
        message=f"Linked '{slug}' → {target_path}",
    )
