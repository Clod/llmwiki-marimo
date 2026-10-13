"""Verification of wiki pages against their sources, for any user interface.

The core is `domain/verify/`. This module reads the settings of the wiki
(`[verify] threshold` and `candidates`), verifies the pages it is given, prints
one progress line per page and never stops at one failing page. It does not
change the text of any page.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from domain.chat.config import load_config
from domain.tools.db import get_connection
from domain.verify import store
from domain.verify.page import PageOutcome, current_state, pages_with_context, verify_page
from domain.verify.verifier import Verifier
from services.wiki import Wiki

Progress = Callable[[str], None]

NO_SOURCES = "no sources to verify"


def _noop(_: str) -> None:
    pass


def relative_path(page: str) -> str:
    """The relative path of a page id (`concepts/x` → `wiki/concepts/x.md`)."""
    return f"wiki/{page}.md"


def page_id(path: str) -> str:
    """The page id of a path: `/wiki/concepts/x.md` or `wiki/concepts/x.md` → `concepts/x`."""
    path = path.lstrip("/")
    return path.removeprefix("wiki/").removesuffix(".md")


def verify_one(wiki: Wiki, page: str, verifier: Verifier) -> PageOutcome:
    """Verify one page and store the result. Raises FileNotFoundError when the
    page has no file or no index row (`index`, `overview` and `log` have none)."""
    config = load_config(wiki.path)
    return verify_page(
        wiki.db_path, wiki.path, relative_path(page), verifier,
        threshold=config.verify_threshold, candidates=config.verify_candidates, language=wiki.language,
    )


def progress_line(page: str, outcome: PageOutcome) -> str:
    """`✔ concepts/x: 20 oraciones, 1 sin respaldo`, or the line of a page with no sources."""
    if outcome.report is None:
        return f"– {page}: {NO_SOURCES}"
    return f"✔ {page}: {outcome.report.checked} sentences, {outcome.report.unsupported} without support"


def verify_pages(wiki: Wiki, pages: Iterable[str], verifier: Verifier, *,
                 progress: Progress = _noop) -> list[PageOutcome]:
    """Verify each page id of `pages`, in order, one progress line per page.

    A page with no sources is reported and skipped; a page that fails is
    reported with its error and does not stop the rest."""
    outcomes: list[PageOutcome] = []
    for page in dict.fromkeys(pages):
        try:
            outcome = verify_one(wiki, page, verifier)
        except Exception as exc:  # noqa: BLE001 — one bad page must not stop the rest
            progress(f"✖ {page}: {exc}")
            continue
        outcomes.append(outcome)
        progress(progress_line(page, outcome))
    return outcomes


def verifiable_pages(wiki: Wiki) -> list[str]:
    """The ids of the pages that have a context to verify against."""
    return [page_id(p) for p in pages_with_context(wiki.db_path, wiki.path)]


def verify_all(wiki: Wiki, verifier: Verifier, *, progress: Progress = _noop) -> list[PageOutcome]:
    """Verify every page that has a context, one progress line per page."""
    pages = verifiable_pages(wiki)
    progress(f"🔎 Verifying {len(pages)} pages…")
    return verify_pages(wiki, pages, verifier, progress=progress)


def summary_line(outcomes: list[PageOutcome]) -> str:
    """The closing line of a verification of many pages."""
    sentences = sum(o.report.checked for o in outcomes if o.report)
    weak = sum(o.report.unsupported for o in outcomes if o.report)
    return f"🏁 Verification: {len(outcomes)} pages, {sentences} sentences, {weak} without support"


def written_pages(wiki: Wiki, source_ids: Iterable[str], *, summaries_only: bool = False) -> list[str]:
    """The ids of the pages an operation wrote for the sources `source_ids`: the
    summaries, and unless `summaries_only` the pages that cite them."""
    ids = list(source_ids)
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    paths = set()
    with get_connection(wiki.db_path) as conn:
        for row in conn.execute(
            f"SELECT relative_path FROM documents WHERE source_kind='wiki' "
            f"AND source_document_id IN ({placeholders})", ids,
        ).fetchall():
            paths.add(row["relative_path"])
        if not summaries_only:
            for row in conn.execute(
                f"SELECT d.relative_path FROM document_references dr "
                f"JOIN documents d ON dr.source_document_id = d.id "
                f"WHERE dr.target_document_id IN ({placeholders}) "
                f"AND dr.reference_type = 'cites' AND d.source_kind = 'wiki'", ids,
            ).fetchall():
                paths.add(row["relative_path"])
    return sorted(page_id(p) for p in paths)


class PageVerification:
    """What the reading screen shows about a page: the state of its verification
    and, when valid, the sentences classified at the threshold in force."""

    def __init__(self, state: store.State, stored: store.StoredVerification | None,
                 threshold: float, has_context: bool) -> None:
        self.state = state
        self.stored = stored
        self.threshold = threshold
        self.has_context = has_context

    @property
    def unsupported(self) -> list[store.StoredSentence]:
        return self.stored.unsupported(self.threshold) if self.stored else []


def page_verification(wiki: Wiki, page: str) -> PageVerification | None:
    """The verification of `page` as the reading screen shows it, or None for a
    page that is not in the index (`index`, `overview`, `log`)."""
    from domain.verify.page import PageContext, resolve_context

    path = relative_path(page)
    file = wiki.path / path
    if not file.is_file():
        return None
    with get_connection(wiki.db_path) as conn:
        row = conn.execute(
            "SELECT id FROM documents WHERE relative_path = ? AND source_kind = 'wiki'", (path,)
        ).fetchone()
        if row is None:
            return None
        context: PageContext = resolve_context(conn, row["id"], file.read_text(encoding="utf-8"))
    state, stored = current_state(wiki.db_path, wiki.path, path)
    return PageVerification(state, stored, load_config(wiki.path).verify_threshold, not context.empty)
