"""Wiki page generation via LLM.

Public API
----------
build_wiki_page()       — legacy single-pass (kept for regenerate_wiki_pages)
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

from domain.i18n import get_locale, with_content_directive

logger = logging.getLogger(__name__)

_MAX_INPUT_CHARS = 80_000
_LONG_DOC_HEAD_PAGES = 5
_LONG_DOC_TAIL_PAGES = 2

# ── Legacy single-pass prompt ─────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a knowledge-base assistant. Your task is to write a structured wiki page
that summarises the provided document. Be factual, concise, and well-organised.
Use markdown formatting. Do not invent information that is not in the document."""

_USER_TEMPLATE = """\
Write a wiki page for the following document.

Document metadata:
- Filename: {filename}
- File type: {file_type}
- Pages: {page_count}
- Parser: {parser}
- Ingested: {ingested_at}

Document content:
---
{content}
---

Produce a wiki page with exactly these sections in this order:

# {title}

**Source:** {filename} | **Type:** {file_type} | **Pages:** {page_count} | **Ingested:** {ingested_at}

## Summary
(2–4 paragraphs: what the document is about, its purpose, main conclusions)

## Key Topics
(bullet list of the main themes and subjects covered)

## Key Entities
(bullet list of notable people, organisations, places, and dates mentioned)

## Important Data & Figures
(bullet list of notable numbers, statistics, metrics, or conclusions)

## Source Information
- **File:** {filename}
- **Type:** {file_type}
- **Pages:** {page_count}
- **Ingested:** {ingested_at}
- **Parser:** {parser}

Output only the markdown. Do not add any preamble or explanation outside the page."""


# ── Phase 2: structured extraction ───────────────────────────────────────────

_EXTRACT_SYSTEM = """\
You are a knowledge extraction assistant. Given a document, extract:
1. A concise summary (2-3 sentences)
2. Key concepts: entities, instruments, or themes worth a standalone wiki page.
   For each concept give a short, clean canonical name (a noun phrase — no
   parenthetical glosses, no "X vs Y") plus any aliases: other names for exactly
   the SAME thing (abbreviations, synonyms, an acronym's expansion). Never list a
   related-but-different concept as an alias.

Respond with valid JSON only — no markdown fences, no commentary."""

_EXTRACT_USER_TEMPLATE = """\
Extract knowledge from this document.

Document: {filename} ({file_type}, {page_count} pages)

Content:
---
{content}
---

Return JSON with this exact structure:
{{
  "document_summary": "2-3 sentence summary of the document",
  "concepts": [
    {{
      "name": "Short canonical name — a noun phrase, no parentheses or vs-comparisons",
      "category": "entity|instrument|theme",
      "insight": "The specific new insight this document adds about this concept",
      "aliases": ["other names for the exact same thing", "abbreviations, synonyms"]
    }}
  ]
}}

Extract 2-5 concepts. Only include concepts worth a dedicated wiki page. Use an
empty list for "aliases" when the concept has no common alternate names."""

_CONCEPT_SYSTEM = """\
You are a wiki page author. Write clear, factual concept pages in markdown.
Each page should have a brief definition, key characteristics, and context.
Never invent facts not supported by the provided content.

Preserve traceability above all. If the provided content carries inline citations \
— e.g. (wiki/summaries/cinderella.md) or (Cinderella.pdf, p. 3) — keep EVERY one \
exactly as written, attached to the same claim. Never drop, merge, summarise away, \
or reword a citation while reshaping the text. A claim you cannot keep its citation \
for must be removed, not left uncited."""

_CONCEPT_NEW_TEMPLATE = """\
Write a wiki concept page for: {name}

Category: {category}
New insight from document "{filename}": {insight}

Produce a markdown page with this structure:

---
tags: [{category}]
sources: [{filename}]
---

# {name}

## {h_definition}
(1-2 sentences defining the concept)

## {h_key_characteristics}
(bullet list)

## {h_context}
(how this concept relates to the broader domain)

## {h_sources}
- {filename}

Output only the markdown."""

_CONCEPT_UPDATE_TEMPLATE = """\
Update this existing wiki concept page for: {name}

New insight from document "{filename}": {insight}

Existing page:
---
{existing}
---

Integrate the new insight. Add the source to the Sources section as a new list item: - {filename}.
Keep all existing content. Output only the updated markdown page."""

_CHAT_CONCEPT_NEW_TEMPLATE = """\
Write a wiki concept page for: {name}

Category: {category}
Content synthesised from a chat conversation:
---
{content}
---

Produce a markdown page with this structure:

---
tags: [{category}]
sources: [chat]
---

# {name}

## {h_definition}
(1-2 sentences defining the concept)

## {h_key_characteristics}
(bullet list)

## {h_context}
(how this concept relates to the broader domain)

## {h_sources}
(list each distinct source cited in the content above, one per line — \
e.g. "- wiki/summaries/cinderella.md" or "- Cinderella.pdf, p. 3")

Keep every inline citation from the content above attached to the exact claim it \
supports — carry them verbatim into the Definition, Key Characteristics, and \
Context sections. Do NOT strip citations when reshaping into this structure, and \
do NOT replace them with a generic "Chat synthesis" line. Output only the markdown."""

