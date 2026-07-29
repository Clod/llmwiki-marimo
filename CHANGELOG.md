# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(pre-1.0: **minor** = features, **patch** = fixes). English is the canonical
language for this file, matching the README policy. There is no PyPI package —
users clone and run — so versions are a communication tool, not a dependency
contract. See [`RELEASING.md`](RELEASING.md) for the process.

## [Unreleased]

## [0.3.0] - 2026-07-29

### Added
- **Dataset engine** — a domain-neutral `datasets/` capability: tabular data
  files the assistant queries through an opt-in `query_dataset` tool, kept
  separate from the curated wiki so numbers come from the data, never the model.
- **Argentine finance advisory** (`finance_argentina`) — a deterministic
  `estimar_alternativas` tool that ranks investment alternatives for a given
  amount and horizon with **code-computed** gains (never LLM-estimated), flags
  non-estimable instruments (equities), and carries a nominal-vs-real inflation
  disclaimer.
- **`finanzas-argentinas` demo** — a pre-ingested Spanish finance wiki with
  `datasets/`, a tuned `wiki_config.toml`, a demo guide, and its own live
  acceptance UAT (`scripts/uat_finanzas.py`).
- **Ingest-time vocabulary subsystem** — the assistant generates data/concept
  aliases while ingesting; a vocabulary linter + auto-repair keeps the alias map
  honest (collisions dropped, stale/covered/ambiguous surfaced); a coverage
  **roster** decides what the wiki actually covers; and a thin-page detector
  flags source chunks the wiki leaves uncovered.
- **Hybrid pre-retrieval** (opt-in per wiki) — code retrieves and injects wiki
  context *before* the model answers, gated on the coverage roster so an
  off-topic or uncovered question is refused deterministically instead of leaking
  from a tangential chunk; tiered curated-then-raw sources with answer-vs-source
  verification; and a live toggle in the read app.
- **Pluggable citation/grounding guardrail** and an **opt-in JSONL chat trace**
  (one row per turn) for offline diagnosis.
- **A tabbed read app ships alongside the grid one** (`marimo/read_app_tabs.py`).
  It is **not the default and not yet documented**: `quickstart.py`, both READMEs
  and the programmer manual still launch `marimo/read_app.py`, and the E2E suite
  still covers that one. Both carry the same chat, including the pre-retrieval
  toggle, so any change to the chat currently has to be made twice. Promotion —
  and the removal of the grid app — waits on a parity test proving nothing was
  lost in the move.
- **Two walkthrough documents, generated from real runs.** The [ingestion
  walkthrough](docs/ingestion_walkthrough.md) follows one small corpus through
  its whole lifecycle — first document, second, a no-op re-ingest, an edited
  source, a deletion — and the [query walkthrough](docs/query_walkthrough.md)
  follows a spectrum of questions through both chat modes. Each is split so a
  reader with an ordinary wiki can stop half way, and each is paired with a
  capture script (`scripts/capture_*_walkthrough.py`) that regenerates its
  appendix, so the figures they quote come from running the pipeline rather
  than from memory.

### Changed
- **The ingest end-to-end test was rewritten** for the current app
  (`tests/e2e/test_ingest_app_v2.py`, replacing `test_ingest_app.py`): it drives
  the wiki picker, ingest form, Activity Log, vocabulary lint lines, scan
  idempotency and cross-links through the real Marimo UI. The slower cases stay
  opt-in behind `E2E_FULL=1` and `E2E_DESTRUCTIVE=1`.

### Fixed
- **Chunk breadcrumbs named the wrong section.** `header_breadcrumb` — the
  heading path a search hit is traced back to, and what a citation names beyond
  a page number — was serialised when a chunk was closed, from the heading stack
  as it then stood. Because a heading is pushed before the size check, a chunk
  flushed at a heading boundary was labelled with the section that *starts after
  it*: in the shipped demo one chunk was named "Principales riesgos
  estructurales" while containing nothing of it but the heading line, and that
  section's actual text sat in the next chunk under a different name. A
  breadcrumb naming the wrong section is worse than none — it aims a citation at
  a passage the fragment does not contain.

  Two further cases came out of the same work: a chunk that merely *ends* on a
  heading no longer takes that section's name, and the heading outline now
  carries across page breaks, so a section running past the bottom of a PDF page
  keeps its name (and its document title) instead of restarting. The column had
  no tests; it has seven now.
- **Pre-retrieval now actually retrieves.** The FTS query reached SQLite FTS5 raw,
  so any natural question crashed the search and silently returned no hits — the
  gate then refused valid, covered questions. Queries are now sanitized; both
  tiers are gated on the coverage roster; a data/advisory question routes to the
  tools before any raw-doc fallback; and a generic advisory question (an amount +
  horizon, no named instrument) reaches the advisory tool instead of being
  refused.
- **Citation detection recognizes the real format.** A `Referencia:`/`Fuente:`
  line (what the prompt asks for and the app emits) and a source-document
  citation (`.docx`/`.pdf`) now count as grounded/cited in the chat trace and the
  eval graders, not only an inline `(wiki/….md)`.
- **Lint & repair** labels advisory-only vocabulary findings clearly instead of
  reporting them as "Unknown check type".

## [0.2.3] - 2026-07-08

### Fixed
- **Demo wikis now cite every fact in the read app.** Both bundled demos
  (`fairy-tales`, `cuentos-de-hadas`) shipped a `wiki_config.toml` system prompt
  with only a single soft citation line ("cite the tale name... for concrete
  facts") — much weaker than the tuned default the model-validation eval runs
  under. In the read app (which uses each wiki's own prompt), a synthesis/
  comparison answer came back grounded but **uncited**, breaking the project's
  core "every fact carries a citation" promise. Both demo prompts were brought up
  to the default's rigor — an explicit grounding mandate, a mandatory-citation
  section with format examples, the "synthesis and comparisons must cite each
  point/row" rule, and a worked cited-comparison example — in each demo's own
  language. Verified end-to-end: the exact comparison that failed now cites every
  point in both English and Spanish.

## [0.2.2] - 2026-07-08

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

[Unreleased]: https://github.com/Clod/llmwiki-marimo/compare/v0.3.0...master
[0.3.0]: https://github.com/Clod/llmwiki-marimo/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/Clod/llmwiki-marimo/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Clod/llmwiki-marimo/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Clod/llmwiki-marimo/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Clod/llmwiki-marimo/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Clod/llmwiki-marimo/releases/tag/v0.1.0
