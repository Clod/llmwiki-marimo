"""Ingestion keeps the images of a PDF, links the text to them and writes the
"Figures" section of the summary page. Uses the real extractor on the Snow White
PDF (skipped without Java) and a fake model."""

import json
import shutil
from pathlib import Path

import pytest

from domain.ingestion import pipeline
from domain.ingestion.extractor import check_java
from domain.ingestion.images import IMAGE_LINK_RE, images_dir
from domain.ingestion.pipeline import ingest_file
from domain.tools.db import get_connection
from tests.helpers.workspace import WorkspaceFixture

REPO = Path(__file__).resolve().parents[2]
PDF = REPO / "examples" / "fairy-tales" / "sources" / "Snow White and the Seven Dwarfs.pdf"

_EXTRACTION = json.dumps({
    "document_summary": "A tale about a princess.",
    "concepts": [{"name": "Snow White", "category": "entity", "insight": "The princess"}],
})
_CONCEPT = "# Snow White\n\nThe princess of the tale.\n\n## Sources\n- [^1]: Snow White.pdf\n"
_OVERVIEW = "# Knowledge Base Overview\n\nTales.\n"


def ingest(ws: WorkspaceFixture, language: str) -> str:
    (ws.workspace / "wiki_config.toml").write_text("[images]\nignore = false\n", encoding="utf-8")
    ws.llm.responses = [_EXTRACTION, _CONCEPT, _OVERVIEW]
    source = ws.workspace / "sources" / PDF.name
    shutil.copy(PDF, source)
    result = ingest_file(source, ws.db_path, ws.workspace, ws.llm, "fake", language=language)
    assert result.status == "ingested", result.message
    return result.doc_id


def summary(ws: WorkspaceFixture) -> str:
    return (ws.workspace / "wiki" / "summaries" / "snow-white-and-the-seven-dwarfs.md").read_text(encoding="utf-8")


@pytest.mark.skipif(check_java() is None, reason="Java is missing: opendataloader-pdf needs it")
@pytest.mark.parametrize("language, heading", [("en", "## Figures"), ("es", "## Figuras")])
def test_ingesting_a_pdf_keeps_its_images_and_writes_the_figures_section(tmp_workspace, language, heading):
    ws = tmp_workspace
    doc_id = ingest(ws, language)

    with get_connection(ws.db_path) as conn:
        texts = [r["content"] for r in conn.execute("SELECT content FROM document_pages WHERE document_id = ?", (doc_id,))]
    links = [m for t in texts for m in IMAGE_LINK_RE.finditer(t)]
    assert len(links) == 10
    assert all(m.group("doc") == doc_id and (images_dir(ws.workspace, doc_id) / m.group("file")).is_file()
               for m in links)

    page = summary(ws)
    assert heading in page
    assert page.count("![Imagen, página") == 10
    assert page.index("A tale about a princess.") < page.index(heading) < page.index("[^1]:")


def test_a_source_without_images_has_no_figures_section(tmp_workspace, monkeypatch):
    ws = tmp_workspace
    monkeypatch.setattr(pipeline, "extract", lambda file_path, cache_dir, *args, **kwargs: ([(1, "Only text.")], "fake"))
    doc_id = ingest(ws, "es")
    assert "## Figuras" not in summary(ws)
    assert not images_dir(ws.workspace, doc_id).exists()