_CHAT_CONCEPT_UPDATE_TEMPLATE = """\
Update this existing wiki concept page for: {name}

New content from a chat synthesis to integrate:
---
{content}
---

Existing page:
---
{existing}
---

Merge the new content into the existing page. Add insights, avoid duplication. \
Keep every inline citation from BOTH the existing page and the new content exactly \
as written, attached to its claim — never drop or reword one. In the Sources \
section, keep the existing entries and add any new distinct source cited in the new \
content (one per line, e.g. "- wiki/summaries/cinderella.md").
Keep all existing content intact. Output only the updated markdown page."""

_OVERVIEW_SYSTEM = """\
You are a knowledge base curator. Rewrite the overview page to reflect the current
state of the knowledge base. Be narrative and synthetic — do not just list pages."""

_OVERVIEW_TEMPLATE = """\
Update the knowledge base overview incorporating the new document.

Current overview:
---
{current_overview}
---

New document summary: {new_summary}

Known concepts: {concept_list}

Write an updated overview (3-5 paragraphs) that synthesises all the knowledge.
Output only the markdown (no frontmatter)."""


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
    user_msg = _EXTRACT_USER_TEMPLATE.format(
        filename=doc_meta["filename"],
        file_type=doc_meta["file_type"],
        page_count=doc_meta["page_count"],
        content=content,
    )
    system = with_content_directive(_EXTRACT_SYSTEM, language)
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
        user_msg = _CONCEPT_UPDATE_TEMPLATE.format(
            name=concept.name,
            filename=filename,
            insight=concept.insight,
            existing=existing_content,
        )
    else:
        user_msg = _CONCEPT_NEW_TEMPLATE.format(
            name=concept.name,
            category=concept.category,
            filename=filename,
            insight=concept.insight,
            h_definition=loc.h_definition,
            h_key_characteristics=loc.h_key_characteristics,
            h_context=loc.h_context,
            h_sources=loc.h_sources,
        )

    system = with_content_directive(_CONCEPT_SYSTEM, language)
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

    Returns structured markdown with YAML frontmatter and standard sections.
    Raises on LLM failure — callers should catch and surface the error.
    The content-language directive is appended to the system prompt so the page
    is written in the wiki language.
    """
    loc = get_locale(language)
    if existing_content:
        user_msg = _CHAT_CONCEPT_UPDATE_TEMPLATE.format(
            name=title,
            content=raw_content,
            existing=existing_content,
        )
    else:
        user_msg = _CHAT_CONCEPT_NEW_TEMPLATE.format(
            name=title,
            category=category,
            content=raw_content,
            h_definition=loc.h_definition,
            h_key_characteristics=loc.h_key_characteristics,
            h_context=loc.h_context,
            h_sources=loc.h_sources,
        )

    system = with_content_directive(_CONCEPT_SYSTEM, language)
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
    """
    concept_list = ", ".join(all_concept_names) if all_concept_names else "none yet"
    user_msg = _OVERVIEW_TEMPLATE.format(
        current_overview=current_overview,
        new_summary=new_summary,
        concept_list=concept_list,
    )
    system = with_content_directive(_OVERVIEW_SYSTEM, language)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.4,
    )
    return _strip_wrapping_fence(response.choices[0].message.content)


# ── Legacy single-pass (kept for regenerate_wiki_pages) ──────────────────────


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

    user_msg = _USER_TEMPLATE.format(
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
            {"role": "system", "content": _SYSTEM_PROMPT},
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

    Matching is case-insensitive: if the slug (hyphens→spaces) appears anywhere in the
    content body, the page is included. Already-linked pages are skipped.

    The section header and the anchor header used to position the block are taken
    from the locale — so an ``es`` page inserts ``## Véase también`` before
    ``## Fuentes``, while an ``en`` page uses ``## See also`` / ``## Sources``.
    This is the one position-sensitive spot where a localized header matters.
    """
    loc = get_locale(language)
    content_lower = content.lower()
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

    see_also_block = (
        f"\n## {loc.h_see_also}\n\n" + "\n".join(f"- [{p['title']}]({p['rel_path']})" for p in matches) + "\n"
    )

    # Insert before the localized ## Sources header if present, otherwise append.
    # This must use the same locale as the page was generated with.
    sources_marker = f"\n## {loc.h_sources}"
    if sources_marker in content:
        return content.replace(sources_marker, see_also_block + sources_marker, 1)
    return content.rstrip() + "\n" + see_also_block
