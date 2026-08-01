"""Citation graph: document_references table helpers.

Sync port of mcp/tools/references.py + mcp/vaultfs/sqlite.py reference methods.
"""

import logging
import re

from domain.tools.db import get_connection

logger = logging.getLogger(__name__)

_CITATION_RE = re.compile(r"\[\^\d+\]:\s*(.+)$", re.MULTILINE)
_WIKI_LINK_RE = re.compile(r"(?<!!)\[(?:[^\]]*)\]\(([^)]+)\)")
# Concept/chat pages list sources as plain "- file.pdf" bullets under a
# "## Sources" heading (summary pages use the "[^N]: file" footnote form above).
# Capture the body of the Sources section, then each bullet within it.
_SOURCES_SECTION_RE = re.compile(r"^##\s+Sources\s*$(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
_SOURCE_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$", re.MULTILINE)
_FOOTNOTE_PREFIX_RE = re.compile(r"^\[\^\w+\]:\s*")


def _parse_citation_filename(raw: str) -> tuple[str, int | None]:
    """Extract filename and optional page from a citation like 'paper.pdf, p.3'."""
    raw = raw.strip().lstrip("*").rstrip("*")
    link_match = re.match(r"\[([^\]]+)\]\([^)]*\)", raw)
    if link_match:
        raw = link_match.group(1)
    parts = re.match(r"^(.+?)(?:,\s*p\.?\s*(\d+))?(?:\s+[-–—].*)?$", raw)
    if not parts:
        return raw, None
    filename = parts.group(1).strip()
    page = int(parts.group(2)) if parts.group(2) else None
    return filename, page


def _parse_wiki_links(content: str, current_dir: str) -> list[str]:
    """Extract internal wiki link paths, resolved relative to current_dir."""
    paths = []
    for match in _WIKI_LINK_RE.finditer(content):
        href = match.group(1)
        if href.startswith(("http", "#", "mailto:", "data:")):
            continue
        if re.search(r"\.(png|jpg|jpeg|gif|webp|svg)$", href, re.IGNORECASE):
            continue
        if href.startswith("/wiki/"):
            resolved = href.replace("/wiki/", "", 1)
        elif href.startswith("./"):
            resolved = (current_dir + href[2:]) if current_dir else href[2:]
        elif href.startswith("../"):
            parts = (current_dir.rstrip("/") + "/" + href).split("/")
            resolved_parts = []
            for p in parts:
                if p == "..":
                    if resolved_parts:
                        resolved_parts.pop()
                elif p and p != ".":
                    resolved_parts.append(p)
            resolved = "/".join(resolved_parts)
        elif "/" not in href:
            resolved = (current_dir + href) if current_dir else href
        else:
            resolved = href
        if resolved:
            paths.append(resolved)
    return paths


def update_references(
    db_path: str,
    document_id: str,
    content: str,
    doc_path: str,
) -> None:
    """Parse citations/wikilinks from content and rebuild document_references edges."""
    wiki_relative_dir = doc_path.replace("/wiki/", "", 1) if doc_path.startswith("/wiki/") else ""

    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, filename, title, path FROM documents WHERE status != 'failed'"
        ).fetchall()
        all_docs = [dict(r) for r in rows]

        filename_to_doc: dict[str, dict] = {}
        wiki_path_to_doc: dict[str, dict] = {}
        for doc in all_docs:
            fn_lower = doc["filename"].lower()
            if fn_lower not in filename_to_doc:
                filename_to_doc[fn_lower] = doc
            if doc.get("title"):
                title_lower = doc["title"].lower()
                if title_lower not in filename_to_doc:
                    filename_to_doc[title_lower] = doc
            if doc["path"].startswith("/wiki/"):
                relative = (doc["path"] + doc["filename"]).replace("/wiki/", "", 1)
                wiki_path_to_doc[relative.lower()] = doc

        edges: list[tuple[str, str, int | None]] = []

        # Citation candidates come from two on-page formats:
        #  1. "[^N]: file.pdf, p.3" footnote markers anywhere (summary pages)
        #  2. plain "- file.pdf" bullets under a "## Sources" heading (concept/chat pages)
        citation_raws: list[str] = [m.group(1) for m in _CITATION_RE.finditer(content)]
        for section in _SOURCES_SECTION_RE.finditer(content):
            for bullet in _SOURCE_BULLET_RE.finditer(section.group(1)):
                # Tolerate legacy "- [^1]: file.pdf" bullets by stripping the marker.
                citation_raws.append(_FOOTNOTE_PREFIX_RE.sub("", bullet.group(1)))

        for raw in citation_raws:
            filename, page = _parse_citation_filename(raw)
            fn_lower = filename.lower()
            target = filename_to_doc.get(fn_lower)
            if not target:
                base = re.sub(r"\.(pdf|docx?|pptx?|xlsx?|csv|html?|md|txt)$", "", fn_lower)
                for doc in all_docs:
                    doc_base = re.sub(r"\.(pdf|docx?|pptx?|xlsx?|csv|html?|md|txt)$", "", doc["filename"].lower())
                    if doc_base == base:
                        target = doc
                        break
            # A citation records where a page's CONTENT came from, so its target
            # must be a source document. Candidates are matched by filename and by
            # title, and wiki pages carry both — so a bullet naming a wiki page
            # (a "See also" link that drifted under "## Sources", say) would
            # otherwise be stored as a citation. Deletion, lint and provenance all
            # read these and all assume the target is a source.
            if target and target["path"].startswith("/wiki/"):
                continue
            if target and str(target["id"]) != document_id:
                edges.append((str(target["id"]), "cites", page))

        for link_path in _parse_wiki_links(content, wiki_relative_dir):
            target = wiki_path_to_doc.get(link_path.lower())
            if not target:
                target = wiki_path_to_doc.get(link_path.lower() + ".md")
            if not target:
                basename = link_path.split("/")[-1].lower()
                target = wiki_path_to_doc.get(basename)
            if target and str(target["id"]) != document_id:
                edges.append((str(target["id"]), "links_to", None))

        seen: set[tuple[str, str]] = set()
        unique_edges = []
        for target_id, ref_type, page in edges:
            key = (target_id, ref_type)
            if key not in seen:
                seen.add(key)
                unique_edges.append((target_id, ref_type, page))

        with conn:
            conn.execute(
                "DELETE FROM document_references WHERE source_document_id=?",
                (document_id,),
            )
            conn.executemany(
                "INSERT INTO document_references "
                "(source_document_id, target_document_id, reference_type, page) "
                "VALUES (?,?,?,?)",
                [(document_id, t, r, p) for t, r, p in unique_edges],
            )

    logger.info(
        "Updated references for doc=%s: %d citations, %d links",
        document_id[:8],
        sum(1 for _, t, _ in unique_edges if t == "cites"),
        sum(1 for _, t, _ in unique_edges if t == "links_to"),
    )


