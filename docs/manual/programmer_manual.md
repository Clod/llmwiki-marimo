# LLMWiki — Programmer Manual

> **Single source of truth.** This document supersedes `programmatic_dev_plan.md`,  
> `implementation_plan.md`, `ingestion_design.md`, `diagnostic_alignment.md`, and  
> `llmwiki_architecture_rag_roadmap.md` (now under `docs/archive/`). The conceptual  
> reference is [Karpathy's LLM Wiki note](https://x.com/karpathy/status/2039805659525644595) (a local working copy, `Karpathy_concepts.md`, is kept untracked at the repo root).
>
> **Companion document:** **§6 Workflows** lives in its own file —
> [`docs/manual/workflows.md`](workflows.md) — because it's the largest,
> most cross-referenced section. Section numbers are global: a `§6.x` reference is
> in `workflows.md`, every other `§N` is here. Everything else stays in this file.

## Table of Contents

**In this file**

1. [Philosophy & Karpathy Alignment](#1-philosophy--karpathy-alignment)
2. [Architecture Overview](#2-architecture-overview) — including the layers and where each section lives
3. [Directory Structure](#3-directory-structure)
10. [Known Constraints & Gotchas](#10-known-constraints--gotchas)
11. [Pending Work & Future Enhancements](#11-pending-work--future-enhancements) — pointer to [`ROADMAP.md`](../../ROADMAP.md)
13. [Glossary](#13-glossary)

**In [`workflows.md`](workflows.md)**

6. Workflows — lint · repair · ingest · batch · scan · regenerate · chat/RAG ·
   chat→wiki · source/page deletion. See also the narrative counterparts, both
   with real, regenerable numbers: the
   [Ingestion Walkthrough](../ingestion_walkthrough.md) (what ingestion builds) and
   the [Query Walkthrough](../query_walkthrough.md) (how a question is routed).

**In [`internals.md`](internals.md)**

4. Database Schema · 5. Native Tool Layer · 14. Tracing & Observability

**In [`apps.md`](apps.md)**

7. The web interface · 8. Configuration · 9. Testing · 15. Datasets, Grounding
   Guardrail & the `finance_argentina` Overlay

Status legend used throughout: ✅ implemented · 🟡 partial · ❌ missing.

> **On line numbers.** Code is cited as `module.py:symbol` (function/constant name),
> which stays valid as the code moves. Any bare `:NN` or `(L NN)` you still see is an
> approximate snapshot — grep for the named symbol rather than jumping to the line.

---

## 1. Philosophy & Karpathy Alignment

LLMWiki is a Python implementation of the LLM-Wiki pattern described in  
Karpathy's note. The idea: instead of re-discovering knowledge from raw  
chunks on every query (classic RAG), an LLM **incrementally builds and maintains**  
**a persistent encyclopedia** of markdown pages that sits between the user and the  
raw sources.

The project maps onto Karpathy's three-layer model as follows:

| Karpathy layer           | This project                                                                                       |
| ------------------------ | -------------------------------------------------------------------------------------------------- |
| Raw sources (immutable)  | `WIKI_PATH/sources/*.pdf` / `*.docx` / `*.doc` / `*.odt` / `*.rtf` / `*.md` / `*.txt`                                                               |
| Wiki (LLM-generated)     | `WIKI_PATH/wiki/{summaries,concepts}/*.md` + `index.md`, `overview.md`, `log.md`                   |
| Schema (LLM conventions) | `prompts/system_prompt.<lang>.md` + optional per-workspace `wiki_config.toml` |

And onto the "Two Layers of Knowledge" framing:

- **Filing Cabinet** → `workspace/.llmwiki/index.db` (SQLite + FTS5)
- **Encyclopedia** → human-readable markdown under `workspace/wiki/`

Two operating principles flow from this:

1. **Ingestion is not just indexing.** Dropping a PDF only saves it to
  `sources/`; submitting the ingest form (or running a scan) then triggers
   extraction + chunking + structured concept extraction + creation/update of  
   summary and concept pages + overview rewrite + git commit. The wiki *grows*  
   with each ingested source.
2. **Wiki-first retrieval.** The chat agent reads curated wiki pages before
  touching raw chunks; raw-source FTS is a fallback, not the default.

### Karpathy coverage matrix

Which ideas from Karpathy's original note this project implements. ✅ done ·
✅➕ done + goes beyond the concept doc · 🟡 partial · ❌ deferred (→ [ROADMAP](../../ROADMAP.md)) · N/A.

| Karpathy concept | | Notes |
| --- | --- | --- |
| Compounding, persistent wiki (vs re-derive per query) | ✅ | the project's thesis (§6.3 builds pages, doesn't just index) |
| Three layers: raw sources · wiki · schema | ✅ | §3; schema = `prompts/system_prompt.<lang>.md` + `wiki_config.toml` |
| Ingest → summary + concept pages + index + overview + log + git | ✅ | §6.3–§6.4 |
| Wiki-first query **with citations** | ✅ | §6.7 (index → wiki FTS → raw chunks) |
| File good answers **back into** the wiki | ✅ | §6.8 Save form → `save_to_wiki` (user-driven; agent has no write tool) |
| Lint: contradictions · stale · orphans · missing concepts · missing xrefs · data gaps | ✅ | §6.1 — all six |
| Auto-repair of flagged issues | ✅➕ | §6.2 — the concept doc only *flags* |
| `index.md` catalog · `log.md` timeline · git repo | ✅ | §3, §13 |
| Search engine over the wiki | 🟡 | SQLite FTS5 (`search_chunks`); no vector/rerank/MCP — [why partial](#why-the-wiki-search-engine-is-partial) |
| Interactive / HITL ingest ("discuss, then write") | 🟡 | auto today; the chat of the reading screen + save-to-wiki (§6.8) partly compensates → ROADMAP |
| Web search (query Phase 4 + data-gap fill) | ❌ | external search + manual add compensates → ROADMAP |
| Image handling (download + vision) | ❌ | → ROADMAP |
| Alternate outputs: Marp decks · charts · canvas | ❌ | → ROADMAP |
| Graph visualization | ✅ | the Relations screen draws `document_references` with force-graph, whole or around one page (§7.5) |
| Obsidian web-clipper · graph view · Dataview | N/A | the interface is a web application (§7), not Obsidian |

### Why the wiki search engine is partial

The matrix grades "search engine over the wiki" against a specific passage in
Karpathy's note: *"as the wiki grows you want proper search… **qmd** is a good
option: a local search engine for markdown files with **hybrid BM25/vector search and
LLM re-ranking**, all on-device, with both a **CLI** and an **MCP server**."* That sets
the ✅-full bar at four named properties.

**What exists.** `tools/search.py:search_chunks` runs a single SQLite **FTS5** query
(`chunks_fts → document_chunks → documents`), scopeable to `wiki` / `sources` / `all`
and ordered by FTS5 `rank` (BM25). It's wired into the agent as `search_wiki_fts` (§6.7).
So there *is* a working keyword search engine over the wiki — strictly more than "just
read `index.md`", which is why this isn't ❌.

**What's missing for ✅** — every piece of the concept's "proper search" definition:

| Karpathy's bar | Here |
| --- | --- |
| **Vector / semantic** retrieval | ❌ FTS5 is purely lexical — matches tokens, not meaning. A query for "central bank" won't surface a page that only says "the Fed". (Snowball stemming, per wiki language, handles word forms, not synonyms.) |
| **Hybrid** BM25 + vector fusion | ❌ BM25 only |
| **LLM re-ranking** | ❌ raw FTS5 `rank` order, no rerank pass |
| **CLI + MCP** surface | ❌ an internal Python function, not a standalone tool an external LLM can shell out to or call over MCP |

**Why it's also a deliberate scope choice, not just unfinished work.** The same concept
doc notes the `index.md` catalog *"works surprisingly well at moderate scale (~100
sources) and avoids the need for embedding-based RAG infrastructure"*, and the project
leans into exactly that: the agent's retrieval cascade is **`index.md` → wiki FTS → raw
source chunks** (wiki-first RAG, §6.7). At PoC scale, keyword FTS plus the curated index
covers the need; vector/rerank/MCP is the "as the wiki grows" upgrade that hasn't been
needed yet. If revisited, the on-brand path is a local hybrid engine (e.g. qmd) exposed
as an MCP tool, alongside — not replacing — the index-first cascade.

---

## 2. Architecture Overview

```
                    PDF / office / md / txt files (sources/)
                            │
                            ▼
              ┌───────────────────────────────┐
              │   Ingestion Pipeline          │ base/domain/ingestion/
              │   extract → chunk → LLM       │
              │   → summary + concept pages   │
              │   → overview rewrite          │
              │   → log entry + git commit    │
              └─────────────┬─────────────────┘
                            ▼
              ┌───────────────────────────────┐
              │   SQLite + Filesystem         │
              │   workspace/.llmwiki/index.db │
              │   workspace/wiki/summaries/   │
              │   workspace/wiki/concepts/    │
              └─────────────┬─────────────────┘
                            ▼
              ┌───────────────────────────────┐
              │   Chat Agent (three modes;    │ base/domain/chat/
              │   RAG: wiki → sources)        │
              └─────────────┬─────────────────┘
                            ▼
              ┌───────────────────────────────┐
              │   Web interface               │ web/ → base/services/
              │   (marimo/ is being retired)  │
              └───────────────────────────────┘

              ┌───────────────────────────────┐
              │   Lint + Repair (orthogonal)  │ base/domain/{lint,repair}/
              │   Runs on demand or after     │
              │   ingest; auto-fixes safe     │
              │   issues, flags the rest.     │
              └───────────────────────────────┘
```

**Key design principle:** the wiki is the primary knowledge layer; raw chunks  
are the fallback. Lint and repair keep the wiki internally consistent over time.

---

### Where each section lives

The manual is split across four files. **Section numbers are global** — a `§N`
means the same section wherever it is cited, which is what keeps ~190
cross-references between these documents valid.

| Sections | File | For |
|---|---|---|
| §1 §2 §3 §10 §11 §13 | this file | what the project is, how it is shaped, what it does not do |
| §6 | [`workflows.md`](workflows.md) | one entry per workflow, with contracts |
| §4 §5 §14 | [`internals.md`](internals.md) | schema, tool layer, tracing |
| §7 §8 §9 §15 | [`apps.md`](apps.md) | the web interface, configuration, testing, datasets |


### The layers

Nine layers were derived from the project's own knowledge graph (449 nodes, 975 edges) rather
than drawn by hand, then checked against the directory tree; every one of the
128 file-level nodes belonged to exactly one of them. `base/services/`, `web/` and
`base/domain/verify/` were added after that derivation, as the three rows marked *added*, so
the table has twelve rows.

| Layer | What belongs here | Start reading at |
|---|---|---|
| **Ingestion** | the document → wiki pipeline: extract, chunk, detect change, generate pages | `ingestion/pipeline.py` |
| **Retrieval & Chat** | the read side: agent, the pre-retrieval scope gate, grounding, post-processing | `chat/agent.py` · `chat/preretrieval.py` |
| **Persistence & Tools** | SQLite, FTS5 search, the citation graph, deletion, git | `tools/db.py` · `tools/search.py` |
| **Quality & Maintenance** | lint finds defects, repair fixes the safe ones — one layer because they are a producer/consumer pair | `lint/runner.py` · `repair/runner.py` |
| **Datasets & Overlays** | the optional lane for facts that expire, plus one example domain overlay | `datasets/source.py` · `finance_argentina/agent_tool.py` |
| **Evaluation** | the offline harness that scores chat and ingestion quality | `eval/packet.py` |
| **Services** *(added)* | the calls every interface makes to the domain: open a wiki, list and save pages, one chat turn per mode, uploads, maintenance, history, the relations graph. No interface code | `base/services/wiki.py` · `base/services/chat.py` · `base/services/ingest.py` |
| **Web interface** *(added)* | the interface of the project: FastAPI routes, Jinja2 templates, HTMX, SSE. Routes hold no logic and call `base/services/` | `web/app.py` · `web/routes/` |
| **Verification** *(added)* | the check of each sentence of a page, or of a chat answer, against the text of its sources with a local NLI model; report only, it never rewrites a page | `verify/page.py` · `verify/core.py` |
| **User Interface (marimo)** | three Marimo notebooks and a widget — the former interface, being retired and kept until a separate change removes it | `marimo/ingest_app.py` · `marimo/read_app_tabs.py` |
| **Core & Configuration** | cross-cutting settings, locales, the prompt files, workspace discovery | `base/config.py` · `domain/i18n.py` · `domain/prompts.py` |
| **Support** | documentation, templates, scripts, the quick-start installer | `quickstart.py` |

Two boundaries are worth stating because they are decisions rather than
consequences of the folder layout:

- **Quality is one layer, not two.** Lint never writes and repair only writes
  what lint found; splitting them would suggest they can be used apart.
- **Datasets and the finance overlay sit outside the engine.** They are inactive
  on most wikis — the overlay activates only when a workspace's data satisfies a
  declared manifest — so treating them as core would misdescribe what a plain
  wiki runs.

Regenerate the graph any time with the `understand-anything` plugin; it is a tool
output and is not committed, so this table is the version that ships.

## 3. Directory Structure

```
llmwiki/
├── base/
│   ├── config.py                       # pydantic-settings (.env)
│   ├── services/                       # What every interface calls
│   │   ├── wiki.py                     #   open_wiki, pages, save, reindex, graph
│   │   ├── chat.py                     #   agents, one chat turn per mode, save_reviewed_page
│   │   ├── ingest.py                   #   uploads, scan, regenerate, lint_and_repair, deletion
│   │   ├── verification.py             #   verify one page, every page, a draft
│   │   ├── vocabulary.py               #   the roster, aliases and blacklist (§7.6)
│   │   └── settings.py                 #   the Settings screen (§7.12)
│   └── domain/
│       ├── chat/
│       │   ├── agent.py                # create_agent() factory (+ extra_tools/extra_prompt seam)
│       │   ├── config.py               # load_config() (default prompt: prompts/system_prompt.<lang>.md)
│       │   ├── tools.py                # search_source_chunks (async, sources scope)
│       │   ├── wiki_tools.py           # read_wiki_page, search_wiki_fts, save_to_wiki
│       │   ├── dataset_tools.py        # query_dataset (current structured values; §15)
│       │   ├── preretrieval.py         # code-driven retrieval of the pre-retrieval mode
│       │   ├── scope.py                # the deterministic scope gate of the pre-retrieval
│       │   ├── overlap.py              # lexical answer-vs-source check (Tier 2 of the pre-retrieval)
│       │   ├── condense.py             # rewrite of a follow-up question into a standalone one
│       │   ├── history.py              # the lean conversation history sent to the model
│       │   ├── guardrail.py            # cite-or-refuse grounding post-check (§15)
│       │   ├── refusal.py              # refusals that say why, and the refusal directive
│       │   ├── postprocess.py          # advisory table + guaranteed source line of an answer
│       │   ├── vocabulary.py           # shared primitives of the ingest-time alias generation
│       │   ├── config_writer.py        # vocabulary edits of wiki_config.toml (tomlkit)
│       │   └── settings_writer.py      # Settings edits of wiki_config.toml (tomlkit)
│       ├── datasets/                    # Generic transient-data engine (§15)
│       │   ├── models.py               # DatasetRow + DatasetSource Protocol
│       │   ├── frontmatter.py          # YAML front-matter (PyYAML)
│       │   ├── parser.py               # matriz/largo markdown → normalized rows
│       │   └── source.py               # LocalMarkdownSource (parse-on-read) + has_active_datasets
│       ├── eval/                        # Half-automated UAT eval packet (§9)
│       │   ├── graders.py              # pure regex/leak checks (shared w/ eval_chat_model)
│       │   ├── rubric.py               # frozen judge instructions + 1–5 rubric + probes
│       │   ├── packet.py               # dataclasses + pure markdown render
│       │   └── reader.py               # read-only evidence gathering from a wiki DB
│       ├── finance_argentina/           # Example domain overlay: investment advisory (§15)
│       │   ├── requirements.py / .md   # manifest: what each category needs
│       │   ├── instrument_attrs.py     # finance attrs read from DATASET front-matter
│       │   ├── validator.py            # domain lint (datasets + attributes)
│       │   ├── formulae.py             # deterministic TEA / gain math
│       │   ├── advisory.py             # estimate_alternatives()
│       │   ├── data_tools.py           # one typed data tool per category (K6–K10)
│       │   └── agent_tool.py           # estimar_alternativas tool + activate()
│       ├── ingestion/
│       │   ├── pipeline.py             # ingest_file, scan_and_ingest,
│       │   │                           #   regenerate_wiki_pages
│       │   ├── batch.py                # batch_ingest
│       │   ├── chunker.py              # chunk_pages (FTS5 units)
│       │   ├── detector.py             # mtime + SHA-256 change detection
│       │   ├── extractor.py            # PDF/office/md/txt → list[(page, markdown)]
│       │   ├── formats.py              # TEXT_/OFFICE_/SUPPORTED_EXTENSIONS, defined once
│       │   ├── index_manager.py        # wiki/index.md upsert (deterministic)
│       │   ├── pdf_extract.py          # opendataloader-pdf (text PDFs; no OCR yet)
│       │   ├── text_extract.py         # .md / .txt reader (no Java, no LibreOffice)
│       │   └── wiki_generator.py       # page builders; their prompts are files in prompts/ (see §6)
│       ├── lint/
│       │   ├── checks.py               # 12 check functions (incl. gap_filled, vocabulary, thin_page, verification)
│       │   ├── markers.py              # DATA_GAP markers + fts_safe (shared w/ repair)
│       │   ├── report.py               # LintIssue, LintReport
│       │   └── runner.py               # lint_wiki()
│       ├── repair/
│       │   ├── actions.py              # 8 repair functions (the report-only checks have none)
│       │   ├── report.py               # RepairResult, RepairReport
│       │   └── runner.py               # repair_wiki()
│       ├── rollback/                    # go back to an earlier point of a wiki (§7.7)
│       │   ├── revert.py               # the revert: pages and index together
│       │   └── snapshots.py            # the DB snapshot cache: one copy of index.db per commit
│       ├── text/
│       │   ├── stemming.py             # Snowball stemming per wiki language (stem_text, stem_fts_query)
│       │   └── stopwords.py
│       ├── tools/
│       │   ├── db.py                   # open_db(), get_connection()
│       │   ├── deletion.py             # delete_source (cascade + stale marking, §6.9)
│       │   ├── git_ops.py              # init_wiki_repo, auto_commit
│       │   ├── references.py           # citation graph CRUD + queries
│       │   ├── reindex.py              # reindex_from_disk: the index rebuilt with no model call
│       │   ├── search.py               # search_chunks() scoped FTS5
│       │   └── wiki_fs.py              # create/read/append/delete_page
│       ├── verify/                      # sentence-level verification against the sources
│       │   ├── page.py                 # verify a wiki page against the text of its sources
│       │   ├── core.py                 # verify an answer against the passages the model read
│       │   ├── sentences.py · checkable.py · passages.py · preselect.py · context.py
│       │   ├── nli.py · verifier.py    # the local NLI verifier and the fake one of the tests
│       │   └── store.py · models.py    # page_verifications / verification_sentences
│       ├── i18n.py                     # Locale per wiki language (en, es)
│       ├── prompts.py                  # load_prompt(name, language): the files of prompts/
│       ├── wiki_settings.py            # load_wiki_language, write_language
│       ├── tracing.py                  # opt-in OpenTelemetry spans, one per diagram node (see §14)
│       └── wiki_registry.py            # wiki discovery + recent list + path hygiene (the picker, §7.1)
├── prompts/                            # Every prompt the model receives, one file per name and language
│                                       #   (<name>.<lang>.md): the chat, the ingestion and the lint
├── web/                                # The web interface (see web/README.md and §7)
│   ├── app.py                          # create_app(); `web.app:app` is what uvicorn loads
│   ├── routes/                         # picker, pages, chat, audit, ingest, history, relations,
│   │                                   #   vocabulary, settings, lang
│   ├── templates/                      # Jinja2 templates (English message ids)
│   ├── locale/                         # messages.pot + es/LC_MESSAGES/messages.po
│   ├── i18n.py                         # the interface language: catalogs, negotiate(), _()
│   ├── static/                         # CSS (tokens.css), JavaScript, htmx, force-graph
│   ├── render.py                       # Markdown → sanitized HTML, internal links rewritten
│   ├── state.py                        # AppState: open wikis, conversations, operations (in memory)
│   └── settings.py                     # WebSettings
├── marimo/                             # Being retired; removal is a separate change
│   ├── ingest_app.py                   # Wiki picker + upload + ingest + scan + regenerate UI
│   ├── read_app.py · read_app_tabs.py  # Wiki picker + reader + chat + save_to_wiki (grid / tabs)
│   ├── layouts/read_app.grid.json      # Grid layout of read_app.py
│   └── widgets/delete_confirm.py       # DeleteConfirmWidget (anywidget)
├── database/
│   └── sqlite_schema.sql               # Canonical schema (applied by open_db)
├── scripts/
│   ├── build_golden_corpus.py          # Build/freeze the golden-corpus snapshot (§9)
│   ├── eval_chat_model.py              # PASS/FAIL smoke test of the chat model (§9)
│   ├── build_eval_packet.py            # Generate the half-automated UAT eval packet (§9)
│   ├── uat_finanzas.py                 # Run the finanzas-argentinas demo UAT (9 GUIA_DEMO.md questions)
│   ├── render_trace.py                 # Render a spans.jsonl trace to a timeline (§14.7)
│   ├── verify_pages.py                 # Verify the pages of a wiki against their sources
│   ├── verify_battery.py               # Ask a list of questions and verify each answer sentence
│   ├── wiki_revert.py                  # Go back to an earlier point of a wiki (command line)
│   ├── capture_ingestion_walkthrough.py · capture_query_walkthrough.py
│   │                                   #   regenerate the figures of the two walkthroughs
│   └── capture_screenshots.py · record_demo.py   # README screenshots and demo video (marimo read app)
├── tests/
│   ├── conftest.py                     # sys.path + fixture registration
│   ├── helpers/{fake_llm.py,workspace.py,golden.py}
│   ├── unit/                           # 1410 unit tests (no LLM, no network)
│   ├── regression/                     # golden-corpus + eval-reader invariants (16 tests)
│   ├── web/                            # 444 tests: routes, rendering, design checks; e2e/ holds the 62 Playwright flows
│   └── e2e/                            # Playwright tests of the marimo apps (live model; not in CI)
├── examples/                          # Pre-ingested demo wikis for quickstart.py (§7)
│   ├── fairy-tales/                   # Complete workspace; browsable with no LLM
│   ├── cuentos-de-hadas/             # Spanish (es) mirror of fairy-tales
│   └── finanzas-argentinas/          # Spanish advisor demo: datasets/ + GUIA_DEMO.md
├── docs/
│   ├── manual/                         # The programmer manual, one file per section group
│   │   ├── programmer_manual.md        # THIS FILE — §1 §2 §3 §10 §11 §13
│   │   ├── workflows.md                # §6 — index: status table + write matrix
│   │   ├── workflows/                  # §6.1–§6.10, one file per workflow
│   │   ├── internals.md                # §4 §5 §14
│   │   └── apps.md                     # §7 §8 §9 §15
│   ├── sqlite_data_dictionary.md       # Per-column DB reference
│   ├── CODEMAPS/                       # Auto-generated code maps
│   └── archive/                        # Superseded design docs
├── README.md · README_ES.md            # Public presentation, English and Spanish
├── .github/workflows/test.yml          # CI: `unit` (tests/unit, tests/regression, ruff) and `web` (tests/web)
├── quickstart.py                       # Stdlib-only console installer (§7)
├── requirements.txt                    # Hash-pinned export of uv.lock (installer pip path, §7)
└── pyproject.toml + uv.lock
```

### Runtime workspace layout

```
workspace/                              # = the active wiki (default $WIKI_PATH; switchable in-app, §7.1)
├── sources/                            # Drop PDF / office / .md / .txt files here
├── wiki/
│   ├── index.md                        # Auto-maintained catalogue
│   ├── overview.md                     # LLM-rewritten narrative synthesis
│   ├── log.md                          # Append-only timeline
│   ├── summaries/
│   │   └── blancanieves.md             # 1-to-1 per source document
│   └── concepts/
│       └── snow-white.md               # Topic-centric, multi-source
├── wiki_config.toml                    # (optional) per-workspace overrides
└── .llmwiki/
    └── index.db                        # SQLite (documents, chunks, FTS5, refs)
```

The whole `workspace/` directory is a git repo so every ingestion is a trackable  
snapshot. `.gitignore` excludes `.llmwiki/` and the raw `sources/`.

---

## 10. Known Constraints & Gotchas

- **FTS5 hyphens.** The unicode61 tokenizer splits hyphens at index time.
An unquoted `mortgage-backed` in a query raises a MATCH syntax error ("no such
column: backed"); `search_chunks` logs a warning and returns `[]`. Use plain terms
or quote the hyphenated phrase (`"mortgage-backed"`).
- **`status='ready'` fires early.** Set at step 6 (after chunking), before the  
LLM work in steps 7–9. Polling only for `status='ready'` misses the wiki  
page creation. Always add a second poll for the wiki page in tests.
- **Circular import.** `wiki_fs.py` imports `chunker.py` inside  
`_insert_chunks()` (deferred) to prevent a load-time cycle with  
`pipeline.py`.
- **Marimo reactivity** (marimo apps only). `ingest_file()` runs synchronously inside a marimo  
cell. Marimo re-runs dependent cells reactively *after* the cell completes,  
not during. Cells are not async unless explicitly written as `async def`.
- **`source_document_id` is set only for summary pages**, not concepts —  
because one concept can be derived from multiple sources.
- **Citation parser dash.** `update_references` requires *one or more* spaces  
before an em-dash (`\s+[-–—]`) so hyphenated filenames like `fed-paper.pdf`  
are not truncated to `fed`.
- **Slug diacritics.** `make_wiki_slug` applies `unicodedata.normalize("NFKD", …)` +  
`encode("ascii","ignore")` before slugifying, so accented characters are  
transliterated rather than silently dropped (`"Política Común"` →  
`politica-comun`, not `poltica-comn`). This is the only safe cross-OS  
behaviour.
- **LibreOffice required for office files (.docx .doc .odt .rtf).** `.md` and `.txt` need neither LibreOffice nor Java (`text_extract.py`). `extractor.check_libreoffice()` raises  
`LibreOfficeNotInstalledError` with install instructions; the pipeline  
surfaces this as `status='failed'`.

---

## 11. Pending Work & Future Enhancements

**Both now live in [`ROADMAP.md`](../../ROADMAP.md)**, which is versioned, linked
from the READMEs and checked by the docs link test. This section used to hold two
lists — near-term work and aspirational features — and a third appeared when the
roadmap was written. Three lists of the same thing diverge; one does not.

What moved there: deepening `data_gap`, giving scan and regenerate the same lint tail ingestion already has,
the duplicate-upload warning, OCR — and, under *Not planned* with the reasoning
intact, web search at query time and as an ingest loop, two-step reviewed
ingestion, image handling, and output formats beyond markdown.

**Where the completed items went.** Chat-to-wiki structuring, the post-save
lint+repair hook, source deletion, the page-deletion widget, and the finished
repairs are all shipped; each is described where it belongs rather than in a
changelog-shaped list — §6.8, §6.9, §6.10 of
[Workflows](workflows.md), and §7 here for the interface. The directory
map in §3 names every module involved.

**On "no open bugs".** This section used to say none were tracked. That was true
of *bugs*; it was never true of known limits, and
[`ROADMAP.md`](../../ROADMAP.md#known-limits-and-open-questions) now records them —
measured, reproduced, and deliberately not fixed yet.

---

## 13. Glossary

| Term                        | Meaning                                                                                                                                                                                                                             |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Source**                  | A raw, immutable file under `workspace/sources/` (PDF, office, .md, .txt). `source_kind='source'` in `documents`.                                                                                                                                |
| **Summary page**            | 1-to-1 LLM-generated markdown reflection of a single source. Lives under `wiki/summaries/`. Carries `source_document_id`.                                                                                                           |
| **Concept page**            | Topic-centric, multi-source markdown page under `wiki/concepts/`. Carries YAML front-matter written by `create_page` (`type`, `title`, `tags`, `sources`) — the model returns the body only. Does NOT carry `source_document_id` — derives from many sources.                                                                            |
| **`index.md`**              | Catalogue of every page in the wiki, organised by category. Deterministically updated by `index_manager.update_index`.                                                                                                              |
| **`overview.md`**           | LLM-rewritten narrative synthesis of the wiki's evolving thesis.                                                                                                                                                                    |
| **`log.md`**                | Append-only chronological audit trail. Prefix `## [YYYY-MM-DD]` makes it parseable with `grep "^## \["`.                                                                                                                            |
| **Slug**                    | `make_wiki_slug(name)` — NFKD-normalise → strip combining marks → lowercase → spaces/underscores → hyphens → remove non-`[a-z0-9-]` chars. Used as the filename of every wiki page. Example: `"Política Común"` → `politica-comun`. |
| **Filing Cabinet**          | The SQLite + FTS5 layer (`workspace/.llmwiki/index.db`).                                                                                                                                                                            |
| **Encyclopedia**            | The human-readable markdown layer (`workspace/wiki/`).                                                                                                                                                                              |
| **Phase 1 / 2 / 3 / 4 RAG** | The agent's routing cascade: index → wiki search → raw chunks → web search (Phase 4 deliberately not built — see the [ROADMAP](../../ROADMAP.md)).                                                                                                                                 |
| **Prompt file**             | A file `prompts/<name>.<lang>.md` holding the exact text of one prompt in one language, read by `domain.prompts.load_prompt`. A language without the file falls back to English; a missing English file is an error.                                                                  |
| **Trace**                   | Opt-in (`WIKI_TRACE=1`) write-only record of one chat turn or one ingest, as OpenTelemetry spans appended to `spans.jsonl`, one span per diagram node. For debugging, not replay. See §14.                                |
| **Sidecar**                 | A content-addressed file under a trace's `payloads/<sha256>.<ext>` holding one heavy payload (prompt, response, extracted text, chunks, or a generated page), referenced from the span by `ref` + `sha256` + `bytes`. See §14.4.     |

---

