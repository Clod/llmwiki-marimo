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
    h_figures: str

    # Inline field labels (WITHOUT trailing ":").
    lbl_source: str
    lbl_type: str
    lbl_pages: str
    lbl_page: str
    lbl_ingested: str
    lbl_parser: str
    lbl_file: str

    # Text the code writes about the images of a source (docs/design_image_handling.md, D5, D10-D12).
    # The templates take `{page}`, `{model}`, `{images}` and `{described}` through str.format.
    lbl_image_generated: str   # label of a description block
    lbl_image_model: str       # the word before the model name: "(model gpt-4o)"
    lbl_image_description: str
    lbl_image_values: str
    lbl_image_visible_text: str
    lbl_image_generated_short: str   # label of the folded block of the Figures section
    image_not_described: str   # marker of an image with no description
    image_unavailable: str     # marker of a Markdown image that cannot be kept
    image_source_notice: str   # notice of a summary page whose source is an image file
    image_counts: str          # the ingestion log line

    # index.md / overview.md scaffold.
    index_title: str  # "Wiki Index" body, no "# "
    index_section_summaries: str  # "Summaries"   (used as "## Summaries")
    index_section_concepts: str  # "Concepts"
    overview_title: str  # "Knowledge Base Overview", no "# "
    overview_empty: str  # placeholder line, including the surrounding _italics_

    # Default chat suggested prompts (used only when the user supplies none).
    suggested_prompts: tuple[str, ...]

    # Warning appended to an answer built from a raw source fragment (Tier 2).
    tier2_warning: str

    # Note appended to an answer whose prose has rounded figures (domain/verify/figures.py).
    # `%(pct)s` is the tolerance as a percentage. The first text holds when the answer has
    # a table; the second one when it has none.
    rounded_figures_note: str
    rounded_figures_note_data: str

    @property
    def content_directive(self) -> str:
        """Directive appended to ingestion system prompts (LLM output language); the file
        `prompts/content_directive.<code>.md`, empty for English (no-op)."""
        from domain.prompts import load_prompt

        return load_prompt("content_directive", self.code)

    @property
    def chat_directive(self) -> str:
        """Directive appended to the chat system prompt (answer language); the file
        `prompts/language_directive.<code>.md`, empty for English."""
        from domain.prompts import load_prompt

        return load_prompt("language_directive", self.code)


_EN = Locale(
    code="en",
    name_native="English",
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
    h_figures="Figures",
    lbl_source="Source",
    lbl_type="Type",
    lbl_pages="Pages",
    lbl_page="Page",
    lbl_ingested="Ingested",
    lbl_parser="Parser",
    lbl_file="File",
    lbl_image_generated="Generated description of the image on page {page}",
    lbl_image_model="model",
    lbl_image_description="Description",
    lbl_image_values="Values",
    lbl_image_visible_text="Visible text",
    lbl_image_generated_short="Generated description",
    image_not_described="[Image not described, page {page}]",
    image_unavailable="[Image not available]",
    image_source_notice="The text of this source is a generated description of an image",
    image_counts="{images} images, {described} described",
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
    tier2_warning=(
        "> ⚠️ This answer comes from a source document with no curated wiki "
        "page; please verify it."
    ),
    rounded_figures_note=(
        "> The figures in the text are approximations. The valid values are those of the "
        "table; a figure in the text can differ from its value in the table by up to %(pct)s%."
    ),
    rounded_figures_note_data=(
        "> The figures in the text are approximations. The valid values are those of the "
        "data consulted; a figure in the text can differ from its value in the data consulted "
        "by up to %(pct)s%."
    ),
)

_ES = Locale(
    code="es",
    name_native="Español",
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
    h_figures="Figuras",
    lbl_source="Fuente",
    lbl_type="Tipo",
    lbl_pages="Páginas",
    lbl_page="Página",
    lbl_ingested="Ingerido",
    lbl_parser="Analizador",
    lbl_file="Archivo",
    lbl_image_generated="Descripción generada de la imagen de la página {page}",
    lbl_image_model="modelo",
    lbl_image_description="Descripción",
    lbl_image_values="Valores",
    lbl_image_visible_text="Texto visible",
    lbl_image_generated_short="Descripción generada",
    image_not_described="[Imagen sin describir, página {page}]",
    image_unavailable="[Imagen no disponible]",
    image_source_notice="El texto de esta fuente es una descripción generada de una imagen",
    image_counts="{images} imágenes, {described} descritas",
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
    tier2_warning=(
        "> ⚠️ Esta respuesta proviene de un documento fuente sin página curada del "
        "wiki; verificá."
    ),
    rounded_figures_note=(
        "> Las cifras del texto son aproximaciones. Los valores válidos son los de la "
        "tabla; una cifra del texto puede diferir de su valor en la tabla hasta un %(pct)s %."
    ),
    rounded_figures_note_data=(
        "> Las cifras del texto son aproximaciones. Los valores válidos son los de los "
        "datos consultados; una cifra del texto puede diferir de su valor en los datos "
        "consultados hasta un %(pct)s %."
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
    """Append the answer-language directive and the refusal directive.

    The answer-language directive is empty for English. The refusal directive
    is appended in every language: it asks the model to open its own refusal
    with the fixed sentence the code recognises (design_refusal_messages.md,
    decision 8). A wiki's `system_prompt` replaces the default prompt entirely,
    so the instruction cannot live in the prompt text.
    """
    from domain.chat.refusal import refusal_directive

    parts = [system_prompt, get_locale(language).chat_directive, refusal_directive(language)]
    return "\n\n".join(p for p in parts if p)
