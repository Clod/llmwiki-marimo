"""Phase 2 pipeline integration tests (2.1, 2.2, 2.4, 2.6, 2.8).

Uses a real PDF from tests/fixtures/pdfs/ and FakeLLMClient so no API calls are made.
"""

import json
import shutil
from pathlib import Path


from domain.ingestion.pipeline import ingest_file
from domain.ingestion.wiki_generator import make_wiki_slug
from domain.tools.db import get_connection
from domain.tools.search import search_chunks
from domain.tools.wiki_fs import create_page
from tests.helpers.fake_llm import FakeLLMClient
from tests.helpers.workspace import WorkspaceFixture

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_PDF = _FIXTURES / "pdfs" / "Snow White and the Seven Dwarfs.pdf"

_EXTRACTION_JSON = json.dumps({
    "document_summary": "Snow White is a classic fairy tale about a princess and an evil queen.",
    "concepts": [
        {"name": "Snow White", "category": "entity",
         "insight": "The protagonist princess of the story"},
        {"name": "Evil Queen", "category": "entity",
         "insight": "The antagonist who tries to poison Snow White"},
    ],
})

_CONCEPT_PAGE_1 = (
    "# Snow White\n\n"
    "Snow White is the titular protagonist of the fairy tale. "
    "She is known for her fairness and kindness.\n\n"
    "## Sources\n- [^1]: Snow White and the Seven Dwarfs.pdf\n"
)

_CONCEPT_PAGE_2 = (
    "# Evil Queen\n\n"
    "The Evil Queen is the antagonist who disguises herself to harm Snow White.\n\n"
    "## Sources\n- [^1]: Snow White and the Seven Dwarfs.pdf\n"
)

_OVERVIEW = (
    "# Knowledge Base Overview\n\n"
    "This knowledge base contains fairy tales including the classic story of Snow White, "
    "featuring themes of jealousy, kindness, and justice.\n"
)


def _make_llm(tmp_workspace: WorkspaceFixture) -> FakeLLMClient:
    """LLM with sequential responses: extract → concept1 → concept2 → overview."""
    tmp_workspace.llm.responses = [
        _EXTRACTION_JSON,
        _CONCEPT_PAGE_1,
        _CONCEPT_PAGE_2,
        _OVERVIEW,
    ]
    return tmp_workspace.llm


def _copy_pdf(tmp_workspace: WorkspaceFixture) -> Path:
    dest = tmp_workspace.workspace / "sources" / _PDF.name
    shutil.copy(_PDF, dest)
    return dest


# ── 2.1: Output path restructured ────────────────────────────────────────────

def test_ingest_creates_summary_in_summaries_subdir(tmp_workspace: WorkspaceFixture) -> None:
    pdf = _copy_pdf(tmp_workspace)
    _make_llm(tmp_workspace)
    result = ingest_file(pdf, tmp_workspace.db_path, tmp_workspace.workspace,
                         tmp_workspace.llm, "fake")
    assert result.status == "ingested"
    assert (tmp_workspace.workspace / "wiki" / "summaries" / "snow-white-and-the-seven-dwarfs.md").exists()
    assert not (tmp_workspace.workspace / "wiki" / "snow-white-and-the-seven-dwarfs.md").exists()


def test_ingest_summary_db_row_has_correct_path(tmp_workspace: WorkspaceFixture) -> None:
    pdf = _copy_pdf(tmp_workspace)
    _make_llm(tmp_workspace)
    ingest_file(pdf, tmp_workspace.db_path, tmp_workspace.workspace,
                tmp_workspace.llm, "fake")
    with get_connection(tmp_workspace.db_path) as conn:
        row = conn.execute(
            "SELECT path, relative_path, source_kind FROM documents "
            "WHERE relative_path = 'wiki/summaries/snow-white-and-the-seven-dwarfs.md'"
        ).fetchone()
    assert row is not None
    assert row["path"] == "/wiki/summaries/"
    assert row["source_kind"] == "wiki"


# ── 2.2: log.md ──────────────────────────────────────────────────────────────

