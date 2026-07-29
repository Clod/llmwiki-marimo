"""YAML front-matter handling for dataset files and generated wiki pages.

Dataset files declare their shape in a YAML front-matter block (between the two
`---` lines). Splitting the block from the body is a markdown convention
(`split_frontmatter`); parsing the block itself is delegated to PyYAML
(`parse_frontmatter`). `render_frontmatter` is the inverse — it renders a fields
dict back into a `---`-delimited block, and is what `create_page`
(domain/tools/wiki_fs.py) uses to write wiki-page frontmatter in code instead
of asking the LLM to transcribe it.

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


class _IndentedListDumper(yaml.SafeDumper):
    """SafeDumper that indents block-sequence items under their parent key.

    PyYAML's default aligns a sequence's `-` with its parent mapping key
    (`sources:\n- resource: ...`). OKF-style provenance lists read better
    indented (`sources:\n  - resource: ...`), so `render_frontmatter` uses this
    dumper for list-of-mapping values (see `sources`).
    """

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


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


# Fixed lead-in order for the keys wiki pages care about; anything else that
# gets passed in follows, alphabetically.
_LEAD_KEYS = ("type", "title", "tags", "sources")


def _is_empty(value: object) -> bool:
    """None, or an empty list/string — the values render_frontmatter omits."""
    if value is None:
        return True
    if isinstance(value, (list, str)):
        return len(value) == 0
    return False


def render_frontmatter(fields: dict[str, object]) -> str:
    """Render a YAML front-matter block, delimiters included, ending in a blank
    line (`---\\n...\\n---\\n\\n`) so a title concatenated right after it lands
    on its own line with a blank line before it — the shape every currently
    shipped wiki page already uses (`---\\n\\n# Title`).

    Key order is fixed: `type`, `title`, `tags`, `sources`, then any remaining
    keys alphabetically. Keys whose value is `None` or an empty list/string are
    skipped (never emits e.g. `tags: []`). Values are rendered with
    `yaml.safe_dump` (not hand-rolled) so anything needing quoting — colons,
    accents, `#` — comes out correctly quoted; lists render inline
    (`tags: [concept]`) to match the existing house style.

    Round-trips through `split_frontmatter`/`parse_frontmatter`:
    `parse_frontmatter(split_frontmatter(render_frontmatter(f))[0]) == f` for
    the fields actually emitted (i.e. minus any skipped empty/None ones).
    """
    kept = {k: v for k, v in fields.items() if not _is_empty(v)}
    ordered: dict[str, object] = {}
    for key in _LEAD_KEYS:
        if key in kept:
            ordered[key] = kept[key]
    for key in sorted(k for k in kept if k not in _LEAD_KEYS):
        ordered[key] = kept[key]

    if not ordered:
        return "---\n---\n\n"

    # Render key-by-key rather than dumping `ordered` in one shot: PyYAML's
    # default_flow_style=None collapses an *entire* mapping to flow style
    # (`{type: concept, title: ...}`) once every value happens to be scalar —
    # not what we want. Dumping one key at a time keeps the mapping in block
    # style while still letting scalar-list values render inline (`tags:
    # [concept]`). A list of mappings (OKF's provenance shape — `sources`) is
    # the exception: those render in block style, one `- resource: ...` entry
    # per line, since a bundle of dicts inline (`sources: [{resource: ...}]`)
    # is unreadable and not what OKF examples look like.
    lines = []
    for key, value in ordered.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            rendered = yaml.dump(
                {key: value}, Dumper=_IndentedListDumper,
                default_flow_style=False, allow_unicode=True,
            )
            lines.append(rendered.rstrip("\n"))
        elif isinstance(value, list):
            rendered = yaml.safe_dump(value, default_flow_style=True, allow_unicode=True).strip()
            lines.append(f"{key}: {rendered}")
        else:
            rendered = yaml.safe_dump({key: value}, default_flow_style=False, allow_unicode=True)
            lines.append(rendered.rstrip("\n"))
    body = "\n".join(lines) + "\n"
    return f"---\n{body}---\n\n"
