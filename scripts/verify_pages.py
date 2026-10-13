#!/usr/bin/env python3
"""Verify the pages of a wiki against their sources and print a table.

Same operation as "Verificar todas las páginas" of the web interface
(`services.verification.verify_all`), with the numbers the owner needs to
calibrate the threshold: per page its context kind, sentences, unsupported
share and time; and the pairs the passage pre-selection saved.

No model of the chat or the ingestion is called. The verifier is the local NLI
model (`uv sync --group verify`), or with `--verifier fake` a lexical-overlap
stand-in whose numbers mean nothing about the real model. The script stores the
verifications in the index of the wiki, so run it on a copy:

    cp -r examples/finanzas-argentinas /tmp/finanzas
    uv run python scripts/verify_pages.py --wiki /tmp/finanzas --verifier fake
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "base"))

from domain.verify import FakeVerifier, nli  # noqa: E402
from domain.verify.page import PageOutcome  # noqa: E402
from services import verification  # noqa: E402
from services.wiki import open_wiki  # noqa: E402


def table(outcomes: list[PageOutcome], wiki_pages: int, seconds: float, verifier: str) -> str:
    """The result as markdown."""
    rows = ["| page | kind | sentences | unsupported | share | pairs | of possible | seconds |",
            "|---|---|---:|---:|---:|---:|---:|---:|"]
    sentences = weak = pairs = possible = 0
    for o in outcomes:
        r = o.report
        rows.append(f"| {verification.page_id(o.relative_path)} | {o.kind} | {r.checked} | {r.unsupported} | "
                    f"{r.unsupported / r.checked:.0%} | {r.pairs} | {r.pairs_possible} | {o.seconds:.2f} |"
                    if r.checked else
                    f"| {verification.page_id(o.relative_path)} | {o.kind} | 0 | 0 | – | 0 | 0 | {o.seconds:.2f} |")
        sentences, weak, pairs, possible = sentences + r.checked, weak + r.unsupported, pairs + r.pairs, possible + r.pairs_possible
    verified = len(outcomes)
    rows += ["", f"- Verifier: `{verifier}`.",
             f"- Pages in the wiki: {wiki_pages}; verified: {verified}; with no sources: {wiki_pages - verified}.",
             f"- Sentences: {sentences}; unsupported: {weak} ({weak / sentences:.0%})." if sentences else "- Sentences: 0.",
             f"- Pairs sent to the verifier: {pairs} of {possible} possible "
             f"({1 - pairs / possible:.0%} saved by the pre-selection)." if possible else "- Pairs: 0.",
             f"- Time: {seconds:.1f} s in total, {seconds / verified:.2f} s per page." if verified else ""]
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--wiki", required=True, type=Path, help="the wiki folder (use a copy)")
    parser.add_argument("--verifier", choices=("nli", "fake"), default="nli")
    args = parser.parse_args(argv)

    if args.verifier == "nli" and not nli.is_available():
        print("The local NLI verifier is not installed: uv sync --group verify, then download the model.",
              file=sys.stderr)
        return 2
    verifier = FakeVerifier() if args.verifier == "fake" else nli.LocalNliVerifier()
    wiki = open_wiki(args.wiki)
    started = time.perf_counter()
    outcomes = verification.verify_all(wiki, verifier, progress=lambda line: print(line, file=sys.stderr))
    seconds = time.perf_counter() - started
    print(table([o for o in outcomes if o.report], len(verification_pages(wiki)), seconds, verifier.name))
    return 0


def verification_pages(wiki) -> list[str]:
    from services.wiki import list_pages
    from services.verification import page_id
    from domain.tools.db import get_connection

    with get_connection(wiki.db_path) as conn:
        indexed = {page_id(r["relative_path"]) for r in conn.execute(
            "SELECT relative_path FROM documents WHERE source_kind = 'wiki'")}
    return [p for p in list_pages(wiki) if p in indexed]


if __name__ == "__main__":
    raise SystemExit(main())
