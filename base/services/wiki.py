"""Opening a wiki and working with its pages, for any user interface.

The marimo apps and the web application call these functions; neither holds
this logic itself. A `Wiki` is the resolved, validated handle every other
service takes.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable
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


FIXED_PAGES = ("index", "overview", "log")


def delete_page(wiki: Wiki, page: str) -> bool:
    """Delete a wiki page; return True if it existed.

    The page leaves the disk, the index and `index.md`, and the inbound links of
    the other pages are stripped (domain/tools/wiki_fs.py). The wiki is then
    committed, so the deletion can be undone from its git history. The three
    fixed pages, `index`, `overview` and `log`, are refused with `ValueError`.
    """
    from domain.tools.git_ops import auto_commit, init_wiki_repo
    from domain.tools.wiki_fs import delete_page as _delete_page

    if page in FIXED_PAGES:
        raise ValueError(f"{page} is a fixed page of the wiki and cannot be deleted")
    dir_path, slug = posixpath.split(page)
    dir_path = f"/wiki/{dir_path}/" if dir_path else "/wiki/"
    existed = _delete_page(wiki.db_path, wiki.path, dir_path, slug)
    if existed:
        init_wiki_repo(wiki.path)
        auto_commit(wiki.path, f"delete: {page}")
    return existed


def source_names(wiki: Wiki) -> set[str]:
    """The file names in the wiki's sources folder."""
    return {f.name for f in wiki.sources_dir.iterdir() if f.is_file() and not f.name.startswith(".")}


def source_view(wiki: Wiki, filename: str) -> Path:
    """The file a browser can show for the source document `filename`.

    A PDF, a .md and a .txt are themselves. An office file (.docx, .doc, .odt,
    .rtf) is the PDF that ingestion converted it to, in the document's cache
    directory; when that PDF is missing, LibreOffice converts the file again
    and the PDF is kept there. Raises FileNotFoundError when `filename` is not
    a file of the sources folder.
    """
    from domain.ingestion.extractor import convert_office_to_pdf
    from domain.ingestion.formats import OFFICE_EXTENSIONS

    if filename not in source_names(wiki):
        raise FileNotFoundError(f"{filename} is not a source of this wiki")
    path = wiki.sources_dir / filename
    if path.suffix.lower() not in OFFICE_EXTENSIONS:
        return path
    with get_connection(wiki.db_path) as conn:
        row = conn.execute(
            "SELECT id FROM documents WHERE source_kind = 'source' AND filename = ?", (filename,),
        ).fetchone()
    if row is None:
        raise FileNotFoundError(f"{filename} has not been ingested")
    cache_dir = wiki.path / ".llmwiki" / "cache" / "local" / row["id"]
    cached = cache_dir / "converted.pdf"
    return cached if cached.is_file() else convert_office_to_pdf(path, cache_dir)


def reindex(wiki: Wiki, *, progress: Callable[[str], None] = lambda _: None):
    """Rebuild the wiki's index from `sources/` and `wiki/`, with no model call.

    Writes a fresh `index.db`, keeps the old one as `index.db.bak`, and never
    modifies a markdown or a source file (domain/tools/reindex.py). Every source
    is extracted again, which needs Java, and LibreOffice for a DOCX. Returns the
    `ReindexReport`. The caller holds the wiki's lock: nothing else may write to
    the index meanwhile.
    """
    from domain.tools.reindex import reindex_from_disk

    return reindex_from_disk(wiki.path, wiki.db_path, progress=progress)


# ── History and rollback ──────────────────────────────────────────────────────
# The wiki is a git repository (domain/tools/git_ops.py) and every commit has an
# index snapshot while snapshots are on (domain/rollback). Without git, or with
# WIKI_AUTOCOMMIT=0, there is no history: every function here returns empty.

_LOG_SEP = "\x1e"
_FIELD_SEP = "\x1f"
_REV_RE = re.compile(r"^[0-9a-f]{7,40}$")


@dataclass(frozen=True)
class Commit:
    """One commit that touched wiki/."""

    sha: str
    date: str                  # ISO-8601 author date
    message: str
    files: tuple[str, ...]     # paths under wiki/, as the commit changed them
    has_snapshot: bool

    @property
    def short(self) -> str:
        return self.sha[:7]

    @property
    def pages(self) -> tuple[str, ...]:
        """The page ids among `files` (a `.md` file under wiki/)."""
        return tuple(f.removeprefix("wiki/").removesuffix(".md") for f in self.files if f.endswith(".md"))


