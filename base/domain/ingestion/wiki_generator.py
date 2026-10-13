"""Wiki page generation via LLM.

Public API
----------
build_wiki_page()       — legacy single-pass (no longer on any path)
extract_structured()    — Phase 2: JSON extraction → ExtractionResult
build_summary_page()    — Phase 2: ExtractionResult → summary markdown
build_concept_page()    — Phase 2: concept → new/updated concept markdown
update_overview()       — Phase 2: LLM rewrites the narrative overview
make_wiki_slug()        — filename → url-safe slug
inject_see_also()       — inject a ## See also section into generated markdown
"""

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from domain.datasets.frontmatter import render_frontmatter, split_frontmatter
from domain.i18n import get_locale, with_content_directive
from domain.prompts import load_prompt

logger = logging.getLogger(__name__)

_MAX_INPUT_CHARS = 80_000
_LONG_DOC_HEAD_PAGES = 5
_LONG_DOC_TAIL_PAGES = 2

@dataclass
class ExtractedConcept:
    name: str
    category: str  # "entity" | "instrument" | "theme"
    insight: str
    aliases: list[str] = field(default_factory=list)  # other names for the SAME thing


@dataclass
class ExtractionResult:
    document_summary: str
    concepts: list[ExtractedConcept] = field(default_factory=list)


def extract_structured(
    doc_meta: dict,
    page_contents: list[tuple[int, str]],
    client,
    model: str,
    language: str = "en",
) -> ExtractionResult:
    """Phase 1 of 3: call LLM to extract summary + concepts as structured JSON.

    The content-language directive is appended to the system prompt so LLM prose
    values (document_summary, concept insights) come back in the wiki language.
    JSON keys and category values (entity|instrument|theme) stay English.
    """
    content = _prepare_content(page_contents)
    user_msg = load_prompt("extract_user_template", language).format(
        filename=doc_meta["filename"],
        file_type=doc_meta["file_type"],
        page_count=doc_meta["page_count"],
        content=content,
    )
    system = with_content_directive(load_prompt("extract_system", language), language)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.2,
    )
    raw = (response.choices[0].message.content or "").strip()
    return _parse_extraction(raw, doc_meta["filename"])


def _clean_aliases(raw) -> list[str]:
    """Keep only non-empty string aliases; tolerate a missing or malformed field."""
    if not isinstance(raw, list):
        return []
    return [a.strip() for a in raw if isinstance(a, str) and a.strip()]


def _parse_extraction(raw: str, filename: str) -> ExtractionResult:
    """Parse JSON response into ExtractionResult; fall back gracefully on error."""
    # Strip optional markdown fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
        summary = data.get("document_summary", "")
        concepts = [
            ExtractedConcept(
                name=c.get("name", ""),
                category=c.get("category", "theme"),
                insight=c.get("insight", ""),
                aliases=_clean_aliases(c.get("aliases")),
            )
            for c in data.get("concepts", [])
            if c.get("name")
        ]
        return ExtractionResult(document_summary=summary, concepts=concepts)
    except (json.JSONDecodeError, TypeError):
        logger.warning("extract_structured: JSON parse failed for %s, using fallback", filename)
        return ExtractionResult(document_summary=raw, concepts=[])


def extract_dataset_aliases(terms, client, model, language: str = "en") -> dict[str, list[str]]:
    """Phase-4 LLM pass: propose aliases for the closed set of known data terms.

    Mirrors ``extract_structured``'s shape — one JSON call, tolerant parse. Returns
    a ``{term: [alias, ...]}`` map with only non-empty string aliases; ``{}`` on
    empty input or a malformed response. The caller validates this against the
    wiki's coverage (dropping collisions) before persisting it, so this function
    only has to fetch and clean, never trust. The content-language directive makes
    the aliases come back in the wiki language.
    """
    terms = [t for t in terms if isinstance(t, str) and t.strip()]
    if not terms:
        return {}
    user_msg = load_prompt("dataset_alias_user_template", language).format(
        terms=json.dumps(sorted(terms), ensure_ascii=False)
    )
    system = with_content_directive(load_prompt("dataset_alias_system", language), language)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.2,
    )
    raw = (response.choices[0].message.content or "").strip()
    return _parse_dataset_aliases(raw)


