"""Restore the frozen golden corpus into a temp workspace for regression tests.

The golden corpus (tests/fixtures/golden_corpus/) is a human-verified, frozen
ingest of the 4 fairy-tale PDFs — see scripts/build_golden_corpus.py. It is the
deterministic starting point for regression tests of non-ingest workflows.
"""

import shutil
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "golden_corpus"


def golden_available() -> bool:
    """True once the corpus has been frozen and committed."""
    return (GOLDEN_DIR / "index.db").exists()


def restore_golden(dest: Path) -> tuple[str, Path]:
    """Restore sources/ + wiki/ + index.db into ``dest``.

    Returns (db_path, workspace). The DB stores only workspace-relative paths, so
    the restored copy is fully self-contained and relocatable.
    """
    workspace = dest / "workspace"
    shutil.copytree(GOLDEN_DIR / "sources", workspace / "sources")
    shutil.copytree(GOLDEN_DIR / "wiki", workspace / "wiki")
    (workspace / ".llmwiki").mkdir(parents=True, exist_ok=True)
    db_path = workspace / ".llmwiki" / "index.db"
    shutil.copy(GOLDEN_DIR / "index.db", db_path)
    return str(db_path), workspace
