"""Phase-2 i18n tests for wiki_generator.py.

Verifies that:
- build_summary_page produces localized headers/labels for es and preserves
  English ones unchanged (regression guard).
- inject_see_also uses the localized see-also header and anchors on the
  localized sources header.
- LLM-calling functions (extract_structured, build_concept_page,
  update_overview, structure_chat_content) send a system message that ends
  with the Spanish content directive when language="es", and send the
  unmodified system prompt when language="en".
- Concept-page templates sent to the LLM contain the localized section
  headers (## Definición / ## Definition etc.) for the requested language.
"""

import json

from domain.i18n import _ES
from domain.ingestion.wiki_generator import (
    ExtractedConcept,
    ExtractionResult,
    build_concept_page,
    build_summary_page,
    extract_structured,
    inject_see_also,
    structure_chat_content,
    update_overview,
    _EXTRACT_SYSTEM,
    _CONCEPT_SYSTEM,
    _OVERVIEW_SYSTEM,
)
from tests.helpers.fake_llm import FakeLLMClient

# ── Shared fixtures ────────────────────────────────────────────────────────────

_DOC_META = {
    "filename": "reporte-anual.pdf",
    "file_type": "pdf",
    "page_count": 5,
    "parser": "opendataloader",
}

_PAGE_CONTENTS = [(1, "Contenido de prueba sobre política monetaria.")]

_EXTRACTION = ExtractionResult(
    document_summary="Resumen del documento de política monetaria.",
    concepts=[
        ExtractedConcept("Banco Central", "entity", "Regulador principal"),
        ExtractedConcept("Inflación", "theme", "Indicador macroeconómico"),
    ],
)

_EXTRACTION_NO_CONCEPTS = ExtractionResult(
    document_summary="Un resumen sin conceptos.",
    concepts=[],
)

_RELATED_PAGES_ES = [
    {"title": "Cinderella", "rel_path": "cinderella.md"},
    {"title": "Snow White", "rel_path": "snow-white.md"},
]


# ── build_summary_page — Spanish ──────────────────────────────────────────────


class TestBuildSummaryPageSpanish:
    """build_summary_page(language='es') must use Spanish headers and labels."""

    def test_contains_resumen_header(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION, language="es")
        assert "## Resumen" in page

    def test_contains_informacion_fuente_header(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION, language="es")
        assert "## Información de la fuente" in page

    def test_contains_fuente_label(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION, language="es")
        assert "**Fuente:**" in page

    def test_contains_tipo_label(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION, language="es")
        assert "**Tipo:**" in page

    def test_contains_paginas_label(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION, language="es")
        assert "**Páginas:**" in page

    def test_contains_ingerido_label(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION, language="es")
        assert "**Ingerido:**" in page

    def test_contains_archivo_label(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION, language="es")
        assert "**Archivo:**" in page

    def test_contains_analizador_label(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION, language="es")
        assert "**Analizador:**" in page

    def test_contains_conceptos_relacionados_header(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION, language="es")
        assert "## Conceptos relacionados" in page

    def test_does_not_contain_english_summary_header(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION, language="es")
        assert "## Summary" not in page

    def test_does_not_contain_english_source_information_header(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION, language="es")
        assert "## Source Information" not in page

    def test_does_not_contain_english_source_label(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION, language="es")
        assert "**Source:**" not in page

    def test_filename_preserved_in_page(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION, language="es")
        assert _DOC_META["filename"] in page

    def test_document_summary_preserved(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION, language="es")
        assert _EXTRACTION.document_summary in page


# ── build_summary_page — English regression guard ─────────────────────────────


class TestBuildSummaryPageEnglishRegression:
    """build_summary_page() with no language arg (default 'en') must be unchanged."""

    def test_contains_summary_header(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION)
        assert "## Summary" in page

    def test_contains_source_label(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION)
        assert "**Source:**" in page

    def test_contains_source_information_header(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION)
        assert "## Source Information" in page

    def test_contains_related_concepts_header(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION)
        assert "## Related Concepts" in page

    def test_no_spanish_headers(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION)
        assert "## Resumen" not in page
        assert "**Fuente:**" not in page

    def test_file_label_english(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION)
        assert "**File:**" in page

    def test_parser_label_english(self) -> None:
        page = build_summary_page(_DOC_META, _EXTRACTION)
        assert "**Parser:**" in page


# ── inject_see_also — Spanish ─────────────────────────────────────────────────

_ES_CONTENT_WITH_SOURCES = (
    "# Comparación\n\n## Definición\nCinderella es un clásico cuento de hadas.\n\n## Fuentes\n- Síntesis del chat\n"
)

