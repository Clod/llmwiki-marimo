"""Importing ``domain.chat`` must not depend on the order of its imports.

``domain/chat/__init__.py`` imports ``agent.py``, which imports the chat tools, which
import ``guardrail.py``. Every module loaded during that chain meets the package
half-initialised: ``from domain.chat import X`` there raises ImportError as soon as X
is a name ``__init__.py`` defines (HALLAZGOS, 2026-10-15). This test lists the modules
the chain loads and checks that none of them imports from the package namespace.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2] / "base"

_LIST_CHAIN = """
import json, sys
before = set(sys.modules)
import domain.chat
loaded = sorted(m for m in set(sys.modules) - before
                if m.startswith("domain.chat.") and sys.modules[m].__file__)
print(json.dumps({m: sys.modules[m].__file__ for m in loaded}))
"""


def _imports_from_package(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module == "domain.chat" or (node.level == 1 and node.module is None))
    ]


def test_no_module_in_the_package_init_chain_imports_from_the_package():
    out = subprocess.run(
        [sys.executable, "-c", _LIST_CHAIN],
        cwd=BASE, capture_output=True, text=True, check=True,
    ).stdout
    chain = json.loads(out)
    assert "domain.chat.guardrail" in chain  # the chain this test guards
    offenders = {
        name: lines
        for name, file in chain.items()
        if (lines := _imports_from_package(Path(file)))
    }
    assert offenders == {}
