# Design: Per-wiki multilingual content (en/es, extensible)

> **Status: IMPLEMENTED** on branch `feat/multilingual-content`. The user-facing
> summary now lives in `docs/programmer_manual.md` §8; this document is retained as
> the design record. Two write paths not in the original §7 were found and
> localized during implementation: the **lint/repair pass**
> (`repair_stale` / `repair_missing_concept` / `repair_missing_xref`, threaded via
> `repair_wiki(language=…)`) and the **chat→wiki save path** (`save_to_wiki(language=…)`).
> Known v1 limitations (English-only by design): the marimo app UI, the `log.md`
> ingest log, the lint/repair *diagnostic* annotations (contradiction / data-gap /
> gap-filled), and the legacy `regenerate_wiki_pages` → `build_wiki_page` path.
> FTS5 tokenizer unchanged (see §8).

## 1. Goal

Let each wiki declare its **content language** in `wiki_config.toml`. The same
person can keep an English wiki and a Spanish wiki side by side — language is a
per-wiki property, not a global setting. When a wiki's language is `es`:

- All LLM-generated prose (summaries, concept pages, overview, chat answers) is
  written in Spanish, **regardless of the source document language** (the LLM
  translates/redacts into the wiki language).
- All structural section headers and field labels in generated pages
  (`## Resumen`, `## Fuentes`, `**Fuente:**`, the `index.md` sections, the
  `overview.md` scaffold) are in Spanish.

## 2. Scope & non-goals

**In scope (v1):**
- Content generation language (ingestion pipeline).
- Chat answer language + localized default suggested prompts.
- Localized structural headers/labels in generated wiki files.
- Two languages implemented and tested: `en` (default) and `es`.
- **Extensible architecture**: adding a third language = adding one `Locale`
  entry, no structural code changes.

**Out of scope (explicit future enhancements):**
- **App UI chrome** (marimo button text, panel titles, status messages) stays in
  English. No UI i18n in v1.
- FTS5 tokenizer changes (see §8 — current behavior is acceptable for Spanish).
- Eval rubric/grader prompts (`base/domain/eval/`) stay English.
- Re-translating an **already-populated** wiki after switching its language
  (see §11 — set the language *before* first ingest).

## 3. Decisions (locked)

| # | Decision |
|---|----------|
| 1 | Language affects **content + chat only**, not the app UI. |
| 2 | Structural headers/labels are **translated** (a Spanish wiki reads 100% Spanish). |
| 3 | The wiki language **governs output**, independent of source-document language. |
| 4 | Chat **answers in the wiki language** by default (not mirroring the question). |
| 5 | **Extensible** (ISO 639-1 code + locale table); `en`/`es` implemented in v1. |
| 6 | Config lives in a new **`[wiki]`** section; default `"en"` (backward compatible). |

## 4. Config contract

`wiki_config.toml` (at the workspace root, i.e. `WIKI_PATH`) gains an optional
top-level section:

```toml
[wiki]
# ISO 639-1 language code for THIS wiki's generated content and chat answers.
# Supported in v1: "en" (default), "es". Unknown/absent → "en".
language = "es"
```

- The existing `[assistant]` section is unchanged.
- Absent `[wiki]`, absent `language`, or unsupported value → `"en"` (fail-soft,
  with a logged warning for unsupported values — never crash ingestion over a
  config typo).
- Region variants normalize to the base code: `"es-AR"`, `"es_ES"`, `"ES"` → `"es"`.

Update `wiki_config.example.toml`: add the `[wiki]` block at the top with the
comment above, before `[assistant]`.

## 5. New module — `base/domain/i18n.py`

Single source of truth for every localized string and the resolution logic.

