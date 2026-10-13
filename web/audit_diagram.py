"""The diagrams of `docs/query_nodes.md` for the audit page of a conversation.

The Mermaid blocks are read from the markdown file each time, so that the file is the only
copy of the diagrams: an edit of the file changes the page. A block is the one of section 1
(the turn and the mode choice), 2 (pre-retrieval) or 3 (strict); sections 4 and 5 have no
diagram. Each diagram comes with a style that marks the nodes the turn went through.
"""

from __future__ import annotations

import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "docs" / "query_nodes.md"

# The position of the block, among the Mermaid blocks of the file, that each mode shows
# besides the overview (block 0). The streaming mode has none.
MODE_BLOCK = {"pre-retrieval": 1, "strict": 2}

VISITED_STYLE = "classDef visited fill:#fde047,stroke:#a16207,stroke-width:3px,color:#1f2937"

_BLOCK = re.compile(r"^```mermaid\n(.*?)^```", re.S | re.M)
# A node line: `    Q["Q1 · question"]`, `C2{"C2 · ..."}`, `IDX[("DB · ...")]`. The first group is
# the id Mermaid uses, the second the id of the node in the path, which opens the label.
_NODE = re.compile(r'^\s*(\w+)\s*(?:\[\(|\[|\{|\()\s*"([A-Za-z]\w*) ·', re.M)


def blocks() -> list[str]:
    """The Mermaid blocks of the file, in order; none when the file cannot be read."""
    try:
        return _BLOCK.findall(SOURCE.read_text(encoding="utf-8"))
    except OSError:
        return []


def mark_visited(text: str, path_ids) -> str:
    """The diagram with the nodes of the path styled: only the ids the block defines."""
    labelled = {node: mermaid for mermaid, node in _NODE.findall(text)}
    marked = sorted({labelled[i] for i in path_ids if i in labelled})
    if not marked:
        return text
    return f"{text.rstrip()}\n    {VISITED_STYLE}\n    class {','.join(marked)} visited\n"


def diagrams_for(mode: str, path_ids) -> list[str]:
    """The diagrams of a turn: the overview and the one of its mode, with the path marked."""
    found = blocks()
    wanted = [0, *([MODE_BLOCK[mode]] if mode in MODE_BLOCK else [])]
    return [mark_visited(found[i], path_ids) for i in wanted if i < len(found)]