def _parse_dataset_aliases(raw: str) -> dict[str, list[str]]:
    """Parse the ``{"aliases": {term: [...]}}`` JSON; ``{}`` on any malformation."""
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
        aliases = data.get("aliases", {})
        if not isinstance(aliases, dict):
            return {}
        return {
            str(term): _clean_aliases(al)
            for term, al in aliases.items()
            if isinstance(term, str) and term.strip()
        }
    except (json.JSONDecodeError, TypeError, AttributeError):
        logger.warning("extract_dataset_aliases: JSON parse failed, using empty map")
        return {}


def build_summary_page(
    doc_meta: dict,
    extraction: ExtractionResult,
    language: str = "en",
) -> str:
    """Phase 2 of 3: build the summary wiki page from the extraction result.

    No LLM call — pure string construction. Section headers and field labels
    are drawn from the locale so a Spanish wiki produces fully Spanish pages.
    """
    loc = get_locale(language)
    ingested_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = _title_from_filename(doc_meta["filename"])

    concept_links = ""
    if extraction.concepts:
        slugs = [make_wiki_slug(c.name) for c in extraction.concepts]
        items = [f"- [{c.name}](../concepts/{s}.md)" for c, s in zip(extraction.concepts, slugs)]
        concept_links = f"\n## {loc.h_related_concepts}\n\n" + "\n".join(items) + "\n"

    return (
        f"# {title}\n\n"
        f"**{loc.lbl_source}:** {doc_meta['filename']} | "
        f"**{loc.lbl_type}:** {doc_meta['file_type']} | "
        f"**{loc.lbl_pages}:** {doc_meta['page_count']} | "
        f"**{loc.lbl_ingested}:** {ingested_at}\n\n"
        f"## {loc.h_summary}\n\n{extraction.document_summary}\n"
        f"{concept_links}\n"
        f"## {loc.h_source_information}\n\n"
        f"- **{loc.lbl_file}:** {doc_meta['filename']}\n"
        f"- **{loc.lbl_type}:** {doc_meta['file_type']}\n"
        f"- **{loc.lbl_pages}:** {doc_meta['page_count']}\n"
        f"- **{loc.lbl_ingested}:** {ingested_at}\n"
        f"- **{loc.lbl_parser}:** {doc_meta.get('parser', 'unknown')}\n\n"
        f"[^1]: {doc_meta['filename']}\n"
    )


def build_concept_page(
    concept: ExtractedConcept,
    filename: str,
    existing_content: str | None,
    client,
    model: str,
    language: str = "en",
) -> str:
    """Phase 3 of 3: generate or update a concept wiki page.

    The content-language directive is appended to the system prompt so the LLM
    writes the page in the wiki language. Section headers in the template are
    filled from the locale before the call.
    """
    loc = get_locale(language)
    if existing_content:
        # Strip any frontmatter before handing the page to the model: it no
        # longer needs to see (and possibly reproduce) that block, and
        # the concept update template itself wraps {existing} in a `---` pair —
        # a real delimiter collision if {existing} starts with its own `---`.
        _, existing_body = split_frontmatter(existing_content)
        user_msg = load_prompt("concept_update_template", language).format(
            name=concept.name,
            filename=filename,
            insight=concept.insight,
            existing=existing_body,
        )
    else:
        user_msg = load_prompt("concept_new_template", language).format(
            name=concept.name,
            category=concept.category,
            filename=filename,
            insight=concept.insight,
            h_definition=loc.h_definition,
            h_key_characteristics=loc.h_key_characteristics,
            h_context=loc.h_context,
            h_sources=loc.h_sources,
        )

    system = with_content_directive(load_prompt("concept_system", language), language)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
    )
    return _strip_wrapping_fence(response.choices[0].message.content)


