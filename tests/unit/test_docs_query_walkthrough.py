"""The query walkthrough quotes a captured run, and the appendix names real nodes.

`docs/query_walkthrough.md` quotes answers of the chat in blockquotes, and
`scripts/capture_query_walkthrough.py` writes those answers to
`docs/query_walkthrough_appendix.md`. A quotation that is not in the appendix was
written by hand, or comes from an older capture. A node of the appendix that
`docs/query_nodes.md` does not list was renamed or removed in the code. A failure
here means the documents have to be captured or edited again, not that the chat
is broken.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "query_walkthrough.md"
APPENDIX = REPO / "docs" / "query_walkthrough_appendix.md"
NODES = REPO / "docs" / "query_nodes.md"


def _quotations(text: str) -> list[str]:
    """The lines of the blockquotes of `text`, without the `> ` mark."""
    return [line[2:].strip() for line in text.splitlines() if line.startswith("> ") and line[2:].strip()]


def test_every_quotation_of_the_walkthrough_is_in_the_appendix():
    appendix = APPENDIX.read_text(encoding="utf-8")
    missing = [q for q in _quotations(DOC.read_text(encoding="utf-8")) if q not in appendix]
    assert not missing, f"quotations not in the appendix: {missing}"


def test_every_node_of_the_appendix_is_a_node_of_query_nodes():
    listed = set(re.findall(r"^\| ([A-Z][A-Z_]*) \|", NODES.read_text(encoding="utf-8"), re.M))
    paths = re.findall(r"^- \*\*Path\*\*: (.+)$", APPENDIX.read_text(encoding="utf-8"), re.M)
    assert paths, "the appendix has no path"
    unknown = {node for path in paths for node in path.split(" → ")} - listed
    assert not unknown, f"nodes of the appendix that query_nodes.md does not list: {sorted(unknown)}"
