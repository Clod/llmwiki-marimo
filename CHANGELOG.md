# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(pre-1.0: **minor** = features, **patch** = fixes). English is the canonical
language for this file, matching the README policy. There is no PyPI package —
users clone and run — so versions are a communication tool, not a dependency
contract. See [`RELEASING.md`](RELEASING.md) for the process.

## [Unreleased]

### Added
- **`ROADMAP.md`** — what is planned next, and what is built but known to be
  imperfect. The second half is the point: the coverage gate matching page
  titles literally, a documentation check that cannot see a link cut in two, a
  guard written for a step that was never built, and the vocabulary lists having
  no interface by design. These were tracked in a working file outside version
  control, which meant the only copy lived on one machine. Four entries turned
  out to be already fixed and were closed rather than published, and one lost
  its rationale to an earlier PR and was dropped. Linked from both READMEs and
  covered by the docs link checker.
- **The pre-retrieval switch is documented where you would look for it.** Both
  `wiki_config.example.toml` templates now carry the `[pre_retrieval]` section and
  its three scope lists, commented out, and both READMEs explain the trade in the
  chat-configuration section. The flag decides the entire shape of the read path,
  and until now it appeared only in the manual, the walkthrough and the finance
  demo's own config — so a user copying the template had no way to learn it exists.

### Changed
- **Pending work lives in one place.** The programmer manual carried §11 "Pending
  Work / Roadmap" and §12 "Future Enhancements"; `ROADMAP.md` arrived three days
  ago and made a third. Three lists of the same thing diverge, so §11 and §12
  moved wholesale into the roadmap — including the full five-step
  `reindex_from_disk` design, and the deliberate deferrals with their reasoning
  intact (web search at query time and as an ingest loop, two-step reviewed
  ingestion, image handling, output formats). The manual keeps a pointer and is
  178 lines shorter. Its "no open bugs" line was true of *bugs* and never of
  known limits, of which the roadmap now records five. 27 dangling `§11.N` / `§12`
  cross-references across both READMEs, `workflows.md` and the data dictionary
  were repointed.
- **The read app's interface is entirely in English.** It was already English
  almost everywhere — "Refresh", "Save to wiki", "Category", "is not a
  directory" — but a handful of Spanish labels had drifted in with the tabs
  variant and stayed: the two chat checkboxes (`Modo estricto`,
  `Pre-retrieval: el código recupera…`), the tab names (`📖 Lectura`,
  `💬 Diálogo`), the chat heading and the save accordion. `read_app.py` already
  said "Chat with your Wiki" while `read_app_tabs.py` said "Chat con tu Wiki",
  which is what gave the drift away. Wiki *content* remains per-wiki
  multilingual and the Spanish system prompts are untouched — this is the
  chrome only.
- **Both walkthroughs are rewritten for a reader who has never seen the project.**
  They no longer assume you can read a schema, and every term borrowed from the
  LLM and search worlds — *token*, *embedding*, *RAG*, *chunk*, *agentic*,
  *corpus*, *system prompt* — is defined where it first appears. The Spanish terms
  in the finance demo's captured answers are glossed in place rather than
  translated away, since the answers are evidence. Both documents now open on
  Karpathy's note and close on an honest account of what this implementation adds
  to it and where it falls short, and both gained diagrams: the workspace layout,
  the two chat checkboxes, and the chat-to-wiki save flow.
- **The query walkthrough describes the mode the app actually ships in.** It was
  built around a single pre-retrieval toggle, but the read app has two independent
  checkboxes, and `Strict mode` is **on by default** — so the "unticked mode" it
  documented was a configuration nobody runs. Its two showcased failures are now
  measured against that default: a missing citation is repaired by
  `ensure_citation`, while a passage retrieved for a question it does not answer
  passes through untouched. `capture_query_walkthrough.py` records both outcomes
  by replaying the guardrail over the captured run, so the claim costs no second
  model call and cannot drift from the app.
- **Wiki-page front-matter is written by code, not by the model, and follows the
  [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog).**
  The prompt templates used to show the LLM a `tags:`/`sources:` block and ask it to
  reproduce values the code already held; on update the block round-tripped through
  the model and could drift. `create_page` now renders it from what it is given, so
  every page carries a `type` (`concept`, `summary`, `overview` — OKF's one mandatory
  field), a `title`, its `tags`, and `sources` as OKF provenance mappings rather than
  bare strings. Reading tolerates the old string form, so existing wikis keep working.

