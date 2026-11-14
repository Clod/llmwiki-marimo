"""The three model tests: reading, ingestion and vision.

Each test runs the path of the product with one model and returns a `Report`. The
reading and the ingestion tests run on a temporary wiki that `temporary_wiki` builds
with the pages of `material.py` and deletes at the end. The vision test reads the
pattern PDF of `assets/`. No test reads `tests/`, needs Java or touches the wiki of
the user.

Every test has the signature `test_<kind>(base_url, api_key, model, language, progress)`.
`progress` receives each `Check` when it ends. A check that raises becomes a check that
did not run (`ran=False`), with the message of the exception as its detail.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import openai
from openai import OpenAI

from domain.modeltest.material import ASSETS, Material, material
from domain.modeltest.report import Check, Report
from services.wiki import Wiki, open_wiki

logger = logging.getLogger(__name__)

Progress = Callable[[Check], None] | None

_DETAIL_LIMIT = 300


@dataclass(frozen=True)
class _Message:
    """A chat message in the shape `services.chat` reads."""

    role: str
    content: str
    context: tuple = ()


class _WatchedClient:
    """An OpenAI client that keeps the errors of the service.

    The ingestion pipeline turns every exception into `status="failed"`, and the OCR
    engine replaces the exception of a page with its own. This client keeps the
    original `openai.APIError`, so a test tells a service that gave no answer from a
    model that answered wrong.
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self.errors: list[openai.APIError] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return self._client.chat.completions.create(*args, **kwargs)
        except openai.APIError as exc:
            self.errors.append(exc)
            raise


def _one_line(value: object) -> str:
    """`value` as one line of at most `_DETAIL_LIMIT` characters."""
    text = " ".join(str(value).split())
    return text if len(text) <= _DETAIL_LIMIT else text[: _DETAIL_LIMIT - 1] + "…"


def _not_run(key: str, exc: BaseException) -> Check:
    logger.warning("model test: the check %s did not run: %s", key, exc)
    return Check(key, False, _one_line(exc) or type(exc).__name__, ran=False)


def _done(check: Check, progress: Progress) -> Check:
    if progress is not None:
        progress(check)
    return check


def _run(key: str, work: Callable[[], Check], progress: Progress) -> Check:
    """Run one check. An exception makes it a check that did not run."""
    try:
        check = work()
    except Exception as exc:  # noqa: BLE001 — a check that cannot run is reported, not raised
        check = _not_run(key, exc)
    return _done(check, progress)


def _seconds(start: float) -> float:
    return round(time.monotonic() - start, 1)


@contextmanager
def temporary_wiki(language: str) -> Iterator[Wiki]:
    """A wiki in a temporary folder, with the two concept pages of `material(language)`.

    No model is called. The folder is deleted when the block ends.
    """
    from domain.tools.wiki_fs import create_page

    with tempfile.TemporaryDirectory(prefix="model-test-") as folder:
        wiki = open_wiki(Path(folder) / "wiki", language)
        for page in material(language).pages:
            create_page(wiki.db_path, wiki.path, "/wiki/concepts/", page.slug, page.title,
                        page.content, [], language=language)
        yield wiki


# ── Reading ───────────────────────────────────────────────────────────────────

def test_reading(base_url: str, api_key: str, model: str, language: str = "en",
                 progress: Progress = None) -> Report:
    """Three chat turns on the temporary wiki.

    answers:    a question the first page answers, in the pre-retrieval mode. The path
                ends in Q21, and the answer holds the datum and cites the page.
    declines:   a question no page answers, in the pre-retrieval mode. The path ends in R5.
    uses_tools: the first question, in the strict mode. The path ends in T5 and has a K
                node (a call to a tool), and the answer holds the datum.
    """
    start = time.monotonic()
    with temporary_wiki(language) as wiki:
        checks = asyncio.run(_reading_checks(wiki, material(language), base_url, api_key, model, progress))
    return Report("reading", model, tuple(checks), _seconds(start))