```python
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
    code: str                 # "es"
    name_native: str          # "Español"

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
    index_title: str               # "# Wiki Index" body, no "# "
    index_section_summaries: str   # "Summaries"   (used as "## Summaries")
    index_section_concepts: str    # "Concepts"
    overview_title: str            # "Knowledge Base Overview", no "# "
    overview_empty: str            # placeholder line, including the surrounding _italics_

    # Default chat suggested prompts (used only when the user supplies none).
    suggested_prompts: tuple[str, ...]


_EN = Locale(
    code="en",
    name_native="English",
    content_directive="",   # no-op: English is the model default here
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
    """Return a supported base code, or DEFAULT_LANGUAGE (warn on unsupported)."""
    if not isinstance(language, str) or not language.strip():
        return DEFAULT_LANGUAGE
    base = language.strip().lower().replace("_", "-").split("-", 1)[0]
    if base in _LOCALES:
        return base
    logger.warning(
        "Unsupported wiki language %r; falling back to %r. Supported: %s",
        language, DEFAULT_LANGUAGE, ", ".join(SUPPORTED_LANGUAGES),
    )
    return DEFAULT_LANGUAGE


def get_locale(language: str | None) -> Locale:
    """Resolve (normalize + validate + fallback) to a Locale."""
    return _LOCALES[normalize_language(language)]


def with_content_directive(base_prompt: str, language: str) -> str:
    """Append the content-language directive to an ingestion system prompt."""
    directive = get_locale(language).content_directive
    return f"{base_prompt}\n\n{directive}" if directive else base_prompt


def apply_chat_directive(system_prompt: str, language: str) -> str:
    """Append the answer-language directive to the chat system prompt."""
    directive = get_locale(language).chat_directive
    return f"{system_prompt}\n\n{directive}" if directive else system_prompt
```

> **Why English directives are empty strings:** the existing prompts already
> produce English. Empty `en` directives mean the `en` code path is byte-for-byte
> identical to today — the cleanest possible backward-compat guarantee and the
> reason existing tests / the golden corpus stay green.

## 6. New module — `base/domain/wiki_settings.py`

One neutral loader so neither ingestion nor chat depends on the other.

```python
"""Per-wiki settings that aren't assistant-specific (currently: content language)."""
import logging
import tomllib
from pathlib import Path

from domain.i18n import get_locale  # returns a Locale; .code is the normalized code

logger = logging.getLogger(__name__)


def load_wiki_language(wiki_path: Path) -> str:
    """Read [wiki].language from WIKI_PATH/wiki_config.toml → normalized code.

    Absent file / section / key → "en". Malformed TOML → "en" (warn).
    """
    config_file = Path(wiki_path) / "wiki_config.toml"
    if not config_file.exists():
        return get_locale(None).code
    try:
        with open(config_file, "rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        logger.warning("Could not read %s: %s; using default language", config_file, exc)
        return get_locale(None).code
    raw = data.get("wiki", {}).get("language")
    return get_locale(raw).code   # normalize + validate + fallback in one place
```

## 7. Changes per file

### 7.1 `base/domain/ingestion/wiki_generator.py`

**Pattern:** turn header text in templates into `{h_*}` placeholders filled from
the locale; append the content directive to each `*_SYSTEM` prompt via
`with_content_directive`. Add `language: str = "en"` to every public function.

- `extract_structured(doc_meta, page_contents, client, model, language="en")`
  - system = `with_content_directive(_EXTRACT_SYSTEM, language)` so
    `document_summary` and each concept `insight` come back in the wiki language.
  - JSON **keys** stay English (`document_summary`, `concepts`, `name`,
    `category`, `insight`) — structural, not user-visible. Only **values** are
    translated. `category` values (`entity|instrument|theme`) stay English too
    (used as tags/logic); they are not rendered as prose.
- `build_summary_page(doc_meta, extraction, language="en")` — **no LLM call.**
  Replace the hardcoded headers/labels with locale fields:
  - `# {title}` (unchanged — title derived from filename)
  - `**{lbl_source}:** … | **{lbl_type}:** … | **{lbl_pages}:** … | **{lbl_ingested}:** …`
  - `## {h_summary}`, `## {h_related_concepts}` (the related-concepts block),
    `## {h_source_information}` with `- **{lbl_file}:**`, `- **{lbl_type}:**`,
    `- **{lbl_pages}:**`, `- **{lbl_ingested}:**`, `- **{lbl_parser}:**`.
- `build_concept_page(concept, filename, existing_content, client, model, language="en")`
  - Build `_CONCEPT_NEW_TEMPLATE` / `_CONCEPT_UPDATE_TEMPLATE` with `{h_definition}`,
    `{h_key_characteristics}`, `{h_context}`, `{h_sources}` filled from the locale.
  - system = `with_content_directive(_CONCEPT_SYSTEM, language)`.
  - **Keep the citation-preservation clause in `_CONCEPT_SYSTEM`** — it is
    language-independent and must survive. Citations are never translated.
