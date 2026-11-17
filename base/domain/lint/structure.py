"""The structure check of a generated wiki: the defects of form the code produces.

A regeneration of the examples left the «See also» section of every summary page after
the footnote `[^1]: source.docx`, and nothing checked a generated wiki for it. This
module lists the defects the code is responsible for: the front-matter fields, the links
between pages, the order of the sections, the pages missing from `wiki/index.md`, and
the extracted text of a source that ends in an empty heading (a table or a block the
extraction dropped). The text the model writes is not checked.

`scripts/check_wiki.py` runs it on a wiki folder; a test runs it on `examples/`.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from domain.datasets.frontmatter import parse_frontmatter, split_frontmatter
from domain.i18n import get_locale

# The front-matter fields each kind of page must declare.
REQUIRED = {
    "concepts": ("type", "title", "description", "sources", "topics"),
    "summaries": ("type", "title", "sources", "topics"),
}

_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
_CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
_FOOTNOTE_RE = re.compile(r"^\[\^[^\]]+\]:", re.MULTILINE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Problem:
    page: str      # relative to wiki/, or `sources/<file>` for an extracted text
    rule: str      # frontmatter | link | sections | index | extraction
    message: str

    def __str__(self) -> str:
        return f"{self.page}: [{self.rule}] {self.message}"


def check_structure(workspace: Path, db_path: str | None, language: str) -> list[Problem]:
    """Every defect of form of the wiki at `workspace`, page by page. Without
    `db_path`, the extracted texts of the sources are not checked."""
    wiki = workspace / "wiki"
    loc = get_locale(language)
    pages = {kind: sorted((wiki / kind).glob("*.md")) for kind in REQUIRED}
    problems: list[Problem] = []
    for kind, paths in pages.items():
        for path in paths:
            text = path.read_text(encoding="utf-8")
            name = path.relative_to(wiki).as_posix()
            problems += _frontmatter(name, text, REQUIRED[kind])
            problems += _sections(name, text, loc.h_see_also, loc.h_sources)
    for path in sorted(wiki.rglob("*.md")):
        problems += _links(path.relative_to(wiki).as_posix(), path, wiki)
    problems += _index(wiki, [p for paths in pages.values() for p in paths])
    if db_path:
        problems += _extraction(db_path)
    return problems


def _frontmatter(name: str, text: str, required: tuple[str, ...]) -> list[Problem]:
    block, _ = split_frontmatter(text)
    if block is None:
        return [Problem(name, "frontmatter", "no front-matter block")]
    try:
        fields = parse_frontmatter(block)
    except ValueError as exc:
        return [Problem(name, "frontmatter", f"invalid front-matter: {exc}")]
    missing = [key for key in required if not fields.get(key)]
    return [Problem(name, "frontmatter", f"missing {', '.join(missing)}")] if missing else []


def _links(name: str, path: Path, wiki: Path) -> list[Problem]:
    _, body = split_frontmatter(path.read_text(encoding="utf-8"))
    problems = []
    for target in _LINK_RE.findall(_CODE_RE.sub("", body)):
        if target.startswith("#") or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE):
            continue
        file = target.split("#", 1)[0]
        if file and not (path.parent / file).resolve().is_file():
            problems.append(Problem(name, "link", f"link to a missing file: {target}"))
    return problems


def _sections(name: str, text: str, see_also: str, sources: str) -> list[Problem]:
    _, body = split_frontmatter(text)
    body = _CODE_RE.sub("", body)
    headings = [(m.start(), m.group(2)) for m in _HEADING_RE.finditer(body) if len(m.group(1)) == 2]
    problems = []
    seen: set[str] = set()
    for _, title in headings:
        if title in seen:
            problems.append(Problem(name, "sections", f"the heading «{title}» appears twice"))
        seen.add(title)
    see = next((pos for pos, title in headings if title == see_also), None)
    if see is not None:
        footnote = _FOOTNOTE_RE.search(body)
        if footnote and footnote.start() < see:
            problems.append(Problem(name, "sections", f"«{see_also}» comes after a footnote"))
        src = next((pos for pos, title in headings if title == sources), None)
        if src is not None and src < see:
            problems.append(Problem(name, "sections", f"«{see_also}» comes after «{sources}»"))
    return problems


def _index(wiki: Path, pages: list[Path]) -> list[Problem]:
    index = wiki / "index.md"
    if not index.is_file():
        return [Problem("index.md", "index", "wiki/index.md does not exist")]
    _, body = split_frontmatter(index.read_text(encoding="utf-8"))
    linked = {(wiki / t.split("#", 1)[0]).resolve() for t in _LINK_RE.findall(body)}
    return [Problem(p.relative_to(wiki).as_posix(), "index", "not linked from index.md")
            for p in pages if p.resolve() not in linked]


def _extraction(db_path: str) -> list[Problem]:
    conn = sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT relative_path, content FROM documents WHERE source_kind='source' AND status='ready'"
        ).fetchall()
    finally:
        conn.close()
    problems = []
    for relative_path, content in rows:
        lines = [line for line in (content or "").splitlines() if line.strip()]
        if lines and re.match(r"^#{1,6}\s", lines[-1]):
            problems.append(Problem(relative_path, "extraction",
                                    f"the extracted text ends in an empty heading: {lines[-1].strip()}"))
    return problems
