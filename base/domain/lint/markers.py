"""Shared markers for the data_gap TODO lifecycle and FTS-safe queries.

Imported by both lint.checks and repair.actions to avoid a circular import.
"""

import re

# The TODO note repair inserts into a host page.
# {slug}=topic slug, {title}=human title, {suggestion}=lint suggestion text.
DATA_GAP_NOTE = (
    "<!-- DATA_GAP: {slug} -->\n"
    "> 🚧 **Missing topic: {title}.** {suggestion} "
    "Drop a source about this into `sources/` and re-ingest to fill it in."
)

# Matches a DATA_GAP note block: the marker line + its single following
# blockquote line. Group "slug" = topic slug.
DATA_GAP_BLOCK_RE = re.compile(
    r"<!-- DATA_GAP: (?P<slug>[a-z0-9-]+) -->\n> .*(?:\n|$)"
)


def contradiction_marker(related_page: str) -> str:
    """Return the idempotency marker for a contradiction annotation."""
    return f"<!-- CONTRADICTION: {related_page} -->"


def fts_safe(text: str) -> str:
    """Reduce a phrase to plain space-separated alphanumeric words for FTS5."""
    return re.sub(r"[^0-9a-zA-Z]+", " ", text).strip()
