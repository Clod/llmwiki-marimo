"""The wiki-wide pass over the images that have no description (D9 and D12 of the image design).

`image_counts` reads the counts the Maintain screen shows (kept, described, skipped by
reason, stale) and how many vision-model calls "Describe the images" would make.
`describe_wiki_images` makes those calls: for each source with images it takes the stored
text back to the form extraction wrote (`strip_descriptions`), describes the images again
(`describe_images`, which reuses every valid description file) and stores the new text,
its fragments, and the Figures section of its summary page.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from domain.i18n import get_locale
from domain.ingestion.chunker import chunk_pages
from domain.ingestion.describe import Vision, describe_images, prompt_sha256, strip_descriptions
from domain.ingestion.images import (
    DESC_SUFFIX, SKIP_REASONS, desc_name, document_dir, file_sha256, image_path, load_figures,
    read_description, read_figures,
)
from domain.text.stemming import stem_text
from domain.tools.db import get_connection, open_db
from domain.wiki_settings import load_images_config, load_wiki_language

Progress = Callable[[str], None]


@dataclass
class ImageCounts:
    """The images of a wiki, counted for the Maintain screen."""

    kept: int = 0
    described: int = 0
    skipped: dict[str, int] = field(default_factory=lambda: {r: 0 for r in SKIP_REASONS})
    without_description: int = 0     # no description file and no reason
    stale: int = 0                   # a description made with another prompt, or of another image
    to_describe: int = 0             # model calls "Describe the images" would make now
    enabled: bool = False            # `[images] describe = true`


def _sources(db_path: str) -> list[dict]:
    with get_connection(db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, filename, relative_path FROM documents "
            "WHERE source_kind = 'source' AND status = 'ready' ORDER BY filename")]


def _pages(db_path: str, doc_id: str) -> list[tuple[int, str]]:
    with get_connection(db_path) as conn:
        return [(r["page"], r["content"] or "") for r in conn.execute(
            "SELECT page, content FROM document_pages WHERE document_id = ? ORDER BY page", (doc_id,))]


def image_counts(workspace: Path, db_path: str) -> ImageCounts:
    """Count the kept images of every source, and the calls the pass would make."""
    config = load_images_config(workspace)
    language = load_wiki_language(workspace)
    current = prompt_sha256(language)
    counts = ImageCounts(enabled=config.describe)
    for src in _sources(db_path):
        folder = document_dir(workspace, src["id"]) / "images"
        figures = [f for f in read_figures(document_dir(workspace, src["id"]))
                   if image_path(workspace, src["id"], f["file"])]
        for fig in figures:
            counts.kept += 1
            desc = read_description(folder / desc_name(fig["file"]))
            label = fig.get("description", "")
            if desc is not None:
                if desc.get("prompt_sha256") != current or desc.get("image_sha256") != (fig.get("sha256") or file_sha256(folder / fig["file"])):
                    counts.stale += 1
            if label in SKIP_REASONS:
                counts.skipped[label] += 1
            elif label.endswith(DESC_SUFFIX):
                counts.described += 1
            else:
                counts.without_description += 1
        if figures and config.describe:
            plan = describe_images(
                [(p, strip_descriptions(t)) for p, t in _pages(db_path, src["id"])],
                workspace=workspace, doc_id=src["id"], language=language, config=config,
                vision=None, dry_run=True)
            counts.to_describe += plan.calls_needed
    return counts


def _store_pages(db_path: str, doc_id: str, pages: list[tuple[int, str]], language: str) -> None:
    """Replace the stored text of a source: its pages, its content and its fragments."""
    chunks = chunk_pages(pages)
    conn = open_db(db_path)
    try:
        with conn:
            conn.execute("UPDATE documents SET content = ?, updated_at = datetime('now') WHERE id = ?",
                         ("\n\n---\n\n".join(text for _, text in pages), doc_id))
            conn.execute("DELETE FROM document_pages WHERE document_id = ?", (doc_id,))
            conn.executemany(
                "INSERT INTO document_pages (id, document_id, page, content) VALUES (?,?,?,?)",
                [(str(uuid.uuid4()), doc_id, number, text) for number, text in pages])
            conn.execute("DELETE FROM document_chunks WHERE document_id = ?", (doc_id,))
            conn.executemany(
                "INSERT INTO document_chunks (id, document_id, chunk_index, content, content_stemmed, "
                "page, start_char, token_count, header_breadcrumb) VALUES (?,?,?,?,?,?,?,?,?)",
                [(str(uuid.uuid4()), doc_id, c.index, c.content, stem_text(c.content, language), c.page,
                  c.start_char, c.token_count, c.header_breadcrumb) for c in chunks])
    finally:
        conn.close()


def replace_figures_section(page: str, section: str, language: str) -> str:
    """`page` with its Figures section replaced by `section` (as `figures_section` builds it).
    A page with no Figures section is returned unchanged."""
    heading = f"## {get_locale(language).h_figures}"
    match = re.search(rf"\n{re.escape(heading)}\n.*?(?=\n## |\Z)", page, re.DOTALL)
    if match is None:
        return page
    return page[:match.start()] + section.rstrip("\n") + "\n" + page[match.end():]


def _refresh_summary(workspace: Path, db_path: str, doc_id: str, language: str) -> bool:
    """Rewrite the Figures section of the summary page of `doc_id`; True when it changed."""
    from domain.ingestion.wiki_generator import figures_section
    from domain.tools.wiki_fs import write_page_content

    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT path, filename, relative_path FROM documents "
            "WHERE source_kind = 'wiki' AND source_document_id = ? AND path = '/wiki/summaries/'",
            (doc_id,)).fetchone()
    if row is None or not (workspace / row["relative_path"]).is_file():
        return False
    text = (workspace / row["relative_path"]).read_text(encoding="utf-8")
    section = figures_section(doc_id, load_figures(workspace, doc_id, with_descriptions=True), language)
    new = replace_figures_section(text, section, language) if section else text
    if new == text:
        return False
    return write_page_content(db_path, workspace, row["path"], row["filename"].removesuffix(".md"), new)


def describe_wiki_images(workspace: Path, db_path: str, vision: Vision, progress: Progress = lambda _: None) -> int:
    """Describe the images that have no valid description; return the number of sources
    whose text changed. Every source with images is taken back to its extracted form and
    described again; a valid description file is reused, so only the missing, stale or
    failed ones make a call."""
    config = load_images_config(workspace)
    language = load_wiki_language(workspace)
    changed = 0
    for src in _sources(db_path):
        if not read_figures(document_dir(workspace, src["id"])):
            continue
        before = _pages(db_path, src["id"])
        raw = [(p, strip_descriptions(t)) for p, t in before]
        progress(f"🖼 {src['filename']}")
        result = describe_images(raw, workspace=workspace, doc_id=src["id"], language=language,
                                 config=config, vision=vision)
        if result.pages != before:
            _store_pages(db_path, src["id"], result.pages, language)
            changed += 1
        _refresh_summary(workspace, db_path, src["id"], language)
        progress(f"  ✅ {result.described} / {result.images}")
    return changed
