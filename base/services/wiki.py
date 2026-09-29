"""Opening a wiki and working with its pages, for any user interface.

The marimo apps and the web application call these functions; neither holds
this logic itself. A `Wiki` is the resolved, validated handle every other
service takes.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

from domain.datasets.frontmatter import split_frontmatter
from domain.tools.db import get_connection, open_db, seed_workspace_row
from domain.wiki_settings import load_wiki_language

# A markdown link that is not an image embed — the same pattern as
# domain/tools/references.py:_WIKI_LINK_RE.
_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")


@dataclass(frozen=True)
class Wiki:
    """One wiki workspace: its directory, index and content language."""

    path: Path
    db_path: str
    language: str

    @property
    def wiki_dir(self) -> Path:
        return self.path / "wiki"

    @property
    def sources_dir(self) -> Path:
        return self.path / "sources"


def open_wiki(path: str | Path) -> Wiki:
    """Resolve a wiki directory, create its folders and index, and seed its
    `workspace` row. Opening the index also rebuilds an index built before
    stemming (`domain.tools.db.open_db`)."""
    root = Path(path).expanduser().resolve()
    (root / "wiki").mkdir(parents=True, exist_ok=True)
    (root / "sources").mkdir(parents=True, exist_ok=True)
    db_path = str(root / ".llmwiki" / "index.db")
    open_db(db_path).close()
    seed_workspace_row(db_path, root.name)
    return Wiki(path=root, db_path=db_path, language=load_wiki_language(root))


# ── Pages ─────────────────────────────────────────────────────────────────────
# A page id is the page's path under wiki/ without the `.md` suffix, for
# example `concepts/plazo-fijo-uva`.

def list_pages(wiki: Wiki) -> list[str]:
    """Every page id of the wiki, sorted."""
    if not wiki.wiki_dir.is_dir():
        return []
    return sorted(str(p.relative_to(wiki.wiki_dir).with_suffix("")) for p in wiki.wiki_dir.rglob("*.md"))


def _page_path(wiki: Wiki, page: str) -> Path:
    """The file of `page`, confined to wiki/: a page id cannot escape it."""
    path = (wiki.wiki_dir / f"{page}.md").resolve()
    if wiki.wiki_dir.resolve() not in path.parents:
        raise ValueError(f"page outside the wiki: {page!r}")
    return path


def read_page(wiki: Wiki, page: str) -> str:
    """The markdown of `page`, or "" when the page does not exist."""
    path = _page_path(wiki, page)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def page_links(content: str, page: str, pages: list[str]) -> dict[str, str]:
    """The internal links of a page: link label → target page id.

    Links are relative to the page's own directory (a concept page links to a
    sibling as `cinderella.md`, to a summary as `../summaries/x.md`), so each is
    resolved against that directory. Links to pages that do not exist, external
    links and repeated targets are left out.
    """
    current_dir = posixpath.dirname(page or "")
    existing = set(pages)
    seen: set[str] = set()
    links: dict[str, str] = {}
    for label, target in _LINK_RE.findall(content or ""):
        if target.startswith(("http", "mailto")):
            continue
        resolved = posixpath.normpath(posixpath.join(current_dir, target.removesuffix(".md")))
        if resolved in existing and resolved not in seen:
            seen.add(resolved)
            links[label] = resolved
    return links


def page_version(wiki: Wiki, page: str) -> int | None:
    """The stored version of `page`, or None for a page with no index row
    (`index.md`, `overview.md`, `log.md`)."""
    with get_connection(wiki.db_path) as conn:
        row = conn.execute(
            "SELECT version FROM documents WHERE relative_path = ?", (f"wiki/{page}.md",),
        ).fetchone()
    return row["version"] if row else None


class PageConflict(RuntimeError):
    """The page changed after the editor loaded it."""


def save_page(wiki: Wiki, page: str, body: str, expected_version: int | None) -> int | None:
    """Save an edited page body; return the new version.

    The front-matter block stays as it is on disk: the editor edits the body
    only, so a manual edit cannot break the fields the code reads back. The save
    refuses with `PageConflict` when the stored version differs from the one the
    editor loaded. It then rebuilds the page's references and commits the wiki.
    """
    from domain.tools.git_ops import auto_commit, init_wiki_repo
    from domain.tools.references import update_references
    from domain.tools.wiki_fs import write_page_content

    current = page_version(wiki, page)
    if expected_version is not None and current != expected_version:
        raise PageConflict(f"{page} changed since it was opened (version {current})")
    block, _ = split_frontmatter(read_page(wiki, page))
    text = (f"---\n{block}\n---\n\n" if block is not None else "") + body.strip() + "\n"

    dir_path, slug = posixpath.split(page)
    dir_path = f"/wiki/{dir_path}/" if dir_path else "/wiki/"
    if not write_page_content(wiki.db_path, wiki.path, dir_path, slug, text, language=wiki.language):
        raise FileNotFoundError(page)
    with get_connection(wiki.db_path) as conn:
        row = conn.execute(
            "SELECT id FROM documents WHERE relative_path = ?", (f"wiki/{page}.md",),
        ).fetchone()
    if row:
        update_references(wiki.db_path, row["id"], text, dir_path)
    init_wiki_repo(wiki.path)
    auto_commit(wiki.path, f"edit: {page}")
    return page_version(wiki, page)
