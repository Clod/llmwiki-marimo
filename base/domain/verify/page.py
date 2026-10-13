"""Verify a wiki page, sentence by sentence, against the text of its sources.

The context of a page depends on its kind:

1. A summary page (`documents.source_document_id` set): the text extracted from
   its source document, from `document_pages`.
2. A concept page: the text of every source document it cites
   (`document_references`, `reference_type = 'cites'`, plus the sources listed in
   its front-matter).
3. A page saved from the chat (`sources` has the entry `chat`): the wiki pages
   it links to and the source documents it cites.
4. A page with none of these has no context and is not verified.

The page text never changes. The result is stored by `domain/verify/store.py`.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from domain.datasets.frontmatter import parse_frontmatter, split_frontmatter
from domain.i18n import SUPPORTED_LANGUAGES, get_locale
from domain.tools.db import get_connection
from domain.verify import store
from domain.verify.core import verify_answer
from domain.verify.models import ContextPassage, VerificationReport
from domain.verify.verifier import Verifier

SUMMARY, CONCEPT, CHAT, NO_CONTEXT = "summary", "concept", "chat", "none"

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_FOOTNOTE = re.compile(r"^\[\^[^\]]+\]:.*$", re.MULTILINE)


def _generated_headings() -> set[str]:
    """Headings of the sections the code writes, in every locale."""
    names: set[str] = set()
    for language in SUPPORTED_LANGUAGES:
        locale = get_locale(language)
        names.update(n.casefold() for n in (
            locale.h_sources, locale.h_see_also, locale.h_related_concepts,
            locale.h_source_information,
        ))
    return names


def generated_texts(body: str) -> list[str]:
    """The parts of a page body that the code wrote, not the model: the sections
    of sources, "see also", related concepts and source information, and the
    footnote definitions. The verifier skips them."""
    skip = _generated_headings()
    texts: list[str] = []
    lines = body.split("\n")
    current: list[str] | None = None
    for line in lines:
        heading = _HEADING.match(line)
        if heading:
            if current:
                texts.append("\n".join(current))
            current = [line] if heading.group(2).casefold() in skip else None
        elif current is not None:
            current.append(line)
    if current:
        texts.append("\n".join(current))
    texts.extend(m.group(0) for m in _FOOTNOTE.finditer(body))
    return [t for t in texts if t.strip()]


@dataclass(frozen=True)
class PageContext:
    """The text a page is verified against, and the hash of each source."""

    kind: str
    passages: tuple[ContextPassage, ...]
    source_hashes: dict[str, str]

    @property
    def empty(self) -> bool:
        return not self.passages


def _source_text(conn: sqlite3.Connection, doc_id: str) -> str:
    """The extracted text of a source document: its pages in order, or its
    stored content when it has no page rows."""
    pages = [r["content"] for r in conn.execute(
        "SELECT content FROM document_pages WHERE document_id = ? ORDER BY page", (doc_id,),
    ).fetchall() if (r["content"] or "").strip()]
    if pages:
        return "\n\n".join(pages)
    row = conn.execute("SELECT content FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return (row["content"] or "") if row else ""


def _front_matter_sources(text: str) -> list[str]:
    """The `sources` entries of the page's front-matter, as resource strings
    (`sources/x.pdf`, `chat`). The legacy bare-string form is accepted."""
    block, _ = split_frontmatter(text)
    if block is None:
        return []
    try:
        sources = parse_frontmatter(block).get("sources")
    except ValueError:
        return []
    resources = []
    for entry in sources if isinstance(sources, list) else []:
        resource = entry.get("resource") if isinstance(entry, dict) else entry
        if isinstance(resource, str) and resource:
            resources.append(resource)
    return resources


def _is_chat_page(text: str) -> bool:
    return "chat" in _front_matter_sources(text)


def _cited_sources(conn: sqlite3.Connection, doc_id: str, page_text: str) -> list[sqlite3.Row]:
    """The source documents the page cites: the `cites` edges of the reference
    graph, plus the sources its front-matter lists. The front-matter is written
    in code, so it covers a page whose edges were not rebuilt (an index built
    before concept pages recorded them)."""
    rows = {r["id"]: r for r in conn.execute(
        "SELECT d.id, d.relative_path FROM document_references dr "
        "JOIN documents d ON d.id = dr.target_document_id "
        "WHERE dr.source_document_id = ? AND dr.reference_type = 'cites' "
        "AND d.source_kind = 'source'", (doc_id,),
    ).fetchall()}
    resources = [r for r in _front_matter_sources(page_text) if r.startswith("sources/")]
    if resources:
        marks = ",".join("?" * len(resources))
        for r in conn.execute(
            f"SELECT id, relative_path FROM documents WHERE source_kind = 'source' "
            f"AND relative_path IN ({marks})", resources,
        ).fetchall():
            rows.setdefault(r["id"], r)
    return sorted(rows.values(), key=lambda r: r["relative_path"])


def resolve_context(conn: sqlite3.Connection, doc_id: str, page_text: str) -> PageContext:
    """The context of the wiki page `doc_id`, whose file text is `page_text`."""
    row = conn.execute(
        "SELECT source_document_id FROM documents WHERE id = ? AND source_kind = 'wiki'", (doc_id,),
    ).fetchone()
    if row is None:
        return PageContext(NO_CONTEXT, (), {})

    items: list[tuple[str, str]] = []   # (relative path, text)

    def add_sources(rows) -> None:
        for r in rows:
            text = _source_text(conn, r["id"])
            if text.strip():
                items.append((r["relative_path"], text))

    cited = _cited_sources(conn, doc_id, page_text)

    if _is_chat_page(page_text):
        kind = CHAT
        linked = conn.execute(
            "SELECT d.relative_path, d.content FROM document_references dr "
            "JOIN documents d ON d.id = dr.target_document_id "
            "WHERE dr.source_document_id = ? AND dr.reference_type = 'links_to' "
            "AND d.source_kind = 'wiki' AND d.id != ? ORDER BY d.relative_path", (doc_id, doc_id),
        ).fetchall()
        for r in linked:
            _, body = split_frontmatter(r["content"] or "")
            if body.strip():
                items.append((r["relative_path"], r["content"]))
        add_sources(cited)
    elif row["source_document_id"]:
        kind = SUMMARY
        add_sources(conn.execute(
            "SELECT id, relative_path FROM documents WHERE id = ?", (row["source_document_id"],),
        ).fetchall())
    else:
        kind = CONCEPT
        add_sources(cited)

    if not items:
        return PageContext(kind, (), {})
    return PageContext(
        kind,
        tuple(ContextPassage(path, text) for path, text in items),
        {path: store.text_hash(text) for path, text in items},
    )


@dataclass(frozen=True)
class PageOutcome:
    """The result of verifying one page."""

    relative_path: str
    kind: str
    report: VerificationReport | None    # None when the page has no context
    seconds: float = 0.0

    @property
    def verified(self) -> bool:
        return self.report is not None


def _page_row(conn: sqlite3.Connection, relative_path: str):
    return conn.execute(
        "SELECT id FROM documents WHERE relative_path = ? AND source_kind = 'wiki'", (relative_path,),
    ).fetchone()


def current_state(db_path: str, workspace: Path, relative_path: str) -> tuple[store.State, store.StoredVerification | None]:
    """Whether the stored verification of the page still describes its file and
    its context sources."""
    file = workspace / relative_path
    if not file.is_file():
        return store.State.NONE, None
    text = file.read_text(encoding="utf-8")
    with get_connection(db_path) as conn:
        row = _page_row(conn, relative_path)
        if row is None:
            return store.State.NONE, None
        stored = store.load_verification(conn, row["id"])
        if stored is None:
            return store.State.NONE, None
        context = resolve_context(conn, row["id"], text)
    return store.state(stored, store.page_body(text), context.source_hashes), stored


def verify_page(
    db_path: str, workspace: Path, relative_path: str, verifier: Verifier, *,
    threshold: float, candidates: int, language: str,
) -> PageOutcome:
    """Verify the page at `relative_path` (`wiki/concepts/x.md`) and store the
    result. A page with no context stores nothing and returns `report=None`.
    Raises FileNotFoundError when the page has no file or no index row."""
    import time

    file = workspace / relative_path
    if not file.is_file():
        raise FileNotFoundError(relative_path)
    text = file.read_text(encoding="utf-8")
    body = store.page_body(text)
    started = time.perf_counter()
    with get_connection(db_path) as conn:
        row = _page_row(conn, relative_path)
        if row is None:
            raise FileNotFoundError(f"{relative_path} is not in the index")
        context = resolve_context(conn, row["id"], text)
        if context.empty:
            store.delete_verification(conn, row["id"])
            conn.commit()
            return PageOutcome(relative_path, context.kind, None)
        report = verify_answer(
            body, context.passages, verifier, threshold=threshold,
            generated=generated_texts(body), candidates=candidates, language=language,
        )
        store.save_verification(conn, row["id"], report, body_hash=store.text_hash(body),
                                source_hashes=context.source_hashes,
                                model=getattr(verifier, "model_name", ""))
    return PageOutcome(relative_path, context.kind, report, time.perf_counter() - started)


def pages_with_context(db_path: str, workspace: Path) -> list[str]:
    """The relative paths (`wiki/concepts/x.md`) of the wiki pages that have a
    context to verify against, sorted. Pages with no sources are left out."""
    paths = []
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, relative_path FROM documents "
            "WHERE source_kind = 'wiki' AND status = 'ready' ORDER BY relative_path"
        ).fetchall()
        for row in rows:
            file = workspace / row["relative_path"]
            if file.is_file() and not resolve_context(
                    conn, row["id"], file.read_text(encoding="utf-8")).empty:
                paths.append(row["relative_path"])
    return paths
