"""Text extraction from .md and .txt files: no Java, no LibreOffice.

The file is decoded as UTF-8, then UTF-8 with a BOM, then cp1252, and the
encoding used goes in the parser name (`text:utf-8`). A .md keeps its markdown
as the page text, minus its front-matter block. The text is split into pages
of about `PAGE_CHARS` characters without cutting a paragraph; a .md also starts
a new page at each level-1 and level-2 heading. Page numbers start at 1.

The image links of a .md are kept like the images of a PDF (docs/design_image_handling.md,
D10): a link to a raster file inside the sources folder is copied to the document's images
folder and rewritten; a link that leaves the sources folder, a URL, a missing file and a
format that is not raster get the marker "image not available"; nothing is downloaded.
"""

import codecs
import re
import shutil
import tempfile
import urllib.parse
from pathlib import Path

from domain.datasets.frontmatter import split_frontmatter
from domain.i18n import get_locale
from domain.wiki_settings import load_images_config, load_wiki_language

from .images import (
    FIGURES_FILENAME, IMAGES_DIRNAME, RASTER_EXTENSIONS, enrich_figures, image_link, kept_name,
    replace_dir, write_figures,
)

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


# `![alt](target "title")`
_IMAGE_RE = re.compile(r'!\[(?P<alt>[^\]]*)\]\((?P<target>[^)\s]*)(?:\s+(?:"[^"]*"|\'[^\']*\'))?\)')
_SCHEME_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)")


def _sources_root(file_path: Path) -> Path:
    """The `sources/` folder that holds `file_path`: its nearest ancestor with that name,
    or the folder of the file when it is outside any."""
    return next((p for p in file_path.parents if p.name == "sources"), file_path.parent)


def _local_image(file_path: Path, target: str) -> Path | None:
    """The raster file a link points to, or None when the link is refused: it leaves the
    sources folder, the file does not exist, or its format is not raster."""
    root = _sources_root(file_path).resolve()
    try:
        resolved = (file_path.parent / urllib.parse.unquote(target.split("#", 1)[0].split("?", 1)[0])).resolve()
    except (OSError, ValueError):
        return None
    if not resolved.is_relative_to(root) or resolved.suffix.lower() not in RASTER_EXTENSIONS:
        return None
    return resolved if resolved.is_file() else None


def referenced_images(markdown_files: list[Path]) -> set[Path]:
    """The raster files the image links of the given .md sources point to, resolved as
    `keep_markdown_images` resolves them (relative to the folder of the source, confined to
    `sources/`; URLs and links inside code fences count for nothing). The scan skips these
    files: they are figures of a Markdown source, not sources of their own."""
    found: set[Path] = set()
    for path in markdown_files:
        try:
            text, _ = decode_text(path.read_bytes(), path.name)
        except (OSError, RuntimeError):
            continue
        fenced = False
        for line in text.replace("\r\n", "\n").split("\n"):
            if _FENCE_RE.match(line):
                fenced = not fenced
            if fenced or "![" not in line:
                continue
            for match in _IMAGE_RE.finditer(line):
                target = match.group("target")
                if not _SCHEME_RE.match(target):
                    image = _local_image(path, target)
                    if image is not None:
                        found.add(image)
    return found


