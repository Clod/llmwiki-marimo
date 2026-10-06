"""Revert a wiki to an earlier commit, markdown and index together.

`revert_wiki` restores forward: the pages of the target commit are checked out
over the working tree and committed as a new commit, so no history is rewritten
and the revert can itself be reverted. `sources/` is never touched (design,
edge cases). The index follows the pages: the snapshot taken at the target
commit when there is one (byte-exact, instant), else `reindex_from_disk` (the
floor; it extracts `sources/` again, which needs Java, and LibreOffice for a DOCX).

Whatever fails, the wiki ends as it began. The reindex route is checked before
anything changes (`index_route`), and a failure after the checkout puts the
pages and the index back from a backup taken first.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from domain.ingestion import extractor
from domain.rollback import snapshots
from domain.tools.git_ops import autocommit_enabled, head_sha
from domain.ingestion.formats import OFFICE_EXTENSIONS, TEXT_EXTENSIONS
from domain.tools.reindex import SOURCE_SUFFIXES, NothingExtracted, ReindexReport, reindex_from_disk

logger = logging.getLogger(__name__)

Progress = Callable[[str], None]
# The files a point of the history covers: the pages, the ignore rules and the
# configuration of the wiki (its vocabulary lists among them, edited from the
# Vocabulario screen). A point from before `wiki_config.toml` was tracked does not
# hold it: going back to that point keeps the current configuration.
CONFIG = "wiki_config.toml"
_TRACKED = ("wiki", ".gitignore", CONFIG)


class RevertError(RuntimeError):
    """The revert did not happen. Nothing changed: the pages and the index are as they were."""


class RevertUnavailable(RevertError):
    """No history to go back to: WIKI_AUTOCOMMIT is off, git is missing, or the wiki has no repository."""


class UnknownRevision(RevertError):
    """`target` is not a commit of this wiki."""


class DirtyWiki(RevertError):
    """wiki/ holds changes that no commit has. `files` lists them."""

    def __init__(self, files: list[str]):
        self.files = files
        super().__init__(f"{len(files)} uncommitted change(s) in wiki/: " + ", ".join(files[:10]))


class IndexUnavailable(RevertError):
    """The target has no usable snapshot and the index cannot be rebuilt on this machine.
    `code` is one of `java`, `libreoffice`, `nothing_extracted`, `rebuild_failed`; `reason` says why."""

    def __init__(self, code: str, reason: str):
        self.code, self.reason = code, reason
        super().__init__(reason)


@dataclass(frozen=True)
class RevertResult:
    target_sha: str
    db_via: Literal["snapshot", "reindex"]
    restored_pages: int
    commit_sha: str | None          # the revert commit; None when the pages were already identical
    db_reason: str                  # why that route: the snapshot was there, or what was missing
    snapshot_captured: bool
    reindex: ReindexReport | None = field(default=None, compare=False)


def _git(workspace: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=workspace, capture_output=True, text=True, check=check)


def resolve(workspace: Path, target: str) -> str:
    """The full sha of the commit `target` names (a sha, `HEAD~1`, a tag)."""
    if not target or target.startswith("-"):
        raise UnknownRevision(f"not a revision: {target!r}")
    try:
        result = _git(workspace, "rev-parse", "--verify", "-q", f"{target}^{{commit}}", check=False)
    except FileNotFoundError as exc:
        raise RevertUnavailable("git is not installed") from exc
    sha = result.stdout.strip()
    if result.returncode != 0 or not sha:
        raise UnknownRevision(f"{target!r} is not a commit of this wiki")
    return sha


def dirty_files(workspace: Path) -> list[str]:
    """The files of wiki/, .gitignore and wiki_config.toml that differ from HEAD, untracked ones included."""
    result = _git(workspace, "status", "--porcelain", "-z", "--no-renames", "--untracked-files=all",
                  "--", *_TRACKED, check=False)
    if result.returncode != 0:
        return []
    return sorted(entry[3:] for entry in result.stdout.split("\0") if len(entry) > 3)


def config_changes(workspace: Path, target_sha: str) -> bool:
    """True when going back to `target_sha` changes `wiki_config.toml`.

    A point that does not hold the file (it predates its tracking) changes nothing.
    """
    held = _git(workspace, "cat-file", "-e", f"{target_sha}:{CONFIG}", check=False).returncode == 0
    return held and _git(workspace, "diff", "--quiet", target_sha, "--", CONFIG, check=False).returncode != 0


def require_history(workspace: Path) -> None:
    """Raise `RevertUnavailable` unless the wiki has a git history to go back to."""
    if not autocommit_enabled():
        raise RevertUnavailable("WIKI_AUTOCOMMIT is off: the wiki keeps no history of its own")
    if not (Path(workspace) / ".git").exists() or head_sha(workspace) is None:
        raise RevertUnavailable("the wiki has no git history yet")


def _extractor_gap(workspace: Path) -> IndexUnavailable | None:
    """What the machine lacks to extract `sources/` again, or None when it can."""
    sources = Path(workspace) / "sources"
    names = [p.name.lower() for p in sources.iterdir()
             if p.is_file() and not p.name.startswith(".")] if sources.is_dir() else []
    names = [n for n in names if n.endswith(SOURCE_SUFFIXES)]
    if not names:
        return None
    # The text formats (.md, .txt) are read directly: they need neither tool.
    needs_java = [n for n in names if not n.endswith(tuple(TEXT_EXTENSIONS))]
    if needs_java and extractor.check_java() is None:
        return IndexUnavailable("java", "Java is not installed; rebuilding the index extracts every source again")
    if any(n.endswith(tuple(OFFICE_EXTENSIONS)) for n in names) and extractor.check_libreoffice() is None:
        return IndexUnavailable("libreoffice", "LibreOffice is not installed; rebuilding the index converts "
                                "each office source again")
    return None


def index_route(workspace: Path, target_sha: str) -> tuple[Literal["snapshot", "reindex"], str]:
    """How the index would be restored for `target_sha`, and why (a clause, not a sentence).

    Raises `IndexUnavailable` when neither a snapshot nor a working extractor is there.
    """
    short = target_sha[:7]
    if snapshots.is_usable(workspace, target_sha):
        return "snapshot", f"the snapshot of {short} is available"
    if not snapshots.snapshots_enabled():
        why = "snapshots are disabled (WIKI_SNAPSHOT_KEEP=0)"
    elif snapshots.has_snapshot(workspace, target_sha):
        why = f"the snapshot of {short} is damaged"
    else:
        why = f"there is no snapshot of {short}"
    gap = _extractor_gap(workspace)
    if gap is not None:
        raise IndexUnavailable(gap.code, f"{why}, and {gap.reason}")
    return "reindex", why


# ── Working-tree backup ───────────────────────────────────────────────────────

def _backup(workspace: Path, directory: Path) -> None:
    for name in _TRACKED:
        src = workspace / name
        if src.is_dir():
            shutil.copytree(src, directory / name)
        elif src.is_file():
            shutil.copy2(src, directory / name)
    live = workspace / ".llmwiki" / "index.db"
    if live.is_file():
        snapshots._backup_to(live, directory / "index.db")


def _undo(workspace: Path, directory: Path, db_replaced: bool) -> None:
    """Put the pages and the index back as `_backup` found them."""
    wiki = workspace / "wiki"
    if wiki.exists():
        shutil.rmtree(wiki)
    if (directory / "wiki").is_dir():
        shutil.copytree(directory / "wiki", wiki)
    if (directory / ".gitignore").is_file():
        shutil.copy2(directory / ".gitignore", workspace / ".gitignore")
    if (directory / CONFIG).is_file():
        shutil.copy2(directory / CONFIG, workspace / CONFIG)
    _git(workspace, "reset", "-q", "--", *_TRACKED, check=False)
    if db_replaced and (directory / "index.db").is_file():
        snapshots.install_db(workspace, directory / "index.db")


def _restore_files(workspace: Path, target_sha: str) -> None:
    """Make wiki/, .gitignore and wiki_config.toml equal to the target commit's, staged.

    `git checkout <sha> -- wiki` restores and overwrites but never deletes a file
    that the target does not have, so those go first: the tracked ones with
    `git rm`, the untracked ones (a forced revert) with `git clean`.
    """
    in_target = set(_git(workspace, "ls-tree", "-r", "--name-only", "-z", target_sha, "--",
                         *_TRACKED).stdout.split("\0")) - {""}
    in_head = set(_git(workspace, "ls-files", "-z", "--", *_TRACKED).stdout.split("\0")) - {""}
    extra = sorted(in_head - in_target - {CONFIG})   # a point without the config keeps the current one
    for i in range(0, len(extra), 200):
        _git(workspace, "rm", "-q", "-f", "--", *extra[i:i + 200])
    _git(workspace, "clean", "-fdq", "--", "wiki")
    present = [name for name in _TRACKED
               if any(f == name or f.startswith(f"{name}/") for f in in_target)]
    if present:
        _git(workspace, "checkout", target_sha, "--", *present)


def _count_pages(workspace: Path) -> int:
    wiki = workspace / "wiki"
    return sum(1 for _ in wiki.rglob("*.md")) if wiki.is_dir() else 0


def revert_wiki(workspace: Path, target: str, *, force: bool = False,
                progress: Progress = lambda _: None) -> RevertResult:
    """Restore the wiki's pages and index to the commit `target` names.

    1. Resolve `target` (a sha or a revision such as `HEAD~1`).
    2. Refuse when wiki/ has uncommitted changes, unless `force`.
    3. Check how the index will be restored (snapshot, else reindex) before changing anything.
    4. Check the pages out forward and commit them as "revert: restore wiki to <short-sha>".
    5. Restore the snapshot of the target, else reindex from disk.
    6. Capture a snapshot of the new commit and prune.

    The caller holds the wiki's lock. On any `RevertError` the pages and the index are as they were.
    """
    workspace = Path(workspace)
    require_history(workspace)
    target_sha = resolve(workspace, target)
    dirty = dirty_files(workspace)
    if dirty and not force:
        raise DirtyWiki(dirty)
    via, why = index_route(workspace, target_sha)
    reason = why if via == "snapshot" else f"{why}; the index was rebuilt from sources/ and wiki/"

    backup_dir = Path(tempfile.mkdtemp(prefix="revert-", dir=_scratch(workspace)))
    db_replaced = False
    try:
        _backup(workspace, backup_dir)
        progress(f"⏪ Restoring the pages of {target_sha[:7]}")
        try:
            _restore_files(workspace, target_sha)
            report = None
            if via == "snapshot":
                progress("🗄 Restoring the index snapshot")
                db_replaced = True   # from here a failure puts the old index back
                if not snapshots.restore(workspace, target_sha):
                    raise IndexUnavailable("rebuild_failed", f"the snapshot of {target_sha[:7]} became unusable")
            else:
                progress("🗄 No snapshot to use: rebuilding the index from the files")
                report = reindex_from_disk(workspace, str(workspace / ".llmwiki" / "index.db"), progress=progress)
                db_replaced = True
            commit_sha = _commit(workspace, target_sha)
        except IndexUnavailable:
            _undo(workspace, backup_dir, db_replaced)
            raise
        except (extractor.JavaNotInstalledError, extractor.LibreOfficeNotInstalledError) as exc:
            _undo(workspace, backup_dir, db_replaced)
            code = "java" if isinstance(exc, extractor.JavaNotInstalledError) else "libreoffice"
            raise IndexUnavailable(code, f"{why}, and {exc}") from exc
        except Exception as exc:
            _undo(workspace, backup_dir, db_replaced)
            code = "nothing_extracted" if isinstance(exc, NothingExtracted) else "rebuild_failed"
            raise IndexUnavailable(code, f"{why}, and the revert failed: {exc}") from exc
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)

    captured = False
    try:
        new_sha = head_sha(workspace)
        if new_sha and snapshots.snapshots_enabled():
            captured = snapshots.capture(workspace, new_sha) is not None
            snapshots.prune(workspace, snapshots.snapshot_keep())
    except Exception:  # noqa: BLE001 — the revert has happened; the cache is optional
        logger.warning("Snapshot of the revert commit failed", exc_info=True)
    return RevertResult(target_sha=target_sha, db_via=via, restored_pages=_count_pages(workspace),
                        commit_sha=commit_sha, db_reason=reason, snapshot_captured=captured, reindex=report)


def _scratch(workspace: Path) -> Path:
    path = workspace / ".llmwiki"
    path.mkdir(exist_ok=True)
    return path


def _commit(workspace: Path, target_sha: str) -> str | None:
    """Commit the staged restore; the new sha, or None when it changed nothing."""
    _git(workspace, "add", "-A", "--", *[n for n in _TRACKED if (workspace / n).exists()])
    if _git(workspace, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return None
    _git(workspace, "commit", "-q", "-m", f"revert: restore wiki to {target_sha[:7]}")
    return head_sha(workspace)
