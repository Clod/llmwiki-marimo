"""YAML front-matter handling for dataset files.

Dataset files declare their shape in a YAML front-matter block (between the two
`---` lines). Splitting the block from the body is a markdown convention
(`split_frontmatter`); parsing the block itself is delegated to PyYAML
(`parse_frontmatter`).

`parse_frontmatter` normalizes PyYAML's behavior to this module's contract:
- an empty block parses to an empty dict;
- a top-level non-mapping (e.g. a bare list/scalar) is a malformed front-matter
  and raises ValueError;
- any YAML syntax error is re-raised as ValueError.
So the caller (parser.py) can reject a bad file via a single `except ValueError`
(datasets-format.md §5: unparseable front-matter -> file rejected).
"""

from __future__ import annotations

import yaml


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split `text` into (frontmatter_block, body).

    Returns (None, text) when there is no leading `---` front-matter block —
    the caller treats that as "not a dataset" (§5: missing `type: dataset`).
    """
    stripped = text.lstrip("﻿")
    if not stripped.startswith("---"):
        return None, text
    lines = stripped.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None, text
    frontmatter_block = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :])
    return frontmatter_block, body


def parse_frontmatter(block: str) -> dict[str, object]:
    """Parse the YAML front-matter block into a dict.

    Raises ValueError on a YAML syntax error or a top-level non-mapping, so the
    caller can reject the file with a clear error rather than guess.
    """
    try:
        loaded = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML front-matter: {exc}") from exc

    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"front-matter must be a mapping, got {type(loaded).__name__}")
    return loaded