- `structure_chat_content(title, category, raw_content, existing_content, client, model, language="en")`
  - Same treatment for `_CHAT_CONCEPT_NEW_TEMPLATE` / `_CHAT_CONCEPT_UPDATE_TEMPLATE`.
- `update_overview(current_overview, new_summary, all_concept_names, client, model, language="en")`
  - Pure prose, no fixed headers → only append the directive to `_OVERVIEW_SYSTEM`.
- `inject_see_also(content, related_pages, language="en")`
  - Emit `## {h_see_also}` instead of `## See also`.
  - Anchor on the localized sources header: insert before `\n## {h_sources}` if
    present, else append. **This is the one place where a header change can break
    placement** — it must use the same locale as the page was generated with.
- `make_wiki_slug` — **unchanged.** It already strips diacritics (NFKD → ascii),
  so `Política Común` → `politica-comun`. Slugs stay ASCII for all languages.

> **Template mechanics:** keep one template string per prompt; fill headers with
> `.format(h_definition=loc.h_definition, …)` alongside the existing `{name}` etc.
> Do **not** create per-language template copies — the whole point is one template,
> locale-filled headers, one appended directive.

### 7.2 `base/domain/ingestion/index_manager.py`

- `update_index(workspace, page_path, one_line_summary, category, language="en")`
  - `loc = get_locale(language)`; `section = "## " + (loc.index_section_summaries
    if category == "summaries" else loc.index_section_concepts)`.
  - Seed (when index missing) uses localized title + sections:
    `f"# {loc.index_title}\n\n## {loc.index_section_summaries}\n\n## {loc.index_section_concepts}\n"`.
- `remove_index_entry(workspace, page_path, category, language="en")` — same
  localized `section` derivation. (Matching logic / `_ENTRY_RE` are
  language-agnostic — unchanged.)

### 7.3 `base/domain/ingestion/pipeline.py`

- `_init_wiki_workspace(workspace, language="en")` — seed `index.md` and
  `overview.md` with localized scaffold (`loc.index_title`,
  `loc.index_section_summaries/concepts`, `loc.overview_title`,
  `loc.overview_empty`).
- Add `language: str = "en"` to `ingest_file`, `scan_and_ingest`,
  `batch_ingest` (in `batch.py`), and `regenerate_wiki_pages`.
- Forward `language` to: `_init_wiki_workspace`, `extract_structured`,
  `build_concept_page`, `build_summary_page`, `update_overview`, and every
  `update_index(...)` / `remove_index_entry(...)` call.

### 7.4 `base/domain/chat/config.py`

- `WikiAssistantConfig` gains `language: str = "en"`.
- `load_config(wiki_path)`:
  - `language = load_wiki_language(wiki_path)` (single source; import from
    `domain.wiki_settings`).
  - If `[assistant].suggested_prompts` is **absent**, default to
    `list(get_locale(language).suggested_prompts)` (localized) instead of the
    English `_DEFAULT_PROMPTS`. A user-supplied list is always respected verbatim.
  - `system_prompt` default stays the English base `_DEFAULT_SYSTEM_PROMPT`. The
    answer-language directive is **not** applied here — it is applied once, at
    agent creation (§7.5), so there is exactly one place that does it.
  - Return `WikiAssistantConfig(system_prompt=…, suggested_prompts=…, language=language)`.

### 7.5 `base/domain/chat/agent.py`

- `create_agent(base_url, api_key, model, system_prompt=_DEFAULT_SYSTEM_PROMPT, language="en")`.
- `effective_prompt = apply_chat_directive(system_prompt, language)` (import from
  `domain.i18n`); pass `system_prompt=effective_prompt` to `Agent(...)`.
- This works for both the default prompt and a user's custom prompt — the Spanish
  directive reinforces the language even if the custom prompt is English.

### 7.6 `marimo/ingest_app.py`

- Resolve `language = load_wiki_language(Path(wiki_path))` in the setup/config
  cell (next to where the pipeline client/model are built).
- Pass `language=language` into the pipeline entry call(s) (`scan_and_ingest` /
  `ingest_file` / `batch_ingest`).
