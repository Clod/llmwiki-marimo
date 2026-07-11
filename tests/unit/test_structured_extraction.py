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


_JSON_WITH_ALIASES = json.dumps({
    "document_summary": "This document explains CEDEARs.",
    "concepts": [
        {
            "name": "CEDEAR",
            "category": "instrument",
            "insight": "A certificate representing a foreign share",
            "aliases": ["Certificado de Depósito Argentino", "CEDEARs"],
        },
    ],
})


def test_extract_structured_parses_aliases() -> None:
    """Phase-1 of the ingest-time vocabulary: concepts carry their alt-names."""
    llm = FakeLLMClient(response_content=_JSON_WITH_ALIASES)
    result = extract_structured(_DOC_META, _PAGE_CONTENTS, llm, "fake")
    assert result.concepts[0].name == "CEDEAR"
    assert result.concepts[0].aliases == ["Certificado de Depósito Argentino", "CEDEARs"]


def test_extract_structured_aliases_default_empty_when_absent() -> None:
    """Back-compat: JSON without an 'aliases' key yields an empty list, not an error."""
    llm = FakeLLMClient(response_content=_VALID_JSON)  # _VALID_JSON has no aliases
    result = extract_structured(_DOC_META, _PAGE_CONTENTS, llm, "fake")
    assert result.concepts[0].aliases == []
    assert result.concepts[1].aliases == []


def test_extract_structured_drops_blank_and_nonstring_aliases() -> None:
    """The parser keeps only non-empty string aliases (defensive against LLM noise)."""
    noisy = json.dumps({
        "document_summary": "s",
        "concepts": [
            {"name": "Dólar", "category": "instrument", "insight": "i",
             "aliases": ["blue", "", "  ", None, 42, "billete verde"]},
        ],
    })
    llm = FakeLLMClient(response_content=noisy)
    result = extract_structured(_DOC_META, _PAGE_CONTENTS, llm, "fake")
    assert result.concepts[0].aliases == ["blue", "billete verde"]


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


# ── inject_see_also ───────────────────────────────────────────────────────────

_RELATED_PAGES = [
    {"title": "Cinderella", "rel_path": "cinderella.md"},
    {"title": "Little Red Riding Hood", "rel_path": "little-red-riding-hood.md"},
    {"title": "Snow White", "rel_path": "snow-white.md"},
    {"title": "Sleeping Beauty", "rel_path": "../summaries/sleeping-beauty.md"},
]


def test_inject_see_also_adds_mentioned_pages() -> None:
    from domain.ingestion.wiki_generator import inject_see_also
    content = (
        "# Comparison\n\n## Definition\n"
        "Cinderella and Little Red Riding Hood are classic tales.\n\n"
        "## Sources\n- Chat synthesis\n"
    )
    result = inject_see_also(content, _RELATED_PAGES)
    assert "## See also" in result
    assert "- [Cinderella](cinderella.md)" in result
    assert "- [Little Red Riding Hood](little-red-riding-hood.md)" in result
    # Not mentioned in body → not linked
    assert "snow-white.md" not in result


def test_inject_see_also_placed_before_sources() -> None:
    from domain.ingestion.wiki_generator import inject_see_also
    content = "# X\n\nAbout Cinderella.\n\n## Sources\n- Chat synthesis\n"
    result = inject_see_also(content, _RELATED_PAGES)
    assert result.index("## See also") < result.index("## Sources")


def test_inject_see_also_skips_already_linked() -> None:
    from domain.ingestion.wiki_generator import inject_see_also
    content = (
        "# X\n\nAbout Cinderella, see [Cinderella](cinderella.md).\n\n"
        "## Sources\n- Chat synthesis\n"
    )
    result = inject_see_also(content, _RELATED_PAGES)
    # The page is already linked inline, so no See also entry is added for it
    assert "## See also" not in result


def test_inject_see_also_no_matches_returns_unchanged() -> None:
    from domain.ingestion.wiki_generator import inject_see_also
    content = "# X\n\nA page about something unrelated.\n\n## Sources\n- Chat synthesis\n"
    result = inject_see_also(content, _RELATED_PAGES)
    assert result == content


def test_inject_see_also_resolves_cross_dir_mention() -> None:
    from domain.ingestion.wiki_generator import inject_see_also
    content = "# X\n\nSleeping Beauty is a classic fairy tale.\n\n## Sources\n- Chat synthesis\n"
    result = inject_see_also(content, _RELATED_PAGES)
    assert "- [Sleeping Beauty](../summaries/sleeping-beauty.md)" in result