def _strip_wrapping_fence(text: str | None) -> str:
    """Remove a single markdown code fence that wraps the entire output.

    Some models return a whole page wrapped in ```​markdown ... ```; written
    verbatim that renders the page as one literal code block (see the
    comparisson.md regression). Strip only an *outer* wrapper — the first line
    opens a fence and the last line is a bare closing fence — so genuine fenced
    code blocks inside the page are preserved.

    `text` may be ``None`` when the model returns a tool call or an empty/
    filtered completion; it is treated as an empty string.
    """
    stripped = (text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 2 or lines[-1].strip() != "```":
        return stripped
    return "\n".join(lines[1:-1]).strip()


def structure_chat_content(
    title: str,
    category: str,
    raw_content: str,
    existing_content: str | None,
    client,
    model: str,
    language: str = "en",
) -> str:
    """Apply an LLM structuring pass to chat-sourced content before saving to the wiki.

    Returns structured markdown with the standard sections. Frontmatter
    (type/title/tags/sources) is no longer part of this output — create_page
    (domain/tools/wiki_fs.py) renders it in code. Raises on LLM failure —
    callers should catch and surface the error. The content-language directive
    is appended to the system prompt so the page is written in the wiki language.
    """
    loc = get_locale(language)
    if existing_content:
        # Strip any frontmatter before handing the page to the model — same
        # reasoning as build_concept_page: the model shouldn't reproduce it,
        # and the chat update template wraps {existing} in a `---` pair.
        _, existing_body = split_frontmatter(existing_content)
        user_msg = load_prompt("chat_concept_update_template", language).format(
            name=title,
            content=raw_content,
            existing=existing_body,
        )
    else:
        user_msg = load_prompt("chat_concept_new_template", language).format(
            name=title,
            category=category,
            content=raw_content,
            h_definition=loc.h_definition,
            h_key_characteristics=loc.h_key_characteristics,
            h_context=loc.h_context,
            h_sources=loc.h_sources,
        )

    system = with_content_directive(load_prompt("concept_system", language), language)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
    )
    return _strip_wrapping_fence(response.choices[0].message.content)


def update_overview(
    current_overview: str,
    new_summary: str,
    all_concept_names: list[str],
    client,
    model: str,
    language: str = "en",
) -> str:
    """Rewrite the narrative overview page incorporating the new knowledge.

    The content-language directive is appended to the system prompt so the
    narrative prose is written in the wiki language.

    overview.md doesn't go through create_page (its caller, pipeline.py,
    writes it directly) and isn't OKF-reserved like index.md/log.md, so it
    needs its own frontmatter block — rendered here, the same "never trust the
    model to transcribe it" rule as everywhere else. `title` comes from the
    locale's `overview_title` (the same string the initial scaffold's `#`
    heading uses — see pipeline.py), so the block's title always matches the
    page's own heading in the wiki's language.
    """
    concept_list = ", ".join(all_concept_names) if all_concept_names else "none yet"
    # Strip any frontmatter before handing the current overview to the model —
    # same reasoning as build_concept_page: it shouldn't reproduce it, and
    # the overview template wraps {current_overview} in a `---` pair, a real
    # delimiter collision now that overview.md carries its own `---` block.
    _, current_body = split_frontmatter(current_overview)
    user_msg = load_prompt("overview_template", language).format(
        current_overview=current_body,
        new_summary=new_summary,
        concept_list=concept_list,
    )
    system = with_content_directive(load_prompt("overview_system", language), language)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.4,
    )
    body = _strip_wrapping_fence(response.choices[0].message.content)
    loc = get_locale(language)
    frontmatter_block = render_frontmatter({"type": "overview", "title": loc.overview_title})
    return frontmatter_block + body


# ── Legacy single-pass (no longer used) ───────────────────────────────────


def build_wiki_page(
    doc_meta: dict,
    page_contents: list[tuple[int, str]],
    client,
    model: str,
) -> str:
    """Legacy: call the LLM and return the full wiki page markdown string."""
    content = _prepare_content(page_contents)
    ingested_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = _title_from_filename(doc_meta["filename"])

    user_msg = load_prompt("ingest_user_template", "en").format(
        filename=doc_meta["filename"],
        file_type=doc_meta["file_type"],
        page_count=doc_meta["page_count"],
        parser=doc_meta.get("parser", "unknown"),
        ingested_at=ingested_at,
        title=title,
        content=content,
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": load_prompt("ingest_system", "en")},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
    )
    return _strip_wrapping_fence(response.choices[0].message.content)


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_wiki_slug(filename: str) -> str:
    """Convert a filename or concept name to a wiki page slug.

    'My Report 2024.pdf' → 'my-report-2024'
    'Federal Reserve'    → 'federal-reserve'
    'Política Común'     → 'politica-comun'
    """
    stem = Path(filename).stem if "." in filename else filename
    # Decompose accented characters and drop combining marks (é→e, ñ→n, etc.)
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    slug = stem.lower().replace(" ", "-").replace("_", "-")
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def strip_accents(text: str) -> str:
    """Drop combining marks: 'Panel Líder' → 'Panel Lider'.

    ``slugify`` already strips them when it builds a slug, so a page filed as
    'Panel Líder' is stored as ``panel-lider.md``. ``inject_see_also`` matches
    that slug against page text, and the text keeps its accents, so the two
    never met until the text is stripped the same way.
    """
    return "".join(
        c for c in unicodedata.normalize("NFKD", text or "")
        if not unicodedata.combining(c)
    )


