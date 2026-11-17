"""A concept the wiki already has, named otherwise by another document, updates its page.

The ingestion found the existing page by the slug of the name the extraction returned,
and the extraction did not see the concepts of the wiki: «BCRA» and «Banco Central de la
República Argentina», from two documents, became two pages (HALLAZGOS, 2026-11-17).
Each concept page now declares a `description`, and the extraction receives the list
`title — description` of the concept pages, to reuse the title of the same entity."""

import json

import domain.ingestion.pipeline as pipeline
from domain.ingestion.pipeline import ingest_file, known_concepts
from domain.ingestion.wiki_generator import ExtractedConcept, ExtractionResult, extract_structured
from domain.tools.wiki_fs import create_page, page_description
from tests.helpers.fake_llm import FakeLLMClient
from tests.helpers.workspace import WorkspaceFixture

BCRA = "Banco Central de la República Argentina, la autoridad monetaria del país."


def _concept(ws, slug, title, body, description=None):
    return create_page(ws.db_path, ws.workspace, "/wiki/concepts/", slug, title, body, ["entity"],
                       overwrite=True, description=description)


def _read(ws, slug):
    return (ws.workspace / "wiki" / "concepts" / f"{slug}.md").read_text(encoding="utf-8")


# ── the front matter ──────────────────────────────────────────────────────────
def test_a_concept_page_declares_its_description(tmp_workspace: WorkspaceFixture):
    _concept(tmp_workspace, "bcra", "BCRA", "# BCRA\n\nTexto.\n", description=BCRA)
    text = _read(tmp_workspace, "bcra")
    assert page_description(text) == BCRA
    assert text.index("title:") < text.index("description:") < text.index("tags:")


def test_a_rewrite_keeps_the_description_the_page_has(tmp_workspace: WorkspaceFixture):
    _concept(tmp_workspace, "bcra", "BCRA", "# BCRA\n\nTexto.\n", description=BCRA)
    _concept(tmp_workspace, "bcra", "BCRA", "# BCRA\n\nOtro texto.\n")
    assert page_description(_read(tmp_workspace, "bcra")) == BCRA
    _concept(tmp_workspace, "bcra", "BCRA", "# BCRA\n\nOtro más.\n", description="Otra descripción.")
    assert page_description(_read(tmp_workspace, "bcra")) == BCRA


# ── the list the extraction receives ──────────────────────────────────────────
def test_known_concepts_lists_title_and_description(tmp_workspace: WorkspaceFixture):
    _concept(tmp_workspace, "bcra", "BCRA", "# BCRA\n\nTexto.\n", description=BCRA)
    _concept(tmp_workspace, "uva", "UVA",
             "# UVA\n\n## Definition\nThe UVA is a unit of account. It follows inflation.\n\n## Context\nX.\n")
    assert known_concepts(tmp_workspace.workspace, "en") == [
        ("BCRA", BCRA), ("UVA", "The UVA is a unit of account.")]


def test_the_extraction_prompt_carries_the_known_concepts():
    llm = FakeLLMClient(response_content=json.dumps({"document_summary": "s", "concepts": []}))
    extract_structured({"filename": "a.pdf", "file_type": "pdf", "page_count": 1}, [(1, "texto")],
                       llm, "fake", language="es", known=[("BCRA", BCRA)])
    user = llm.calls[0]["messages"][1]["content"]
    assert f"- BCRA — {BCRA}" in user
    assert "usá exactamente ese título" in user


def test_without_known_concepts_the_prompt_has_no_list():
    llm = FakeLLMClient(response_content=json.dumps({"document_summary": "s", "concepts": []}))
    extract_structured({"filename": "a.pdf", "file_type": "pdf", "page_count": 1}, [(1, "texto")],
                       llm, "fake", language="es")
    assert "Conceptos que el wiki ya tiene" not in llm.calls[0]["messages"][1]["content"]


def test_the_extraction_reads_the_description_of_each_concept():
    llm = FakeLLMClient(response_content=json.dumps({"document_summary": "s", "concepts": [
        {"name": "BCRA", "category": "entity", "insight": "i", "description": BCRA}]}))
    result = extract_structured({"filename": "a.pdf", "file_type": "pdf", "page_count": 1}, [(1, "t")],
                                llm, "fake")
    assert result.concepts[0].description == BCRA


# ── the ingestion ─────────────────────────────────────────────────────────────
def test_a_second_document_naming_the_same_entity_updates_its_page(tmp_workspace: WorkspaceFixture,
                                                                   monkeypatch):
    """The model returns the title of the existing page: one page, written twice."""
    seen_known = []

    def extraction(name):
        def fake(*args, known=None, **kwargs):
            seen_known.append(list(known or []))
            return ExtractionResult("s", [ExtractedConcept(name, "entity", "i", description=BCRA)])
        return fake

    monkeypatch.setattr(pipeline, "build_concept_page",
                        lambda concept, *a, **k: f"# {concept.name}\n\n## Definición\nTexto.\n")
    src = tmp_workspace.workspace / "sources"
    for i, name in enumerate(["BCRA", "BCRA"]):
        doc = src / f"doc{i}.md"
        doc.write_text(f"# Documento {i}\n\nEl banco central fija tasas.\n", encoding="utf-8")
        monkeypatch.setattr(pipeline, "extract_structured", extraction(name))
        ingest_file(doc, tmp_workspace.db_path, tmp_workspace.workspace, tmp_workspace.llm, "fake",
                    language="es")
    assert seen_known[0] == [] and seen_known[1] == [("BCRA", BCRA)]
    assert sorted(p.name for p in (tmp_workspace.workspace / "wiki" / "concepts").glob("*.md")) == ["bcra.md"]
    assert page_description(_read(tmp_workspace, "bcra")) == BCRA
