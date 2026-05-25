"""Tests for wiki_generator structured extraction — step 2.5."""

import json

from domain.ingestion.wiki_generator import (
    ExtractedConcept,
    ExtractionResult,
    build_concept_page,
    build_summary_page,
    extract_structured,
    update_overview,
)
from tests.helpers.fake_llm import FakeLLMClient

_DOC_META = {
    "filename": "qe-study.pdf",
    "file_type": "pdf",
    "page_count": 3,
    "parser": "opendataloader",
}

_PAGE_CONTENTS = [(1, "Quantitative easing is a monetary policy tool.")]

_VALID_JSON = json.dumps({
    "document_summary": "This paper analyses quantitative easing programs.",
    "concepts": [
        {"name": "Federal Reserve", "category": "entity", "insight": "Implemented QE after 2008"},
        {"name": "Quantitative Easing", "category": "instrument", "insight": "Asset purchase programme"},
    ],
})


def test_extract_structured_parses_valid_json() -> None:
    llm = FakeLLMClient(response_content=_VALID_JSON)
    result = extract_structured(_DOC_META, _PAGE_CONTENTS, llm, "fake")
    assert isinstance(result, ExtractionResult)
    assert result.document_summary == "This paper analyses quantitative easing programs."
    assert len(result.concepts) == 2
    assert result.concepts[0].name == "Federal Reserve"
    assert result.concepts[0].category == "entity"
    assert result.concepts[1].name == "Quantitative Easing"


def test_extract_structured_strips_markdown_fences() -> None:
    fenced = f"```json\n{_VALID_JSON}\n```"
    llm = FakeLLMClient(response_content=fenced)
    result = extract_structured(_DOC_META, _PAGE_CONTENTS, llm, "fake")
    assert len(result.concepts) == 2


def test_extract_structured_falls_back_on_invalid_json() -> None:
    llm = FakeLLMClient(response_content="not valid json at all")
    result = extract_structured(_DOC_META, _PAGE_CONTENTS, llm, "fake")
    assert isinstance(result, ExtractionResult)
    assert result.concepts == []
    assert result.document_summary == "not valid json at all"


def test_build_summary_page_contains_summary() -> None:
    extraction = ExtractionResult(
        document_summary="QE is a monetary policy tool.",
        concepts=[ExtractedConcept("Federal Reserve", "entity", "Central bank")],
    )
    page = build_summary_page(_DOC_META, extraction)
    assert "QE is a monetary policy tool." in page
    assert "qe-study.pdf" in page
    assert "Federal Reserve" in page


def test_build_summary_page_includes_concept_links() -> None:
    extraction = ExtractionResult(
        document_summary="Summary.",
        concepts=[
            ExtractedConcept("Federal Reserve", "entity", "insight"),
            ExtractedConcept("Inflation", "theme", "insight"),
        ],
    )
    page = build_summary_page(_DOC_META, extraction)
    assert "federal-reserve" in page
    assert "inflation" in page
    assert "## Related Concepts" in page


def test_build_summary_page_no_concepts() -> None:
    extraction = ExtractionResult(document_summary="Summary.", concepts=[])
    page = build_summary_page(_DOC_META, extraction)
    assert "Summary." in page
    assert "## Related Concepts" not in page


def test_build_concept_page_new() -> None:
    concept = ExtractedConcept("Federal Reserve", "entity", "Implemented QE after 2008")
    llm = FakeLLMClient(response_content="# Federal Reserve\n\nThe Federal Reserve...")
    page = build_concept_page(concept, "qe-study.pdf", None, llm, "fake")
    assert page.startswith("# Federal Reserve")
    assert len(llm.calls) == 1
    assert "Federal Reserve" in llm.calls[0]["messages"][1]["content"]


def test_build_concept_page_update_includes_existing() -> None:
    concept = ExtractedConcept("Federal Reserve", "entity", "New 2024 insight")
    existing = "# Federal Reserve\n\nExisting content.\n\n## Sources\n- [^1]: old.pdf\n"
    llm = FakeLLMClient(response_content="# Federal Reserve\n\nUpdated content.\n")
    build_concept_page(concept, "new.pdf", existing, llm, "fake")
    prompt = llm.calls[0]["messages"][1]["content"]
    assert "Existing content" in prompt
    assert "New 2024 insight" in prompt


def test_update_overview_calls_llm() -> None:
    llm = FakeLLMClient(response_content="# Overview\n\nUpdated narrative.\n")
    result = update_overview(
        current_overview="# Overview\n\nOld text.\n",
        new_summary="New doc about QE.",
        all_concept_names=["Federal Reserve", "Inflation"],
        client=llm,
        model="fake",
    )
    assert "Updated narrative" in result
    assert len(llm.calls) == 1
    prompt = llm.calls[0]["messages"][1]["content"]
    assert "Federal Reserve" in prompt
    assert "New doc about QE" in prompt


# ── make_wiki_slug ────────────────────────────────────────────────────────────

def test_make_wiki_slug_basic() -> None:
    from domain.ingestion.wiki_generator import make_wiki_slug
    assert make_wiki_slug("Federal Reserve") == "federal-reserve"
    assert make_wiki_slug("My Report 2024.pdf") == "my-report-2024"


def test_make_wiki_slug_diacritics() -> None:
    from domain.ingestion.wiki_generator import make_wiki_slug
    assert make_wiki_slug("Política Común") == "politica-comun"
    assert make_wiki_slug("Tasa de Interés") == "tasa-de-interes"
    assert make_wiki_slug("Año Fiscal") == "ano-fiscal"
    assert make_wiki_slug("Überblick") == "uberblick"