async def _reading_checks(wiki: Wiki, m: Material, base_url: str, api_key: str, model: str,
                          progress: Progress) -> list[Check]:
    # One event loop for the three turns: the agents keep one HTTP client, bound to it.
    from services import chat as chat_service

    keys = ("answers", "declines", "uses_tools")
    try:
        agents = chat_service.build_agents(wiki, base_url, api_key, model)
    except Exception as exc:  # noqa: BLE001 — a check that cannot run is reported, not raised
        return [_done(_not_run(key, exc), progress) for key in keys]

    async def turn(question: str, mode: str) -> tuple[str, list[str]]:
        result = await chat_service.chat_turn_with_context(wiki, agents, [_Message("user", question)], mode=mode)
        return result.answer, [n["node"] for n in result.path]

    slug = m.pages[0].slug
    cases = (
        ("answers", m.answerable, chat_service.PRE_RETRIEVAL),
        ("declines", m.unanswerable, chat_service.PRE_RETRIEVAL),
        ("uses_tools", m.answerable, chat_service.STRICT),
    )
    checks = []
    for key, question, mode in cases:
        try:
            answer, path = await turn(question, mode)
        except Exception as exc:  # noqa: BLE001 — a check that cannot run is reported, not raised
            checks.append(_done(_not_run(key, exc), progress))
            continue
        checks.append(_done(_judge_reading(key, answer, path, m.fact, slug), progress))
    return checks


_LAST_NODE = {"answers": "Q21", "declines": "R5", "uses_tools": "T5"}


def _judge_reading(key: str, answer: str, path: list[str], fact: str, slug: str) -> Check:
    """The check `key` of a turn whose answer is `answer` and whose nodes are `path`."""
    last = path[-1] if path else "no node"
    expected = _LAST_NODE[key]
    problems = [] if last == expected else [f"the path ends in {last}, not in {expected}"]
    if key == "uses_tools" and not any(node.startswith("K") for node in path):
        problems.append("the path has no K node, so the model called no tool")
    if key in ("answers", "uses_tools") and fact not in answer:
        problems.append(f"the answer does not hold {fact}")
    if key == "answers" and slug not in answer:
        problems.append(f"the answer does not cite {slug}")
    if problems:
        return Check(key, False, ", ".join(problems))
    return Check(key, True, f"the path ends in {expected}")


# ── Ingestion ─────────────────────────────────────────────────────────────────

def test_ingestion(base_url: str, api_key: str, model: str, language: str = "en",
                   progress: Progress = None) -> Report:
    """The ingestion of the source text of the material into the temporary wiki.

    ingests: the ingestion ends as "ingested", the summary page exists, at least one new
             concept page names the source, and the lint finds no error.
    """
    start = time.monotonic()
    m = material(language)
    with temporary_wiki(language) as wiki:
        check = _run("ingests", lambda: _ingests(wiki, m, base_url, api_key, model, language), progress)
    return Report("ingestion", model, (check,), _seconds(start))


def _ingests(wiki: Wiki, m: Material, base_url: str, api_key: str, model: str, language: str) -> Check:
    from domain.ingestion import ingest_file
    from domain.ingestion.wiki_generator import make_wiki_slug
    from domain.lint import lint_wiki

    source = wiki.sources_dir / m.source_name
    source.write_text(m.source_text, encoding="utf-8")
    client = _WatchedClient(OpenAI(base_url=base_url, api_key=api_key))
    result = ingest_file(source, wiki.db_path, wiki.path, client, model, language=language)
    if result.status != "ingested":
        if client.errors:
            return _not_run("ingests", client.errors[-1])
        return Check("ingests", False, _one_line(f"the ingestion ended as {result.status}: {result.message}"))

    summary = f"summaries/{make_wiki_slug(m.source_name)}.md"
    if not (wiki.wiki_dir / summary).is_file():
        return Check("ingests", False, f"the summary page {summary} does not exist")

    initial = {page.slug for page in m.pages}
    name = Path(m.source_name).stem
    new = [p for p in sorted((wiki.wiki_dir / "concepts").glob("*.md")) if p.stem not in initial]
    naming = [p for p in new if name in p.read_text(encoding="utf-8")]
    if not new:
        return Check("ingests", False, "the ingestion wrote no new concept page")
    if not naming:
        return Check("ingests", False, f"none of the {len(new)} new concept pages names {name}")

    errors = lint_wiki(wiki.db_path, wiki.path).errors
    if errors:
        found = ", ".join(sorted({issue.check for issue in errors}))
        return Check("ingests", False, f"the lint found {len(errors)} error(s): {found}")
    return Check("ingests", True, f"the ingestion wrote the summary page and {len(new)} new concept page(s)")


