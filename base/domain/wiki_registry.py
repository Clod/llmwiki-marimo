"""Wiki folder registry: discovery, recent-list persistence, path hygiene.

Pure helpers (no marimo / UI imports) so they can be unit-tested and reused by
both marimo apps. Used by the wiki picker that lets the user switch between
multiple wikis at runtime instead of editing WIKI_PATH in .env.

Why no file_browser: marimo's `file_browser(selection_mode="directory")` does not
emit a value in the 0.23.x line (GH issue #1478), so directory picking is done
via discovery + a recent list + a sanitised text path instead.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Recent wikis live outside any single wiki so the list survives switching.
RECENT_FILE = Path.home() / ".llmwiki" / "recent_wikis.json"


def is_wiki_dir(path: Path) -> bool:
    """A folder looks like a wiki if it has a wiki/ or .llmwiki/ subdir."""
    return (path / "wiki").is_dir() or (path / ".llmwiki").is_dir()


def clean_path_input(raw: str) -> str:
    """Strip whitespace and a single pair of surrounding quotes from a path.

    Handles 'Copy as Pathname' / shell-style paste such as ``'/a/b c'`` or
    ``"/a/b"`` — a leading quote would otherwise make the path relative and
    corrupt resolution.
    """
    s = (raw or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1]
    return s.strip()


def load_recent(recent_file: Path = RECENT_FILE) -> list[str]:
    """Load the recent-wikis list; return [] if missing or malformed."""
    try:
        data = json.loads(recent_file.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    return [p for p in data if isinstance(p, str)]


def save_recent(paths: list[str], recent_file: Path = RECENT_FILE) -> None:
    """Persist the recent-wikis list, creating the parent dir if needed."""
    recent_file.parent.mkdir(parents=True, exist_ok=True)
    recent_file.write_text(json.dumps(paths, indent=2))


def push_recent(
    path: str,
    existing: list[str],
    cap: int = 12,
    recent_file: Path = RECENT_FILE,
) -> list[str]:
    """Add `path` to the front (most-recent-first), de-duplicate, cap, persist."""
    updated = [path] + [p for p in existing if p != path]
    updated = updated[:cap]
    save_recent(updated, recent_file)
    return updated


def discover_wikis(home: Path) -> list[str]:
    """Immediate sub-folders of `home` that look like wikis, plus `home` itself."""
    found: list[str] = []
    if is_wiki_dir(home):
        found.append(str(home))
    try:
        for child in sorted(home.iterdir()):
            if child.is_dir() and is_wiki_dir(child):
                found.append(str(child))
    except (FileNotFoundError, PermissionError, OSError):
        pass
    return found


def merge_options(home: Path, recent: list[str], active: str | None = None) -> list[str]:
    """Ordered, de-duplicated wiki options: active first, then discovered, then recent."""
    ordered: dict[str, None] = {}
    for p in ([active] if active else []) + discover_wikis(home) + recent:
        if p:
            ordered.setdefault(p, None)
    return list(ordered.keys())


def short_label(path: str, parts_shown: int = 2) -> str:
    """A compact dropdown label like '…/finanzas/my-wiki'."""
    parts = Path(path).parts
    if len(parts) > parts_shown:
        return "…/" + "/".join(parts[-parts_shown:])
    return path


def resolve_wiki_home(env_wiki_path: str | None = None) -> Path:
    """Folder to scan for wikis: $WIKI_HOME, else parent of WIKI_PATH, else ~."""
    home_env = os.environ.get("WIKI_HOME", "").strip()
    if home_env:
        return Path(home_env).expanduser().resolve()
    wp = env_wiki_path if env_wiki_path is not None else os.environ.get("WIKI_PATH", "")
    if wp:
        return Path(wp).expanduser().resolve().parent
    return Path.home()
