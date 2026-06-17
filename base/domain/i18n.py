"""Locale registry for per-wiki content language (ISO 639-1).

Adding a language = add one Locale to _LOCALES. No other structural change.
Everything that emits language-specific text (ingestion templates, page
builders, the index/overview scaffold, the chat directive) reads from here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "en"


@dataclass(frozen=True)
class Locale:
    """All locale-specific strings and directives for a single content language."""

    code: str  # "es"
    name_native: str  # "Español"

    # Directive appended to ingestion system prompts (LLM output language).
    # Empty string for English (no-op — preserves current English behavior).
    content_directive: str

    # Directive appended to the chat system prompt (answer language).
    # Empty for English.
    chat_directive: str

    # Section headers (WITHOUT the leading "## ").
    h_summary: str
    h_key_topics: str
    h_key_entities: str
    h_important_data: str
    h_source_information: str
    h_definition: str
    h_key_characteristics: str
    h_context: str
    h_sources: str
    h_related_concepts: str
    h_see_also: str

    # Inline field labels (WITHOUT trailing ":").
    lbl_source: str
    lbl_type: str
    lbl_pages: str
    lbl_ingested: str
    lbl_parser: str
    lbl_file: str

    # index.md / overview.md scaffold.
    index_title: str  # "Wiki Index" body, no "# "
    index_section_summaries: str  # "Summaries"   (used as "## Summaries")
    index_section_concepts: str  # "Concepts"
    overview_title: str  # "Knowledge Base Overview", no "# "
    overview_empty: str  # placeholder line, including the surrounding _italics_

    # Default chat suggested prompts (used only when the user supplies none).
    suggested_prompts: tuple[str, ...]


_EN = Locale(
    code="en",
    name_native="English",
    content_directive="",  # no-op: English is the model default here
    chat_directive="",
    h_summary="Summary",
    h_key_topics="Key Topics",
    h_key_entities="Key Entities",
    h_important_data="Important Data & Figures",
    h_source_information="Source Information",
    h_definition="Definition",
    h_key_characteristics="Key Characteristics",
    h_context="Context",
    h_sources="Sources",
    h_related_concepts="Related Concepts",
    h_see_also="See also",
    lbl_source="Source",
    lbl_type="Type",
    lbl_pages="Pages",
    lbl_ingested="Ingested",
    lbl_parser="Parser",
    lbl_file="File",
    index_title="Wiki Index",
    index_section_summaries="Summaries",
    index_section_concepts="Concepts",
    overview_title="Knowledge Base Overview",
    overview_empty="_No documents ingested yet._",
    suggested_prompts=(
        "What topics are covered in my wiki?",
        "Summarize the main documents",
        "Which documents mention [term]?",
        "What are the key facts about [topic]?",
    ),
)

_ES = Locale(
    code="es",
    name_native="Español",
    content_directive=(
        "Redacta TODA la salida en español, con un español natural y fluido, "
        "independientemente del idioma del documento de origen. Traduce al "
        "español los títulos de sección y las etiquetas que se te indiquen."
    ),
    chat_directive=(
        "## Idioma\n"
        "Responde SIEMPRE en español, con redacción natural y fluida, sin "
        "importar el idioma de la pregunta o de las fuentes. Mantén intactas las "
        "citas (rutas de página y nombres de archivo) tal como las devuelven las "
        "herramientas."
    ),
    h_summary="Resumen",
    h_key_topics="Temas clave",
    h_key_entities="Entidades clave",
    h_important_data="Datos y cifras importantes",
    h_source_information="Información de la fuente",
    h_definition="Definición",
    h_key_characteristics="Características clave",
    h_context="Contexto",
    h_sources="Fuentes",
    h_related_concepts="Conceptos relacionados",
    h_see_also="Véase también",
    lbl_source="Fuente",
    lbl_type="Tipo",
    lbl_pages="Páginas",
    lbl_ingested="Ingerido",
    lbl_parser="Analizador",
    lbl_file="Archivo",
    index_title="Índice del wiki",
    index_section_summaries="Resúmenes",
    index_section_concepts="Conceptos",
    overview_title="Resumen general de la base de conocimiento",
    overview_empty="_Aún no se ingirió ningún documento._",
    suggested_prompts=(
        "¿Qué temas cubre mi wiki?",
        "Resume los documentos principales",
        "¿Qué documentos mencionan [término]?",
        "¿Cuáles son los datos clave sobre [tema]?",
    ),
)

_LOCALES: dict[str, Locale] = {"en": _EN, "es": _ES}

SUPPORTED_LANGUAGES: tuple[str, ...] = tuple(_LOCALES.keys())


def normalize_language(language: str | None) -> str:
    """Return a supported base code, or DEFAULT_LANGUAGE (warn on unsupported).

    Normalizes region variants and case: "es-AR", "es_ES", "ES" → "es".
    Unsupported but stringy values produce a logged warning before falling back.
    """
    if not isinstance(language, str) or not language.strip():
        return DEFAULT_LANGUAGE
    base = language.strip().lower().replace("_", "-").split("-", 1)[0]
    if base in _LOCALES:
        return base
    logger.warning(
        "Unsupported wiki language %r; falling back to %r. Supported: %s",
        language,
        DEFAULT_LANGUAGE,
        ", ".join(SUPPORTED_LANGUAGES),
    )
    return DEFAULT_LANGUAGE


def get_locale(language: str | None) -> Locale:
    """Resolve (normalize + validate + fallback) to a Locale.

    Always returns a valid Locale — falls back to English on any bad input.
    """
    return _LOCALES[normalize_language(language)]


def with_content_directive(base_prompt: str, language: str) -> str:
    """Append the content-language directive to an ingestion system prompt.

    Returns the prompt unchanged for English (empty directive → no-op).
    """
    directive = get_locale(language).content_directive
    return f"{base_prompt}\n\n{directive}" if directive else base_prompt


def apply_chat_directive(system_prompt: str, language: str) -> str:
    """Append the answer-language directive to the chat system prompt.

    Returns the prompt unchanged for English (empty directive → no-op).
    """
    directive = get_locale(language).chat_directive
    return f"{system_prompt}\n\n{directive}" if directive else system_prompt
