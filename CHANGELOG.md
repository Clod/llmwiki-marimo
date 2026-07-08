# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(pre-1.0: **minor** = features, **patch** = fixes). English is the canonical
language for this file, matching the README policy. There is no PyPI package —
users clone and run — so versions are a communication tool, not a dependency
contract. See [`RELEASING.md`](RELEASING.md) for the process.

## [Unreleased]

### Fixed
- **Model-validation eval: the off-topic check no longer false-fails strong
  models.** The "refuses off-topic questions" check requires a retrieval tool
  call, but the production system prompt both names the wiki's subject (through its
  worked example) and explicitly permits declining obvious trivia *without*
  searching — so a capable model (e.g. `gpt-4o`) correctly refused "what is the
  capital of France?" with zero tool calls and was intermittently marked as
  failing. The off-topic check now runs under a **domain-blind, strict-search
  prompt**: with the subject hidden and the decline-without-searching shortcut
  removed, the model must retrieve before it may decline, so the tool-call
  requirement is meaningful and non-flaky. The two citation checks still run under
  the real production prompt (whose worked example is what makes citation reliable).

## [0.2.1] - 2026-07-08

### Fixed
- **Ingestion now cross-links generated pages.** A final `crosslink_wiki_pages`
  pass injects a localized "See also" section into every concept/summary page
  after all documents are ingested, so a page written early can still link to a
  concept extracted from a later document. Previously `inject_see_also` was wired
  only into the chat "Save to wiki" path, so pipeline-generated concept pages
  never linked to one another. Deterministic and idempotent; runs from both
  `scan_and_ingest` and the ingest app. Both demo wikis were regenerated so their
  concept pages now interlink.
- **OpenRouter `openai/*` models silently ran at the provider's default
  temperature.** pydantic-ai mis-profiled the OpenRouter-namespaced OpenAI models
  (e.g. `openai/gpt-4o`) as *reasoning* models and dropped the pinned
  `temperature=0`, making grounding non-deterministic (the model-validation eval
  flapped between pass and fail on the same model). The chat agent now routes
  OpenRouter endpoints through pydantic-ai's dedicated `OpenRouterProvider`, which
  resolves vendor-prefixed model profiles correctly; other OpenAI-compatible
  endpoints (OpenAI, LM Studio, Ollama) are unchanged. Ingestion was unaffected
  (it calls the raw OpenAI SDK, which passes `temperature` through directly).

## [0.2.0] - 2026-07-07

### Added
- **One-command quick-start installer (`quickstart.py`).** A stdlib-only console
  installer — the only prerequisite is Python 3.12+. It builds an isolated venv
  from a lock-pinned `requirements.txt`, drops in a pre-ingested demo wiki
  (`examples/fairy-tales/`, browsable with no LLM), runs a provider wizard
  (local Ollama / any OpenAI-compatible endpoint), writes `.env`, validates the
  configured model, and launches the read app. Interactive with sensible
  defaults; scriptable via `--demo / --provider / --yes / --no-launch / --no-eval`.
- **Advisory model-validation step in the installer.** `scripts/eval_chat_model.py`
  now runs as a non-blocking install step (skip with `--no-eval`) that validates
  **every** configured model (chat `LLM_*` + ingest `WIKI_LLM_*` when distinct).
  It verifies the model **actually called a retrieval tool** — not merely that the
  answer looks cited — so a model that fabricates a citation from memory (zero
  tool calls) fails. Every check requires real retrieval, including the refusal.
- **Spanish demo wiki (`examples/cuentos-de-hadas/`).** A pre-ingested
  `language = "es"` demo (three public-domain tales) mirroring `fairy-tales/` to
  exercise the multilingual path end-to-end.
- **Per-wiki multilingual content (en/es, extensible).** `[wiki] language` in
  `wiki_config.toml` now governs the language of generated pages, section
  headers, *and* chat answers — independent of the source documents' language.
  Includes an i18n locale registry, localized ingestion / chat / lint / repair,
  a Spanish `README_ES.md` (English remains canonical), and
  `wiki_config_es.example.toml`.

### Changed
- **Provider-wizard default is now explicit** in the installer prompt and in both
  READMEs — "Ollama by default" was too easy to select blindly and silently
  misconfigure chat.
- **Chat agent pins `ModelSettings(temperature=0.0)`** for deterministic,
  reproducible grounding (higher temperatures made models intermittently skip
  tools or drop citations).

### Fixed
- **Demo wikis now ship a real grounding system prompt.** The `fairy-tales`
  config shipped a permissive *test* prompt that could hallucinate on a new
  user's first question; both demos now carry an explicit cite-or-refuse prompt.
- Guard `None` LLM message content and log previously-swallowed backend errors.
- Render delete feedback in the marimo apps and surface notebook errors in logs.

## [0.1.0] - 2026-06-12

Initial public release — a local-first, agentic LLM-wiki.

### Added
- **Ingestion pipeline** — PDF/DOCX → page text → overlapped chunks → structured
  concept extraction → summary + concept pages, catalogue, overview, and
  timeline; content-hash change detection and optional git snapshots per ingest.
- **Agentic wiki-first chat** — a PydanticAI agent that reads the curated wiki
  first (`index.md` → wiki FTS5 → raw source chunks as fallback) and cites the
  document + page for every fact; streamed answers; a human-in-the-loop
  **Save to wiki** step.
- **Self-maintenance** — lint checks (contradictions, stale pages, orphans,
  missing concepts/cross-refs, data gaps) with auto-repair of the safe ones.
- **Evaluation** — a judge-ready eval packet (frozen 1–5 rubric) and a
  one-command model-suitability PASS/FAIL check.
- **Multi-wiki picker** — discovery + recent list + path hygiene, shared by both
  marimo apps.
- **Transparency** — a queryable SQLite citation graph and opt-in JSONL tracing
  (`WIKI_TRACE=1`) with a dedicated trace-report app.
- **Local-first & provider-agnostic** — runs on-device against any
  OpenAI-compatible endpoint; split chat/ingestion models via `.env`.

[Unreleased]: https://github.com/Clod/llmwiki-marimo/compare/v0.2.1...master
[0.2.1]: https://github.com/Clod/llmwiki-marimo/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Clod/llmwiki-marimo/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Clod/llmwiki-marimo/releases/tag/v0.1.0
