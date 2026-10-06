"""CHANGELOG.md keeps each Keep a Changelog heading once per release block."""

import re
from collections import Counter
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"


def test_each_release_block_uses_every_heading_once():
    text = CHANGELOG.read_text(encoding="utf-8")
    blocks = re.split(r"^## \[", text, flags=re.M)[1:]
    assert blocks and blocks[0].startswith("Unreleased")
    for block in blocks:
        name = block.split("]", 1)[0]
        counts = Counter(re.findall(r"^### (\w+)", block, flags=re.M))
        repeated = {h: n for h, n in counts.items() if n > 1}
        assert not repeated, f"[{name}] repeats {repeated}"
