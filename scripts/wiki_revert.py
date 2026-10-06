#!/usr/bin/env python3
"""Go back to an earlier point of a wiki: the pages and the index together.

The pages of the target commit are restored forward, as a new commit
"revert: restore wiki to <short-sha>"; the history is never rewritten and
`sources/` is not touched. The index comes back from the snapshot taken at that
commit, else it is rebuilt from `sources/` and `wiki/` (needs Java, and
LibreOffice for DOCX files). Run it with nothing else using the wiki.

Usage:
    python scripts/wiki_revert.py WIKI_DIR --list
    python scripts/wiki_revert.py WIKI_DIR --to HEAD~1
    python scripts/wiki_revert.py WIKI_DIR --to <sha> [--force]

--force discards uncommitted changes in wiki/; without it the revert is refused.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "base"))

from domain.rollback.revert import DirtyWiki, IndexUnavailable, RevertError  # noqa: E402
from services import wiki as wiki_service  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("wiki", type=Path, help="the wiki directory")
    parser.add_argument("--to", metavar="REV", help="a commit sha, or a revision such as HEAD~1")
    parser.add_argument("--list", action="store_true", help="list the commits that touched wiki/ and exit")
    parser.add_argument("--force", action="store_true", help="discard uncommitted changes in wiki/")
    args = parser.parse_args(argv)
    if not args.list and not args.to:
        parser.error("give --to REV, or --list")

    wiki = wiki_service.open_wiki(args.wiki)
    if args.list:
        for c in wiki_service.history(wiki):
            print(f"{c.short}  {c.date[:16].replace('T', ' ')}  {'●' if c.has_snapshot else '○'}  "
                  f"{len(c.pages):>3} pages  {c.message}")
        return 0
    try:
        result = wiki_service.revert(wiki, args.to, force=args.force, progress=print)
    except DirtyWiki as exc:
        print(f"Refused: {exc}\nCommit or discard these files, or run again with --force.", file=sys.stderr)
        return 2
    except IndexUnavailable as exc:
        print(f"Refused, nothing changed: {exc.reason}", file=sys.stderr)
        return 3
    except RevertError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 1
    print(f"Wiki restored to {result.target_sha[:7]}: {result.restored_pages} pages. "
          f"Index: {result.db_via} ({result.db_reason}). "
          + (f"New commit {result.commit_sha[:7]}." if result.commit_sha else "The pages were already identical."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