_ES_CONTENT_NO_SOURCES = "# Comparación\n\n## Definición\nCinderella es un clásico cuento de hadas.\n"


class TestInjectSeeAlsoSpanish:
    """inject_see_also(language='es') must emit ## Véase también and anchor on ## Fuentes."""

    def test_emits_vease_tambien_header(self) -> None:
        result = inject_see_also(_ES_CONTENT_WITH_SOURCES, _RELATED_PAGES_ES, language="es")
        assert "## Véase también" in result

    def test_inserts_before_fuentes_when_present(self) -> None:
        result = inject_see_also(_ES_CONTENT_WITH_SOURCES, _RELATED_PAGES_ES, language="es")
        assert result.index("## Véase también") < result.index("## Fuentes")

    def test_no_see_also_header_in_spanish(self) -> None:
        """The English "## See also" must NOT appear in a Spanish result."""
        result = inject_see_also(_ES_CONTENT_WITH_SOURCES, _RELATED_PAGES_ES, language="es")
        assert "## See also" not in result

    def test_appends_when_no_fuentes_section(self) -> None:
        result = inject_see_also(_ES_CONTENT_NO_SOURCES, _RELATED_PAGES_ES, language="es")
        assert "## Véase también" in result
        assert "## Fuentes" not in result  # original had no Fuentes, still none

    def test_linked_pages_included(self) -> None:
        result = inject_see_also(_ES_CONTENT_WITH_SOURCES, _RELATED_PAGES_ES, language="es")
        assert "- [Cinderella](cinderella.md)" in result

    def test_unmentioned_pages_not_linked(self) -> None:
        result = inject_see_also(_ES_CONTENT_WITH_SOURCES, _RELATED_PAGES_ES, language="es")
        # "snow white" not mentioned in the content
        assert "snow-white.md" not in result


# ── inject_see_also — English regression guard ────────────────────────────────

_EN_CONTENT_WITH_SOURCES = "# Comparison\n\nAbout Cinderella.\n\n## Sources\n- Chat synthesis\n"


class TestInjectSeeAlsoEnglishRegression:
    """inject_see_also() with default language='en' must still use ## See also and ## Sources."""

    def test_emits_see_also_header(self) -> None:
        result = inject_see_also(_EN_CONTENT_WITH_SOURCES, _RELATED_PAGES_ES)
        assert "## See also" in result

    def test_inserts_before_sources(self) -> None:
        result = inject_see_also(_EN_CONTENT_WITH_SOURCES, _RELATED_PAGES_ES)
        assert result.index("## See also") < result.index("## Sources")

    def test_no_spanish_header(self) -> None:
        result = inject_see_also(_EN_CONTENT_WITH_SOURCES, _RELATED_PAGES_ES)
        assert "## Véase también" not in result


# ── Fake-client assertions: system prompt ends with Spanish directive ──────────

_SPANISH_DIRECTIVE = _ES.content_directive


class TestExtractStructuredSystemPrompt:
    """extract_structured sends the Spanish directive in the system message for es."""

    def test_es_system_ends_with_directive(self) -> None:
        valid_json = json.dumps({"document_summary": "Resumen.", "concepts": []})
        llm = FakeLLMClient(response_content=valid_json)
        extract_structured(_DOC_META, _PAGE_CONTENTS, llm, "fake", language="es")
        assert len(llm.calls) == 1
        system_content = llm.calls[0]["messages"][0]["content"]
        assert system_content.endswith(_SPANISH_DIRECTIVE)

    def test_en_system_unchanged(self) -> None:
        valid_json = json.dumps({"document_summary": "Summary.", "concepts": []})
        llm = FakeLLMClient(response_content=valid_json)
        extract_structured(_DOC_META, _PAGE_CONTENTS, llm, "fake", language="en")
        system_content = llm.calls[0]["messages"][0]["content"]
        # English directive is empty → system prompt is the base, no suffix added
        assert system_content == _EXTRACT_SYSTEM

    def test_default_language_is_en(self) -> None:
        valid_json = json.dumps({"document_summary": "Summary.", "concepts": []})
        llm = FakeLLMClient(response_content=valid_json)
        extract_structured(_DOC_META, _PAGE_CONTENTS, llm, "fake")
        system_content = llm.calls[0]["messages"][0]["content"]
        assert system_content == _EXTRACT_SYSTEM