def test_ingest_appends_to_log(tmp_workspace: WorkspaceFixture) -> None:
    pdf = _copy_pdf(tmp_workspace)
    _make_llm(tmp_workspace)
    ingest_file(pdf, tmp_workspace.db_path, tmp_workspace.workspace,
                tmp_workspace.llm, "fake")
    log = (tmp_workspace.workspace / "wiki" / "log.md").read_text()
    assert "Snow White and the Seven Dwarfs.pdf" in log
    assert "Ingested" in log


def test_ingest_twice_log_has_two_entries(tmp_workspace: WorkspaceFixture) -> None:
    pdf1 = _copy_pdf(tmp_workspace)
    _make_llm(tmp_workspace)
    ingest_file(pdf1, tmp_workspace.db_path, tmp_workspace.workspace,
                tmp_workspace.llm, "fake")

    pdf2_src = _FIXTURES / "pdfs" / "Cinderella.pdf"
    pdf2 = tmp_workspace.workspace / "sources" / pdf2_src.name
    shutil.copy(pdf2_src, pdf2)
    tmp_workspace.llm._call_index = 0  # reset sequence for second ingest
    ingest_file(pdf2, tmp_workspace.db_path, tmp_workspace.workspace,
                tmp_workspace.llm, "fake")

    log = (tmp_workspace.workspace / "wiki" / "log.md").read_text()
    assert log.count("## [") >= 2


# ── 2.4: overview.md updated ─────────────────────────────────────────────────

def test_ingest_updates_overview(tmp_workspace: WorkspaceFixture) -> None:
    pdf = _copy_pdf(tmp_workspace)
    _make_llm(tmp_workspace)
    ingest_file(pdf, tmp_workspace.db_path, tmp_workspace.workspace,
                tmp_workspace.llm, "fake")
    overview = (tmp_workspace.workspace / "wiki" / "overview.md").read_text()
    assert "Knowledge Base Overview" in overview
    assert overview != "# Knowledge Base Overview\n\n_No documents ingested yet._\n"


# ── 2.6: concept pages created ───────────────────────────────────────────────

def test_ingest_creates_concept_pages(tmp_workspace: WorkspaceFixture) -> None:
    pdf = _copy_pdf(tmp_workspace)
    _make_llm(tmp_workspace)
    ingest_file(pdf, tmp_workspace.db_path, tmp_workspace.workspace,
                tmp_workspace.llm, "fake")
    assert (tmp_workspace.workspace / "wiki" / "concepts" / "snow-white.md").exists()
    assert (tmp_workspace.workspace / "wiki" / "concepts" / "evil-queen.md").exists()


def test_ingest_concept_pages_in_db(tmp_workspace: WorkspaceFixture) -> None:
    pdf = _copy_pdf(tmp_workspace)
    _make_llm(tmp_workspace)
    ingest_file(pdf, tmp_workspace.db_path, tmp_workspace.workspace,
                tmp_workspace.llm, "fake")
    with get_connection(tmp_workspace.db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM documents "
            "WHERE path='/wiki/concepts/' AND source_kind='wiki'"
        ).fetchone()[0]
    assert count == 2


def test_ingest_concept_pages_searchable(tmp_workspace: WorkspaceFixture) -> None:
    pdf = _copy_pdf(tmp_workspace)
    _make_llm(tmp_workspace)
    ingest_file(pdf, tmp_workspace.db_path, tmp_workspace.workspace,
                tmp_workspace.llm, "fake")
    results = search_chunks(tmp_workspace.db_path, "protagonist", scope="wiki")
    assert results


# ── 2.3: index.md updated ────────────────────────────────────────────────────

def test_ingest_updates_index(tmp_workspace: WorkspaceFixture) -> None:
    pdf = _copy_pdf(tmp_workspace)
    _make_llm(tmp_workspace)
    ingest_file(pdf, tmp_workspace.db_path, tmp_workspace.workspace,
                tmp_workspace.llm, "fake")
    index = (tmp_workspace.workspace / "wiki" / "index.md").read_text()
    assert "snow-white-and-the-seven-dwarfs" in index.lower()
    assert "## Summaries" in index
    assert "## Concepts" in index


# ── 2.7: git commit created ───────────────────────────────────────────────────

def test_ingest_creates_git_commit(tmp_workspace: WorkspaceFixture) -> None:
    import subprocess
    pdf = _copy_pdf(tmp_workspace)
    _make_llm(tmp_workspace)
    ingest_file(pdf, tmp_workspace.db_path, tmp_workspace.workspace,
                tmp_workspace.llm, "fake")
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=tmp_workspace.workspace,
        capture_output=True, text=True,
    ).stdout
    assert "Snow White and the Seven Dwarfs.pdf" in log


