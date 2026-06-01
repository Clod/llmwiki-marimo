# LLMWiki — Programmer Manual

> **Single source of truth.** This document supersedes `programmatic_dev_plan.md`,  
> `implementation_plan.md`, `ingestion_design.md`, `diagnostic_alignment.md`, and  
> `llmwiki_architecture_rag_roadmap.md` (now under `docs/archive/`). The conceptual  
> reference `Karpathy_concepts.md` remains at the repo root.
>
> **Companion document:** **§6 Workflows** lives in its own file —
> [`docs/manual/workflows.md`](manual/workflows.md) — because it's the largest,
> most cross-referenced section. Section numbers are global: a `§6.x` reference is
> in `workflows.md`, every other `§N` is here. Everything else stays in this file.

## Table of Contents

1. [Philosophy & Karpathy Alignment](#1-philosophy--karpathy-alignment)
2. [Architecture Overview](#2-architecture-overview)
3. [Directory Structure](#3-directory-structure)
4. [Database Schema](#4-database-schema)
5. [Native Tool Layer](#5-native-tool-layer)
6. [Workflows](manual/workflows.md) — *(companion file)* lint · repair · ingest ·
   batch · scan · regenerate · chat/RAG · chat→wiki · source/page deletion
7. [Marimo Apps](#7-marimo-apps)
8. [Configuration](#8-configuration)
9. [Testing](#9-testing)
10. [Known Constraints & Gotchas](#10-known-constraints--gotchas)
11. [Pending Work / Roadmap](#11-pending-work--roadmap)
12. [Future Enhancements](#12-future-enhancements)
13. [Glossary](#13-glossary)
14. [Tracing & Observability](#14-tracing--observability)

Status legend used throughout: ✅ implemented · 🟡 partial · ❌ missing.

> **On line numbers.** Code is cited as `module.py:symbol` (function/constant name),
> which stays valid as the code moves. Any bare `:NN` or `(L NN)` you still see is an
> approximate snapshot — grep for the named symbol rather than jumping to the line.

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

### Karpathy coverage matrix

Which ideas from `Karpathy_concepts.md` this project implements. ✅ done ·
✅➕ done + goes beyond the concept doc · 🟡 partial · ❌ deferred (→ §12) · N/A.

| Karpathy concept | | Notes |
| --- | --- | --- |
| Compounding, persistent wiki (vs re-derive per query) | ✅ | the project's thesis (§6.3 builds pages, doesn't just index) |
| Three layers: raw sources · wiki · schema | ✅ | §3; schema = `_DEFAULT_SYSTEM_PROMPT` + `wiki_config.toml` |
| Ingest → summary + concept pages + index + overview + log + git | ✅ | §6.3–§6.4 |
| Wiki-first query **with citations** | ✅ | §6.7 (index → wiki FTS → raw chunks) |
| File good answers **back into** the wiki | ✅ | §6.8 `file_to_wiki` / `save_to_wiki` |
| Lint: contradictions · stale · orphans · missing concepts · missing xrefs · data gaps | ✅ | §6.1 — all six |
| Auto-repair of flagged issues | ✅➕ | §6.2 — the concept doc only *flags* |
| `index.md` catalog · `log.md` timeline · git repo | ✅ | §3, §13 |
| Search engine over the wiki | 🟡 | SQLite FTS5 (`search_chunks`); no vector/rerank/MCP |
| Interactive / HITL ingest ("discuss, then write") | 🟡 | auto today; post-ingest read-app chat + save-to-wiki partly compensates → §12 |
| Web search (query Phase 4 + data-gap fill) | ❌ | external search + manual add compensates → §12 |
| Image handling (download + vision) | ❌ | → §12 |
| Alternate outputs: Marp decks · charts · canvas | ❌ | → §12 |
| Graph visualization | ❌ | the graph exists in `document_references`; just not rendered → §12 |
| Obsidian web-clipper · graph view · Dataview | N/A | this is a marimo project, not Obsidian |

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
│       │   ├── pdf_extract.py          # opendataloader-pdf (text PDFs; no OCR yet)
│       │   ├── trace.py                # opt-in ingestion trace (see §14)
│       │   └── wiki_generator.py       # all LLM prompt builders (see §6)
│       ├── lint/
│       │   ├── checks.py               # 7 check functions (incl. gap_filled)
│       │   ├── markers.py              # DATA_GAP markers + fts_safe (shared w/ repair)
│       │   ├── report.py               # LintIssue, LintReport
│       │   └── runner.py               # lint_wiki()
│       ├── repair/
│       │   ├── actions.py              # 6 repair functions
│       │   ├── report.py               # RepairResult, RepairReport
│       │   └── runner.py               # repair_wiki()
│       ├── tools/
│       │   ├── db.py                   # open_db(), get_connection()
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
│   └── render_trace.py                 # Render a trace.jsonl run to a timeline (§14.7)
├── tests/
│   ├── conftest.py                     # sys.path + fixture registration
│   ├── helpers/{fake_llm.py,workspace.py,golden.py}
│   ├── unit/                           # 248 unit tests (no LLM, no network)
│   ├── regression/                     # golden-corpus invariants (skips until frozen)
│   └── e2e/                            # 9 Playwright tests (live marimo + LLM)
├── docs/
│   ├── programmer_manual.md            # THIS FILE
│   ├── sqlite_data_dictionary.md       # Per-column DB reference
│   ├── CODEMAPS/                       # Auto-generated code maps
│   └── archive/                        # Superseded design docs
├── Karpathy_concepts.md                # Foundational pattern reference
├── README.md                           # End-user quickstart
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

## 4. Database Schema

**Location:** `workspace/.llmwiki/index.db`. Opened by `domain/tools/db.py:open_db()`,  
schema applied from `database/sqlite_schema.sql` on first run. Uses  
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

### The citation graph (nodes & edges)

The wiki is not just a folder of markdown files — it is a **directed graph**, stored
in the `document_references` table. Understanding this graph is essential to
understanding how lint, repair, stale-detection, and backlinks all work.

- **Nodes** = documents. *Every* row in the `documents` table is a node — this
  includes both raw sources (`source_kind='source'`, e.g. `Cenicienta.pdf`) and
  generated wiki pages (`source_kind='wiki'`, e.g. `concepts/cinderella.md`).
- **Edges** = rows in `document_references`. Each row is a *directed* link from one
  document to another:

  ```
  source_document_id  →  target_document_id   (reference_type)
  ```

The `reference_type` column says **what kind** of link the edge is. There are exactly
two kinds:

| `reference_type` | Meaning | Parsed from | Example |
| ---------------- | ------- | ----------- | ------- |
| **`cites`**      | "this page was built from / draws on that source" | the `## Sources` section of a page | `cinderella.md` **cites** `Cenicienta.pdf` |
| **`links_to`**   | "this page hyperlinks to that page" | inline `[text](path)` wiki links (e.g. *See also* links) | `cinderella.md` **links_to** `little-red-riding-hood.md` |

So a **"cites edge"** is one row in `document_references` with `reference_type='cites'`.
It records the single fact *"document A cites document B."* When you ingest
`Cenicienta.pdf` and it produces `cinderella.md`, the intended edge is:

```
source = cinderella.md   target = Cenicienta.pdf   reference_type = 'cites'
```

That one row is what powers the two traversal helpers in `references.py`:

- **`get_forward_refs(node)`** — follows edges *out of* a node → "what does this page cite / link to?"
- **`get_backlinks(node)`** — follows edges *into* a node → "what cites / links to this document?"

And it is what the reconciliation checks reason over:

- **`find_uncited_sources`** — a source with **no incoming `cites` edge** is an orphan.
- **`missing_xref`** — relies on `cites` edges to know which page came from which source.
- **stale detection** — follows `cites` edges to mark a page stale when its source changes.

If the `cites` edges are missing, the graph still has all its **nodes** (the documents
exist) but is missing the **arrows** between them — so every check above silently
produces wrong answers. This was a real regression once: the `## Sources` parser
stopped matching the page format, so concept pages generated zero `cites` edges.
The parser now accepts both the footnote (`[^N]: file`) and plain-bullet (`- file`)
Sources forms — see `references.py:update_references` and its regression tests in
`tests/unit/test_references.py`.

#### Edges are rebuilt, never patched

`update_references(db, doc_id, content, path)` does **not** diff against existing rows.
Inside one transaction it deletes the page's current outgoing edges and re-inserts the
full freshly-parsed set:

```python
with conn:
    conn.execute(
        "DELETE FROM document_references WHERE source_document_id=?",
        (document_id,),
    )
    conn.executemany(
        "INSERT INTO document_references "
        "(source_document_id, target_document_id, reference_type, page) "
        "VALUES (?,?,?,?)",
        [(document_id, t, r, p) for t, r, p in unique_edges],
    )
```

Three properties follow:

- **Scope is one node's *outgoing* edges.** The `DELETE` is keyed on
  `source_document_id = doc_id`, so it clears every edge *leaving* this page (both
  `cites` and `links_to`) and nothing else. Edges *into* the page (other pages citing
  it) belong to other source nodes and are rebuilt when *those* pages are reprocessed.
- **Page content is the single source of truth.** After the call, the page's outgoing
  edges are exactly what the current markdown says — no more, no less. The operation is
  idempotent: running it twice on the same content yields the same rows, with no
  accumulation or stale leftovers.
- **Why rebuild instead of patch:** a diff-and-patch approach is more code and every
  diff path is a chance to leave the graph inconsistent (e.g. a removed `## Sources`
  entry whose `cites` edge lingers forever). Wiki pages are small and fully available at
  write time, so "delete-all-then-insert-all" is both cheap and trivially correct — the
  DB can never drift from the markdown.

The practical consequence cuts both ways. **Regenerating or editing a page automatically
heals its edges** — which is why the fix for a Sources-parser regression needs no migration:
correct the parser, reprocess each affected page (or run a regen pass), and every missing
`cites` edge is rebuilt. But the same property makes a parser bug *total*: since edges are
never patched incrementally, there is no historical residue to fall back on. The moment
the parser stops matching the page format, the very next `update_references` call deletes
the old (correct) edges and inserts nothing — one run is enough to zero out a page's
citations.

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
| `tools/references.py` | `update_references`, `get_backlinks`, `get_forward_refs`, `find_orphan_pages`, `find_uncited_sources`, `find_stale_pages` | Parses `[[wikilinks]]` plus citations in both `[^N]: file.pdf, p.3` footnote and `- file.pdf` Sources-bullet form; maintains `document_references` |
| `tools/git_ops.py`    | `init_wiki_repo`, `auto_commit`, `autocommit_enabled`                                                                     | Idempotent git init + silent commits of `wiki/` in the workspace repo; both no-op when `WIKI_AUTOCOMMIT` is falsy. **git is optional** — a missing/failing `git` is caught (one-time warning) and skipped, never failing an ingest |

Two structural notes:

- `wiki_fs.py` defers `from domain.ingestion.chunker import chunk_pages` inside  
`_insert_chunks()` to break a load-time circular import with `pipeline.py`.
- The citation parser uses `\s+[-–—]` (one *or more* spaces before the dash) so  
hyphenated filenames like `fed-paper.pdf` are not truncated to `fed`.

---

## 6. Workflows

> **Moved to [`docs/manual/workflows.md`](manual/workflows.md).** The workflows
> section — the lint→repair reconciliation cycle, single/batch/scan ingestion,
> regenerate, chat/RAG, chat→wiki, and the two deletion flows, with per-workflow
> Mermaid diagrams and the table-write matrix — lives in its own file (~860 lines).
> All `§6.x` references throughout this manual point there.

---

## 7. Marimo Apps

Both apps live in `marimo/` and are self-contained `uv` scripts — the  
script header declares their dependencies inline. They share no global state.

### 7.1 Wiki picker (shared by both apps)

Both apps let you **switch the active wiki at runtime** instead of editing
`WIKI_PATH` in `.env` and restarting. `WIKI_PATH` is now only the *default*
selection. The picker (top-left in `read_app`, top of `ingest_app`) is one
`mo.ui.dropdown` over **discovered + recent** wikis, plus an accordion text box
to open any other folder (including a new/empty one).

Pure logic lives in `base/domain/wiki_registry.py` (unit-tested,
`tests/unit/test_wiki_registry.py`):

| Function | Role |
| --- | --- |
| `discover_wikis(home)` | immediate sub-folders of `home` that look like a wiki (`is_wiki_dir` → has `wiki/` or `.llmwiki/`), plus `home` itself |
| `merge_options(home, recent, active)` | ordered, de-duplicated option list: active first, then discovered, then recent |
| `load/save/push_recent(...)` | recent-wikis list persisted to `~/.llmwiki/recent_wikis.json` (most-recent-first, capped) |
| `clean_path_input(raw)` | strips surrounding quotes/whitespace from a pasted path ("Copy as Pathname" yields `'/a/b c'`) |
| `resolve_wiki_home(env_wiki_path)` | folder to scan: `$WIKI_HOME`, else parent of `WIKI_PATH`, else `~` |
| `short_label(path)` | compact dropdown label like `…/finanzas/my-wiki` |

**Reactive wiring.** Each app holds an `active_wiki` `mo.state` (seeded from
`WIKI_PATH`). A `wiki_context` cell *derives* the path-bound objects from it and
re-runs on switch, so the rest of the graph retargets automatically:

- `read_app` → `WIKI_PATH`, `wiki_db_path`, `wiki_chat_config`, `wiki_agent`
- `ingest_app` → `WORKSPACE`, `DB_PATH`, `SOURCES_DIR` (+ workspace-row DB init)

Because `ingest_app` injects these by name, moving their definition from `setup`
into `wiki_context` needed **no** changes to consumer cells.

> **Why a path picker, not a folder browser:** `mo.ui.file_browser(selection_mode="directory")`
> does not emit a value in marimo 0.23.x (GH #1478), so directory picking is done
> via discovery + recent list + a sanitised text path instead.

### `ingest_app.py`

Cells (selected — see source for the full list):

| Cell                  | Purpose                                                                         |
| --------------------- | ------------------------------------------------------------------------------- |
| `setup`               | `.env` + logging + `sys.path` + build the `openai.OpenAI` client from `settings.WIKI_LLM_*`/`LLM_*` + picker defaults (`ENV_DEFAULT`, `WIKI_HOME`) |
| `wiki_state` / `wiki_context` | Active-wiki `mo.state`; derives `WORKSPACE`/`DB_PATH`/`SOURCES_DIR` + DB init on switch (§7.1) |
| `wiki_picker` / `wiki_add` / `wiki_add_runner` | Wiki dropdown + "open another folder" accordion (§7.1) |
| `op_state`            | Shared `mo.state`: the log lines + per-operation trigger/`running_op` states     |
| `timing_helper`       | `make_timed_logger(set_log_lines, logger, tag)` — timed cb + a `domain.ingestion` log handler that streams INFO into the panel (de-duped, capped) |
| `upload_widget` / `handle_upload` | `mo.ui.file(filetypes=[".pdf",".docx"], multiple)`; saves dropped files to `sources/` |
| `ingest_form_cell`    | Form: "⚙️ Ingest uploaded file(s)" submit + "full LLM lint & repair" checkbox → sets the ingest trigger |
| `action_buttons`      | "🔄 Scan sources" / "🤖 Regenerate wiki" / "🗑 Clear log" buttons → triggers      |
| `ingest_runner` / `scan_runner` / `regen_runner` | Do the work in a `mo.Thread`; the ingest runner closes with the scoped lint+repair tail (§6.3) |
| `auto_refresh`        | 1s `mo.ui.refresh` mounted while an op runs (drives live panel repaint)           |
| `op_spinner`          | Non-blocking "⏳ Running…" indicator (re-evaluated on each refresh tick)          |
| `activity_log`        | Fixed-height, `column-reverse` auto-scrolling log panel (sticks to the newest line) |
| `lint_repair_widget_cell` / `lint_repair_runner` | Manual "Run Wiki Lint & Repair" (wiki-wide, LLM-enabled) |
| `sources_table_cell` / `also_file_check_cell` / `delete_widget_cell` / `delete_runner` | Source list + delete flow (§6.9) |
| `debug_panel`         | Visible when `WIKI_DEBUG=1`                                                      |

**Timed Activity Log.** Each runner (`ingest`, `scan`, `regen`, `lint_repair`) wraps
its `progress_cb` with `make_timed_logger` (the `timing_helper` cell). Every log line
is prefixed with the elapsed time since the previous message (`` `+  8.1s` 🤖 … ``) and
a bold `total: Ns` is appended when the run finishes. Because messages mark the *start*
of each step, the delta on a line is the duration of the step named on the line above —
which makes the slow steps (the LLM calls) jump out for optimization. Timing lives
entirely in the app layer; `pipeline.py` and the domain are untouched.

To keep progress visible, `make_timed_logger` also installs a `logging.Handler` on
the `domain.ingestion` logger for the duration of each run, so those modules' INFO
lines — e.g. the extractor's per-file progress, which inherit the root `WARNING`
level and so reach neither console nor panel today — stream into the Activity Log
too (the "app + ingestion" subset). Lines are de-duped against the `progress_cb`
copy (a domain `_cb` logs the same text to its module logger *and* via `progress_cb`)
and the panel is capped to the last 200 lines so a chatty run can't flood the
reactive UI. The handler is attached per run and removed in `finish()` (called from
each runner's `finally`), with a defensive sweep of leaked handlers on the next run.

### `read_app.py`

Three-column grid:

| Pane                  | Cell                       | Role                                                                                                   |
| --------------------- | -------------------------- | ------------------------------------------------------------------------------------------------------ |
| Left (top)            | `wiki_picker` / `wiki_add` / `wiki_add_runner` | Wiki dropdown + "open another folder" accordion (§7.1)                             |
| — (logic only)        | `wiki_state` / `wiki_context` | Active-wiki `mo.state`; derives `WIKI_PATH`/`wiki_db_path`/`wiki_chat_config`/`wiki_agent` on switch (§7.1) |
| Left                  | `left_panel`               | Page selector with refresh button (`scan_pages()`)                                                     |
| Left (below selector) | `delete_widget_cell`       | Renders `DeleteConfirmWidget` — disabled when no page is selected; resets event counter on page change |
| — (logic only)        | `delete_event_cell`        | Watches `delete_widget.event_id`; fires `set_delete_trigger` on confirm                                |
| — (logic only)        | `delete_runner`            | Calls `wiki_fs.delete_page`, rescans page list, clears selection                                       |
| Middle                | `middle_panel`             | Renders the selected page as markdown + nav links                                                      |
| Right                 | `chat_panel`               | PydanticAI agent stream + suggested prompts                                                            |
| Right (below chat)    | `save_form`, `save_action` | Saves the last assistant reply to the wiki via `save_to_wiki` with LLM structuring pass                |

Two rendering details in this app:

- **`middle_panel` strips citation footnotes at render time.** `## Sources` bullets and
  inline citations carry `[^n]:` markers that marimo's markdown renderer would otherwise
  show as empty bullets; `middle_panel` removes the `- [^n]:` prefix (and inlines link
  text) before display, so the rendered page is clean while the underlying markers stay
  intact for `references.update_references`.
- **`page_links_nav` resolves relative links before matching.** A concept page links to a
  sibling as `cinderella.md` or to a summary as `../summaries/x.md`; the nav resolves each
  against the current page's directory (`posixpath.normpath`) before matching the scanned
  page list (which stores directory-prefixed stems like `concepts/cinderella`), and skips
  `![alt](src)` image embeds. Without this, chat-generated pages showed no nav links.

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

The agent is created once per session via `create_agent(base_url, api_key, model)`
and reused across messages; the `db_path` is passed as the agent's `deps` on each
`run_stream(...)` call, not to the factory.

### `trace_report_app.py`

A read-only viewer for ingestion traces (§14). Point it at a directory, it
discovers every `trace.jsonl` run underneath, and renders each run two ways: a
human-readable per-document timeline (same layout as `scripts/render_trace.py`)
and an `mo.tree` of the raw events grouped by document. Payload channels
(`prompts`, `responses`, `extracted_text`, `chunks`, `markdown`) can be inlined
on demand. It only reads traces produced by `WIKI_TRACE=1` runs — it never
ingests or writes anything.

### Running locally

```bash
# Opens on $WIKI_PATH from .env (the default) — switch wikis in-app via the picker (§7.1)
uv run marimo run marimo/ingest_app.py --port 2718
uv run marimo run marimo/read_app.py --port 2720

# Start on a specific workspace (still switchable in-app afterwards)
WIKI_PATH=/path/to/workspace uv run marimo run marimo/read_app.py --port 2720
```

---

## 8. Configuration

### `.env` (loaded by `base/config.py` via `pydantic-settings`)

```ini
WIKI_PATH=/path/to/workspace   # default wiki on launch; switchable in-app (§7.1)
WIKI_HOME=                      # optional: folder the picker scans for sibling wikis
                               #          (default: parent of WIKI_PATH)
# Any OpenAI-compatible endpoint. Example: Ollama (local, free).
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=llama3.2
# Cloud alternative: LLM_BASE_URL=https://openrouter.ai/api/v1 / sk-or-... / anthropic/claude-haiku-4-5

# Optional override for ingestion-time LLM (falls back to LLM_* if blank)
WIKI_LLM_BASE_URL=
WIKI_LLM_API_KEY=
WIKI_LLM_MODEL=
```

PDF extraction uses opendataloader-pdf (text-based PDFs only; no OCR backend yet
— see §12). There is no PDF-backend selector setting today.

### `workspace/wiki_config.toml` (optional, per-workspace)

Overrides the chat assistant's system prompt and suggested prompts. See §6.7  
for an example. Absent file → defaults from `chat/config.py` are used.

### Environment flags

| Flag                       | Effect                                                                                       |
| -------------------------- | ------------------------------------------------------------------------------------------- |
| `WIKI_DEBUG=1`             | Shows debug panel in `ingest_app.py`                                                         |
| `WIKI_HOME=…`              | Folder the wiki picker scans for sibling wikis (default: parent of `WIKI_PATH`). See §7.1.   |
| `WIKI_AUTOCOMMIT=0`        | Disable the per-ingest git auto-commit of `wiki/` in the workspace (default: on). Falsy values `0/false/no/off`; read by `git_ops.autocommit_enabled` — skips both `init_wiki_repo` and `auto_commit`. |
| `HEADLESS=1`               | Used by the E2E test suite for non-interactive Playwright runs                               |
| `WIKI_TRACE=1`             | Turns on the opt-in ingestion trace (LLM exchanges + data-flow). See §14.                    |
| `WIKI_TRACE_CAPTURE=…`     | Selects trace payload channels: `all` (default) · `none` · CSV of `extracted_text,chunks,prompts,responses,markdown`. See §14. |

---

## 9. Testing

### Run

```bash
uv run pytest tests/unit/ -v               # 248 unit tests — fast, no LLM
uv run pytest tests/e2e/ -v -s             # 9 E2E tests — live marimo + LLM (test_ingest_pdf is parametrized over 3 PDFs)
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

### Golden-corpus regression

Ingestion is non-deterministic (LLM output varies), so it can't be strict-diffed.
Instead a fixed set of **4 public-domain English fairy-tale PDFs** (Cinderella, Little
Red Riding Hood, The Sleeping Beauty in the Wood — from *The Blue Fairy Book*, Project
Gutenberg #503 — plus Snow White and the Seven Dwarfs, all in `tests/fixtures/pdfs/`) is
ingested **once** (1 individual + 3 batch), human-verified, and frozen into a tracked
snapshot. That "golden corpus" turns every *other* workflow into a deterministic
regression test.

```bash
python scripts/build_golden_corpus.py build    # ingest into _golden_staging/ (needs LLM keys)
# inspect tests/fixtures/_golden_staging/wiki/ — the report flags missing cites edges
python scripts/build_golden_corpus.py freeze    # snapshot -> tests/fixtures/golden_corpus/
git add tests/fixtures/golden_corpus            # sources/ + wiki/ + index.db + index.db.sql
```

- `tests/helpers/golden.py:restore_golden(tmp)` copies the snapshot into a fresh
  workspace and returns `(db_path, workspace)` (the DB stores only relative paths, so
  it is relocatable).
- `tests/regression/test_golden_corpus.py` asserts LLM-variation-robust invariants:
  4 sources `ready`, **every concept page has a `cites` edge** (the citation-graph guard), each
  summary cites its source, lint reports no errors, and the DB rows agree with the
  markdown tree on disk. The whole module **skips** until the corpus is frozen.
- The snapshot ships both `index.db` (binary — the restore source; FTS5 doesn't
  round-trip through a `.dump`) and `index.db.sql` (the human-auditable companion).

### E2E infrastructure

> **Run with the test ports free.** The fixtures start their own marimo servers
> on **2719** (ingest) and **2720** (read). They do *not* fail if the port is
> already taken — Playwright will silently connect to whatever is listening, so a
> dev app left running on those ports makes the suite connect to the wrong
> instance (different workspace/state) and produce spurious failures. Stop any
> marimo app on 2719/2720 before running the E2E suite.

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
  `tools/deletion.py`. FK cascade cleans up chunks, references, and FTS; 1-to-1  
   summary pages are deleted while citing concept pages are marked `stale_since`  
   (see §6.9, M2). UI: dropdown + confirm checkbox + `delete_runner` cell in  
   `marimo/ingest_app.py`.
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
9. ✅ **Grid column for wiki page title** in `read_app.py:left_panel` — the
  sources table shows Title + Directory + Slug + Excerpt.
10. **Document `scan_and_ingest` precisely** for end users (§6.5 — what it
  touches, when to prefer `batch_ingest` instead).
11. 🟡 **Lint+repair always close every ingest, scan, and regenerate** (§6.1–§6.6).
  **Done for ingest:** the `ingest_app` ingest form (`ingest_form_cell` +  
   `ingest_runner`) now auto-runs a lint **and** repair pass after every ingest —  
   **deterministic by default**, or **full LLM** when the form checkbox is ticked —  
   with the `orphan` check excluded so just-created pages survive. The manual "Run  
   Wiki Lint & Repair" button (`lint_repair_widget` + `lint_repair_runner`) remains  
   for an on-demand **wiki-wide** sweep — the automatic post-ingest pass is instead  
   **scoped to the pages the ingest touched** so it never rewrites unrelated pages.  
   **Remaining:** give **scan** (§6.5) and **regenerate** (§6.6) the same automatic  
   tail (today they still don't reconcile afterwards), and optionally surface the  
   same checkbox on those actions.
12. ✅ **Finish the skipped repairs** (§6.2). Implemented `repair_missing_xref`
  (appends `## See also` + records `links_to` edge), `repair_contradiction`  
   (idempotent `⚠️` callout), and `repair_data_gap` (inserts `<!-- DATA_GAP -->`  
   TODO note), plus a new `gap_filled` check+repair that replaces a resolved TODO  
   note with a link. All deterministic; see `repair/actions.py` and  
   `tests/unit/test_repair_finish.py`.
13. **Warn on duplicate upload** (§6.3–§6.4). When an uploaded or dropped file is
  already ingested and unchanged, surface a GUI warning instead of the current  
   silent `status="skipped"`.

### Open bugs

None currently tracked.

> Earlier the read-app E2E tests appeared to fail (the chat panel "never
> rendered"). On a clean run — with **nothing else listening on the E2E ports**
> (2719/2720) — the full suite is green. The failures were port contention: the
> test connected to a *separate* marimo instance already on the port (e.g. a dev
> app) whose workspace/state differed, not a `read_app` bug. See §9's note on
> running E2E with the ports free.

---

## 12. Future Enhancements

Aspirational features from `Karpathy_concepts.md` not yet on the roadmap:

- **Two-step HITL ingestion.** Decouple ingestion into `extract_only(file)`  
and `commit_to_wiki(edited_json)` so the user can review and edit the LLM's  
extraction before it's written. *Today's partial compensation:* ingestion is
automated, but after it you can discuss the document in the read-app chat and
correct/refine the resulting pages via `file_to_wiki`/`save_to_wiki` (§6.8) —
post-hoc rather than mid-ingest, but the human still gets to shape the wiki.
- **Web search → ingest loop.** When lint reveals a gap, a tool can search the  
web, present candidate articles, and on approval ingest the content as a new  
source. (Distinct from §11.5 web search at query time.) *Today's manual
compensation:* run the search yourself and drop the finding into `sources/` (or
paste it into the chat and `file_to_wiki`) — the corpus still compounds, just
without the in-loop automation.
- **OCR for scanned / image-only PDFs.** Today `pdf_extract.py` uses only
opendataloader-pdf, which extracts *text*; image-only PDFs yield empty/garbled
output. Add a pluggable OCR path in `extractor._extract_pdf`. Keep it
**provider-agnostic** (don't hardcode a vendor): the most on-brand options are a
**local OCR engine** (Tesseract via ocrmypdf, docTR, Surya, RapidOCR, or Docling
— fits the local-first ethos, no extra key) or **reusing a vision-capable LLM
through the already-configured OpenAI-compatible endpoint** (send page images to
the same `LLM_*`/`WIKI_LLM_*` model — no new provider). A hosted document-OCR
API is a third option but adds a vendor + key.
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
- **Non-LLM reindex from disk (`reindex_from_disk`).** Today the only way to
  repopulate a lost or corrupt `index.db` is to re-run the LLM ingestion pipeline
  (`scan_and_ingest`). That path is **non-deterministic** — it re-invokes the model,
  so regenerated wiki pages, document IDs, and chunk boundaries all differ run to run —
  and it **overwrites** the on-disk wiki markdown, destroying any manual edits (and it
  can't reconstruct a page at all once its source file is gone). That contradicts the
  derived-state principle in [`sqlite_data_dictionary.md`](sqlite_data_dictionary.md) §1
  and the Two-Layers framing in §1 here: the durable layer is the **Encyclopedia**
  (`wiki/**/*.md`) plus the sources, and the **Filing Cabinet** (the DB) should be
  rebuildable from them *mechanically, without the LLM*. `reindex_from_disk(workspace,
  db_path)` would be that deterministic complement:
  1. Apply the schema to a fresh DB (`open_db`) and re-create the `workspace` row.
  2. Walk `sources/*` → one `source_kind='source'` `documents` row per file (recompute
     `content_hash` / `mtime_ns` / `file_size`), **re-extract** pages with the existing
     deterministic extractor, re-chunk, and fill `document_pages` + `document_chunks`.
     Re-extraction is the only step that reads the original file and it uses **no LLM**.
  3. Walk `wiki/**/*.md` → one `source_kind='wiki'` row per page; read title/tags from
     frontmatter and re-chunk the markdown into `document_chunks` (the FTS5 triggers
     repopulate `chunks_fts`).
  4. Once every node exists, run `update_references` per wiki page to rebuild
     `document_references` from the on-disk citations / wikilinks — already idempotent
     (§4, "Edges are rebuilt, never patched").
  5. Re-derive `source_document_id` for each `wiki/summaries/<slug>.md` by matching
     `<slug>` back to the source whose `make_wiki_slug(filename)` equals it (a
     deterministic heuristic — the link itself is never written to disk).

  **Recovered deterministically (identical every run, no LLM):** all `documents` rows,
  `document_chunks` + `chunks_fts`, the `document_references` graph, and
  `index.md` / `overview.md` / `log.md` (read back verbatim — they are just files).
  **Cannot come from disk alone:** internal counters (`version`, `document_number`)
  reset, and `created_at` resets to "now" (git history could backfill it — out of
  scope). **Caveats:** `document_pages` / `elements` repopulate only while the source
  files are still present to re-extract — a "metadata-only" fast mode could skip that
  and leave `regenerate_wiki_pages` degraded until a real ingest; and a wiki page whose
  source was deleted re-registers fine but its `cites` edge stays dangling, exactly as
  today. Net: same index end-state as `scan_and_ingest`, but it treats the
  human-readable markdown as the source of truth and never rewrites it.

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
| **Trace (ingestion)**       | Opt-in (`WIKI_TRACE=1`) write-only JSONL record of every LLM exchange + the data-flow path of an ingestion run, correlated to DB rows via a `db_join_map` header. For debugging, not replay. See §14.                                |
| **Sidecar**                 | A content-addressed file under a trace's `payloads/<sha256>.<ext>` holding one heavy payload (prompt, response, extracted text, chunks, or a generated page), referenced from the event by `ref` + `sha256` + `bytes`. See §14.4.     |

---

## 14. Tracing & Observability

**Entry point:** `base/domain/ingestion/trace.py`. **Activation:** `WIKI_TRACE=1`.
**Status:** ✅ ingestion only (chat agent, lint, and repair are out of scope for v1).

The trace is a **write-only observability layer** for the ingestion pipeline. With
`WIKI_TRACE=1` set, a run records (a) **every LLM exchange** and (b) **the path each
piece of information takes** through the pipeline — extract → chunk →
structured_extraction → concepts → summary → overview — so a single PDF can be
followed end to end and the result cross-checked against the database.

### 14.1 What it is — and what it is *not*

| | |
| --- | --- |
| ✅ **Is** | A debugging/observability artifact you read after a run (or feed to an LLM to audit). One JSONL event stream per run + content-addressed sidecars for heavy payloads. |
| ❌ **Is not** | A record/replay or regression mechanism. There is **no replay and no assertion** anywhere. |

> **Why not record/replay?** A "cassette" approach (freeze the LLM responses, replay
> them to make ingestion deterministic, strict-diff the output) was considered and
> **deliberately rejected**: the first prompt improvement would invalidate every frozen
> response, turning the suite into a re-freezing chore instead of a bug detector.
> Deterministic regression stays *structural-invariant* via the golden corpus (§9);
> this trace is purely for human/LLM inspection.

### 14.2 Activation & output layout

| Variable | Values | Effect |
| -------- | ------ | ------ |
| `WIKI_TRACE` | `1` to enable (anything but unset/`0`/`false`) | Master switch. Unset → a `NullTracer` no-op; ingestion behaviour and output are byte-identical to an untraced run. |
| `WIKI_TRACE_CAPTURE` | `all` (default when unset) · `none` · CSV of channels | Which payload channels write sidecars (see §14.4). Unknown channel names are ignored with a warning. |

Output goes under the workspace (which is gitignored via `.llmwiki/`):

```
<workspace>/.llmwiki/traces/<run_id>/
├── trace.jsonl              # the event stream; line 1 is the meta header
└── payloads/
    └── <sha256>.<ext>       # one content-addressed sidecar per captured payload
```

`run_id` = `YYYYMMDDTHHMMSSZ-<6hex>` (UTC timestamp + short uuid), e.g.
`20260528T203202Z-bea166`.

### 14.3 The trace file (`trace.jsonl`)

One self-describing JSON object per line. Every event carries `seq` (monotonic),
`ts` (UTC ISO-8601 ms), `event`, and `run_id`; events inside a document/stage scope
also carry `document_id`, `relative_path`, and `stage` so each line stands alone.

| `event` | Emitted when | Key fields (beyond the common ones) |
| ------- | ------------ | ----------------------------------- |
| `meta` | first line | `schema_version`, `workspace`, `db_path`, `capture`, `channels_available`, **`db_join_map`** |
| `run_start` / `run_end` | run open / close | `workspace`, `db_path` |
| `document_start` / `document_end` | per source file | `document_id`, `filename`, `relative_path`; `status` (`ok`/`error`) on end |
| `stage_start` / `stage_end` | per pipeline stage | `stage`; on end: `status`, `elapsed_ms` |
| `llm_call` | every `client.chat.completions.create` | `model`, `params` (kwargs minus `model`/`messages`), `latency_ms`, `usage` (prompt/completion/total tokens), `prompt_sha256`/`prompt_bytes`/`prompt_ref`, `response_sha256`/`response_bytes`/`response_ref` |
| `artifact` | intermediate data produced | `channel`, `name`, `sha256`, `bytes`, `ref`, plus structural meta (`relative_path`, `page_count`, `parser`, `count`, `concept_name`, `category`, `source_document_id`) |

**The `db_join_map` header is the point.** It maps trace fields to the columns they
correspond to, so an LLM (or a script) can join `trace.jsonl` against `index.db`
without guessing:

| Trace field | DB column |
| ----------- | --------- |
| `document_id` | `documents.id` |
| `relative_path` | `documents.relative_path` |
| `filename` | `documents.filename` |
| `status` | `documents.status` |
| `page` | `document_pages.page` |
| `chunk_index` | `document_chunks.chunk_index` |
| `reference.{source,target}_document_id`, `reference.reference_type` | `document_references.*` |

### 14.4 Sidecars & unpluggable channels

Heavy payloads never bloat the event stream — they are written to
`payloads/<sha256>.<ext>` and referenced from the event by `ref` + `sha256` + `bytes`.
Sidecars are **content-addressed**, so identical payloads are stored once (and a
generated concept page's `artifact.sha256` equals the `response_sha256` of the
`llm_call` that produced it — the page *is* the model output).

The five channels are independently **unpluggable** via `WIKI_TRACE_CAPTURE`:

| Channel | Captures |
| ------- | -------- |
| `extracted_text` | the joined per-page source text |
| `chunks` | the FTS5 chunk list (JSON: index, page, token_count, start_char, content) |
| `prompts` | the full request `messages` for each LLM call |
| `responses` | the raw model response text for each LLM call |
| `markdown` | each generated concept / summary / overview page |

**Key invariant:** turning a channel *off* does **not** blind the trace structurally —
the `artifact`/`llm_call` event still records `sha256` + `bytes`; only the sidecar
file is skipped (`ref` is `null`). So `WIKI_TRACE_CAPTURE=none` still lets you verify
*that* content existed and *whether it changed*, just not read it.

### 14.5 How it's wired

- **Transparent client proxy.** `tracer.wrap(client)` returns a `TracingClient` that
  delegates everything to the real client and returns the real response object
  untouched — it only *observes* `chat.completions.create`. This is why none of the
  ~6 call sites in `wiki_generator.py` changed. `wrap()` is idempotent (a client
  already wrapped for this run is returned as-is), which matters on the batch path.
- **Correlation via contextvars.** `tracer.document(...)`/`tracer.stage(...)` set
  `_current_doc`/`_current_stage`, so the proxy can tag each `llm_call` with the
  document and stage that triggered it without threading the tracer through every
  signature.
- **Run ownership.** `trace.run_scope(workspace, db_path)` (used by `batch_ingest`
  and `scan_and_ingest`) opens one run for the whole operation; `ingest_file` calls
  `trace.active_or_start(...)` and **joins** that run if one is active, else creates
  and finalises its own. Net effect: a batch or a scan is **one** `trace.jsonl`; a
  lone `ingest_file` is its own.
- **Disabled = free.** When `WIKI_TRACE` is unset, `active_or_start` returns the
  shared `NULL` tracer: `wrap()` returns the client unchanged, every method is a
  no-op, and no directory is created. Tracing failures are swallowed (logged at
  debug) and can never break an ingest.

### 14.6 What each stage emits

| Stage | `llm_call` | `artifact` |
| ----- | ---------- | ---------- |
| `extract` | — | `extracted_text` (`page_count`, `parser`) |
| `chunk` | — | `chunks` (`count`) |
| `structured_extraction` | 1 (the extraction JSON lands in the `responses` channel) | — |
| `concepts` | 1 per concept | `markdown` `concept:<slug>` per concept (`relative_path`, `concept_name`, `category`) |
| `summary` | — (`build_summary_page` is deterministic) | `markdown` `summary:<slug>` (`relative_path`, `source_document_id`) |
| `overview` | 1 (single-file path, and once per batch at batch level) | `markdown` `overview` (`relative_path`) |

### 14.7 Rendering — `scripts/render_trace.py`

`trace.jsonl` is machine-first; the render script turns a run into a readable
per-document timeline.

```bash
# Timeline (events only)
python scripts/render_trace.py <run_dir-or-trace.jsonl>

# Inline the actual prompts + responses (resolves the sidecars)
python scripts/render_trace.py <run_dir> --show prompts,responses

# One document only
python scripts/render_trace.py <run_dir> --doc <document_id> --show markdown
```

### 14.8 Cross-checking a trace against the DB

The intended audit: every `document_start` should have a matching `documents` row,
and the `chunks` artifact `count` should equal the rows in `document_chunks`. The
`db_join_map` makes this a straightforward join — e.g. for a real 4-PDF run the
trace's `document_id`/`relative_path`/chunk counts matched the DB exactly (10, 2, 13,
5 chunks; all `status='ready'`). Hand the `trace.jsonl` (its header included) to an
LLM and ask it to reconcile against `index.db`, or script it in a few lines of SQL +
`json`.

### 14.9 Guarantees & references

- **No credentials.** Only `messages`, non-secret `params` (the proxy drops `model`
  and `messages` from `params`), and response text are recorded — never the API key
  (it lives on the client, which is never serialised).
- **Crash-safe.** Each line is flushed on write, so a trace is useful even if a long
  run is interrupted.
- **Code:** `base/domain/ingestion/trace.py` — `IngestionTracer`, `NullTracer`/`NULL`,
  `TracingClient`, `active_or_start`, `run_scope`, `CHANNELS`, `DB_JOIN_MAP`,
  `SCHEMA_VERSION`. Instrumentation lives in `pipeline.py` (`ingest_file`,
  `scan_and_ingest`) and `batch.py` (`batch_ingest`).
- **Tests:** `tests/unit/test_trace.py` — channel resolution, disabled no-op, proxy
  transparency, sha/size-always-present, meta header shape, contextvar tagging, and an
  end-to-end ingest whose trace joins against the DB.