class TestBuildConceptPageSystemPrompt:
    """build_concept_page sends the Spanish directive for es; English path is unchanged."""

    _CONCEPT = ExtractedConcept("Banco Central", "entity", "Controla la política monetaria")

    def test_es_system_ends_with_directive(self) -> None:
        llm = FakeLLMClient(response_content="# Banco Central\n\nContenido.\n")
        build_concept_page(self._CONCEPT, "doc.pdf", None, llm, "fake", language="es")
        system_content = llm.calls[0]["messages"][0]["content"]
        assert system_content.endswith(_SPANISH_DIRECTIVE)

    def test_en_system_unchanged(self) -> None:
        llm = FakeLLMClient(response_content="# Banco Central\n\nContent.\n")
        build_concept_page(self._CONCEPT, "doc.pdf", None, llm, "fake", language="en")
        system_content = llm.calls[0]["messages"][0]["content"]
        assert system_content == _CONCEPT_SYSTEM

    def test_default_language_is_en(self) -> None:
        llm = FakeLLMClient(response_content="# Banco Central\n\nContent.\n")
        build_concept_page(self._CONCEPT, "doc.pdf", None, llm, "fake")
        system_content = llm.calls[0]["messages"][0]["content"]
        assert system_content == _CONCEPT_SYSTEM

    def test_es_concept_template_contains_definicion(self) -> None:
        llm = FakeLLMClient(response_content="# Banco Central\n\nContenido.\n")
        build_concept_page(self._CONCEPT, "doc.pdf", None, llm, "fake", language="es")
        user_content = llm.calls[0]["messages"][1]["content"]
        assert "## Definición" in user_content

    def test_es_concept_template_contains_caracteristicas_clave(self) -> None:
        llm = FakeLLMClient(response_content="# Banco Central\n\nContenido.\n")
        build_concept_page(self._CONCEPT, "doc.pdf", None, llm, "fake", language="es")
        user_content = llm.calls[0]["messages"][1]["content"]
        assert "## Características clave" in user_content

    def test_es_concept_template_contains_contexto(self) -> None:
        llm = FakeLLMClient(response_content="# Banco Central\n\nContenido.\n")
        build_concept_page(self._CONCEPT, "doc.pdf", None, llm, "fake", language="es")
        user_content = llm.calls[0]["messages"][1]["content"]
        assert "## Contexto" in user_content

    def test_es_concept_template_contains_fuentes(self) -> None:
        llm = FakeLLMClient(response_content="# Banco Central\n\nContenido.\n")
        build_concept_page(self._CONCEPT, "doc.pdf", None, llm, "fake", language="es")
        user_content = llm.calls[0]["messages"][1]["content"]
        assert "## Fuentes" in user_content

    def test_en_concept_template_contains_definition(self) -> None:
        llm = FakeLLMClient(response_content="# Banco Central\n\nContent.\n")
        build_concept_page(self._CONCEPT, "doc.pdf", None, llm, "fake", language="en")
        user_content = llm.calls[0]["messages"][1]["content"]
        assert "## Definition" in user_content

    def test_update_path_does_not_fill_locale_headers(self) -> None:
        """The update template has no {h_*} placeholders — it passes through unchanged."""
        existing = "# Banco Central\n\n## Definition\n\nExisting.\n\n## Sources\n- old.pdf\n"
        llm = FakeLLMClient(response_content="# Banco Central\n\nUpdated.\n")
        build_concept_page(self._CONCEPT, "doc.pdf", existing, llm, "fake", language="es")
        # The update path uses _CONCEPT_UPDATE_TEMPLATE which has no locale headers;
        # but the system prompt still gets the directive.
        system_content = llm.calls[0]["messages"][0]["content"]
        assert system_content.endswith(_SPANISH_DIRECTIVE)


class TestUpdateOverviewSystemPrompt:
    """update_overview sends the Spanish directive for es; English path is unchanged."""

    def test_es_system_ends_with_directive(self) -> None:
        llm = FakeLLMClient(response_content="# Resumen\n\nNueva narrativa.\n")
        update_overview(
            current_overview="# Overview\n\nOld.\n",
            new_summary="Nuevo documento.",
            all_concept_names=["Banco Central"],
            client=llm,
            model="fake",
            language="es",
        )
        system_content = llm.calls[0]["messages"][0]["content"]
        assert system_content.endswith(_SPANISH_DIRECTIVE)

    def test_en_system_unchanged(self) -> None:
        llm = FakeLLMClient(response_content="# Overview\n\nUpdated narrative.\n")
        update_overview(
            current_overview="# Overview\n\nOld.\n",
            new_summary="New document.",
            all_concept_names=["Federal Reserve"],
            client=llm,
            model="fake",
            language="en",
        )
        system_content = llm.calls[0]["messages"][0]["content"]
        assert system_content == _OVERVIEW_SYSTEM

    def test_default_language_is_en(self) -> None:
        llm = FakeLLMClient(response_content="# Overview\n\nUpdated narrative.\n")
        update_overview(
            current_overview="# Overview\n\nOld.\n",
            new_summary="New document.",
            all_concept_names=[],
            client=llm,
            model="fake",
        )
        system_content = llm.calls[0]["messages"][0]["content"]
        assert system_content == _OVERVIEW_SYSTEM


