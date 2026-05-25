"""Tests for batch_ingest — Phase 5.1."""

import json
import shutil
import subprocess
from pathlib import Path

from domain.ingestion.batch import batch_ingest
from tests.helpers.fake_llm import FakeLLMClient
from tests.helpers.workspace import WorkspaceFixture

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_PDF_1 = _FIXTURES / "pdfs" / "Blancanieves.pdf"
_PDF_2 = _FIXTURES / "pdfs" / "Cenicienta.pdf"

_BASE = (
    "This concept covers important narrative themes related to fairness, "
    "perseverance, and justice in classic fairy tales.\n"
)

# FakeLLM responses for a 2-file batch (1 concept each) + 1 overview at end
_EXTRACT_1 = json.dumps({
    "document_summary": "Snow White is a classic fairy tale about a princess.",
    "concepts": [{"name": "Snow White", "category": "entity",
                  "insight": "The protagonist princess of the story"}],
})
_EXTRACT_2 = json.dumps({
    "document_summary": "Cinderella is a fairy tale about perseverance and kindness.",
    "concepts": [{"name": "Cinderella", "category": "entity",
                  "insight": "A kind-hearted protagonist who overcomes adversity"}],
})
_CONCEPT_1 = f"# Snow White\n\n{_BASE}"
_CONCEPT_2 = f"# Cinderella\n\n{_BASE}"
_OVERVIEW   = "# Knowledge Base Overview\n\nThis base covers classic fairy tales.\n"


def _copy_pdfs(tmp_workspace: WorkspaceFixture) -> tuple[Path, Path]:
    p1 = tmp_workspace.workspace / "sources" / _PDF_1.name
    p2 = tmp_workspace.workspace / "sources" / _PDF_2.name
    shutil.copy(_PDF_1, p1)
    shutil.copy(_PDF_2, p2)
    return p1, p2


def _make_llm(tmp_workspace: WorkspaceFixture) -> FakeLLMClient:
    tmp_workspace.llm.responses = [
        _EXTRACT_1,   # extract_structured — file 1
        _CONCEPT_1,   # build_concept_page — Snow White
        _EXTRACT_2,   # extract_structured — file 2
        _CONCEPT_2,   # build_concept_page — Cinderella
        _OVERVIEW,    # update_overview — batch end
    ]
    return tmp_workspace.llm


# ── Core batch behaviour ──────────────────────────────────────────────────────

def test_batch_ingest_creates_both_summaries(tmp_workspace: WorkspaceFixture) -> None:
    p1, p2 = _copy_pdfs(tmp_workspace)
    _make_llm(tmp_workspace)
    results = batch_ingest([p1, p2], tmp_workspace.db_path,
                           tmp_workspace.workspace, tmp_workspace.llm, "fake")
    assert len(results) == 2
    assert all(r.status == "ingested" for r in results)
    assert (tmp_workspace.workspace / "wiki" / "summaries" / "blancanieves.md").exists()
    assert (tmp_workspace.workspace / "wiki" / "summaries" / "cenicienta.md").exists()


def test_batch_ingest_creates_concept_pages(tmp_workspace: WorkspaceFixture) -> None:
    p1, p2 = _copy_pdfs(tmp_workspace)
    _make_llm(tmp_workspace)
    batch_ingest([p1, p2], tmp_workspace.db_path,
                 tmp_workspace.workspace, tmp_workspace.llm, "fake")
    assert (tmp_workspace.workspace / "wiki" / "concepts" / "snow-white.md").exists()
    assert (tmp_workspace.workspace / "wiki" / "concepts" / "cinderella.md").exists()


