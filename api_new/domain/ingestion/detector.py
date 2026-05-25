"""Change detection: decide whether a file needs (re-)ingestion.

Logic lifted from api/domain/watcher.py::_index_file().
Uses mtime_ns as a fast pre-check; only computes SHA-256 when mtime changed.
"""

import hashlib
import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def compute_file_hash(path: Path) -> str:
    """Return SHA-256 hex digest of the file's bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def needs_ingestion(path: Path, conn: sqlite3.Connection) -> tuple[bool, str]:
    """Return (should_process, current_hash).

    Fast path: if mtime_ns matches the stored value, skip the hash entirely.
    Slow path: if mtime_ns differs, compute hash and compare.
    Returns (True, hash) when the file is new or has changed.
    Returns (False, stored_hash) when the file is unchanged.
    """
    relative = _relative_path(path)
    stat = path.stat()
    current_mtime_ns = stat.st_mtime_ns

    row = conn.execute(
        "SELECT mtime_ns, content_hash FROM documents WHERE relative_path = ?",
        (relative,),
    ).fetchone()

    if row is None:
        current_hash = compute_file_hash(path)
        logger.debug("New file: %s", relative)
        return True, current_hash

    stored_mtime_ns, stored_hash = row["mtime_ns"], row["content_hash"]

    if stored_mtime_ns == current_mtime_ns:
        logger.debug("Unchanged (mtime match): %s", relative)
        return False, stored_hash or ""

    current_hash = compute_file_hash(path)
    if current_hash == stored_hash:
        # mtime changed (e.g. touch) but content is the same — update mtime only
        conn.execute(
            "UPDATE documents SET mtime_ns = ? WHERE relative_path = ?",
            (current_mtime_ns, relative),
        )
        conn.commit()
        logger.debug("Unchanged (hash match, mtime updated): %s", relative)
        return False, current_hash

    logger.debug("Modified: %s", relative)
    return True, current_hash


def _relative_path(path: Path) -> str:
    """Return the path relative to sources/ as stored in the DB."""
    # Normalise to just the filename under sources/
    # Pipeline always places files as sources/{filename}
    return f"sources/{path.name}"