class TestStructureChatContentSystemPrompt:
    """structure_chat_content sends the Spanish directive for es; English path is unchanged."""

    def test_es_system_ends_with_directive(self) -> None:
        llm = FakeLLMClient(response_content="# Concepto\n\nContenido.\n")
        structure_chat_content(
            title="Política Monetaria",
            category="theme",
            raw_content="Texto del chat con (doc.pdf, p. 1).",
            existing_content=None,
            client=llm,
            model="fake",
            language="es",
        )
        system_content = llm.calls[0]["messages"][0]["content"]
        assert system_content.endswith(_SPANISH_DIRECTIVE)

    def test_en_system_unchanged(self) -> None:
        llm = FakeLLMClient(response_content="# Concept\n\nContent.\n")
        structure_chat_content(
            title="Monetary Policy",
            category="theme",
            raw_content="Chat text with (doc.pdf, p. 1).",
            existing_content=None,
            client=llm,
            model="fake",
            language="en",
        )
        system_content = llm.calls[0]["messages"][0]["content"]
        assert system_content == _CONCEPT_SYSTEM

    def test_default_language_is_en(self) -> None:
        llm = FakeLLMClient(response_content="# Concept\n\nContent.\n")
        structure_chat_content(
            title="Monetary Policy",
            category="theme",
            raw_content="Chat text.",
            existing_content=None,
            client=llm,
            model="fake",
        )
        system_content = llm.calls[0]["messages"][0]["content"]
        assert system_content == _CONCEPT_SYSTEM

    def test_es_chat_new_template_contains_definicion(self) -> None:
        llm = FakeLLMClient(response_content="# Concepto\n\nContenido.\n")
        structure_chat_content(
            title="Política Monetaria",
            category="theme",
            raw_content="Texto.",
            existing_content=None,
            client=llm,
            model="fake",
            language="es",
        )
        user_content = llm.calls[0]["messages"][1]["content"]
        assert "## Definición" in user_content

    def test_es_chat_new_template_contains_caracteristicas_clave(self) -> None:
        llm = FakeLLMClient(response_content="# Concepto\n\nContenido.\n")
        structure_chat_content(
            title="Política Monetaria",
            category="theme",
            raw_content="Texto.",
            existing_content=None,
            client=llm,
            model="fake",
            language="es",
        )
        user_content = llm.calls[0]["messages"][1]["content"]
        assert "## Características clave" in user_content

    def test_es_chat_new_template_contains_contexto(self) -> None:
        llm = FakeLLMClient(response_content="# Concepto\n\nContenido.\n")
        structure_chat_content(
            title="Política Monetaria",
            category="theme",
            raw_content="Texto.",
            existing_content=None,
            client=llm,
            model="fake",
            language="es",
        )
        user_content = llm.calls[0]["messages"][1]["content"]
        assert "## Contexto" in user_content

    def test_es_chat_new_template_contains_fuentes(self) -> None:
        llm = FakeLLMClient(response_content="# Concepto\n\nContenido.\n")
        structure_chat_content(
            title="Política Monetaria",
            category="theme",
            raw_content="Texto.",
            existing_content=None,
            client=llm,
            model="fake",
            language="es",
        )
        user_content = llm.calls[0]["messages"][1]["content"]
        assert "## Fuentes" in user_content

    def test_en_chat_new_template_contains_definition(self) -> None:
        llm = FakeLLMClient(response_content="# Concept\n\nContent.\n")
        structure_chat_content(
            title="Monetary Policy",
            category="theme",
            raw_content="Text.",
            existing_content=None,
            client=llm,
            model="fake",
            language="en",
        )
        user_content = llm.calls[0]["messages"][1]["content"]
        assert "## Definition" in user_content

    def test_update_path_system_gets_directive(self) -> None:
        """Update path: system prompt still gets the language directive."""
        existing = "# Política Monetaria\n\n## Definición\n\nExistente.\n"
        llm = FakeLLMClient(response_content="# Política Monetaria\n\nActualizado.\n")
        structure_chat_content(
            title="Política Monetaria",
            category="theme",
            raw_content="Nuevo texto.",
            existing_content=existing,
            client=llm,
            model="fake",
            language="es",
        )
        system_content = llm.calls[0]["messages"][0]["content"]
        assert system_content.endswith(_SPANISH_DIRECTIVE)