# ── 2.8: skip re-ingest ───────────────────────────────────────────────────────

def test_ingest_skips_unchanged_file(tmp_workspace: WorkspaceFixture) -> None:
    pdf = _copy_pdf(tmp_workspace)
    _make_llm(tmp_workspace)
    ingest_file(pdf, tmp_workspace.db_path, tmp_workspace.workspace,
                tmp_workspace.llm, "fake")
    tmp_workspace.llm._call_index = 0
    result = ingest_file(pdf, tmp_workspace.db_path, tmp_workspace.workspace,
                         tmp_workspace.llm, "fake")
    assert result.status == "skipped"


# ── M3: partial-failure rollback ──────────────────────────────────────────────

class _FailingLLM(FakeLLMClient):
    """FakeLLMClient that raises on the Nth call (1-based) to simulate a
    mid-ingest LLM failure (e.g. a timeout while building the 2nd concept)."""

    def __init__(self, responses: list[str], fail_on_call: int) -> None:
        super().__init__(responses=responses)
        self._fail_on_call = fail_on_call

    def next_response(self) -> str:
        if self._call_index + 1 == self._fail_on_call:
            raise RuntimeError("simulated LLM failure")
        return super().next_response()


def test_ingest_rolls_back_created_pages_on_failure(tmp_workspace: WorkspaceFixture) -> None:
    """A failure partway through concept generation must delete the concept
    pages already created in this run (and their index entries)."""
    pdf = _copy_pdf(tmp_workspace)
    # call 1 = extract, call 2 = concept "Snow White", call 3 = concept "Evil Queen".
    # Fail on call 3 so Snow White is created, then the run fails.
    llm = _FailingLLM(
        responses=[_EXTRACTION_JSON, _CONCEPT_PAGE_1, _CONCEPT_PAGE_2, _OVERVIEW],
        fail_on_call=3,
    )
    result = ingest_file(pdf, tmp_workspace.db_path, tmp_workspace.workspace, llm, "fake")

    assert result.status == "failed"
    slug = make_wiki_slug("Snow White")
    page = tmp_workspace.workspace / "wiki" / "concepts" / f"{slug}.md"
    assert not page.exists()  # created-then-rolled-back
    with get_connection(tmp_workspace.db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM documents"
            " WHERE source_kind='wiki' AND path='/wiki/concepts/'"
        ).fetchone()[0]
    assert count == 0
    index_text = (tmp_workspace.workspace / "wiki" / "index.md").read_text(encoding="utf-8")
    assert f"{slug}.md" not in index_text  # dangling index entry removed


def test_ingest_restores_overwritten_page_on_failure(tmp_workspace: WorkspaceFixture) -> None:
    """A failure after overwriting a pre-existing concept must restore the prior
    content, not leave the half-merged version behind."""
    pdf = _copy_pdf(tmp_workspace)
    slug = make_wiki_slug("Snow White")
    original = "# Snow White\n\nORIGINAL CONTENT FROM A PRIOR SOURCE.\n"
    create_page(
        tmp_workspace.db_path, tmp_workspace.workspace,
        "/wiki/concepts/", slug, "Snow White", original, ["entity"],
    )

    # Snow White (call 2) overwrites the existing page, then Evil Queen (call 3) fails.
    llm = _FailingLLM(
        responses=[_EXTRACTION_JSON, _CONCEPT_PAGE_1, _CONCEPT_PAGE_2, _OVERVIEW],
        fail_on_call=3,
    )
    result = ingest_file(pdf, tmp_workspace.db_path, tmp_workspace.workspace, llm, "fake")

    assert result.status == "failed"
    page = tmp_workspace.workspace / "wiki" / "concepts" / f"{slug}.md"
    assert page.exists()  # pre-existing page survives
    assert page.read_text(encoding="utf-8") == original  # restored to prior content
    with get_connection(tmp_workspace.db_path) as conn:
        row = conn.execute(
            "SELECT content FROM documents WHERE relative_path=?",
            (f"wiki/concepts/{slug}.md",),
        ).fetchone()
    assert row["content"] == original  # DB row restored too