# ── Vision ────────────────────────────────────────────────────────────────────

def test_vision(base_url: str, api_key: str, model: str, language: str = "en",
                progress: Progress = None) -> Report:
    """Two calls with the pattern PDF of the material, a scanned page with no text layer.

    reads_page: the OCR of the vision engine reads the page, and every datum of
                `<pattern>.json` is in the text it reads.
    describes:  the description of the page as an image has the format the prompt asks
                for (`describe.parse_answer` accepts it).
    """
    start = time.monotonic()
    m = material(language)
    pdf = ASSETS / f"{m.pattern}.pdf"
    data = json.loads((ASSETS / f"{m.pattern}.json").read_text(encoding="utf-8"))["data"]
    with tempfile.TemporaryDirectory(prefix="model-test-") as name:
        folder = Path(name)
        checks = (
            _run("reads_page", lambda: _reads_page(pdf, data, folder, base_url, api_key, model, language), progress),
            _run("describes", lambda: _describes(pdf, folder, base_url, api_key, model, language), progress),
        )
    return Report("vision", model, checks, _seconds(start))


def _reads_page(pdf: Path, data: list[str], folder: Path, base_url: str, api_key: str, model: str,
                language: str) -> Check:
    from domain.ingestion import ocr

    client = _WatchedClient(OpenAI(base_url=base_url, api_key=api_key))
    engine = ocr.VisionEngine(base_url, api_key, model, client=client)
    try:
        pages = engine.run(pdf, [1], language, cache_dir=folder)
    except RuntimeError:
        # The engine raises its own error when every page failed: the error of the
        # service says more.
        if client.errors:
            return _not_run("reads_page", client.errors[-1])
        raise
    text = dict(pages).get(1, "")
    missing = [datum for datum in data if datum not in text]
    if missing:
        return Check("reads_page", False, _one_line(
            f"the reading lacks {len(missing)} of the {len(data)} data: {', '.join(missing)}"))
    return Check("reads_page", True, f"the reading holds the {len(data)} data")


def _describes(pdf: Path, folder: Path, base_url: str, api_key: str, model: str, language: str) -> Check:
    import pypdfium2

    from domain.ingestion import describe, ocr

    image = folder / f"{pdf.stem}.png"
    document = pypdfium2.PdfDocument(str(pdf))
    try:
        image.write_bytes(ocr._render_png(document[0]))     # the conversion of the OCR engine
    finally:
        document.close()
    vision = describe.Vision(OpenAI(base_url=base_url, api_key=api_key), model)
    try:
        answer, _, _ = describe.call_vision(vision, image, language)
    except describe.BadAnswer as exc:
        return Check("describes", False, f"the answer does not have the format asked: {exc}")
    return Check("describes", True, f"the answer has the {len(answer)} keys of the format")


# The names start with `test_`: pytest would collect them from any test module that
# imports them.
test_reading.__test__ = False      # type: ignore[attr-defined]
test_ingestion.__test__ = False    # type: ignore[attr-defined]
test_vision.__test__ = False       # type: ignore[attr-defined]