def test_inject_see_also_respects_word_boundaries() -> None:
    """L2: a short slug must not match inside a larger word."""
    from domain.ingestion.wiki_generator import inject_see_also
    related = [{"title": "Art", "rel_path": "art.md"}]
    # "art" only appears inside "started"/"partly" — never as a whole word.
    content = "# X\n\nThe project started and partly finished.\n\n## Sources\n- Chat synthesis\n"
    result = inject_see_also(content, related)
    assert "## See also" not in result
    assert "art.md" not in result


def test_inject_see_also_matches_whole_word() -> None:
    """L2: a whole-word mention is still matched."""
    from domain.ingestion.wiki_generator import inject_see_also
    related = [{"title": "Art", "rel_path": "art.md"}]
    content = "# X\n\nThis page is about art history.\n\n## Sources\n- Chat synthesis\n"
    result = inject_see_also(content, related)
    assert "- [Art](art.md)" in result


def test_strip_wrapping_fence_removes_outer_markdown_fence() -> None:
    """Regression (comparisson.md): a whole page wrapped in ```markdown ... ```
    must not be written verbatim, or it renders as one literal code block."""
    from domain.ingestion.wiki_generator import _strip_wrapping_fence
    wrapped = "```markdown\n---\ntags: [concept]\n---\n\n# Title\n\nBody.\n```"
    result = _strip_wrapping_fence(wrapped)
    assert not result.startswith("```")
    assert result.startswith("---")
    assert result.rstrip().endswith("Body.")


def test_strip_wrapping_fence_preserves_inner_code_block() -> None:
    """An inner fenced code block (not an outer wrapper) must be left intact."""
    from domain.ingestion.wiki_generator import _strip_wrapping_fence
    page = "# Title\n\nExample:\n\n```python\nprint('hi')\n```\n\nDone."
    assert _strip_wrapping_fence(page) == page


def test_strip_wrapping_fence_noop_on_plain_markdown() -> None:
    """Plain markdown with no wrapping fence is returned unchanged (trimmed)."""
    from domain.ingestion.wiki_generator import _strip_wrapping_fence
    page = "# Title\n\nBody."
    assert _strip_wrapping_fence(page) == page


def test_strip_wrapping_fence_tolerates_none() -> None:
    """A None completion (tool call / empty / filtered response) is treated as ''."""
    from domain.ingestion.wiki_generator import _strip_wrapping_fence
    assert _strip_wrapping_fence(None) == ""


def test_extract_structured_handles_none_content() -> None:
    """LLM returning message.content=None must not raise (graceful fallback)."""
    llm = FakeLLMClient(response_content=None)
    result = extract_structured(_DOC_META, _PAGE_CONTENTS, llm, "fake")
    assert isinstance(result, ExtractionResult)
    assert result.concepts == []
    assert result.document_summary == ""


def test_build_concept_page_handles_none_content() -> None:
    """build_concept_page must not crash when the model returns content=None."""
    concept = ExtractedConcept("Federal Reserve", "entity", "insight")
    llm = FakeLLMClient(response_content=None)
    page = build_concept_page(concept, "qe-study.pdf", None, llm, "fake")
    assert page == ""


def test_update_overview_handles_none_content() -> None:
    """update_overview must not crash when the model returns content=None."""
    llm = FakeLLMClient(response_content=None)
    result = update_overview(
        current_overview="# Overview\n\nOld text.\n",
        new_summary="New doc.",
        all_concept_names=["Federal Reserve"],
        client=llm,
        model="fake",
    )
    assert result == ""


def test_concept_structuring_prompts_preserve_citations() -> None:
    """Contract: the save-time structuring pass must NOT strip inline citations.

    Root cause of the 'references gone on save' regression — structure_chat_content
    reshapes a cited chat answer into a concept page; if its prompts don't mandate
    keeping citations, the saved page loses all traceability. Guards that mandate.
    """
    from domain.ingestion.wiki_generator import (
        _CONCEPT_SYSTEM,
        _CHAT_CONCEPT_NEW_TEMPLATE,
        _CHAT_CONCEPT_UPDATE_TEMPLATE,
    )

    system = _CONCEPT_SYSTEM.lower()
    assert "citation" in system
    assert "verbatim" in system or "exactly as written" in system

    new = _CHAT_CONCEPT_NEW_TEMPLATE.lower()
    assert "citation" in new
    # Sources must be derived from what was cited, not a hardcoded generic line.
    assert "- chat synthesis\n" not in _CHAT_CONCEPT_NEW_TEMPLATE

    update = _CHAT_CONCEPT_UPDATE_TEMPLATE.lower()
    assert "citation" in update
