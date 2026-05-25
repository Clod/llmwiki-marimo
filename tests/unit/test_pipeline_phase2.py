"""Phase 2 pipeline integration tests (2.1, 2.2, 2.4, 2.6, 2.8).

Uses a real PDF from tests/fixtures/pdfs/ and FakeLLMClient so no API calls are made.
"""

import json
import shutil
from pathlib import Path


from domain.ingestion.pipeline import ingest_file
from domain.tools.db import get_connection
from domain.tools.search import search_chunks
from tests.helpers.fake_llm import FakeLLMClient
from tests.helpers.workspace import WorkspaceFixture

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_PDF = _FIXTURES / "pdfs" / "Blancanieves.pdf"

_EXTRACTION_JSON = json.dumps({
    "document_summary": "Blancanieves is a classic fairy tale about a princess and an evil queen.",
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
    "## Sources\n- [^1]: Blancanieves.pdf\n"
)

_CONCEPT_PAGE_2 = (
    "# Evil Queen\n\n"
    "The Evil Queen is the antagonist who disguises herself to harm Snow White.\n\n"
    "## Sources\n- [^1]: Blancanieves.pdf\n"
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
    assert (tmp_workspace.workspace / "wiki" / "summaries" / "blancanieves.md").exists()
    assert not (tmp_workspace.workspace / "wiki" / "blancanieves.md").exists()


def test_ingest_summary_db_row_has_correct_path(tmp_workspace: WorkspaceFixture) -> None:
    pdf = _copy_pdf(tmp_workspace)
    _make_llm(tmp_workspace)
    ingest_file(pdf, tmp_workspace.db_path, tmp_workspace.workspace,
                tmp_workspace.llm, "fake")
    with get_connection(tmp_workspace.db_path) as conn:
        row = conn.execute(
            "SELECT path, relative_path, source_kind FROM documents "
            "WHERE relative_path = 'wiki/summaries/blancanieves.md'"
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
    assert "Blancanieves.pdf" in log
    assert "Ingested" in log


def test_ingest_twice_log_has_two_entries(tmp_workspace: WorkspaceFixture) -> None:
    pdf1 = _copy_pdf(tmp_workspace)
    _make_llm(tmp_workspace)
    ingest_file(pdf1, tmp_workspace.db_path, tmp_workspace.workspace,
                tmp_workspace.llm, "fake")

    pdf2_src = _FIXTURES / "pdfs" / "Cenicienta.pdf"
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
    assert "blancanieves" in index.lower()
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
    assert "Blancanieves.pdf" in log


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