- **No UI string changes** (out of scope).

### 7.7 `marimo/read_app.py`

- `cfg = load_config(wiki_path)` already gives `cfg.language` and localized
  `cfg.suggested_prompts`.
- `create_agent(..., system_prompt=cfg.system_prompt, language=cfg.language)`.
- Suggested-prompt buttons already render from `cfg.suggested_prompts` — no extra
  change beyond the config returning localized defaults.

### 7.8 `wiki_config.example.toml`

- Add the `[wiki] language` block (see §4) at the top, with the supported-values
  comment.

## 8. FTS5 — no change in v1 (rationale)

`database/sqlite_schema.sql` defines `chunks_fts` with `tokenize='porter unicode61'`.
This is **acceptable for Spanish** as-is:

- `unicode61` folds diacritics by default, so `política` ≈ `politica` —
  accent-insensitive search already works.
- `porter` is an English stemmer, but on Spanish text it is mostly inert
  (it strips English suffixes that rarely occur in Spanish; trailing-`s`
  stripping even helps Spanish plurals incidentally). No correctness regression.

**Future refinement (not v1):** since each wiki is its own `index.db`, the
tokenizer could be chosen per-wiki at schema-creation time — `unicode61
remove_diacritics 2` (dropping `porter`) for non-English wikis. This requires
templating the FTS `CREATE VIRTUAL TABLE` statement in `db.open_db` based on the
resolved language and is deferred to keep v1 scope tight and the golden corpus
(English, `porter unicode61`) untouched.

## 9. Validation matrix

| `[wiki].language` value | Resolved code | Notes |
|---|---|---|
| absent file / absent `[wiki]` / absent key | `en` | silent (expected) |
| `"en"` / `"es"` | `en` / `es` | exact |
| `"ES"`, `"es-AR"`, `"es_ES"` | `es` | normalized (case + region stripped) |
| `"fr"`, `"xx"`, `"de"` | `en` | **warn** (unsupported) |
| `""`, `"   "`, non-string, malformed TOML | `en` | warn only for malformed TOML |

All resolution funnels through `i18n.normalize_language` / `get_locale`, so the
matrix is enforced in one place and tested once.

## 10. Test plan (pytest, deterministic)

Reuse the existing **fake-LLM client** pattern from `tests/unit/` (a stub whose
`chat.completions.create` records the `messages` it was called with and returns a
canned response) so prompt-construction is asserted without a real model.

**`tests/unit/test_i18n.py`**
- `get_locale("es")` → `code == "es"`, headers non-empty and ≠ English.
- `get_locale("en")` → `content_directive == ""` and `chat_directive == ""`.
- `normalize_language` table: `"ES"`,`"es-AR"`,`"es_ES"` → `"es"`; `"fr"`,`"xx"`,
  `""`, `None`, `123` → `"en"` (assert a warning is logged via `caplog` for the
  unsupported-but-stringy cases).
- `SUPPORTED_LANGUAGES == ("en","es")`.
- `with_content_directive(p,"en") == p`; `with_content_directive(p,"es")` ends
  with the Spanish directive. Same shape for `apply_chat_directive`.

**`tests/unit/test_wiki_settings.py`**
- `[wiki].language="es"` → `"es"`; absent file / section / key → `"en"`;
  `"fr"` → `"en"` (+warning); malformed TOML → `"en"` (+warning).

**`tests/unit/test_wiki_generator_i18n.py`**
- `build_summary_page(meta, extraction, language="es")` contains `## Resumen`,
  `## Información de la fuente`, `**Fuente:**`; does **not** contain `## Summary`.
- `build_summary_page(meta, extraction)` (default) still contains `## Summary`,
  `**Source:**` — **regression guard for the English default.**
- `inject_see_also(es_content, related, language="es")` emits `## Véase también`
  and inserts before `## Fuentes` when present.
- `inject_see_also(en_content, related)` still emits `## See also` before
  `## Sources`.
- Fake-client assertions: `extract_structured(..., language="es")`,
  `build_concept_page(..., language="es")`, `update_overview(..., language="es")`,
  `structure_chat_content(..., language="es")` each send a **system message that
  ends with the Spanish content directive**, and the concept templates contain
  `## Definición` / `## Características clave` / `## Contexto` / `## Fuentes`.
  The `en` variants send **no** directive (system message unchanged).

