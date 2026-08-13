# LLMWiki — Programmer Manual

> **Single source of truth.** This document supersedes `programmatic_dev_plan.md`,  
> `implementation_plan.md`, `ingestion_design.md`, `diagnostic_alignment.md`, and  
> `llmwiki_architecture_rag_roadmap.md` (now under `docs/archive/`). The conceptual  
> reference is [Karpathy's LLM Wiki note](https://x.com/karpathy/status/2039805659525644595) (a local working copy, `Karpathy_concepts.md`, is kept untracked at the repo root).
>
> **Companion document:** **§6 Workflows** lives in its own file —
> [`docs/manual/workflows.md`](manual/workflows.md) — because it's the largest,
> most cross-referenced section. Section numbers are global: a `§6.x` reference is
> in `workflows.md`, every other `§N` is here. Everything else stays in this file.

## Table of Contents

**In this file**

1. [Philosophy & Karpathy Alignment](#1-philosophy--karpathy-alignment)
2. [Architecture Overview](#2-architecture-overview) — including the nine layers and where each section lives
3. [Directory Structure](#3-directory-structure)
10. [Known Constraints & Gotchas](#10-known-constraints--gotchas)
11. [Pending Work & Future Enhancements](#11-pending-work--future-enhancements) — pointer to [`ROADMAP.md`](../ROADMAP.md)
13. [Glossary](#13-glossary)

**In [`manual/workflows.md`](manual/workflows.md)**

6. Workflows — lint · repair · ingest · batch · scan · regenerate · chat/RAG ·
   chat→wiki · source/page deletion. See also the narrative counterparts, both
   with real, regenerable numbers: the
   [Ingestion Walkthrough](ingestion_walkthrough.md) (what ingestion builds) and
   the [Query Walkthrough](query_walkthrough.md) (how a question is routed).

**In [`manual/internals.md`](manual/internals.md)**

4. Database Schema · 5. Native Tool Layer · 14. Tracing & Observability

**In [`manual/apps.md`](manual/apps.md)**

7. Marimo Apps · 8. Configuration · 9. Testing · 15. Datasets, Grounding
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
| Raw sources (immutable)  | `WIKI_PATH/sources/*.pdf` / `*.docx`                                                               |
| Wiki (LLM-generated)     | `WIKI_PATH/wiki/{summaries,concepts}/*.md` + `index.md`, `overview.md`, `log.md`                   |
| Schema (LLM conventions) | `base/domain/chat/config.py:_DEFAULT_SYSTEM_PROMPT` + optional per-workspace `wiki_config.toml` |

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
✅➕ done + goes beyond the concept doc · 🟡 partial · ❌ deferred (→ [ROADMAP](../ROADMAP.md)) · N/A.

| Karpathy concept | | Notes |
| --- | --- | --- |
| Compounding, persistent wiki (vs re-derive per query) | ✅ | the project's thesis (§6.3 builds pages, doesn't just index) |
| Three layers: raw sources · wiki · schema | ✅ | §3; schema = `_DEFAULT_SYSTEM_PROMPT` + `wiki_config.toml` |
| Ingest → summary + concept pages + index + overview + log + git | ✅ | §6.3–§6.4 |
| Wiki-first query **with citations** | ✅ | §6.7 (index → wiki FTS → raw chunks) |
| File good answers **back into** the wiki | ✅ | §6.8 Save form → `save_to_wiki` (user-driven; agent has no write tool) |
| Lint: contradictions · stale · orphans · missing concepts · missing xrefs · data gaps | ✅ | §6.1 — all six |
| Auto-repair of flagged issues | ✅➕ | §6.2 — the concept doc only *flags* |
| `index.md` catalog · `log.md` timeline · git repo | ✅ | §3, §13 |
| Search engine over the wiki | 🟡 | SQLite FTS5 (`search_chunks`); no vector/rerank/MCP — [why partial](#why-the-wiki-search-engine-is-partial) |
| Interactive / HITL ingest ("discuss, then write") | 🟡 | auto today; post-ingest read-app chat + save-to-wiki partly compensates → ROADMAP |
| Web search (query Phase 4 + data-gap fill) | ❌ | external search + manual add compensates → ROADMAP |
| Image handling (download + vision) | ❌ | → ROADMAP |
| Alternate outputs: Marp decks · charts · canvas | ❌ | → ROADMAP |
| Graph visualization | ❌ | the graph exists in `document_references`; just not rendered → ROADMAP |
| Obsidian web-clipper · graph view · Dataview | N/A | this is a marimo project, not Obsidian |

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
| **Vector / semantic** retrieval | ❌ FTS5 is purely lexical — matches tokens, not meaning. A query for "central bank" won't surface a page that only says "the Fed". (Porter stemming handles word forms, not synonyms.) |
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
                    PDFs / DOCXs (sources/)
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
              │   Chat Agent (multi-phase     │ base/domain/chat/
              │   RAG: wiki → sources → web*) │  (*web pending)
              └─────────────┬─────────────────┘
                            ▼
              ┌───────────────────────────────┐
              │   Marimo UIs                  │ marimo/
              │   ingest_app + read_app       │
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
| §6 | [`manual/workflows.md`](manual/workflows.md) | one entry per workflow, with contracts |
| §4 §5 §14 | [`manual/internals.md`](manual/internals.md) | schema, tool layer, tracing |
| §7 §8 §9 §15 | [`manual/apps.md`](manual/apps.md) | Marimo apps, configuration, testing, datasets |


### The nine layers

Derived from the project's own knowledge graph (449 nodes, 975 edges) rather
than drawn by hand, then checked against the directory tree. Every one of the
128 file-level nodes belongs to exactly one layer.

| Layer | What belongs here | Start reading at |
|---|---|---|
| **Ingestion** | the document → wiki pipeline: extract, chunk, detect change, generate pages | `ingestion/pipeline.py` |
| **Retrieval & Chat** | the read side: agent, the pre-retrieval scope gate, grounding, post-processing | `chat/agent.py` · `chat/preretrieval.py` |
| **Persistence & Tools** | SQLite, FTS5 search, the citation graph, deletion, git | `tools/db.py` · `tools/search.py` |
| **Quality & Maintenance** | lint finds defects, repair fixes the safe ones — one layer because they are a producer/consumer pair | `lint/runner.py` · `repair/runner.py` |
| **Datasets & Overlays** | the optional lane for facts that expire, plus one example domain overlay | `datasets/source.py` · `finance_argentina/agent_tool.py` |
| **Evaluation** | the offline harness that scores chat and ingestion quality | `eval/packet.py` |
| **User Interface** | three Marimo notebooks and a widget — the *entire* interface | `marimo/ingest_app.py` · `marimo/read_app_tabs.py` |
| **Core & Configuration** | cross-cutting settings, locales, workspace discovery | `base/config.py` · `domain/i18n.py` |
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
│   └── domain/
│       ├── chat/
│       │   ├── agent.py                # create_agent() factory (+ extra_tools/extra_prompt seam)
│       │   ├── config.py               # _DEFAULT_SYSTEM_PROMPT + load_config()
│       │   ├── tools.py                # search_source_chunks (async, sources scope)
│       │   ├── wiki_tools.py           # read_wiki_page, search_wiki_fts, save_to_wiki
│       │   ├── dataset_tools.py        # query_dataset (current structured values; §15)
│       │   └── guardrail.py            # cite-or-refuse grounding post-check (§15)
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
│       │   └── agent_tool.py           # estimar_alternativas tool + activate()
│       ├── ingestion/
│       │   ├── pipeline.py             # ingest_file, scan_and_ingest,
│       │   │                           #   regenerate_wiki_pages
│       │   ├── batch.py                # batch_ingest
│       │   ├── chunker.py              # chunk_pages (FTS5 units)
│       │   ├── detector.py             # mtime + SHA-256 change detection
│       │   ├── extractor.py            # PDF/DOCX → list[(page, markdown)]
│       │   ├── index_manager.py        # wiki/index.md upsert (deterministic)
│       │   ├── pdf_extract.py          # opendataloader-pdf (text PDFs; no OCR yet)
│       │   ├── trace.py                # opt-in ingestion trace (see §14)
│       │   └── wiki_generator.py       # all LLM prompt builders (see §6)
│       ├── lint/
│       │   ├── checks.py               # 7 check functions (incl. gap_filled)
│       │   ├── markers.py              # DATA_GAP markers + fts_safe (shared w/ repair)
│       │   ├── report.py               # LintIssue, LintReport
│       │   └── runner.py               # lint_wiki()
│       ├── repair/
│       │   ├── actions.py              # 7 repair functions (one per lint check)
│       │   ├── report.py               # RepairResult, RepairReport
│       │   └── runner.py               # repair_wiki()
│       ├── tools/
│       │   ├── db.py                   # open_db(), get_connection()
│       │   ├── deletion.py             # delete_source (cascade + stale marking, §6.9)
│       │   ├── git_ops.py              # init_wiki_repo, auto_commit
│       │   ├── references.py           # citation graph CRUD + queries
│       │   ├── search.py               # search_chunks() scoped FTS5
│       │   └── wiki_fs.py              # create/read/append/delete_page
│       └── wiki_registry.py            # wiki discovery + recent list + path hygiene (the picker, §7.1)
├── marimo/
│   ├── ingest_app.py                   # Wiki picker + upload + ingest + scan + regenerate UI
│   ├── read_app.py                     # Wiki picker + 3-pane reader + chat + save_to_wiki
│   ├── trace_report_app.py             # WIKI_TRACE run viewer (see §7, §14)
│   ├── widgets/
│   │   ├── __init__.py
│   │   └── delete_confirm.py           # DeleteConfirmWidget (anywidget) — reusable
│   └── prototypes/                     # Scratch experiments (chat_app.py et al.) — not imported by apps
├── database/
│   └── sqlite_schema.sql               # Canonical schema (applied by open_db)
├── scripts/
│   ├── build_golden_corpus.py          # Build/freeze the golden-corpus snapshot (§9)
│   ├── eval_chat_model.py              # PASS/FAIL smoke test of the chat model (§9)
│   ├── build_eval_packet.py            # Generate the half-automated UAT eval packet (§9)
│   ├── uat_finanzas.py                 # Run the finanzas-argentinas demo UAT (9 GUIA_DEMO.md questions)
│   └── render_trace.py                 # Render a trace.jsonl run to a timeline (§14.7)
├── tests/
│   ├── conftest.py                     # sys.path + fixture registration
│   ├── helpers/{fake_llm.py,workspace.py,golden.py}
│   ├── unit/                           # 487 unit tests (no LLM, no network)
│   ├── regression/                     # golden-corpus + eval-reader invariants (skips until frozen)
│   └── e2e/                            # 11 Playwright tests (live marimo + LLM)
├── examples/                          # Pre-ingested demo wikis for quickstart.py (§7)
│   ├── fairy-tales/                   # Complete workspace; browsable with no LLM
│   ├── cuentos-de-hadas/             # Spanish (es) mirror of fairy-tales
│   └── finanzas-argentinas/          # Spanish advisor demo: datasets/ + GUIA_DEMO.md
├── docs/
│   ├── programmer_manual.md            # THIS FILE
│   ├── sqlite_data_dictionary.md       # Per-column DB reference
│   ├── CODEMAPS/                       # Auto-generated code maps
│   └── archive/                        # Superseded design docs
├── README.md                           # End-user quickstart
├── quickstart.py                       # Stdlib-only console installer (§7)
├── requirements.txt                    # Hash-pinned export of uv.lock (installer pip path, §7)
└── pyproject.toml + uv.lock
```

### Runtime workspace layout

```
workspace/                              # = the active wiki (default $WIKI_PATH; switchable in-app, §7.1)
├── sources/                            # Drop PDFs / DOCXs here
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

- **FTS5 hyphens.** The porter unicode61 tokenizer splits hyphens at index time.
An unquoted `mortgage-backed` in a query raises a MATCH syntax error ("no such
column: backed") which `search_chunks` swallows, returning `[]`. Use plain terms
or quote the hyphenated phrase (`"mortgage-backed"`).
- **`status='ready'` fires early.** Set at step 6 (after chunking), before the  
LLM work in steps 7–9. Polling only for `status='ready'` misses the wiki  
page creation. Always add a second poll for the wiki page in tests.
- **Circular import.** `wiki_fs.py` imports `chunker.py` inside  
`_insert_chunks()` (deferred) to prevent a load-time cycle with  
`pipeline.py`.
- **Marimo reactivity.** `ingest_file()` runs synchronously inside a marimo  
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
- **LibreOffice required for DOCX.** `extractor.check_libreoffice()` raises  
`LibreOfficeNotInstalledError` with install instructions; the pipeline  
surfaces this as `status='failed'`.

---

## 11. Pending Work & Future Enhancements

**Both now live in [`ROADMAP.md`](../ROADMAP.md)**, which is versioned, linked
from the READMEs and checked by the docs link test. This section used to hold two
lists — near-term work and aspirational features — and a third appeared when the
roadmap was written. Three lists of the same thing diverge; one does not.

What moved there: the deterministic `reindex_from_disk` design, deepening
`data_gap`, giving scan and regenerate the same lint tail ingestion already has,
the duplicate-upload warning, OCR — and, under *Not planned* with the reasoning
intact, web search at query time and as an ingest loop, two-step reviewed
ingestion, image handling, and output formats beyond markdown.

**Where the completed items went.** Chat-to-wiki structuring, the post-save
lint+repair hook, source deletion, the page-deletion widget, and the finished
repairs are all shipped; each is described where it belongs rather than in a
changelog-shaped list — §6.8, §6.9, §6.10 of
[Workflows](manual/workflows.md), and §7 here for the interface. The directory
map in §3 names every module involved.

**On "no open bugs".** This section used to say none were tracked. That was true
of *bugs*; it was never true of known limits, and
[`ROADMAP.md`](../ROADMAP.md#known-limits-and-open-questions) now records five —
measured, reproduced, and deliberately not fixed yet.

---

## 13. Glossary

| Term                        | Meaning                                                                                                                                                                                                                             |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Source**                  | A raw, immutable file under `workspace/sources/` (PDF, DOCX). `source_kind='source'` in `documents`.                                                                                                                                |
| **Summary page**            | 1-to-1 LLM-generated markdown reflection of a single source. Lives under `wiki/summaries/`. Carries `source_document_id`.                                                                                                           |
| **Concept page**            | Topic-centric, multi-source markdown page under `wiki/concepts/`. Carries YAML front-matter written by `create_page` (`type`, `title`, `tags`, `sources`) — the model returns the body only. Does NOT carry `source_document_id` — derives from many sources.                                                                            |
| **`index.md`**              | Catalogue of every page in the wiki, organised by category. Deterministically updated by `index_manager.update_index`.                                                                                                              |
| **`overview.md`**           | LLM-rewritten narrative synthesis of the wiki's evolving thesis.                                                                                                                                                                    |
| **`log.md`**                | Append-only chronological audit trail. Prefix `## [YYYY-MM-DD]` makes it parseable with `grep "^## \["`.                                                                                                                            |
| **Slug**                    | `make_wiki_slug(name)` — NFKD-normalise → strip combining marks → lowercase → spaces/underscores → hyphens → remove non-`[a-z0-9-]` chars. Used as the filename of every wiki page. Example: `"Política Común"` → `politica-comun`. |
| **Filing Cabinet**          | The SQLite + FTS5 layer (`workspace/.llmwiki/index.db`).                                                                                                                                                                            |
| **Encyclopedia**            | The human-readable markdown layer (`workspace/wiki/`).                                                                                                                                                                              |
| **Phase 1 / 2 / 3 / 4 RAG** | The agent's routing cascade: index → wiki search → raw chunks → web search (Phase 4 deliberately not built — see the [ROADMAP](../ROADMAP.md)).                                                                                                                                 |
| **Trace (ingestion)**       | Opt-in (`WIKI_TRACE=1`) write-only JSONL record of every LLM exchange + the data-flow path of an ingestion run, correlated to DB rows via a `db_join_map` header. For debugging, not replay. See §14.                                |
| **Sidecar**                 | A content-addressed file under a trace's `payloads/<sha256>.<ext>` holding one heavy payload (prompt, response, extracted text, chunks, or a generated page), referenced from the event by `ref` + `sha256` + `bytes`. See §14.4.     |

---

