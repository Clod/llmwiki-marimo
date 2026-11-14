"""Individual repair actions — one function per lint check type.

Each function accepts a LintIssue and the runtime context (db_path, workspace,
optional LLM client) and returns a RepairResult describing what was done.
"""

import json
import posixpath
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
from domain.i18n import get_locale, with_content_directive
from .report import RepairResult

logger = logging.getLogger(__name__)


def _insert_into_section(content: str, heading: str, line: str) -> str:
    """Put `line` at the end of `heading`'s section, before the next `## ` heading.

    Appending to the end of the file instead would land the line under whatever
    section happens to come last — which for a generated page is "## Sources", a
    list of the documents the page was written from.
    """
    start = content.index(heading) + len(heading)
    nxt = content.find("\n## ", start)
    end = len(content) if nxt == -1 else nxt
    section = content[start:end].rstrip("\n")
    return f"{content[:start]}{section}\n{line}\n{content[end:]}"


def _relative_link(from_page: str, to_page: str) -> str:
    """Relative href (always '/'-separated) from one /wiki/.../x.md page to another.

    Uses posixpath so the result never contains backslashes on Windows, which
    would break the markdown link.
    """
    return posixpath.relpath(to_page, start=posixpath.dirname(from_page))


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
    language: str = "en",
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
        extraction = extract_structured(doc_meta, page_contents, llm_client, model, language=language)
        summary_md = build_summary_page(doc_meta, extraction, language=language)
        wiki_slug = make_wiki_slug(src["filename"])
        name = src["filename"].rsplit(".", 1)[0].replace("-", " ").replace("_", " ").title()

        result = create_page(
            db_path, workspace, "/wiki/summaries/", wiki_slug,
            name, summary_md, [], overwrite=True, source_document_id=src_id,
            clear_stale=True,
        )
        update_references(db_path, result["id"], summary_md, "/wiki/summaries/")

        return RepairResult(
            check="stale", page=issue.page,
            action="regenerated", success=True,
            message=f"Regenerated from source: {src['filename']}",
            written=f"summaries/{wiki_slug}",
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
    language: str = "en",
) -> RepairResult:
    """Append a "See also" link from page A to page B, then rebuild references.

    The section header is localized to the wiki language so an es page keeps a
    single "## Véase también" section instead of gaining a duplicate English one.
    """
    from domain.tools.wiki_fs import append_to_page, read_page, write_page_content

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

    # Idempotency — already linked to B at this relative href?
    if f"]({rel})" in content_a:
        return RepairResult(
            check="missing_xref", page=a_path,
            action="skipped", success=True,
            message="already linked",
        )

    loc = get_locale(language)
    see_also = f"## {loc.h_see_also}"
    bullet = f"- [{title_b}]({rel})"
    if see_also in content_a:
        # Write INTO the section, not at the end of the file. Generated pages put
        # "See also" before "Sources", so appending would drop the link under a
        # heading that lists the documents the page was written from.
        updated = _insert_into_section(content_a, see_also, bullet)
        write_page_content(db_path, workspace, dir_a, slug_a, updated)
    else:
        block = f"{see_also}\n\n{bullet}"
        # No section yet: open one just above Sources when that heading exists,
        # matching how inject_see_also positions it at generation time.
        sources = f"## {loc.h_sources}"
        if sources in content_a:
            updated = content_a.replace(sources, f"{block}\n\n{sources}", 1)
            write_page_content(db_path, workspace, dir_a, slug_a, updated)
        else:
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
        message=f"Linked {a_path} → {b_path}",
    )


# ── Missing concept ───────────────────────────────────────────────────────────