def keep_markdown_images(
    pages: list[str], file_path: Path, cache_dir: Path,
) -> list[str]:
    """Rewrite the image links of the pages of the .md `file_path` (D10 of the image design).

    A link to a raster file inside the sources folder is copied to
    `cache_dir/images/` (replacing the folder; the descriptions of images that stay are
    kept), listed in `figures.json` with the alt text as caption, and becomes
    `![Imagen, página N](.llmwiki/cache/local/<id>/images/<file>)` on a line of its own.
    A URL stays as text followed by the marker; any other link that cannot be kept is
    replaced by the marker. Links inside fenced code blocks are left as they are. Returns
    the pages; without any image link nothing is written.
    """
    loc = get_locale(load_wiki_language(_sources_root(file_path).parent))
    doc_id = cache_dir.name
    names: dict[Path, str] = {}
    taken: set[str] = set()
    figures: list[dict] = []
    scratch = Path(tempfile.mkdtemp())
    seen_link = False

    def rewrite_page(number: int, text: str) -> str:
        nonlocal seen_link
        kept_here = False

        def handle(match: re.Match[str]) -> str:
            nonlocal seen_link, kept_here
            seen_link = True
            target = match.group("target")
            if _SCHEME_RE.match(target):
                return f"{match.group(0)} {loc.image_unavailable}"
            source = _local_image(file_path, target)
            if source is None:
                return loc.image_unavailable
            if source not in names:
                name, n = kept_name(source.name), 1
                while name in taken:
                    n += 1
                    name = f"{Path(kept_name(source.name)).stem}-{n}{source.suffix.lower()}"
                taken.add(name)
                names[source] = name
                shutil.copy2(source, scratch / name)
            figures.append({"page": number, "file": names[source], "caption": " ".join(match.group("alt").split())})
            kept_here = True
            return f"\n\n{image_link(doc_id, names[source], number)}\n\n"

        out, fenced = [], False
        for line in text.split("\n"):
            if _FENCE_RE.match(line):
                fenced = not fenced
            out.append(line if fenced or "![" not in line else _IMAGE_RE.sub(handle, line))
        rewritten = "\n".join(out)
        return re.sub(r"\n{3,}", "\n\n", rewritten).strip("\n") if kept_here else rewritten

    try:
        result = [rewrite_page(number, text) for number, text in enumerate(pages, start=1)]
        images = cache_dir / IMAGES_DIRNAME
        if figures:
            replace_dir(scratch, images)
            write_figures(cache_dir, figures)
            enrich_figures(cache_dir)
        elif seen_link or images.exists() or (cache_dir / FIGURES_FILENAME).exists():
            # The source no longer keeps an image: the folder of an earlier extraction goes.
            if images.exists():
                shutil.rmtree(images)
            if cache_dir.exists():
                write_figures(cache_dir, [])
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return result


def extract_image_file(file_path: Path, cache_dir: Path) -> tuple[list[tuple[int, str]], str]:
    """The pages of an image file used as a source (D11 of the image design): the file is
    copied to `cache_dir/images/` and the one page is its link, which the describing step
    follows with the generated description. Raises RuntimeError when the file is empty."""
    if file_path.stat().st_size == 0:
        raise RuntimeError(f"'{file_path.name}' is empty")
    name = kept_name(file_path.name)
    scratch = Path(tempfile.mkdtemp())
    try:
        shutil.copy2(file_path, scratch / name)
        replace_dir(scratch, cache_dir / IMAGES_DIRNAME)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    write_figures(cache_dir, [{"page": 1, "file": name, "caption": ""}])
    enrich_figures(cache_dir)
    return [(1, image_link(cache_dir.name, name, 1))], "image"


def extract_text_file(file_path: Path, cache_dir: Path | None = None) -> tuple[list[tuple[int, str]], str]:
    """Extract (page_number, text) pages from a .md or .txt file.

    With `cache_dir`, the image links of a .md are kept in `cache_dir/images/`
    (`keep_markdown_images`), when the wiki sets `[images] describe = true` and
    `ignore = false`. Raises RuntimeError when the file is empty, holds only
    whitespace, or is not readable text.
    """
    text, encoding = decode_text(file_path.read_bytes(), file_path.name)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    markdown = file_path.suffix.lower() == ".md"
    if markdown:
        _, text = split_frontmatter(text)
    pages = split_pages(text, markdown=markdown)
    if not pages:
        raise RuntimeError(f"'{file_path.name}' is empty or contains only whitespace")
    # Only a wiki that describes its images keeps the images of a .md: with `describe = false`
    # or `ignore = true` the stored text is the text of the file, as before (goal 5 of the
    # image design).
    images = load_images_config(_sources_root(file_path).parent) if markdown and cache_dir is not None else None
    if images is not None and images.describe and not images.ignore:
        pages = keep_markdown_images(pages, file_path, cache_dir)
    return list(enumerate(pages, start=1)), f"text:{encoding}"
