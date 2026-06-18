# Per-wiki multilingual content (en/es, extensible)

## Goal

Let each wiki declare its **content language** in `wiki_config.toml` (`[wiki].language`).
Language is a per-wiki property — the same user can keep an English wiki and a
Spanish wiki side by side. When a wiki's language is `es`, all LLM-generated prose
(summaries, concept pages, overview, chat answers) **and** all structural section
headers/labels in generated files are in Spanish, regardless of the source-document
language.

> **Full executable design:** [`docs/design_multilingual_content.md`](../../../docs/design_multilingual_content.md)
> — read it first. It contains the exact `Locale` table (en/es strings), function
> signatures, per-file changes, validation matrix, test plan, and phased order.
> This PRD is the summary; the design doc is the contract.

## Locked decisions

1. Affects **content + chat only**, not the marimo app UI (UI i18n is a future enhancement).
2. Structural headers/labels are **translated** (a Spanish wiki reads 100% Spanish).
3. Wiki language **governs output**, independent of source-document language.
4. Chat **answers in the wiki language** by default (not mirroring the question).
5. **Extensible** architecture (ISO 639-1 code + `Locale` registry); `en`/`es` in v1.
6. Config in a new **`[wiki]`** section; default `"en"` (backward compatible).

## Requirements

- New module `base/domain/i18n.py`: `Locale` dataclass + `_LOCALES` (en, es),
  `normalize_language`, `get_locale`, `with_content_directive`, `apply_chat_directive`.
  English directives are **empty strings** so the `en` path is byte-identical to today.
- New module `base/domain/wiki_settings.py`: `load_wiki_language(wiki_path) -> str`.
- Thread `language: str = "en"` through `wiki_generator.py` (5 generation fns +
  `inject_see_also`), `index_manager.py` (`update_index`, `remove_index_entry`),
  `pipeline.py` (`_init_wiki_workspace` + `ingest_file`, `scan_and_ingest`,
  `regenerate_wiki_pages`), and `batch.py` (`batch_ingest`).
- `chat/config.py`: `WikiAssistantConfig.language`; localized default
  `suggested_prompts`; load language via `wiki_settings`.
- `agent.py`: `create_agent(..., language="en")` applies the chat directive once.
- App wiring: `ingest_app.py` resolves + passes language to the pipeline;
  `read_app.py` passes `cfg.language` to `create_agent`.
- `wiki_config.example.toml`: document the `[wiki] language` block.
- FTS5: **no change in v1** (rationale in design §8 — `unicode61` already folds
  diacritics; per-language tokenizer deferred).

## Acceptance Criteria

- [ ] `[wiki].language = "es"` → Spanish summaries, concept pages, overview, chat
      answers; Spanish headers/labels; Spanish `index.md`/`overview.md`.
- [ ] English source PDF in an `es` wiki yields Spanish output.
- [ ] Chat answers in Spanish; citations (page paths, filenames) preserved verbatim.
- [ ] Unsupported/absent language → English (+warning for unsupported); ingestion
      never crashes on a bad value.
- [ ] All existing English unit tests **and** the golden-corpus regression pass unchanged.
- [ ] Adding a hypothetical third language needs only a new `Locale` entry (verify
      by code inspection — no pipeline/agent/index signature changes).
- [ ] `ruff` / `black` / type-check clean; annotations + docstrings match surrounding code.

## Test Plan (deterministic, pytest)

Reuse the existing fake-LLM client pattern to assert prompt construction without a
real model. New/updated tests: `test_i18n.py`, `test_wiki_settings.py`,
`test_wiki_generator_i18n.py`, `test_index_manager_i18n.py`,
`test_chat_agent_i18n.py`, extend `test_chat_config.py`. Optional fake-LLM
integration: `ingest_file(..., language="es")` end-to-end → Spanish files.
See design §10 for exact cases.

## Implementation Order (phases)

1. `i18n.py` + `wiki_settings.py` + tests (pure; no wiring).
2. `wiki_generator.py` localization + tests.
3. `index_manager.py` + `pipeline.py`/`batch.py` threading + tests.
4. `chat/config.py` + `agent.py` + tests.
5. App wiring (`ingest_app.py`, `read_app.py`) + `wiki_config.example.toml` + manual smoke.
6. Docs (`programmer_manual.md`, FTS note in `sqlite_data_dictionary.md`); remove the
   PROPOSED banner from the design doc.

## Technical Notes

- Run lint + tests **after each phase** before moving on; keep the `en` path green
  throughout (it is the backward-compat guarantee).
- Slugs stay ASCII for all languages — `make_wiki_slug` already strips diacritics
  (do **not** change it).
- Citations are never translated, in any prompt — preserve the citation clause in
  `_CONCEPT_SYSTEM` verbatim.
- `inject_see_also` is the only header-position-sensitive spot: it must anchor on
  the **localized** `## {sources}` header matching the page's language.
