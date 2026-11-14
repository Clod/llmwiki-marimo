#!/usr/bin/env python3
"""Write `docs/workflow_diagrams.md`, the flowchart of each chat workflow.

The flowcharts come from the graphs of the workflows
(`base/domain/chat/workflow/diagrams.py`), with the conversion that the audit
page uses. Run it after a node, a decision or a branch of a workflow changes:

    uv run python scripts/workflow_diagrams.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "base"))

from domain.chat.workflow.diagrams import markdown  # noqa: E402

TARGET = ROOT / "docs" / "workflow_diagrams.md"


def main() -> None:
    TARGET.write_text(markdown(), encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