**`tests/unit/test_index_manager_i18n.py`** (or extend existing index test)
- `update_index(ws, "summaries/x.md", "…", "summaries", language="es")` seeds/uses
  `## Resúmenes`; English default still uses `## Summaries`.

**`tests/unit/test_chat_agent_i18n.py`**
- `apply_chat_directive(base, "es")` includes the Spanish directive;
  `(base, "en")` returns `base` unchanged. (Unit-test the pure helper; optionally
  assert `create_agent(..., language="es")` builds an Agent whose system prompt
  contains the directive.)

**`tests/unit/test_chat_config.py`** (extend)
- es wiki, no `[assistant].suggested_prompts` → Spanish default prompts;
  `cfg.language == "es"`.
- Custom `suggested_prompts` respected regardless of language.
- `[wiki]` absent → `cfg.language == "en"`, English default prompts.

**Optional integration (`@pytest.mark.integration`, fake LLM)**
- Drive `ingest_file(..., language="es")` end-to-end with the fake client; assert
  the written `summaries/*.md` has Spanish headers, `index.md` has `## Resúmenes`,
  and `overview.md` seed is Spanish.

**Must stay green (no edits):** the golden-corpus regression and all existing
English unit tests — guaranteed by the empty-string `en` directives and `en`
defaults.

## 11. Backward compatibility & migration

- **Existing wikis** (no `[wiki]`): resolve to `en`; behavior byte-identical to
  today. Golden corpus unaffected.
- **Shipped sample wiki**: stays English.
- **Switching an existing, populated wiki to `es`**: only **newly generated**
  pages follow the new language; already-written pages keep their language, and
  the already-seeded `index.md`/`overview.md` keep their original headers (a new
  language's `update_index` would append a fresh localized section rather than
  reuse the old one). **Recommendation: set `[wiki].language` before the first
  ingest.** A full "retranslate existing wiki" flow is out of scope for v1;
  `regenerate_wiki_pages(language="es")` re-localizes summaries/concepts prose but
  not pre-existing index section headers — document this limitation.

## 12. Implementation order (phased)

1. **i18n + settings (pure):** `i18n.py`, `wiki_settings.py` + their tests. No
   downstream wiring yet. Lint/type-check green.
2. **Generator localization:** `wiki_generator.py` (templates, `build_summary_page`,
   `inject_see_also`) + `test_wiki_generator_i18n.py`.
3. **Index + pipeline threading:** `index_manager.py`, `pipeline.py`
   (`_init_wiki_workspace` + the four entry points), `batch.py` + tests.
4. **Chat:** `chat/config.py`, `agent.py` + `test_chat_config.py`,
   `test_chat_agent_i18n.py`.
5. **App wiring + example config:** `ingest_app.py`, `read_app.py`,
   `wiki_config.example.toml`. Manual smoke: a fresh `es` wiki, ingest one PDF,
   confirm Spanish pages + Spanish chat answer with citations intact.
6. **Docs:** note the feature in `docs/programmer_manual.md`, add the FTS §8 note
   to `docs/sqlite_data_dictionary.md`, and (if relevant) a line in the README
   alignment matrix. Remove the PROPOSED banner from this file or delete it.

## 13. Acceptance criteria

- [ ] `[wiki].language = "es"` produces summaries, concept pages, overview, and
      chat answers in Spanish, with Spanish section headers/labels and a Spanish
      `index.md`/`overview.md`.
- [ ] Source language is irrelevant: an English PDF in an `es` wiki yields Spanish
      output.
- [ ] Chat answers in Spanish; citations (page paths, filenames) are preserved
      verbatim, never translated.
- [ ] Unsupported/absent language → English, with a warning for unsupported
      values; ingestion never crashes on a bad language value.
- [ ] All existing English tests and the golden-corpus regression pass unchanged.
- [ ] Adding a hypothetical third language needs only a new `Locale` entry (no
      changes to pipeline/agent/index signatures) — verify by code inspection.
- [ ] `ruff`/`black`/type-check clean; new code carries type annotations and
      docstrings consistent with the surrounding modules.
```