def get_backlinks(db_path: str, doc_id: str) -> list[dict]:
    """Return all documents that link to or cite doc_id."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT d.id, d.path, d.filename, d.title, dr.reference_type "
            "FROM document_references dr "
            "JOIN documents d ON dr.source_document_id = d.id "
            "WHERE dr.target_document_id = ? AND d.status != 'failed' "
            "ORDER BY d.path, d.filename",
            (doc_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_forward_refs(db_path: str, doc_id: str) -> list[dict]:
    """Return all documents that doc_id links to or cites."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT d.id, d.filename, d.title, d.path, dr.reference_type, dr.page "
            "FROM document_references dr "
            "JOIN documents d ON dr.target_document_id = d.id "
            "WHERE dr.source_document_id = ? AND d.status != 'failed' "
            "ORDER BY dr.reference_type, d.path, d.filename",
            (doc_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def find_orphan_pages(db_path: str) -> list[dict]:
    """Return wiki pages with no inbound references."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT d.id, d.filename, d.title, d.path "
            "FROM documents d "
            "WHERE d.source_kind = 'wiki' AND d.status != 'failed' "
            "  AND d.id NOT IN (SELECT target_document_id FROM document_references) "
            "ORDER BY d.path, d.filename",
        ).fetchall()
    return [dict(r) for r in rows]


def find_uncited_sources(db_path: str) -> list[dict]:
    """Return source documents not cited by any wiki page."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT d.id, d.filename, d.title, d.path, d.file_type "
            "FROM documents d "
            "WHERE d.source_kind = 'source' AND d.status != 'failed' "
            "  AND d.id NOT IN ("
            "    SELECT target_document_id FROM document_references "
            "    WHERE reference_type = 'cites'"
            "  ) "
            "ORDER BY d.filename",
        ).fetchall()
    return [dict(r) for r in rows]


def find_stale_pages(db_path: str) -> list[dict]:
    """Return wiki pages whose stale_since flag is set."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT d.id, d.filename, d.title, d.path, d.stale_since "
            "FROM documents d "
            "WHERE d.status != 'failed' AND d.stale_since IS NOT NULL "
            "ORDER BY d.stale_since DESC",
        ).fetchall()
    return [dict(r) for r in rows]
