"""The ingestion proposes the topics of each concept page and of the summary page.
They are added to the topics a page already declares, and the new ones are named for
a curator to review. No LLM: FakeLLMClient answers."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from domain.ingestion.pipeline import ingest_file
from domain.ingestion.wiki_generator import extract_structured
from domain.tools.wiki_fs import create_page, page_topics
from tests.helpers.fake_llm import FakeLLMClient
from tests.helpers.workspace import WorkspaceFixture

_PDF = Path(__file__).parent.parent / "fixtures" / "pdfs" / "Snow White and the Seven Dwarfs.pdf"

_EXTRACTION = json.dumps({
    "document_summary": "Snow White is a classic fairy tale about a princess and an evil queen.",
    "document_topics": ["jealousy", "poisoned apple"],
    "concepts": [
        {"name": "Snow White", "category": "entity", "insight": "The protagonist princess",
         "topics": ["princess", "beauty"]},
        {"name": "Evil Queen", "category": "entity", "insight": "The antagonist",
         "topics": ["jealousy", "magic mirror"]},
    ],
})
_PAGE_1 = "# Snow White\n\nSnow White is the protagonist of the tale.\n"
_PAGE_2 = "# Evil Queen\n\nThe Evil Queen is the antagonist of the tale.\n"
_OVERVIEW = "# Knowledge Base Overview\n\nFairy tales.\n"


def _ingest(ws: WorkspaceFixture) -> list[str]:
    """Ingest the Snow White PDF; return the progress lines."""
    pdf = ws.workspace / "sources" / _PDF.name
    shutil.copy(_PDF, pdf)
    ws.llm.responses = [_EXTRACTION, _PAGE_1, _PAGE_2, _OVERVIEW]
    lines: list[str] = []
    result = ingest_file(pdf, ws.db_path, ws.workspace, ws.llm, "fake", progress_cb=lines.append)
    assert result.status == "ingested"
    return [str(line) for line in lines]


def _topics(ws: WorkspaceFixture, page: str) -> list[str]:
    return page_topics((ws.workspace / "wiki" / f"{page}.md").read_text(encoding="utf-8"))


# ── extraction ────────────────────────────────────────────────────────────────

def test_the_extraction_reads_the_topics_of_each_concept_and_of_the_document():
    result = extract_structured({"filename": "a.pdf", "file_type": "pdf", "page_count": 1},
                                [(1, "text")], FakeLLMClient(response_content=_EXTRACTION), "fake")
    assert result.document_topics == ["jealousy", "poisoned apple"]
    assert [c.topics for c in result.concepts] == [["princess", "beauty"], ["jealousy", "magic mirror"]]


def test_an_answer_without_topics_gives_no_topics():
    raw = json.dumps({"document_summary": "S.", "concepts": [{"name": "X", "category": "theme",
                                                              "insight": "i", "topics": "not a list"}]})
    result = extract_structured({"filename": "a.pdf", "file_type": "pdf", "page_count": 1},
                                [(1, "text")], FakeLLMClient(response_content=raw), "fake")
    assert result.document_topics == [] and result.concepts[0].topics == []


# ── create_page ───────────────────────────────────────────────────────────────

def test_create_page_adds_the_proposed_topics_to_the_ones_the_page_has(tmp_workspace: WorkspaceFixture):
    ws = tmp_workspace
    path = ws.workspace / "wiki" / "concepts" / "queen.md"
    create_page(ws.db_path, ws.workspace, "/wiki/concepts/", "queen", "Queen", "# Queen\n\nText.\n", [])
    path.write_text(path.read_text().replace("title: Queen", "title: Queen\ntopics: [vanity]"))
    result = create_page(ws.db_path, ws.workspace, "/wiki/concepts/", "queen", "Queen", "# Queen\n\nNew.\n",
                         [], overwrite=True, topics=["Vanity", "jealousy", " ", "jealousy"])
    assert page_topics(path.read_text()) == ["vanity", "jealousy"]
    assert result["new_topics"] == ["jealousy"]


# ── the ingestion ─────────────────────────────────────────────────────────────

def test_the_ingestion_writes_the_topics_and_names_the_new_ones(tmp_workspace: WorkspaceFixture):
    lines = _ingest(tmp_workspace)
    assert _topics(tmp_workspace, "concepts/snow-white") == ["princess", "beauty"]
    assert _topics(tmp_workspace, "concepts/evil-queen") == ["jealousy", "magic mirror"]
    assert _topics(tmp_workspace, "summaries/snow-white-and-the-seven-dwarfs") == ["jealousy", "poisoned apple"]
    report = [line for line in lines if line.startswith("⚠️ New topics to review")]
    assert len(report) == 1
    assert "«princess» → concepts/snow-white" in report[0]
    assert "«poisoned apple» → summaries/snow-white-and-the-seven-dwarfs" in report[0]


def test_a_reingestion_keeps_the_topics_of_the_curator_and_adds_the_new_ones(tmp_workspace: WorkspaceFixture):
    ws = tmp_workspace
    create_page(ws.db_path, ws.workspace, "/wiki/concepts/", "evil-queen", "Evil Queen",
                "---\ntype: concept\ntitle: Evil Queen\ntopics: [stepmother, jealousy]\n---\n\n# Evil Queen\n\nOld.\n",
                ["entity"])
    lines = _ingest(ws)
    assert _topics(ws, "concepts/evil-queen") == ["stepmother", "jealousy", "magic mirror"]
    report = next(line for line in lines if line.startswith("⚠️ New topics to review"))
    assert "«magic mirror» → concepts/evil-queen" in report
    assert "«jealousy» → concepts/evil-queen" not in report