def repair_missing_concept(
    issue: LintIssue,
    db_path: str,
    workspace: Path,
    llm_client,
    model: str,
    language: str = "en",
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
    # Generate the page in the wiki's content language (no-op for English).
    prompt = with_content_directive(prompt, language)

    try:
        response = llm_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        content = (response.choices[0].message.content or "").strip()

        result = create_page(
            db_path, workspace, "/wiki/concepts/", slug,
            title, content, [], overwrite=False,
        )
        update_references(db_path, result["id"], content, "/wiki/concepts/")
        update_index(workspace, f"concepts/{slug}.md", title, "concepts", language=language)

        return RepairResult(
            check="missing_concept", page=issue.page,
            action="concept_created", success=True,
            message=f"Created wiki/concepts/{concept_file}",
            written=f"concepts/{slug}",
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


def repair_vocab_collision(
    issue: LintIssue,
    db_path: str,
    workspace: Path,
) -> RepairResult:
    """Drop a colliding alias from the GENERATED artifact (machine-owned only).

    A `vocab_collision` can come from two places: the generated artifact
    (`.llmwiki/aliases.generated.toml`, machine-owned) or a hand override in
    `wiki_config.toml` (human-authored). This repair only ever edits the generated
    artifact — never the human's config. If the offending alias isn't in the
    artifact, it came from a hand override, so we surface it and skip rather than
    silently rewrite a file the user wrote by hand.
    """
    from domain.chat.vocabulary import (
        normalize,
        read_generated_aliases,
        write_generated_aliases,
    )

    alias_norm = normalize(issue.alias)
    current = read_generated_aliases(workspace)
    in_artifact = any(
        normalize(a) == alias_norm for aliases in current.values() for a in aliases
    )
    if not alias_norm or not in_artifact:
        return RepairResult(
            check="vocab_collision", page=issue.page,
            action="skipped", success=True,
            message=(
                f"Alias '{issue.alias}' is a hand override in wiki_config.toml, not in the "
                "generated artifact — resolve it by hand or add it to [falsos_sinonimos]"
            ),
        )

    cleaned = {
        canonical: kept
        for canonical, aliases in current.items()
        if (kept := [a for a in aliases if normalize(a) != alias_norm])
    }
    write_generated_aliases(workspace, cleaned)
    return RepairResult(
        check="vocab_collision", page=issue.page,
        action="deleted", success=True,
        message=f"Dropped colliding alias '{issue.alias}' from the generated artifact",
    )


# ── Images kept from PDF sources ──────────────────────────────────────────────

def _figure_pages(workspace: Path, doc_id: str) -> dict[str, int]:
    """file → page of the images of a document, from its `figures.json`."""
    from domain.ingestion.images import FIGURES_FILENAME, document_dir

    try:
        figures = json.loads((document_dir(workspace, doc_id) / FIGURES_FILENAME).read_text(encoding="utf-8"))
        return {f["file"]: int(f["page"]) for f in figures}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _rewrite_image_links(
    text: str, workspace: Path, page: int | None, own_doc: str | None,
    by_stem: dict[str, str],
) -> tuple[str, int, int]:
    """Rewrite the image links of `text`; return (text, restored, replaced).

    A link whose file exists is kept. A link in the old form whose image was
    extracted again becomes the new form. Any other missing link becomes
    "[Imagen no disponible, página N]". `page` is the page of `text` when it is
    one page of a source; otherwise the page comes from the alt text or from
    `figures.json`.
    """
    from domain.ingestion.images import (
        alt_page, image_link, image_path, links_in, unavailable_text,
    )

    out, last, restored, replaced = [], 0, 0, 0
    for m, old in links_in(text):
        if old:
            doc = own_doc or by_stem.get(m.group("stem").rsplit("/", 1)[-1])
            file = m.group("file")
            n = page or (_figure_pages(workspace, doc).get(file) if doc else None)
            if doc and n and image_path(workspace, doc, file):
                new, restored = image_link(doc, file, n), restored + 1
            else:
                new, replaced = unavailable_text(n or "?"), replaced + 1
        else:
            if image_path(workspace, m.group("doc"), m.group("file")):
                continue
            n = alt_page(m.group("alt")) or page
            new, replaced = unavailable_text(n or "?"), replaced + 1
        out.append(text[last:m.start()])
        out.append(new)
        last = m.end()
    out.append(text[last:])
    return "".join(out), restored, replaced


def repair_missing_image(
    issue: LintIssue,
    db_path: str,
    workspace: Path,
) -> RepairResult:
    """Restore the images a text links to, or replace the links with a note.

    The images are extracted again with opendataloader-pdf when the source file
    is still in `sources/`; no model is called and the stored text is not
    extracted again. A link whose source is gone becomes
    "[Imagen no disponible, página N]". Links in the old form are rewritten to
    the new form when their image comes back.
    """
    from domain.ingestion.extractor import extract_images
    from domain.ingestion.images import document_dir, image_path, links_in
    from domain.tools.wiki_fs import read_page, write_page_content

    def fail(message: str) -> RepairResult:
        return RepairResult(check="missing_image", page=issue.page, action="failed",
                            success=False, message=message)

    with get_connection(db_path) as conn:
        sources = [dict(r) for r in conn.execute(
            "SELECT id, filename, relative_path FROM documents WHERE source_kind = 'source'")]
    by_stem = {Path(s["filename"]).stem: s["id"] for s in sources}
    by_id = {s["id"]: s for s in sources}
    own = next((s for s in sources if s["relative_path"] == issue.page), None)

    if own is not None:
        with get_connection(db_path) as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, page, content FROM document_pages WHERE document_id = ? ORDER BY page",
                (own["id"],))]
        texts = [r["content"] or "" for r in rows]
    else:
        dir_path, slug = _parse_page_path(issue.page)
        content = read_page(db_path, workspace, dir_path, slug)
        if content is None:
            return fail(f"Page not found: {issue.page}")
        texts = [content]

    # The documents whose images are needed.
    needed: set[str] = set()
    for text in texts:
        for m, old in links_in(text):
            if old:
                doc = own["id"] if own else by_stem.get(m.group("stem").rsplit("/", 1)[-1])
            else:
                doc = m.group("doc") if image_path(workspace, m.group("doc"), m.group("file")) is None else None
            if doc:
                needed.add(doc)

    # Extract again, when the source file is still there.
    extracted: list[str] = []
    for doc in sorted(needed):
        src = by_id.get(doc)
        if src is None or not (workspace / src["relative_path"]).is_file():
            continue
        try:
            extract_images(workspace / src["relative_path"], document_dir(workspace, doc))
        except Exception as exc:  # noqa: BLE001 — Java or LibreOffice missing, corrupt PDF
            return fail(f"Could not extract the images of {src['filename']}: {exc}")
        extracted.append(doc)

    restored = replaced = 0
    if own is not None:
        with get_connection(db_path) as conn:
            with conn:
                new_pages = []
                for row, text in zip(rows, texts):
                    new, r, p = _rewrite_image_links(text, workspace, row["page"], own["id"], by_stem)
                    restored, replaced = restored + r, replaced + p
                    new_pages.append(new)
                    if new != text:
                        conn.execute("UPDATE document_pages SET content = ? WHERE id = ?", (new, row["id"]))
                conn.execute("UPDATE documents SET content = ? WHERE id = ?",
                             ("\n\n---\n\n".join(new_pages), own["id"]))
                for chunk in conn.execute(
                        "SELECT id, page, content FROM document_chunks WHERE document_id = ?",
                        (own["id"],)).fetchall():
                    new, _, _ = _rewrite_image_links(chunk["content"] or "", workspace,
                                                     chunk["page"], own["id"], by_stem)
                    if new != chunk["content"]:
                        conn.execute("UPDATE document_chunks SET content = ? WHERE id = ?",
                                     (new, chunk["id"]))
    else:
        new, restored, replaced = _rewrite_image_links(texts[0], workspace, None, None, by_stem)
        if new != texts[0]:
            write_page_content(db_path, workspace, dir_path, slug, new)

    message = f"{restored} image link(s) restored, {replaced} replaced with a note"
    if extracted:
        message += f" (images extracted again for {len(extracted)} source(s))"
    return RepairResult(
        check="missing_image", page=issue.page,
        action="images_restored" if restored else "links_replaced", success=True, message=message,
    )


