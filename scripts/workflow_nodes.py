#!/usr/bin/env python3
"""Write `docs/workflow_nodes.md`, the table of the nodes of each chat workflow.

The table comes from the node lists that each workflow declares
(`base/domain/chat/workflow/table.py`). Run it after a node is added to or
removed from a workflow:

    uv run python scripts/workflow_nodes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "base"))

from domain.chat.workflow.table import markdown  # noqa: E402

TARGET = ROOT / "docs" / "workflow_nodes.md"


def main() -> None:
    TARGET.write_text(markdown(), encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