def _prepare_content(page_contents: list[tuple[int, str]]) -> str:
    if not page_contents:
        return "(no content extracted)"
    full = "\n\n---\n\n".join(f"[Page {n}]\n{md}" for n, md in page_contents)
    if len(full) <= _MAX_INPUT_CHARS:
        return full
    head = page_contents[:_LONG_DOC_HEAD_PAGES]
    tail = page_contents[-_LONG_DOC_TAIL_PAGES:]
    skipped = len(page_contents) - _LONG_DOC_HEAD_PAGES - _LONG_DOC_TAIL_PAGES
    head_text = "\n\n---\n\n".join(f"[Page {n}]\n{md}" for n, md in head)
    tail_text = "\n\n---\n\n".join(f"[Page {n}]\n{md}" for n, md in tail)
    return f"{head_text}\n\n--- [{skipped} pages omitted for length] ---\n\n{tail_text}"


def _title_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    return stem.replace("-", " ").replace("_", " ").strip().title()


def inject_see_also(
    content: str,
    related_pages: list[dict],
    language: str = "en",
) -> str:
    """Scan generated markdown for mentions of known wiki pages and inject a See also section.

    related_pages: list of {"title": str, "rel_path": str}

    Matching is case-insensitive and accent-insensitive: if the slug
    (hyphens→spaces) appears anywhere in the content body, the page is included.
    Already-linked pages are skipped.

    The section header and the anchor header used to position the block are taken
    from the locale — so an ``es`` page inserts ``## Véase también`` before
    ``## Fuentes``, while an ``en`` page uses ``## See also`` / ``## Sources``.
    This is the one position-sensitive spot where a localized header matters.
    """
    loc = get_locale(language)
    # Accents are stripped from the haystack, not from the needle: slugify has
    # already stripped them from every slug, so 'Panel Líder' in the prose and
    # 'panel-lider.md' on disk now match.
    content_lower = strip_accents(content.lower())
    matches = []
    for page in related_pages:
        slug_text = (
            page["rel_path"]
            .replace(".md", "")
            .replace("../concepts/", "")
            .replace("../summaries/", "")
            .replace("-", " ")
            .lower()
        )
        # Word-boundary match so a short slug doesn't match inside a larger word
        # (e.g. "art" inside "started"); skip pages already linked in the content.
        if slug_text and re.search(rf"\b{re.escape(slug_text)}\b", content_lower) and page["rel_path"] not in content:
            matches.append(page)

    if not matches:
        return content

    bullets = "\n".join(f"- [{p['title']}]({p['rel_path']})" for p in matches)

    # A page that already has the section (the `missing_xref` repair opens it before
    # the ingestion's last cross-link pass) gets the links inside it, not a second one.
    heading = re.search(rf"(?m)^## {re.escape(loc.h_see_also)}[ \t]*$", content)
    if heading:
        nxt = content.find("\n## ", heading.end())
        end = len(content) if nxt == -1 else nxt
        section = content[heading.end():end].rstrip("\n")
        return f"{content[:heading.end()]}{section}\n{bullets}\n{content[end:]}"

    see_also_block = f"\n## {loc.h_see_also}\n\n" + bullets + "\n"

    # Insert before the localized ## Sources header if present, otherwise append.
    # This must use the same locale as the page was generated with.
    sources_marker = f"\n## {loc.h_sources}"
    if sources_marker in content:
        return content.replace(sources_marker, see_also_block + sources_marker, 1)
    return content.rstrip() + "\n" + see_also_block