def repair_orphan_image(
    issue: LintIssue,
    db_path: str,
    workspace: Path,
) -> RepairResult:
    """Delete an image no link uses. The use is checked again here: a repair that
    ran before this one may have linked the file."""
    from domain.ingestion.images import DESC_SUFFIX, image_path
    from domain.lint.image_checks import linked_files

    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM documents WHERE source_kind = 'source' AND relative_path = ?",
            (issue.page,)).fetchone()
    path = image_path(workspace, row["id"], issue.target) if row else None
    if path is None:
        return RepairResult(check="orphan_image", page=issue.page, action="skipped",
                            success=True, message=f"{issue.target} no longer exists")
    if issue.target.endswith(DESC_SUFFIX):
        if image_path(workspace, row["id"], issue.target.removesuffix(DESC_SUFFIX)) is not None:
            return RepairResult(check="orphan_image", page=issue.page, action="skipped",
                                success=True, message=f"the image of {issue.target} exists now")
    elif issue.target in linked_files(db_path, workspace, row["id"]):
        return RepairResult(check="orphan_image", page=issue.page, action="skipped",
                            success=True, message=f"{issue.target} is used by a link now")
    path.unlink()
    return RepairResult(check="orphan_image", page=issue.page, action="deleted",
                        success=True, message=f"Deleted orphan image: {issue.target}")