@dataclass(frozen=True)
class PageChanges:
    """How wiki/ at HEAD differs from wiki/ at a commit: pages the wiki gained, rewrote
    and lost since then. A revert to that commit undoes all three."""

    added: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]


def _git(wiki: Wiki, *args: str) -> str:
    """Run git in the wiki; "" when git or the repository is missing."""
    import subprocess

    try:
        result = subprocess.run(["git", *args], cwd=wiki.path, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return ""
    return result.stdout if result.returncode == 0 else ""


def _parse_log(wiki: Wiki, raw: str) -> list[Commit]:
    from domain.rollback.snapshots import has_snapshot

    commits = []
    for record in raw.split(_LOG_SEP):
        if not record.strip():
            continue
        head, _, files = record.partition("\n")
        sha, date, message = head.split(_FIELD_SEP, 2)
        commits.append(Commit(sha=sha.strip(), date=date, message=message,
                              files=tuple(f for f in files.split("\n") if f.strip()),
                              has_snapshot=has_snapshot(wiki.path, sha.strip())))
    return commits


def history(wiki: Wiki, limit: int | None = None) -> list[Commit]:
    """The commits that touched wiki/, newest first."""
    args = ["log", "--no-renames", "--name-only", f"--format={_LOG_SEP}%H{_FIELD_SEP}%aI{_FIELD_SEP}%s"]
    if limit:
        args.append(f"--max-count={int(limit)}")
    return _parse_log(wiki, _git(wiki, *args, "--", "wiki"))


def page_history(wiki: Wiki, page: str) -> list[Commit]:
    """The commits that touched `page`, newest first. `files` holds that page only."""
    path = _page_path(wiki, page)
    relative = path.relative_to(wiki.path.resolve()).as_posix()
    return _parse_log(wiki, _git(wiki, "log", "--no-renames", "--name-only",
                                 f"--format={_LOG_SEP}%H{_FIELD_SEP}%aI{_FIELD_SEP}%s", "--", relative))


def commit_id(wiki: Wiki, sha: str) -> str:
    """A full sha for `sha`, which must be a hex abbreviation of a commit of this wiki."""
    if not _REV_RE.match(sha or ""):
        raise ValueError(f"not a commit id: {sha!r}")
    full = _git(wiki, "rev-parse", "--verify", "-q", f"{sha}^{{commit}}").strip()
    if not full:
        raise ValueError(f"unknown commit: {sha}")
    return full


def page_at(wiki: Wiki, page: str, sha: str) -> str:
    """The markdown of `page` as the commit `sha` had it, or "" when the commit has no such page."""
    path = _page_path(wiki, page)
    relative = path.relative_to(wiki.path.resolve()).as_posix()
    return _git(wiki, "show", f"{commit_id(wiki, sha)}:{relative}")


def pages_at(wiki: Wiki, sha: str) -> list[str]:
    """The page ids the commit `sha` had, sorted."""
    out = _git(wiki, "ls-tree", "-r", "--name-only", "-z", commit_id(wiki, sha), "--", "wiki")
    return sorted(f.removeprefix("wiki/").removesuffix(".md") for f in out.split("\0") if f.endswith(".md"))


def changes_since(wiki: Wiki, sha: str) -> PageChanges:
    """The pages added, changed and removed between the commit `sha` and HEAD."""
    added, changed, removed = [], [], []
    out = _git(wiki, "diff", "--no-renames", "--name-status", "-z", commit_id(wiki, sha), "HEAD", "--", "wiki")
    fields = out.split("\0")
    for status, name in zip(fields[0::2], fields[1::2]):
        if not name.endswith(".md"):
            continue
        page = name.removeprefix("wiki/").removesuffix(".md")
        {"A": added, "D": removed}.get(status[:1], changed).append(page)
    return PageChanges(tuple(added), tuple(changed), tuple(removed))


def uncommitted(wiki: Wiki) -> list[str]:
    """The files of wiki/ that differ from the last commit. A revert is refused while there are any."""
    from domain.rollback.revert import dirty_files

    return dirty_files(wiki.path)


@dataclass(frozen=True)
class RevertPlan:
    """What a revert to a commit would do, decided before anything changes."""

    sha: str
    changes: PageChanges
    db_via: str | None         # "snapshot" or "reindex"; None when the index cannot be restored
    db_reason: str
    problem: str               # why the revert cannot run now, or ""
    problem_code: str          # "dirty", "index", "unavailable" or ""
    files: tuple[str, ...] = ()
    index_code: str = ""       # with problem_code "index": why (IndexUnavailable.code)
    config_changes: bool = False   # going back also changes wiki_config.toml (the vocabulary lists)


def revert_plan(wiki: Wiki, sha: str) -> RevertPlan:
    """Check a revert without doing it: the pages it changes, how the index would
    be restored, and whether anything stops it (uncommitted changes, no way to
    restore the index). Raises `ValueError` for a commit that does not exist."""
    from domain.rollback.revert import IndexUnavailable, RevertError, config_changes, index_route, require_history

    full = commit_id(wiki, sha)
    changes = changes_since(wiki, full)
    try:
        require_history(wiki.path)
        dirty = uncommitted(wiki)
        if dirty:
            return RevertPlan(full, changes, None, "", f"{len(dirty)} uncommitted change(s) in wiki/", "dirty",
                              tuple(dirty))
        via, reason = index_route(wiki.path, full)
    except IndexUnavailable as exc:
        return RevertPlan(full, changes, None, exc.reason, exc.reason, "index", index_code=exc.code)
    except RevertError as exc:
        return RevertPlan(full, changes, None, "", str(exc), "unavailable")
    return RevertPlan(full, changes, via, reason, "", "", config_changes=config_changes(wiki.path, full))


def revert(wiki: Wiki, sha: str, *, force: bool = False, progress: Callable[[str], None] = lambda _: None):
    """Restore the pages and the index to the commit `sha`, as a new commit
    (domain/rollback/revert.py). Returns the `RevertResult`. The caller holds the
    wiki's lock. Raises a `RevertError` with the wiki unchanged."""
    from domain.rollback.revert import revert_wiki

    return revert_wiki(wiki.path, sha, force=force, progress=progress)


# ── Graph ─────────────────────────────────────────────────────────────────────

def graph(wiki: Wiki) -> dict:
    """The relations of the wiki, read from `documents` and `document_references`.

    `nodes`: one per wiki page (`id` is the page id, `kind` "concept" or
    "summary") and one per source document (`id` is `source:<filename>`, `kind`
    "source"). `index`, `overview` and `log` are not nodes. `edges`: one per
    (source, target, type) with `type` "links_to" or "cites"; repeated edges and
    a page linking to itself are dropped. `degree` counts the edges of a node.
    A node with no edges is still a node.
    """
    with get_connection(wiki.db_path) as conn:
        docs = conn.execute(
            "SELECT id, source_kind, relative_path, filename, title FROM documents "
            "WHERE status != 'failed' OR source_kind = 'wiki'").fetchall()
        refs = conn.execute(
            "SELECT source_document_id, target_document_id, reference_type "
            "FROM document_references ORDER BY rowid").fetchall()
    nodes: dict[str, dict] = {}
    for d in docs:
        if d["source_kind"] == "wiki":
            page = d["relative_path"].removeprefix("wiki/").removesuffix(".md")
            if page in FIXED_PAGES:
                continue
            kind = "summary" if page.startswith("summaries/") else "concept"
            title = d["title"] or page.rsplit("/", 1)[-1].replace("-", " ")
            nodes[d["id"]] = {"id": page, "title": title, "kind": kind, "degree": 0}
        elif d["source_kind"] == "source":
            nodes[d["id"]] = {"id": f"source:{d['filename']}", "title": d["filename"],
                              "kind": "source", "degree": 0}
    edges: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for r in refs:
        src, dst = nodes.get(r["source_document_id"]), nodes.get(r["target_document_id"])
        if src is None or dst is None or src is dst:
            continue
        key = (src["id"], dst["id"], r["reference_type"])
        if key in seen:
            continue
        seen.add(key)
        src["degree"] += 1
        dst["degree"] += 1
        edges.append({"source": src["id"], "target": dst["id"], "type": r["reference_type"]})
    return {"nodes": sorted(nodes.values(), key=lambda n: (n["kind"], n["id"])), "edges": edges}
