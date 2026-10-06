"""regenerate_wiki_pages follows the wiki language (no model is called)."""

import json
import shutil
from pathlib import Path

from domain.i18n import get_locale
from domain.ingestion.pipeline import ingest_file, regenerate_wiki_pages
from tests.helpers.fake_llm import FakeLLMClient
from tests.helpers.workspace import WorkspaceFixture

_PDF = Path(__file__).parent.parent / "fixtures" / "pdfs" / "Snow White and the Seven Dwarfs.pdf"
_SUMMARY = "wiki/summaries/snow-white-and-the-seven-dwarfs.md"

_CONCEPTS = [{"name": "Snow White", "category": "entity", "insight": "La princesa"}]
_INGEST_EXTRACTION = json.dumps({"document_summary": "Un cuento.", "concepts": _CONCEPTS})
# The regeneration extracts a second concept that has no page.
_EXTRACTION = json.dumps({
    "document_summary": "Un cuento clásico.",
    "concepts": _CONCEPTS + [
        {"name": "Ghost Concept", "category": "theme", "insight": "No tiene página"},
    ],
})


def _ingest(ws: WorkspaceFixture, language: str) -> None:
    shutil.copy(_PDF, ws.workspace / "sources" / _PDF.name)
    ws.llm.responses = [
        _INGEST_EXTRACTION, "# Snow White\n\nTexto.\n\n## Fuentes\n- [^1]: x.pdf\n", "# Resumen\n",
    ]
    result = ingest_file(ws.workspace / "sources" / _PDF.name, ws.db_path,
                         ws.workspace, ws.llm, "fake", language=language)
    assert result.status == "ingested"


def _regenerate(ws: WorkspaceFixture, language: str) -> tuple[FakeLLMClient, str]:
    client = FakeLLMClient(response_content=_EXTRACTION)
    results = regenerate_wiki_pages(ws.workspace, ws.db_path, client, "fake", language=language)
    assert [r.status for r in results] == ["ingested"]
    return client, (ws.workspace / _SUMMARY).read_text(encoding="utf-8")


def test_regenerated_summary_of_a_spanish_wiki_is_spanish(tmp_workspace: WorkspaceFixture) -> None:
    _ingest(tmp_workspace, "es")
    client, page = _regenerate(tmp_workspace, "es")
    loc = get_locale("es")
    system = client.calls[0]["messages"][0]["content"]
    assert loc.content_directive in system
    assert f"## {loc.h_summary}" in page
    assert f"## {loc.h_source_information}" in page
    assert "## Summary" not in page and "## Key Topics" not in page
    assert "Un cuento clásico." in page


def test_regenerated_summary_links_only_existing_concept_pages(
    tmp_workspace: WorkspaceFixture,
) -> None:
    _ingest(tmp_workspace, "es")
    _, page = _regenerate(tmp_workspace, "es")
    assert "../concepts/snow-white.md" in page
    assert "ghost-concept" not in page


def test_regenerated_summary_of_an_english_wiki_has_no_directive(
    tmp_workspace: WorkspaceFixture,
) -> None:
    _ingest(tmp_workspace, "en")
    client, page = _regenerate(tmp_workspace, "en")
    assert get_locale("es").content_directive not in client.calls[0]["messages"][0]["content"]
    assert "## Summary" in page
