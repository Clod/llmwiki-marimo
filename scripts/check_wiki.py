"""Check a generated wiki for the defects of form the code produces.

Usage:  uv run python scripts/check_wiki.py <wiki folder> [<wiki folder> ...]

Prints one problem per line (`page: [rule] message`) and exits with 1 when some wiki
has a problem, 0 otherwise. The rules are in `base/domain/lint/structure.py`: the
front-matter fields, the links between pages, the order of the sections, the pages
missing from `wiki/index.md`, and the extracted text of a source that ends in an empty
heading. The text the model writes is not checked.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "base"))

from domain.lint.structure import check_structure  # noqa: E402
from domain.wiki_settings import load_wiki_language  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wikis", nargs="+", type=Path, help="wiki folders")
    args = parser.parse_args(argv)
    failed = False
    for wiki in args.wikis:
        if not (wiki / "wiki").is_dir():
            print(f"{wiki}: not a wiki folder (no wiki/ inside)", file=sys.stderr)
            failed = True
            continue
        db = wiki / ".llmwiki" / "index.db"
        problems = check_structure(wiki, str(db) if db.is_file() else None, load_wiki_language(wiki))
        print(f"{wiki}: {len(problems)} problem(s)")
        for problem in problems:
            print(f"  {problem}")
        failed = failed or bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