def test_batch_ingest_single_overview_update(tmp_workspace: WorkspaceFixture) -> None:
    p1, p2 = _copy_pdfs(tmp_workspace)
    _make_llm(tmp_workspace)
    batch_ingest([p1, p2], tmp_workspace.db_path,
                 tmp_workspace.workspace, tmp_workspace.llm, "fake")
    overview = (tmp_workspace.workspace / "wiki" / "overview.md").read_text()
    assert "Knowledge Base Overview" in overview
    # Overview was updated exactly once (the FakeLLM has exactly 1 overview response)
    assert overview.strip() == _OVERVIEW.strip()


def test_batch_ingest_single_log_entry(tmp_workspace: WorkspaceFixture) -> None:
    p1, p2 = _copy_pdfs(tmp_workspace)
    _make_llm(tmp_workspace)
    batch_ingest([p1, p2], tmp_workspace.db_path,
                 tmp_workspace.workspace, tmp_workspace.llm, "fake")
    log = (tmp_workspace.workspace / "wiki" / "log.md").read_text()
    # Exactly ONE batch entry, not two per-file entries
    assert log.count("Batch ingested") == 1
    assert "Blancanieves" in log
    assert "Cenicienta" in log


def test_batch_ingest_overview_not_called_per_file(tmp_workspace: WorkspaceFixture) -> None:
    """Overview LLM call must happen exactly once, not once per file."""
    p1, p2 = _copy_pdfs(tmp_workspace)
    _make_llm(tmp_workspace)
    batch_ingest([p1, p2], tmp_workspace.db_path,
                 tmp_workspace.workspace, tmp_workspace.llm, "fake")
    # 5 calls total: extract×2 + concept×2 + overview×1
    assert len(tmp_workspace.llm.calls) == 5


def test_batch_ingest_single_git_commit(tmp_workspace: WorkspaceFixture) -> None:
    p1, p2 = _copy_pdfs(tmp_workspace)
    _make_llm(tmp_workspace)
    batch_ingest([p1, p2], tmp_workspace.db_path,
                 tmp_workspace.workspace, tmp_workspace.llm, "fake")
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=tmp_workspace.workspace,
        capture_output=True, text=True,
    ).stdout.strip().splitlines()
    assert len(log) == 1
    assert "batch ingest" in log[0]


def test_batch_ingest_failed_file_continues(tmp_workspace: WorkspaceFixture) -> None:
    p1, _ = _copy_pdfs(tmp_workspace)
    ghost = tmp_workspace.workspace / "sources" / "ghost.pdf"
    _make_llm(tmp_workspace)
    results = batch_ingest([p1, ghost], tmp_workspace.db_path,
                           tmp_workspace.workspace, tmp_workspace.llm, "fake")
    statuses = {r.file_path.name: r.status for r in results}
    assert statuses["Blancanieves.pdf"] == "ingested"
    assert statuses["ghost.pdf"] == "failed"


def test_batch_ingest_skip_returns_results(tmp_workspace: WorkspaceFixture) -> None:
    p1, _ = _copy_pdfs(tmp_workspace)
    _make_llm(tmp_workspace)
    # First ingest
    batch_ingest([p1], tmp_workspace.db_path,
                 tmp_workspace.workspace, tmp_workspace.llm, "fake")
    tmp_workspace.llm._call_index = 0
    # Second ingest of same file → skipped
    results = batch_ingest([p1], tmp_workspace.db_path,
                           tmp_workspace.workspace, tmp_workspace.llm, "fake")
    assert results[0].status == "skipped"


def test_batch_ingest_with_lint(tmp_workspace: WorkspaceFixture) -> None:
    p1, _ = _copy_pdfs(tmp_workspace)
    tmp_workspace.llm.responses = [
        _EXTRACT_1, _CONCEPT_1, _OVERVIEW,
    ]
    results = batch_ingest([p1], tmp_workspace.db_path,
                           tmp_workspace.workspace, tmp_workspace.llm, "fake",
                           run_lint=True)
    assert results[0].status == "ingested"
    log = (tmp_workspace.workspace / "wiki" / "log.md").read_text()
    assert "Batch ingested" in log
