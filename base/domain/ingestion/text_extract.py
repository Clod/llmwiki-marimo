"""Text extraction from .md and .txt files: no Java, no LibreOffice.

The file is decoded as UTF-8, then UTF-8 with a BOM, then cp1252, and the
encoding used goes in the parser name (`text:utf-8`). A .md keeps its markdown
as the page text, minus its front-matter block. The text is split into pages
of about `PAGE_CHARS` characters without cutting a paragraph; a .md also starts
a new page at each level-1 and level-2 heading. Page numbers start at 1.
"""

import codecs
import re
from pathlib import Path

from domain.datasets.frontmatter import split_frontmatter

PAGE_CHARS = 4000

_HEADING_RE = re.compile(r"^#{1,2}[ \t]+\S")
_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")


def decode_text(data: bytes, filename: str = "") -> tuple[str, str]:
    """Return (text, encoding): UTF-8, then UTF-8 with a BOM, then cp1252."""
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252"), "cp1252"
    except UnicodeDecodeError as exc:
        raise RuntimeError(
            f"Cannot read '{filename}': it is not valid UTF-8 or cp1252 text"
        ) from exc


def _blocks(text: str, markdown: bool) -> list[tuple[bool, str]]:
    """Split `text` into paragraphs at blank lines: (starts_with_heading, text).

    A blank line inside a fenced code block does not end the paragraph.
    """
    blocks: list[tuple[bool, str]] = []
    current: list[str] = []
    fence = False

    def flush() -> None:
        if current:
            body = "\n".join(current).strip("\n")
            if body.strip():
                blocks.append((markdown and bool(_HEADING_RE.match(body)), body))
            current.clear()

    for line in text.split("\n"):
        if markdown and _FENCE_RE.match(line):
            fence = not fence
        if not fence and not line.strip():
            flush()
            continue
        # A heading line ends the paragraph before it, so it opens a section.
        if markdown and not fence and _HEADING_RE.match(line) and current:
            flush()
        current.append(line)
    flush()
    return blocks


def split_pages(text: str, *, markdown: bool, page_chars: int = PAGE_CHARS) -> list[str]:
    """Split `text` into pages of about `page_chars` characters.

    Paragraphs are never cut; a paragraph longer than a page is a page of its
    own. With `markdown`, a level-1 or level-2 heading always opens a new page.
    """
    pages: list[str] = []
    current: list[str] = []
    size = 0
    for starts_section, block in _blocks(text, markdown):
        if current and (starts_section or size + 2 + len(block) > page_chars):
            pages.append("\n\n".join(current))
            current, size = [], 0
        size += len(block) + (2 if current else 0)
        current.append(block)
    if current:
        pages.append("\n\n".join(current))
    return pages


def extract_text_file(file_path: Path) -> tuple[list[tuple[int, str]], str]:
    """Extract (page_number, text) pages from a .md or .txt file.

    Raises RuntimeError when the file is empty, holds only whitespace, or is
    not readable text.
    """
    text, encoding = decode_text(file_path.read_bytes(), file_path.name)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    markdown = file_path.suffix.lower() == ".md"
    if markdown:
        _, text = split_frontmatter(text)
    pages = split_pages(text, markdown=markdown)
    if not pages:
        raise RuntimeError(f"'{file_path.name}' is empty or contains only whitespace")
    return list(enumerate(pages, start=1)), f"text:{encoding}"
