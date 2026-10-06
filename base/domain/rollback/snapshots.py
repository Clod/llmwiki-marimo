"""The DB snapshot cache: one byte-exact copy of `index.db` per commit.

`.llmwiki/snapshots/<commit-sha>.db` pairs with the commit of the same sha. The
snapshots are a cache: git holds the pages, and `reindex_from_disk` rebuilds the
index when a snapshot is missing. They are local and ignored by git (the whole
`.llmwiki/` folder is).

The database runs in WAL mode, so a plain file copy can miss frames that no
checkpoint has written to the main file. `capture` uses the sqlite3 online
backup API, which reads the committed state through the WAL. `restore` swaps a
temp file in with `os.replace`, so a reader sees the old index or the new one,
never a half-written file.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_KEEP = 5
KEEP_ENV = "WIKI_SNAPSHOT_KEEP"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SIDE_FILES = ("-wal", "-shm")


@dataclass(frozen=True)
class Snapshot:
    commit_sha: str
    db_path: Path
    created_at: str        # ISO-8601, UTC
    size_bytes: int


def snapshot_keep() -> int:
    """How many snapshots to keep: `WIKI_SNAPSHOT_KEEP`, default 5. A value that is
    not a non-negative integer falls back to the default; 0 disables the cache."""
    raw = os.environ.get(KEEP_ENV, "").strip()
    if not raw:
        return DEFAULT_KEEP
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer; using %d", KEEP_ENV, raw, DEFAULT_KEEP)
        return DEFAULT_KEEP
    return value if value >= 0 else DEFAULT_KEEP


def snapshots_enabled() -> bool:
    return snapshot_keep() > 0


def _dir(workspace: Path) -> Path:
    return Path(workspace) / ".llmwiki" / "snapshots"


def _live_db(workspace: Path) -> Path:
    return Path(workspace) / ".llmwiki" / "index.db"


def _snapshot_file(workspace: Path, commit_sha: str) -> Path:
    if not _SHA_RE.match(commit_sha):
        raise ValueError(f"not a full commit sha: {commit_sha!r}")
    return _dir(workspace) / f"{commit_sha}.db"


def _read_manifest(workspace: Path) -> dict[str, str]:
    """sha → created_at from manifest.json; {} when it is missing or unreadable."""
    try:
        data = json.loads((_dir(workspace) / "manifest.json").read_text(encoding="utf-8"))
        return {e["sha"]: e["created_at"] for e in data}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _backup_to(source: Path, dest: Path) -> None:
    """Copy the committed state of `source` into a new single-file database at `dest`."""
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
            # A single self-contained file: no -wal/-shm beside the snapshot.
            dst.execute("PRAGMA journal_mode=DELETE")
        finally:
            dst.close()
    finally:
        src.close()


def _is_sound(path: Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def capture(workspace: Path, commit_sha: str) -> Snapshot | None:
    """Write a consistent copy of index.db to snapshots/<sha>.db.

    Uses the sqlite3 online backup API, not a file copy: the database runs in WAL
    mode, and a raw copy of index.db can miss frames no checkpoint has written.
    Returns None when the index is absent or snapshots are disabled.
    """
    workspace = Path(workspace)
    live = _live_db(workspace)
    if not snapshots_enabled() or not live.is_file():
        return None
    target = _snapshot_file(workspace, commit_sha)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".capture-", suffix=".db")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        _backup_to(live, tmp)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    manifest = _read_manifest(workspace)
    manifest[commit_sha] = created_at
    snapshot = Snapshot(commit_sha, target, created_at, target.stat().st_size)
    _write_manifest(workspace, manifest)
    return snapshot


def _write_manifest(workspace: Path, created: dict[str, str]) -> None:
    """Write manifest.json from `created` plus the files on disk."""
    entries = []
    for path in _dir(workspace).glob("*.db"):
        if not _SHA_RE.match(path.stem):
            continue
        stat = path.stat()
        entries.append({"sha": path.stem, "size_bytes": stat.st_size, "_mtime": stat.st_mtime_ns,
                        "created_at": created.get(path.stem) or datetime.fromtimestamp(
                            stat.st_mtime, timezone.utc).isoformat(timespec="microseconds")})
    entries.sort(key=lambda e: (e["created_at"], e["_mtime"]), reverse=True)
    for e in entries:
        del e["_mtime"]
    target = _dir(workspace) / "manifest.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    os.replace(tmp, target)


def list_snapshots(workspace: Path) -> list[Snapshot]:
    """The snapshots on disk, newest first. The files are the truth; manifest.json
    only supplies the creation time, so a lost manifest loses nothing."""
    directory = _dir(Path(workspace))
    if not directory.is_dir():
        return []
    created = _read_manifest(Path(workspace))
    found = []
    for path in directory.glob("*.db"):
        if not _SHA_RE.match(path.stem):
            continue
        stat = path.stat()
        when = created.get(path.stem) or datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(
            timespec="microseconds")
        found.append((when, stat.st_mtime_ns, Snapshot(path.stem, path, when, stat.st_size)))
    found.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [s for _, _, s in found]


def has_snapshot(workspace: Path, commit_sha: str) -> bool:
    return _SHA_RE.match(commit_sha) is not None and _snapshot_file(Path(workspace), commit_sha).is_file()


def is_usable(workspace: Path, commit_sha: str) -> bool:
    """True when snapshots are enabled and snapshots/<sha>.db exists and is sound."""
    if not snapshots_enabled() or not has_snapshot(workspace, commit_sha):
        return False
    return _is_sound(_snapshot_file(Path(workspace), commit_sha))


def install_db(workspace: Path, source: Path) -> None:
    """Replace index.db with a copy of the database `source`, atomically.

    The copy is written to a temp file beside index.db and swapped in with
    `os.replace`. The stale `-wal` and `-shm` of the old database go first:
    applied to the new file they would corrupt it. The caller closes every
    connection to index.db and holds the wiki's lock.
    """
    live = _live_db(Path(workspace))
    live.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=live.parent, prefix=".restore-", suffix=".db")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        _backup_to(Path(source), tmp)
        for suffix in _SIDE_FILES:
            with contextlib.suppress(FileNotFoundError):
                Path(f"{live}{suffix}").unlink()
        os.replace(tmp, live)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def restore(workspace: Path, commit_sha: str) -> bool:
    """Atomically replace index.db with snapshots/<sha>.db (temp file + os.replace).

    Returns False when that snapshot is absent or fails its integrity check;
    index.db has not been touched and the caller reindexes. See `install_db` for
    what the caller guarantees.
    """
    snapshot = _snapshot_file(Path(workspace), commit_sha)
    if not snapshot.is_file() or not _is_sound(snapshot):
        return False
    install_db(workspace, snapshot)
    return True


def prune(workspace: Path, keep: int = DEFAULT_KEEP) -> int:
    """Keep the newest `keep` snapshots; return how many were removed."""
    workspace = Path(workspace)
    removed = 0
    for snapshot in list_snapshots(workspace)[max(keep, 0):]:
        with contextlib.suppress(FileNotFoundError):
            snapshot.db_path.unlink()
            removed += 1
    if removed or _dir(workspace).is_dir():
        _write_manifest(workspace, _read_manifest(workspace))
    return removed
