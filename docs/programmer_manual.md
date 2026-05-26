# LLMWiki — Programmer Manual

> **Single source of truth.** This document supersedes `programmatic_dev_plan.md`,  
> `implementation_plan.md`, `ingestion_design.md`, `diagnostic_alignment.md`, and  
> `llmwiki_architecture_rag_roadmap.md` (now under `docs/archive/`). The conceptual  
> reference `Karpathy_concepts.md` remains at the repo root.

## Table of Contents

1. [Philosophy & Karpathy Alignment](#1-philosophy--karpathy-alignment)
2. [Architecture Overview](#2-architecture-overview)
3. [Directory Structure](#3-directory-structure)
4. [Database Schema](#4-database-schema)
5. [Native Tool Layer](#5-native-tool-layer)
6. [Workflows](#6-workflows)
  - [6.1 Lint](#61-lint-)
  - [6.2 Repair](#62-repair-)
  - [6.3 Single-document ingestion](#63-single-document-ingestion-)
  - [6.4 Batch / multi-document ingestion](#64-batch--multi-document-ingestion-)
  - [6.5 Scan sources folder](#65-scan-sources-folder-)
  - [6.6 Regenerate wiki pages](#66-regenerate-wiki-pages-)
  - [6.7 Query / Chat (multi-phase RAG)](#67-query--chat-multi-phase-rag-)
  - [6.8 Chat → Wiki (file_to_wiki)](#68-chat--wiki-file_to_wiki-)
  - [6.9 Source deletion](#69-source-deletion-)
  - [6.10 Wiki page deletion](#610-wiki-page-deletion-)
7. [Marimo Apps](#7-marimo-apps)
8. [Configuration](#8-configuration)
9. [Testing](#9-testing)
10. [Known Constraints & Gotchas](#10-known-constraints--gotchas)
11. [Pending Work / Roadmap](#11-pending-work--roadmap)
12. [Future Enhancements](#12-future-enhancements)
13. [Glossary](#13-glossary)

Status legend used throughout: ✅ implemented · 🟡 partial · ❌ missing.

---

## 1. Philosophy & Karpathy Alignment

LLMWiki is a Python implementation of the LLM-Wiki pattern described in  
`Karpathy_concepts.md`. The idea: instead of re-discovering knowledge from raw  
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

1. **Ingestion is not just indexing.** Dropping a PDF triggers extraction +
  chunking + structured concept extraction + creation/update of summary and  
   concept pages + overview rewrite + git commit. The wiki *grows* with each  
   source.
2. **Wiki-first retrieval.** The chat agent reads curated wiki pages before
  touching raw chunks; raw-source FTS is a fallback, not the default.

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

## 3. Directory Structure

```
llmwiki/
├── base/
│   ├── config.py                       # pydantic-settings (.env)
│   └── domain/
│       ├── chat/
│       │   ├── agent.py                # create_agent() factory
│       │   ├── config.py               # _DEFAULT_SYSTEM_PROMPT + load_config()
│       │   ├── tools.py                # search_source_chunks (async, sources scope)
│       │   └── wiki_tools.py           # read_wiki_page, search_wiki_fts,
│       │                               #   file_to_wiki, save_to_wiki
│       ├── ingestion/
│       │   ├── pipeline.py             # ingest_file, scan_and_ingest,
│       │   │                           #   regenerate_wiki_pages
│       │   ├── batch.py                # batch_ingest
│       │   ├── chunker.py              # chunk_pages (FTS5 units)
│       │   ├── detector.py             # mtime + SHA-256 change detection
│       │   ├── extractor.py            # PDF/DOCX → list[(page, markdown)]
│       │   ├── index_manager.py        # wiki/index.md upsert (deterministic)
│       │   ├── pdf_extract.py          # opendataloader / mistral backends
│       │   └── wiki_generator.py       # all LLM prompt builders (see §6)
│       ├── lint/
│       │   ├── checks.py               # 6 check functions
│       │   ├── report.py               # LintIssue, LintReport
│       │   └── runner.py               # lint_wiki()
│       ├── repair/
│       │   ├── actions.py              # 6 repair functions
│       │   ├── report.py               # RepairResult, RepairReport
│       │   └── runner.py               # repair_wiki()
│       └── tools/
│           ├── db.py                   # open_db(), get_connection()
│           ├── git_ops.py              # init_wiki_repo, auto_commit
│           ├── references.py           # citation graph CRUD + queries
│           ├── search.py               # search_chunks() scoped FTS5
│           └── wiki_fs.py              # create/read/append/delete_page
├── marimo/
│   ├── ingest_app.py                   # Upload + ingest + scan + regenerate UI
│   ├── read_app.py                     # 3-pane reader + chat + save_to_wiki
│   ├── chat_app.py                     # Standalone chat tester
│   ├── widgets/
│   │   ├── __init__.py
│   │   └── delete_confirm.py           # DeleteConfirmWidget (anywidget) — reusable
│   └── prototypes/                     # Experimental patterns (not imported by apps)
├── shared/
│   └── sqlite_schema.sql               # Canonical schema (applied by open_db)
├── tests/
│   ├── conftest.py                     # sys.path + fixture registration
│   ├── helpers/{fake_llm.py,workspace.py}
│   ├── unit/                           # 125 unit tests (no LLM, no network)
│   └── e2e/                            # Playwright tests (live marimo + LLM)
├── docs/
│   ├── programmer_manual.md            # THIS FILE
│   └── archive/                        # Superseded design docs
├── Karpathy_concepts.md                # Foundational pattern reference
├── README.md                           # End-user quickstart
└── pyproject.toml + uv.lock
```

### Runtime workspace layout

```
workspace/                              # = $WIKI_PATH
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

## 4. Database Schema

**Location:** `workspace/.llmwiki/index.db`. Opened by `domain/tools/db.py:open_db()`,  
schema applied from `shared/sqlite_schema.sql` on first run. Uses  
`PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`.

### Tables

| Table                 | Purpose                                                                        |
| --------------------- | ------------------------------------------------------------------------------ |
| `workspace`           | Single row: workspace id, name, user_id                                        |
| `documents`           | Every file (`source_kind='source'` or `'wiki'`) with status, paths, hashes     |
| `document_pages`      | Raw page-by-page text extracted from sources (used by `regenerate_wiki_pages`) |
| `document_chunks`     | FTS5 units (~512 tokens, ~128 overlap)                                         |
| `chunks_fts`          | Virtual FTS5 table mirroring `document_chunks` via triggers                    |
| `document_references` | Citation graph edges (`reference_type` ∈ {`cites`, `links_to`})                |

### Notable `documents` columns

| Column                     | Values                                  | Meaning                                    |
| -------------------------- | --------------------------------------- | ------------------------------------------ |
| `source_kind`              | `'source'` / `'wiki'`                   | Raw PDF/DOCX vs LLM-generated markdown     |
| `status`                   | `'processing'` / `'ready'` / `'failed'` | Pipeline stage (see §10)                   |
| `path`                     | e.g. `/wiki/summaries/`                 | Directory path                             |
| `relative_path`            | UNIQUE                                  | Full path from workspace root — upsert key |
| `source_document_id`       | UUID or NULL                            | Summary pages point back to their source   |
| `content_hash`, `mtime_ns` | —                                       | Used by `detector.needs_ingestion`         |

### FTS5 tokenizer

`chunks_fts` uses `porter unicode61`, which **splits on hyphens**. Querying  
`"mortgage-backed"` is interpreted as `mortgage NOT backed`. Use plain terms in  
queries — `search.search_chunks` returns `[]` silently on malformed expressions.

---

## 5. Native Tool Layer

These functions are the CRUD primitives every other layer depends on. They know  
*how* to read/write the wiki and the DB; they do not know *why* or *when*.

| Module                | Key functions                                                                                                             | What it does                                                                                |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `tools/db.py`         | `open_db(path)`, `get_connection(path)`                                                                                   | Opens (and migrates) the SQLite DB; provides a context-manager connection                   |
| `tools/wiki_fs.py`    | `create_page`, `read_page`, `append_to_page`, `delete_page`                                                               | Disk + DB simultaneously (single source of truth — never bypass)                            |
| `tools/search.py`     | `search_chunks(db, query, limit, scope)`                                                                                  | FTS5 search; `scope ∈ {"all", "wiki", "sources"}`                                           |
| `tools/references.py` | `update_references`, `get_backlinks`, `get_forward_refs`, `find_orphan_pages`, `find_uncited_sources`, `find_stale_pages` | Parses `[[wikilinks]]` and `[^N]: file.pdf, p.3` citations; maintains `document_references` |
| `tools/git_ops.py`    | `init_wiki_repo`, `auto_commit`                                                                                           | Idempotent git init + silent commits                                                        |

Two structural notes:

- `wiki_fs.py` defers `from domain.ingestion.chunker import chunk_pages` inside  
`_insert_chunks()` to break a load-time circular import with `pipeline.py`.
- The citation parser uses `\s+[-–—]` (one *or more* spaces before the dash) so  
hyphenated filenames like `fed-paper.pdf` are not truncated to `fed`.

---

## 6. Workflows

Each workflow below follows the same template:

> **Status · Entry · Steps · LLM prompts (inline) · Triggers · Today vs Target · Verification**

### Quick-status table

| #    | Workflow           | Status | Entry                                                                | Pending                                                |
| ---- | ------------------ | ------ | -------------------------------------------------------------------- | ------------------------------------------------------ |
| 6.1  | Lint               | ✅      | `lint/runner.py:17`                                                  | `data_gap` shallow; `gap_filled_check` runs always; not auto-triggered yet (§11.11) |
| 6.2  | Repair             | ✅      | `repair/runner.py:30`                                                | All five deterministic repairs implemented             |
| 6.3  | Single ingest      | ✅      | `ingestion/pipeline.py:88`                                           | Lint+repair tail opt-in today (§11.11)                 |
| 6.4  | Batch ingest       | ✅      | `ingestion/batch.py:20`                                              | Lint+repair tail opt-in today (§11.11)                 |
| 6.5  | Scan sources       | ✅      | `ingestion/pipeline.py:340`                                          | Should chain into lint+repair (§11.11)                 |
| 6.6  | Regenerate         | ✅      | `ingestion/pipeline.py:379`                                          | Should chain into lint+repair (§11.8)                  |
| 6.7  | Chat / RAG         | ✅      | `chat/agent.py:12` + `chat/config.py:7`                              | Phases 1–3 (wiki + sources) complete; web search (Phase 4) is a future enhancement (§12) |
| 6.8  | Chat → Wiki        | ✅      | `chat/wiki_tools.py:87` (`file_to_wiki`) and `:168` (`save_to_wiki`) | Post-save lint+repair + cross-linking ✅; LLM-gated checks & bidirectional links deferred (§12) |
| 6.9  | Source deletion    | ✅      | `tools/deletion.py:11`                                               | —                                                      |
| 6.10 | Wiki page deletion | ✅      | `tools/wiki_fs.py:173` (`delete_page`)                               | —                                                     |

Every ingestion and save workflow shares one goal: **leave the wiki in an
internally consistent state.** The mechanism is the **lint → repair reconciliation
cycle**, documented first (§6.1–§6.2) because §6.3–§6.6 and §6.8 all converge on it.

**Mental model.** Ingestion does the reconciliation *inline* — it creates/updates
the concept and summary pages, rewrites `overview.md`, and updates the citation
graph and `index.md`. **Lint is the verification gate**: if ingestion did its job,
a follow-up lint should report *"no actions needed."* **Repair is the safety net**
for whatever lint still flags. The steady-state success criterion for any ingest
is therefore *"lint comes back clean."*

**Two-column convention.** Each workflow below is described as **Today** (what the
code does now) and **Target** (the intended end state, tracked in §11). The status
legend (✅ implemented · 🟡 partial · ❌ missing) still applies per workflow.

**Plan note (§11.11).** Today lint runs after ingest only when
`lint_after_ingest=True` (single) / `run_lint=True` (batch) — both default to
`False` — and repair has no UI trigger at all. The Target is for lint+repair to
**always** close every ingest, scan, and regenerate, with explicit "Run Lint" /
"Run Repair" buttons in `ingest_app.py`.

**Entry duality.** Single (§6.3) and batch (§6.4) ingestion can start either from
the GUI (upload widget) **or** by dropping files into `workspace/sources/` and
running Scan sources (§6.5).

---

### 6.1 Lint ✅

**Entry:** `lint_wiki()` — `base/domain/lint/runner.py:17`

Lint is the **verification gate** of the reconciliation cycle: it inspects the
wiki for internal-consistency defects and reports them, but changes nothing. In
the steady state — right after a successful ingest — lint should return *"no
actions needed."* A non-empty report means ingestion (or a manual edit, or a
deleted/modified source) left the wiki out of sync, which §6.2 Repair then fixes.

```python
report = lint_wiki(
    db_path,
    workspace,
    client=None,    # pass an LLM client to enable the LLM checks
    model="",
)
print(report.summary())             # "3 issue(s): 1 error, 2 warning"
for issue in report.issues: ...
```

**Six checks (`base/domain/lint/checks.py`):**

| Check             | Function (line)                | Type                            | Severity       | What it finds                                                                                  |
| ----------------- | ------------------------------ | ------------------------------- | -------------- | ---------------------------------------------------------------------------------------------- |
| `orphan`          | `orphan_check` (L12)           | deterministic                   | warning        | Concept pages with no inbound `links_to` edge                                                  |
| `stale`           | `staleness_check` (L35)        | deterministic                   | warning        | Wiki pages older than any of their cited sources (SQL `MAX(src.updated_at) > wiki.updated_at`) |
| `missing_xref`    | `missing_xref_check` (L73)     | deterministic                   | info           | Concept pairs that share a cited source but don't link to each other                           |
| `missing_concept` | `missing_concept_check` (L127) | deterministic                   | warning        | `[text](concepts/foo.md)` links to non-existent files (regex `_CONCEPT_LINK_RE` at L122)       |
| `contradiction`   | `contradiction_check` (L172)   | **LLM** (skip if `client=None`) | error          | Pair-wise LLM comparison of concepts sharing a source                                          |
| `data_gap`        | `data_gap_check` (L242)        | **LLM** (skip if `client=None`) | info / warning | LLM scan of all concept titles for missing/underdeveloped topics                               |

**LLM prompts (in `checks.py`):**

| Prompt                | Template                                                         | Input                                              | Output                                                  | Temperature |
| --------------------- | ---------------------------------------------------------------- | -------------------------------------------------- | ------------------------------------------------------- | ----------- |
| `contradiction_check` | `_CONTRADICTION_SYSTEM` (L155), `_CONTRADICTION_TEMPLATE` (L159) | path_a, content_a≤2000ch, path_b, content_b≤2000ch | `"CONTRADICTION: <desc>"` or `"NO CONTRADICTION"`       | 0.1         |
| `data_gap_check`      | `_GAP_TEMPLATE` (L232)                                           | bullet list of all concept titles                  | `"GAP: <topic> — <suggestion>"` per line or `"NO GAPS"` | 0.3         |

**Report shape (`lint/report.py`):**

```python
@dataclass
class LintIssue:
    check: str                # "orphan" | "stale" | "missing_xref" | "missing_concept" | "contradiction" | "data_gap" | "gap_filled"
    severity: str             # "error" | "warning" | "info"
    page: str                 # e.g. "/wiki/concepts/federal-reserve.md"
    description: str
    suggestion: str

@dataclass
class LintReport:
    issues: list[LintIssue]
    checked_at: str           # ISO timestamp
    # Properties: .errors, .warnings, .summary()
```

**Today:**

- `ingest_file(..., lint_after_ingest=True)` runs deterministic checks only (no
  LLM) and appends a one-line summary to `wiki/log.md`. Default is `False`.
- `batch_ingest(..., run_lint=True)` — same, once per batch. Default is `False`.
- Manual function call from a notebook cell.
- No "Run Lint" button in `ingest_app.py`.
- `data_gap_check` is intentionally shallow — it only sees titles (§11.7).

**Target:** lint runs automatically at the end of every ingest, scan, and
regenerate (not opt-in), plus an explicit "Run Lint" button. Deepen `data_gap`
beyond titles (§11.7). Tracked in §11.11.

---

### 6.2 Repair ✅

**Entry:** `repair_wiki()` — `base/domain/repair/runner.py:30`

Repair is the **safety net** of the cycle: it consumes a `LintReport` and applies
automatic fixes where it is safe to do so, skipping anything that needs human
judgement.

```python
lint_report = lint_wiki(db_path, workspace, client=llm_client, model=model)
repair_report = repair_wiki(
    lint_report, db_path, workspace,
    llm_client=llm_client, model=model, progress_cb=print,
)
print(repair_report.summary())   # "4 issue(s): 2 fixed, 1 skipped, 1 failed"
```

**Repair dispatch (`repair/actions.py`):**

| Issue type        | Function (line)                 | Action                                                                                                                                                             | Needs LLM                                                 | Status    |
| ----------------- | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------- | --------- |
| `orphan`          | `repair_orphan` (L30)           | Delete the orphan concept page (file + DB row + chunks + references)                                                                                               | No                                                        | ✅         |
| `stale`           | `repair_stale` (L55)            | Reload page text from `document_pages` → re-run `extract_structured` + `build_summary_page` → `create_page(overwrite=True)` → `update_references`                  | Yes                                                       | ✅         |
| `missing_xref`    | `repair_missing_xref`           | Append `## See also` bullet linking A→B; call `update_references` to record the `links_to` edge. Idempotent.                                                        | No                                                        | ✅         |
| `missing_concept` | `repair_missing_concept`        | Parse filename out of the issue description; gather context via `search_chunks`; LLM writes new concept page; `create_page` + `update_references` + `update_index` | Yes (inline f-string prompt, temperature 0.3)             | ✅         |
| `contradiction`   | `repair_contradiction`          | Append idempotent `<!-- CONTRADICTION: path_b -->` + `⚠️` callout to page A; call `update_references`. Needs a human to resolve; repair only flags.                | No                                                        | ✅         |
| `data_gap`        | `repair_data_gap`               | Insert `<!-- DATA_GAP: slug -->` TODO note into the most-related wiki page (FTS host selection). Skips if topic already covered by a source.                        | No                                                        | ✅         |
| `gap_filled`      | `repair_gap_filled`             | Replace DATA_GAP block with `> ℹ️ See [Title](rel).` link; `create_page(overwrite=True)` + `update_references`. Fires when a topic's source is ingested.           | No                                                        | ✅         |

LLM-dependent repairs (`stale`, `missing_concept`) are automatically skipped when
`llm_client=None`.

**Report shape (`repair/report.py`):**

```python
@dataclass
class RepairResult:
    check: str            # original lint check
    action: str           # "deleted_orphan" | "regenerated" | "created" | "skipped"
    page: str
    success: bool
    message: str

@dataclass
class RepairReport:
    results: list[RepairResult]
    repaired_at: str
    # Properties: .fixed, .skipped, .failed, .summary()
```

**Today:** manual function call only; no "Run Repair" button. All five
deterministic repair types are implemented. Already auto-runs after chat→wiki
save (§6.8).

**Target:** repair runs automatically after lint at the end of every ingest,
scan, and regenerate, with an explicit "Run Repair" button. Tracked in §11.11.

**Verification:** `tests/unit/test_repair_*.py`.

---

### 6.3 Single-document ingestion ✅

**Entry:** `ingest_file()` — `base/domain/ingestion/pipeline.py:88`

```python
result = ingest_file(
    file_path,          # Path to PDF or DOCX
    db_path,            # str path to index.db
    workspace,          # Path to workspace root
    llm_client,         # OpenAI-compatible client
    model,              # e.g. "anthropic/claude-haiku-4-5"
    progress_cb=None,        # optional callable(str)
    lint_after_ingest=False, # run deterministic lint after step 12
    _batch_mode=False,       # internal — suppresses steps 10-13
)
# IngestResult(file_path, status="ingested"|"skipped"|"failed", message, doc_id)
```

**Pipeline steps:**

| #     | Step                                                                                                      | Where                                               |
| ----- | --------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| 1     | Validate file (exists, supported extension)                                                               | `pipeline.py`                                       |
| 2     | Hash + mtime change detection                                                                             | `detector.py:needs_ingestion`                       |
| 3     | Extract `(page_number, markdown)` pairs                                                                   | `extractor.py:extract` (PDF / DOCX-via-LibreOffice) |
| 4     | Chunk pages into FTS5 units                                                                               | `chunker.py:chunk_pages`                            |
| 5     | Atomic DB write: `documents` + `document_pages` + `document_chunks`                                       | `pipeline.py`                                       |
| **6** | **Commit `status='ready'**` — readers can now see the source                                              | `pipeline.py`                                       |
| 7     | LLM: structured extraction → `ExtractionResult(document_summary, concepts[])`                             | `wiki_generator.py:extract_structured` (line 189)   |
| 8     | For each concept: build page (LLM) → `create_page(overwrite=True)` → `update_references` → `update_index` | `wiki_generator.build_concept_page` (270)           |
| 9     | Build summary page (deterministic) → `create_page` with `source_document_id`                              | `wiki_generator.build_summary_page` (238)           |
| 10    | LLM: rewrite `wiki/overview.md`                                                                           | `wiki_generator.update_overview` (304)              |
| 11    | Append `## [date] Ingested | filename` to `wiki/log.md`                                                   | `wiki_fs.append_to_page`                            |
| 12    | `auto_commit("ingest: ...")`                                                                              | `git_ops.auto_commit`                               |
| 13    | Optional deterministic lint pass (if `lint_after_ingest=True`)                                            | `lint/runner.lint_wiki`                             |

**LLM prompts used (all in `base/domain/ingestion/wiki_generator.py`):**

| Prompt               | Template constants                                                                             | Inputs                                                             | Output                                                               | Temperature |
| -------------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ | -------------------------------------------------------------------- | ----------- |
| `extract_structured` | `_EXTRACT_SYSTEM` (L78), `_EXTRACT_USER_TEMPLATE` (L85)                                        | filename, file_type, page_count, content ≤80 KB                    | JSON `{document_summary, concepts:[{name,category,insight}]}`        | 0.2         |
| `build_concept_page` | `_CONCEPT_SYSTEM` (L109) + `_CONCEPT_NEW_TEMPLATE` (L114) OR `_CONCEPT_UPDATE_TEMPLATE` (L143) | concept name/category/insight, filename, existing content (if any) | Markdown w/ frontmatter + Definition/Characteristics/Context/Sources | 0.3         |
| `update_overview`    | `_OVERVIEW_SYSTEM` (L156), `_OVERVIEW_TEMPLATE` (L160)                                         | current overview, new summary, all concept names                   | 3–5 paragraph narrative                                              | 0.4         |

> The legacy single-shot `build_wiki_page` (L331) is kept for backward  
> compatibility but is no longer on the ingest path.

**Triggers:**

- Marimo button "⚙️ Ingest uploaded file" in `ingest_app.py:190` (`ingest_btn`).
- Directly callable as a Python function.

**Today vs Target:**

- **Coverage.** A single ingest already creates *both* concept pages (step 8) and
  the 1-to-1 summary page (step 9) — not just the summary.
- **Reconciliation tail (steps 10–13).** Today the only wiki-wide reconciliation
  after a single ingest is the `overview.md` rewrite (step 10); a lint pass runs
  only if `lint_after_ingest=True` (default `False`), and repair never runs.
  **Target:** steps 10–13 always close with lint **and** repair, so the new
  concept/summary pages get cross-linked into existing pages and lint comes back
  clean (§11.11).
- **Duplicate handling.** Today an unchanged file returns `status="skipped"`
  silently (`detector.needs_ingestion`). **Target:** the GUI warns "already
  ingested" rather than skipping quietly (§11.13).

`status='ready'` is set at step 6 (before the LLM work in steps 7–9), see §10.

**Verification:**

```bash
HEADLESS=1 uv run pytest tests/e2e/test_ingest_app.py -v -s
uv run pytest tests/unit/test_pipeline_phase2.py -v
```

---

### 6.4 Batch / multi-document ingestion ✅

**Entry:** `batch_ingest()` — `base/domain/ingestion/batch.py:20`

```python
results = batch_ingest(
    files=[Path("sources/doc1.pdf"), Path("sources/doc2.pdf")],
    db_path=db_path,
    workspace=workspace,
    llm_client=client,
    model=model,
    progress_cb=None,
    run_lint=False,     # run deterministic lint once at the end
)
```

**Difference from 6.3:** each file goes through steps 1–9 of `ingest_file`  
with `_batch_mode=True` (set at `batch.py:63`), which suppresses steps 10–13.  
Then at the end of the batch the wrapper does **once**:

1. `update_overview()` with all new summaries combined.
2. Single `wiki/log.md` entry: `## [date] Batch ingested | N file(s)`.
3. Single `auto_commit("batch ingest: N file(s)")`.
4. Optional `lint_wiki()` deterministic pass.

**LLM call count for N files, K concepts/file:**

- `batch_ingest`: `N × (1 extract + K concepts) + 1 overview`
- `ingest_file` × N: `N × (1 extract + K concepts + 1 overview)`

Concept pages compound naturally across the batch — the second file's mention  
of an existing concept hits the `_CONCEPT_UPDATE_TEMPLATE` branch in step 8.

**Today vs Target:**

- **Reconciliation tail.** Today the batch ends with one `overview.md` rewrite and
  one optional deterministic lint pass (`run_lint=True`, default `False`); repair
  never runs. **Target:** the batch closes with a single lint **and** repair pass
  over the whole wiki — *including the pages just created in this batch* — so newly
  related concepts get cross-linked and lint comes back clean (§11.11).
- **Duplicate handling.** Like §6.3, unchanged files are skipped silently today;
  **Target** warns when any uploaded file is already ingested (§11.13).

**Triggers:** currently invoked through `ingest_app.py` "Ingest" button when  
multiple files are uploaded (the underlying widget supports multi-select).

**Verification:** `tests/unit/test_batch_ingest.py` — 9 tests, including a key  
assertion `len(llm.calls) == 5` for a 2-file batch (extract×2 + concept×2 +  
overview×1) which proves the overview is *not* called per file.

---

### 6.5 Scan sources folder ✅

**Entry:** `scan_and_ingest()` — `base/domain/ingestion/pipeline.py:340`

Walks `workspace/sources/` recursively, collects `.pdf` / `.docx` files  
(case-insensitive, skipping hidden entries), and calls `ingest_file()` for each.  
Unchanged files (detected by `detector.needs_ingestion` — mtime then hash)  
return `status="skipped"` without re-running the LLM.

**Why it exists:** lets you drop several files into `sources/` from outside the  
UI (Finder, `cp`, `obsidian-clipper`, etc.) and then re-ingest only the new or  
modified ones in one click. The pipeline does NOT run as a daemon — it scans  
on demand.

**Distinction from 6.4:** `scan_and_ingest` discovers files automatically,  
`batch_ingest` takes an explicit list. Internally `scan_and_ingest` calls  
`ingest_file` *sequentially* (not in batch mode) — so each scanned file still  
triggers an overview rewrite and a git commit. If you scan many files, prefer  
calling `batch_ingest(files=list(...))` directly.

**Triggers:** Marimo button "🔄 Scan sources" in `ingest_app.py:194`  
(`scan_btn`).

**Scan vs lint+repair (important).** Scanning is *source→wiki freshness*: it
detects new or modified **source files** and ingests them. Lint+repair is
*wiki→wiki consistency* (orphans, stale pages, missing cross-refs) — the two are
orthogonal (see the architecture diagram in §2). They meet at one point: a
*modified* source makes its dependent wiki pages **stale**, which the `stale`
lint check (§6.1) flags and `repair_stale` (§6.2) regenerates. So "scan sources
and update the wiki accordingly" = **scan to ingest the changed sources, then run
lint+repair to bring the wiki back into a consistent state.**

**Today vs Target:**

- **Today:** scan only ingests new/modified sources (each via `ingest_file`); it
  does **not** run lint+repair afterwards, so stale dependents are detected but
  not fixed in the same pass. The per-file overview rewrite is also wasteful for
  large scans — treat scan as "pick up the one or two files I dropped"; for bulk
  imports call `batch_ingest` directly (§11.9).
- **Target:** scan closes with a single lint **and** repair pass so a modified
  source's stale pages are regenerated automatically and lint comes back clean
  (§11.11).

---

### 6.6 Regenerate wiki pages ✅

**Entry:** `regenerate_wiki_pages()` — `base/domain/ingestion/pipeline.py:379`

Iterates over every `documents` row with `source_kind='source'` and  
`status='ready'`, reloads the cached page text from `document_pages` (no PDF  
re-extraction), and re-runs:

1. `extract_structured()` (LLM)
2. `build_summary_page()` (deterministic)
3. `create_page(..., overwrite=True)` → `update_references` → `update_index`

**Use cases:**

- LLM model changed.
- Prompt templates in `wiki_generator.py` were refined.
- A wiki page was accidentally deleted from disk.

**Triggers:** Marimo button "🤖 Regenerate wiki" in `ingest_app.py:198`  
(`regen_btn`).

**Today vs Target:**

- **Today:** regenerate refreshes **only the summary pages** — it does NOT rebuild
  concept pages, NOR `overview.md`, NOR run lint/repair afterwards. Failed/processing
  sources are skipped silently.
- **Target:** regenerate rebuilds concept pages and overview too, then closes with
  a lint **and** repair pass so a regenerate never leaves stale concept/overview
  pages behind and lint comes back clean (§11.8, §11.11).

---

### 6.7 Query / Chat (multi-phase RAG) ✅

**Entry:** `create_agent()` — `base/domain/chat/agent.py:12`, paired with  
the system prompt in `base/domain/chat/config.py:7` (`_DEFAULT_SYSTEM_PROMPT`).

This is the most important section for understanding *how answers are*  
*generated*. The routing is **prompt-driven, not code-driven** — there is no  
Python `if`/`else` deciding which tool to call. The LLM reads the system  
prompt and picks tools accordingly. The code just provides the toolbox.

**Core principle — wiki-first.** The agent answers from the **curated wiki pages**
(the Encyclopedia) wherever possible, and only drops to the raw `document_chunks`
(the Filing Cabinet, via `search_source_chunks`) when the wiki pages don't contain
enough detail. The wiki is the default context; the DB is the fallback. This is
the whole point of the LLM-Wiki pattern (§1) — re-reading curated pages is cheaper
and higher-signal than re-deriving knowledge from raw chunks on every query.

#### Tool inventory

| Tool                                     | Module:fn                  | Scope                  | When the agent calls it                   |
| ---------------------------------------- | -------------------------- | ---------------------- | ----------------------------------------- |
| `read_wiki_page(path)`                   | `chat/wiki_tools.py:24`    | single file            | Direct page lookup by known path          |
| `search_wiki_fts(query, limit=10)`       | `chat/wiki_tools.py:47`    | `source_kind='wiki'`   | Topic discovery across all wiki pages     |
| `file_to_wiki(title, content, category)` | `chat/wiki_tools.py:87`    | write                  | Persist a synthesis (see §6.8)            |
| `search_source_chunks(query, limit=10)`  | `chat/tools.py:10` (async) | `source_kind='source'` | Last-resort lookup into raw PDFs/DOCXs    |
| Web search                               | —                          | —                      | ❌ **NOT YET IMPLEMENTED** (Pending §11.5) |

The agent receives `db_path` as `deps_type=str`. Every tool derives the  
workspace from it via `workspace = Path(db_path).parent.parent` (because the  
DB is always at `workspace/.llmwiki/index.db`).

#### Routing rules (from `_DEFAULT_SYSTEM_PROMPT`)

The intended retrieval flow is staged — *wiki first, raw sources second, web*  
*search last*:

1. **Phase 1 — Try the index.** Call `read_wiki_page("wiki/index.md")`. If
  missing or empty, do not conclude the wiki is empty; continue.
2. **Phase 2 — Search the wiki.** Call `search_wiki_fts` with the question's
  key terms. Optionally `read_wiki_page` on likely paths  
   (`wiki/concepts/xyz.md`, `wiki/summaries/xyz.md`). This step always runs.
3. **Phase 3 — Fall back to raw sources.** Only call `search_source_chunks`
  when the wiki results don't contain enough detail.
4. **Phase 4 — Web search.** Only when phases 1–3 returned nothing useful.
  **Not implemented yet** — track in §11.5 and §11.6.
5. **Capture.** When the agent produces a comparison/analysis/summary worth
  keeping, call `file_to_wiki` (§6.8).

**Output guidelines (also in the prompt):** cite source + page for facts; use  
tables for comparisons, bullets for enumerations; "no information found" is  
only allowed after both `search_wiki_fts` and `search_source_chunks` were  
tried.

#### Customising per workspace

`workspace/wiki_config.toml`:

```toml
[assistant]
system_prompt = """
You are a specialist in mortgage-backed securities...
"""
suggested_prompts = [
    "What is a CDO?",
    "Compare MBS and ABS",
]
```

Loaded by `chat/config.py:load_config()` at agent creation time; falls back to  
the defaults if absent.

**Triggers:** the right panel chat in `marimo/read_app.py` (`chat_panel`  
cell at L174). The agent streams responses via `Agent.iter_stream(...)`.

**Gaps:**

- **Web search (Phase 4) is intentionally deferred** — see §12 for the
  rationale. Phases 1–3 (wiki index → wiki FTS → raw source chunks) are fully
  implemented and cover the project's core thesis: answer from your own curated
  corpus. Phase 4 is the only workflow that reaches outside it.
- The "always check index first" rule is advisory — there's no programmatic  
guarantee the LLM does it. Track regressions via the E2E suite.
- Phases are not numbered explicitly in the prompt today; tightening them to
  "Phase 1 / Phase 2 / Phase 3" labels would only matter once Phase 4 lands (§12).

---

### 6.8 Chat → Wiki (`file_to_wiki`) ✅

**Entry:**

- Agent tool: `file_to_wiki()` — `base/domain/chat/wiki_tools.py:87`
- UI-direct (no `RunContext`): `save_to_wiki()` — `base/domain/chat/wiki_tools.py:168`

```python
# Agent tool — called by PydanticAI via RunContext
file_to_wiki(ctx, title="Yield Curve Analysis", content="...", category="concept")

# UI helper — called directly from read_app.py:save_action
save_to_wiki(
    db_path, workspace, title, content, category,
    client=llm_client,   # OpenAI-compatible client; if None, built from config
    model=settings.LLM_MODEL,
)
```

Both share identical logic:

1. Slugify the title with `wiki_generator.make_wiki_slug` (NFKD-normalised — diacritics stripped, so "Política Común" → `politica-comun`).
2. Pick directory by category: `concept` → `/wiki/concepts/`, `summary` → `/wiki/summaries/`.
3. Read existing page content (if any) via `wiki_fs.read_page`.
4. **LLM structuring pass** — call `wiki_generator.structure_chat_content(title, category, raw_content, existing, client, model)`. Returns properly structured markdown (YAML frontmatter + Definition / Key Characteristics / Context / Sources).
5. Write with `create_page(overwrite=True)` if the page existed (LLM merge); `create_page(overwrite=False)` if new.
6. Look up the doc id and call `references.update_references` to keep the citation graph in sync.
7. Derive a one-line summary from the first heading and call `index_manager.update_index`.
8. Return `"Updated wiki page: wiki/concepts/foo.md"` or `"Created wiki page: ..."`.

**LLM prompts used (all in `wiki_generator.py`):**

| Prompt                              | Template constants                                         | Inputs                            | Output                                                               | Temperature |
| ----------------------------------- | ---------------------------------------------------------- | --------------------------------- | -------------------------------------------------------------------- | ----------- |
| `structure_chat_content` (new page) | `_CONCEPT_SYSTEM` (L109) + `_CHAT_CONCEPT_NEW_TEMPLATE`    | title, category, raw content      | Markdown w/ frontmatter + Definition/Characteristics/Context/Sources | 0.3         |
| `structure_chat_content` (update)   | `_CONCEPT_SYSTEM` (L109) + `_CHAT_CONCEPT_UPDATE_TEMPLATE` | title, raw content, existing page | Merged markdown, no duplication                                      | 0.3         |

`**file_to_wiki` — client injection:** builds `openai.OpenAI` lazily from  
`config.settings` (`WIKI_LLM_*` falling back to `LLM_*`). Keeps `deps_type=str`  
(db path) unchanged so existing RunContext mocks in tests remain unaffected.

`**save_to_wiki` — client injection:** optional keyword-only `client=None, model=None`; builds from settings when omitted, allowing tests to inject  
`FakeLLMClient` directly.

**Triggers:**

- Agent-decided when the chat produces something worth keeping (governed by  
the system prompt's "Capture" rule).
- Manual save form in `read_app.py:209` (`save_form` cell) → `save_action`  
cell at ~L235 calls `save_to_wiki` with an explicit client built from  
`settings.LLM_*`.

**Today:**

This is the **reference implementation of the reconciliation cycle** the ingest
workflows are moving toward — it already closes with lint+repair. When the user
saves a chat reply, the LLM structures it into a proper page (step 4),
`create_page` adds it to the wiki, and then:

- ✅ **Post-save lint+repair**. `_lint_and_repair_after_save` in
  `chat/wiki_tools.py` runs a deterministic lint scoped to the saved page and
  feeds fixable issues to `repair_wiki`. The `orphan` check is excluded so the
  just-created page is never auto-deleted; a `🔧 Post-save repair: …` line is
  appended to the save confirmation.
- ✅ **Cross-linking on save**. Since the three formerly-skipped repairs are now
  implemented (§6.2), a saved page that shares a cited source with an existing
  page is automatically cross-linked (`repair_missing_xref` adds a `## See also`
  link and records the `links_to` edge). Verified end-to-end by
  `tests/unit/test_lint_repair_after_save.py::test_save_to_wiki_auto_cross_links_shared_source`.

**Two known limitations (both deferred to §12, acceptable for a PoC):**

1. *Cross-linking is directional.* `missing_xref_check` emits one issue per pair,
   keyed on `path_a` (the page whose id sorts lower). The post-save filter only
   acts when the saved page is `path_a`; otherwise the link is added on the next
   full lint+repair, not on save.
2. *LLM-gated checks don't run on save.* The post-save lint is intentionally
   called **without** an LLM client, so `contradiction` and `data_gap` (both
   LLM-powered checks) never fire on save — only the deterministic checks
   (`missing_xref`, `missing_concept`, `stale`, `gap_filled`) do. This keeps save
   latency and cost low.

---

### 6.9 Source deletion ✅

**Entry:** `delete_source()` — `base/domain/tools/deletion.py:11`

```python
delete_source(db_path, workspace, doc_id, *, also_delete_file=False)
# -> RepairResult(action="deleted" | "failed", ...)
```

Removes the source `documents` row; FK `ON DELETE CASCADE` automatically cleans  
up `document_pages`, `document_chunks`, `chunks_fts` (via triggers), and  
`document_references`. Before the cascade, wiki pages that cited the source are  
marked `stale_since = now()` so the lint+repair cycle can prompt regeneration.  
File removal is opt-in (`also_delete_file=True`). Calls  
`auto_commit(workspace, "delete source: {filename}")` on success.

UI: "🗑 Delete Source" section at the bottom of `marimo/ingest_app.py` —  
dropdown of indexed sources, a confirmation checkbox, and an optional  
"also remove file from sources/" checkbox. The `delete_runner` cell mirrors the  
`ingest_runner` / `scan_runner` trigger pattern.

---

### 6.10 Wiki page deletion ✅

**Entry:** `delete_page()` — `base/domain/tools/wiki_fs.py:173`

```python
delete_page(db_path, workspace, dir_path="/wiki/concepts/", slug="snow-white")
# True if page existed; removes file, DB row, chunks, references,
# and strips dead links from all pages that referenced it.
```

What `delete_page` cleans up atomically:

| Layer                 | What is removed                                                                       |
| --------------------- | ------------------------------------------------------------------------------------- |
| Disk                  | The `.md` file                                                                        |
| `documents`           | The row for this page                                                                 |
| `document_chunks`     | All FTS5 units (and via trigger, `chunks_fts`)                                        |
| `document_references` | All edges where this page is source **or** target                                     |
| Other wiki pages      | Inline markdown links to this page are rewritten to plain text by `_strip_dead_links` |

**Triggers:**

- `repair_orphan` (§6.2) — automatic repair path.
- `read_app.py` — `delete_widget_cell` + `delete_event_cell` (see §7).

**Gaps:** none.

---

## 7. Marimo Apps

Both apps live in `marimo/` and are self-contained `uv` scripts — the  
script header declares their dependencies inline. They share no global state.

### `ingest_app.py`

Cells (selected — see source for the full list):

| Cell                 | Purpose                                                                         |
| -------------------- | ------------------------------------------------------------------------------- |
| `setup`              | `.env` + paths + open SQLite                                                    |
| `llm_setup`          | Build `openai.OpenAI` from `settings.wiki_*`                                    |
| `source_uploader`    | `mo.ui.file(filetypes=[".pdf",".docx"])` → saves to `sources/`                  |
| `ingest_btn` (L190)  | "⚙️ Ingest uploaded file(s)" → `ingest_file` (or `batch_ingest` for multi-file) |
| `scan_btn` (L194)    | "🔄 Scan sources" → `scan_and_ingest`                                           |
| `regen_btn` (L198)   | "🤖 Regenerate wiki" → `regenerate_wiki_pages`                                  |
| `clear_btn` (L202)   | Resets the live progress log                                                    |
| `progress_display`   | Accumulates `progress_cb(message)` lines                                        |
| `debug_panel` (L330) | Visible when `WIKI_DEBUG=1`                                                     |

### `read_app.py`

Three-column grid:

| Pane                  | Cell                       | Role                                                                                                   |
| --------------------- | -------------------------- | ------------------------------------------------------------------------------------------------------ |
| Left                  | `left_panel`               | Page selector with refresh button (`scan_pages()`)                                                     |
| Left (below selector) | `delete_widget_cell`       | Renders `DeleteConfirmWidget` — disabled when no page is selected; resets event counter on page change |
| — (logic only)        | `delete_event_cell`        | Watches `delete_widget.event_id`; fires `set_delete_trigger` on confirm                                |
| — (logic only)        | `delete_runner`            | Calls `wiki_fs.delete_page`, rescans page list, clears selection                                       |
| Middle                | `middle_panel`             | Renders the selected page as markdown + nav links                                                      |
| Right                 | `chat_panel`               | PydanticAI agent stream + suggested prompts                                                            |
| Right (below chat)    | `save_form`, `save_action` | Saves the last assistant reply to the wiki via `save_to_wiki` with LLM structuring pass                |

#### `DeleteConfirmWidget` (`marimo/widgets/delete_confirm.py`)

An `anywidget.AnyWidget` subclass — the delete button and its confirmation  
panel are a single self-contained JS/CSS widget. Show/hide is handled entirely  
in the JS layer so marimo's reactive execution model does not interfere.

| Trait          | Type | Default | Purpose                                                                     |
| -------------- | ---- | ------- | --------------------------------------------------------------------------- |
| `label`        | str  | `""`    | Item name used in default button text and panel message                     |
| `button_label` | str  | `""`    | Override trigger button text (empty → `"Delete {label}"`)                   |
| `message`      | str  | `""`    | Override panel message (empty → `"Delete {label}? This cannot be undone."`) |
| `disabled`     | bool | `False` | Grays out and disables the trigger button                                   |
| `is_open`      | bool | `False` | Whether the confirmation panel is visible (managed by JS)                   |
| `event_id`     | int  | `0`     | Increments on each confirmed deletion — the Python signal                   |

Usage pattern in any marimo app:

```python
# Cell A — render widget
widget = mo.ui.anywidget(DeleteConfirmWidget(label=item_name, disabled=not item_name))
widget
return widget, item_name

# Cell B — react to confirmation (separate state tracks last handled event)
if widget.event_id > last_event() and item_name:
    set_last_event(widget.event_id)
    do_deletion(item_name)
```

`marimo` is added to `sys.path` in `read_app.py`'s setup block so  
`from widgets.delete_confirm import DeleteConfirmWidget` resolves correctly.

`scan_pages()` uses `wiki_dir.rglob("*.md")` and returns paths relative to  
`wiki/` (e.g. `concepts/federal-reserve`, `summaries/my-doc`, `index`), so all  
subdirectory pages appear in the left-panel table. `read_page(rel_path)` reads  
`wiki/{rel_path}.md`. The title display strips the directory prefix with  
`.rsplit("/", 1)[-1]`.

The agent is created once per session via `create_agent(db_path)` and reused  
across messages.

### Running locally

```bash
# Against $WIKI_PATH from .env
uv run marimo run marimo/ingest_app.py --port 2718
uv run marimo run marimo/read_app.py --port 2720

# Against a specific workspace
WIKI_PATH=/path/to/workspace uv run marimo run marimo/read_app.py --port 2720
```

---

## 8. Configuration

### `.env` (loaded by `base/config.py` via `pydantic-settings`)

```ini
WIKI_PATH=/path/to/workspace
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=sk-or-...
LLM_MODEL=anthropic/claude-haiku-4-5

# Optional override for ingestion-time LLM (falls back to LLM_* if blank)
WIKI_LLM_BASE_URL=
WIKI_LLM_API_KEY=
WIKI_LLM_MODEL=

# PDF backend
PDF_BACKEND=opendataloader     # or "mistral"
MISTRAL_API_KEY=
```

### `workspace/wiki_config.toml` (optional, per-workspace)

Overrides the chat assistant's system prompt and suggested prompts. See §6.7  
for an example. Absent file → defaults from `chat/config.py` are used.

### Environment flags

| Flag           | Effect                                                         |
| -------------- | -------------------------------------------------------------- |
| `WIKI_DEBUG=1` | Shows debug panel in `ingest_app.py`                           |
| `HEADLESS=1`   | Used by the E2E test suite for non-interactive Playwright runs |

---

## 9. Testing

### Run

```bash
uv run pytest tests/unit/ -v               # 125 unit tests — fast, no LLM
uv run pytest tests/e2e/ -v -s             # 9 E2E tests — live marimo + LLM
```

Slash commands: `/test-ingest`, `/test-read`, `/test-all`.

### Unit infrastructure

`**FakeLLMClient**` (`tests/helpers/fake_llm.py`) duck-types the OpenAI client.  
Configure responses before each test:

```python
llm = FakeLLMClient(response_content="## Fixed response")

# Sequential multi-step pipelines
llm.responses = ["JSON extraction", "Concept page", "Overview text"]
# Call index advances automatically; last response repeats if exhausted.

assert len(llm.calls) == 3
```

`**tmp_workspace**` (`tests/helpers/workspace.py`) yields a fresh disposable  
workspace per test:

```python
def test_something(tmp_workspace: WorkspaceFixture) -> None:
    # .workspace  — Path to temp workspace root
    # .db_path    — str path to index.db (schema applied)
    # .llm        — FakeLLMClient instance
```

**Mock RunContext** for PydanticAI tool tests:

```python
class _Ctx:
    def __init__(self, deps): self.deps = deps

ctx = _Ctx(tmp_workspace.db_path)
result = read_wiki_page(ctx, "wiki/index.md")
```

### E2E infrastructure

Uses `async_playwright` (the test runner lives inside an asyncio loop — anyio  
4.x). Configured in `pytest.ini`:

```ini
asyncio_mode = auto
asyncio_default_fixture_loop_scope = session
```

**Two-phase wait pattern** (because `status='ready'` fires at step 6, before  
the wiki page is created at step 9):

```python
wait_for_ingestion(filename)             # source status='ready'
src = assert_source_ok(filename)
wait_for_wiki_page(src["id"], filename)  # wiki page actually exists in DB
assert_wiki_ok(src["id"], filename)
```

---

## 10. Known Constraints & Gotchas

- **FTS5 hyphens.** The porter unicode61 tokenizer splits hyphens.  
`"mortgage-backed"` is two tokens. Use plain terms.
- `**status='ready'` fires early.** Set at step 6 (after chunking), before the  
LLM work in steps 7–9. Polling only for `status='ready'` misses the wiki  
page creation. Always add a second poll for the wiki page in tests.
- **Circular import.** `wiki_fs.py` imports `chunker.py` inside  
`_insert_chunks()` (deferred) to prevent a load-time cycle with  
`pipeline.py`.
- **Marimo reactivity.** `ingest_file()` runs synchronously inside a marimo  
cell. Marimo re-runs dependent cells reactively *after* the cell completes,  
not during. Cells are not async unless explicitly written as `async def`.
- `**source_document_id` is set only for summary pages**, not concepts —  
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

## 11. Pending Work / Roadmap

All §6 workflows are now ✅. The items below are incremental improvements to
those working workflows (auto-triggering lint+repair, UI buttons, duplicate
warnings, etc.); completed items are marked ✅. Larger out-of-scope features live
in §12.

1. ✅ **LLM structuring pass on chat-to-wiki save** (§6.8). Implemented:
  `wiki_generator.structure_chat_content()` with `_CHAT_CONCEPT_NEW_TEMPLATE`  
   / `_CHAT_CONCEPT_UPDATE_TEMPLATE` (both use `_CONCEPT_SYSTEM`). Applied in  
   `file_to_wiki` and `save_to_wiki`; `create_page(overwrite=True)` replaces  
   blind `append_to_page` for existing pages. `save_to_wiki` accepts optional  
   `client`/`model` keyword args. `make_wiki_slug` now normalises diacritics  
   via NFKD.
2. ✅ **Chat→Wiki post-save lint+repair trigger** (§6.8). Implemented:
  `_lint_and_repair_after_save(db_path, workspace, page_path, client, model)`  
   in `chat/wiki_tools.py`. Runs deterministic `lint_wiki`, filters issues to  
   the saved page, excludes the `orphan` check (would delete the new page), then  
   calls `repair_wiki`. Both `save_to_wiki` and `file_to_wiki` append a  
   `🔧 Post-save repair: …` line to their return message when repairs occur.
3. ✅ **Source deletion** (§6.9). `delete_source(db_path, workspace, doc_id, ...)` in
  `tools/deletion.py`. FK cascade cleans up chunks, references, and FTS; dependent  
   wiki pages marked `stale_since`. UI: dropdown + confirm checkbox + `delete_runner`  
   cell in `marimo/ingest_app.py`.
4. ✅ **Wiki page deletion UI button** (§6.10). `DeleteConfirmWidget`
  (`marimo/widgets/delete_confirm.py`) — anywidget-based delete button  
   with inline JS confirmation panel. Wired into `read_app.py` via  
   `delete_widget_cell` + `delete_event_cell`. Dead-link cleanup and full DB  
   teardown happen inside `wiki_fs.delete_page` automatically.
5. **Web search tool for the chat agent** (§6.7). New async tool alongside
  `search_wiki_fts` / `search_source_chunks`, backed by a search API.  
   **Intentionally deferred for the PoC — rationale and revisit plan in §12.**
6. **Explicit multi-phase RAG labels in the agent system prompt**
  (`chat/config.py`). Rename the prompt sections to "Phase 1 / … / Phase 4"  
   for clearer routing. Only meaningful once §11.5 lands — deferred with it (§12).
7. **Deepen & promote `data_gap`** (§6.1–§6.2). The `data_gap` lint check is
  shallow — it only sees concept *titles* — and its repair is a stub. Deepen the
  check to read page bodies, and implement the repair to suggest specific web  
   searches or sub-questions for each gap (`repair/actions.py:254`).
8. `**regenerate_wiki_pages` should auto-run lint+repair** afterwards
  (§6.6) so a regenerate doesn't leave stale concept/overview pages. Subsumed by
   §11.11.
9. **Grid column for wiki page title** in `read_app.py:left_panel` — show
  slug *and* extracted title.
10. **Document `scan_and_ingest` precisely** for end users (§6.5 — what it
  touches, when to prefer `batch_ingest` instead).
11. **Lint+repair always close every ingest, scan, and regenerate** (§6.1–§6.6).
  Today lint is opt-in (`lint_after_ingest` / `run_lint`, both default `False`)  
   and repair has no automatic trigger outside chat→wiki save (§6.8). Make every  
   ingest/scan/regenerate end with a lint **and** repair pass so the wiki is left  
   consistent and a follow-up lint reports "no actions needed", and add explicit  
   "Run Lint" / "Run Repair" buttons to `ingest_app.py`.
12. ✅ **Finish the skipped repairs** (§6.2). Implemented `repair_missing_xref`
  (appends `## See also` + records `links_to` edge), `repair_contradiction`  
   (idempotent `⚠️` callout), and `repair_data_gap` (inserts `<!-- DATA_GAP -->`  
   TODO note), plus a new `gap_filled` check+repair that replaces a resolved TODO  
   note with a link. All deterministic; see `repair/actions.py` and  
   `tests/unit/test_repair_finish.py`.
13. **Warn on duplicate upload** (§6.3–§6.4). When an uploaded or dropped file is
  already ingested and unchanged, surface a GUI warning instead of the current  
   silent `status="skipped"`.

---

## 12. Future Enhancements

Aspirational features from `Karpathy_concepts.md` not yet on the roadmap:

- **Two-step HITL ingestion.** Decouple ingestion into `extract_only(file)`  
and `commit_to_wiki(edited_json)` so the user can review and edit the LLM's  
extraction before it's written.
- **Web search → ingest loop.** When lint reveals a gap, a tool can search the  
web, present candidate articles, and on approval ingest the content as a new  
source. (Distinct from §11.5 web search at query time.)
- **Image handling.** Store images from clipped articles in `sources/assets/`;  
optionally pass them to a vision-capable LLM during ingestion.
- **Knowledge graph visualisation.** Render `document_references` as an  
interactive D3.js / Mermaid graph in marimo.
- **Marp slide-deck generation.** `generate_marp_deck(topic, pages)` with a  
template under `wiki/templates/`, integrated with `marp-cli`.
- **Obsidian Canvas output.** Generate `.canvas` JSON for spatial layouts of  
related concepts.
- **Deeper post-save reconciliation** (§6.8). The chat→wiki post-save hook today  
runs only deterministic lint checks, and cross-linking is directional. Two  
optional upgrades, deferred as the project is a proof of concept: (a) pass an  
LLM client to the post-save `lint_wiki` so `contradiction` and `data_gap` also  
fire on save — at the cost of extra LLM calls and latency per save; and (b) make  
post-save cross-linking direction-independent, so the saved page is linked  
regardless of how its id sorts in `missing_xref_check` (e.g. emit both pair  
directions, or match the saved page as either `path_a` or `path_b`).
- **Web search as Phase 4 of chat/RAG** (§6.7). The agent's retrieval cascade  
implements Phases 1–3 (wiki index → wiki FTS → raw source chunks); Phase 4 (web  
search) is intentionally **not** built. Rationale: the project's thesis is  
answering from a *curated, local corpus* — Phases 1–3 fully exercise that.  
Web search is the only workflow that reaches outside your own data and is the  
only one with a recurring external cost (API key + per-query billing) and a  
network dependency that complicates testing. For a PoC that tradeoff isn't  
worth it. If revisited: add an async tool in `chat/tools.py` alongside  
`search_source_chunks`, pick a provider (e.g. Tavily/Brave free tiers, both  
RAG-oriented; DuckDuckGo keyless but weaker), number the prompt phases  
explicitly, and mock the network in tests.

---

## 13. Glossary

| Term                        | Meaning                                                                                                                                                                                                                             |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Source**                  | A raw, immutable file under `workspace/sources/` (PDF, DOCX). `source_kind='source'` in `documents`.                                                                                                                                |
| **Summary page**            | 1-to-1 LLM-generated markdown reflection of a single source. Lives under `wiki/summaries/`. Carries `source_document_id`.                                                                                                           |
| **Concept page**            | Topic-centric, multi-source markdown page under `wiki/concepts/`. Has YAML frontmatter. Does NOT carry `source_document_id` — derives from many sources.                                                                            |
| `**index.md**`              | Catalogue of every page in the wiki, organised by category. Deterministically updated by `index_manager.update_index`.                                                                                                              |
| `**overview.md**`           | LLM-rewritten narrative synthesis of the wiki's evolving thesis.                                                                                                                                                                    |
| `**log.md**`                | Append-only chronological audit trail. Prefix `## [YYYY-MM-DD]` makes it parseable with `grep "^## \["`.                                                                                                                            |
| **Slug**                    | `make_wiki_slug(name)` — NFKD-normalise → strip combining marks → lowercase → spaces/underscores → hyphens → remove non-`[a-z0-9-]` chars. Used as the filename of every wiki page. Example: `"Política Común"` → `politica-comun`. |
| **Filing Cabinet**          | The SQLite + FTS5 layer (`workspace/.llmwiki/index.db`).                                                                                                                                                                            |
| **Encyclopedia**            | The human-readable markdown layer (`workspace/wiki/`).                                                                                                                                                                              |
| **Phase 1 / 2 / 3 / 4 RAG** | The agent's routing cascade: index → wiki search → raw chunks → web search (Phase 4 pending §11.5).                                                                                                                                 |