### Fixed
- **The "needs a model" skip message speaks to whoever reads it.** When lint
  finds a `stale` or `missing_concept` issue and the repair pass was given no
  model — the default after every ingest — it logs a skip. That skip used to read
  *"LLM client required for 'stale' repair — pass llm_client"*: accurate, and no
  use to its only audience, who is looking at the ingest app's Activity Log where
  there is no argument to pass and two buttons that do the job. It now names
  those buttons. Two tests pin it, one of them checking the button names against
  `ingest_app.py` itself, since nothing in code reads those strings and they
  could otherwise drift apart unnoticed.
- **Injected passages are labelled by page, and their front-matter is dropped.**
  With pre-retrieval on, every curated block reaching the model was labelled
  `[/wiki/concepts/]` — the folder, identical for all six — so in the one mode
  whose prompt asks the model to cite, it could not tell the blocks apart.
  `filename` was already in the search row and unused. Each block also carried
  its YAML front-matter (`type`, `tags`, `sources`) as if it were prose: 8% of
  the injected context on the shipped demo, and metadata rather than text to
  answer from — the reason `retrieve_collection_pages` already stripped it on
  its own path. The two are fixed together on purpose: that front-matter's
  `sources:` line was in practice what the model cited from, so removing it
  before the label identified the page would have taken the attribution away and
  put nothing back. Four tests; the existing fixture had folded `path` and
  `filename` into one string, which is why the suite never saw the defect.
- **Stop words are per language, which unblocks the Tier-2 fallback.** The list
  of ubiquitous words dropped before building the full-text query
  (`preretrieval.py`) held only Spanish entries. In an English wiki `the` — three
  characters, past the length filter — reached FTS5 and matched nearly every
  chunk, so `wiki_hits` was never empty; and because `doc_hits` is computed only
  when `wiki_hits` **is** empty, the raw-source tier could not be reached at all.
  An off-topic question also had six unrelated curated chunks injected rather
  than none. Now `_STOPWORDS` is keyed by ISO code and the wiki's language
  selects the set; **adding a language means adding an entry**, documented at the
  constant and in `workflows.md` §6.7. The sets stay separate rather than merged
  on purpose: Spanish `son` is an English content word appearing in six chunks of
  the fairy-tale corpus, and one shared list would drop the key word of "Who is
  the king's son?". Measured on the shipped demo: *"What is the capital of
  France?"* went from 6 injected chunks to 0, and *"Tell me about Cinderwench"* —
  an ingest-generated alias no curated page mentions — now reaches Tier 2 with 4
  source fragments, which was unreachable before.

- **`thin_page` findings were reported as `Unknown check type`.** The check has no
  automatic repair on purpose — it says the wiki under-covers a source, and the
  choice between expanding the page and accepting the Tier-2 fallback is a human
  one — but it was missing from `_ADVISORY_CHECKS`, so every ingestion log that
  hit it printed what looked like an internal error, including the walkthrough
  appendix that ships with the project. A new test now asserts that *every* check
  lint can emit is either repairable or declared advisory, so the next one added
  cannot reopen the gap.
- **Ingestion-walkthrough figures can no longer go stale unnoticed.** The appendix
  is regenerated by really running the pipeline, so the model picks different
  concept names and link counts every time and silently invalidates the prose
  quoting them. `tests/unit/test_docs_ingestion_acts.py` compares the two and
  names the figure to fix.
- **Summary pages had no front-matter at all.** `build_summary_page` is pure code and
  never emitted a block, so every summary in every shipped wiki was missing one. They
  have one now, which also makes a generated wiki OKF-conformant end to end.
- **A rollback no longer claims a source it does not have.** Restoring a page after a
  failed ingest used to keep the filename that ingest had added, because sources only
  ever accumulated. Restores are now authoritative.
- **Three prompts wrapped existing page content in `---` while that content itself
  started with `---`**, leaving the model to guess where the delimiters ended.
- **A ticked wiki can answer questions about itself.** With pre-retrieval on, a
  question about the collection as a whole — "what is in this wiki?", "compare all
  of them" — was refused, because coverage is derived from concept-page names and
  such a question names no concept. Widening that list does not help and was
  measured not to: no roster of item names can contain a question about the
  collection. `wiki/overview.md` and `wiki/index.md` exist to describe it, and are
  now injected directly when the question is shaped that way — they carry no
  `documents` row, so search could never have reached them. The branch cannot fire
  without a page to inject, so a wiki with neither refuses exactly as before.

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
