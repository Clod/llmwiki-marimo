# LLMWiki — Internals (§4, §5, §14)

> Part of the [LLMWiki Programmer Manual](../programmer_manual.md). Section
> numbers are **global** — a `§N` always means the same section wherever it is
> cited. Where each lives:
>
> | Sections | File |
> |---|---|
> | §1 §2 §3 §10 §11 §13 | [`programmer_manual.md`](../programmer_manual.md) — orientation, layers, directory map, constraints, glossary |
> | §6 | [`workflows.md`](workflows.md) — one entry per workflow, with contracts |
> | §4 §5 §14 | [`internals.md`](internals.md) — schema, tool layer, tracing |
> | §7 §8 §9 §15 | [`apps.md`](apps.md) — Marimo apps, configuration, testing, datasets |

The parts you touch when changing how data is stored, read, or observed: the
SQLite schema and its citation graph, the tool layer built on it, and the opt-in
trace of every LLM call.

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

`chunks_fts` uses `porter unicode61`, which **splits on hyphens**. An unquoted
`mortgage-backed` raises a MATCH syntax error ("no such column: backed") —
FTS5 parses the hyphen as a column filter — which `search_chunks` swallows,
returning `[]`. Quote the term (`"mortgage-backed"`) or use plain words.

The tokenizer is unchanged for non-English wikis in v1: `unicode61` folds
diacritics (`política` matches `politica` — accent-insensitive search already
works), and the English `porter` stemmer is largely inert on Spanish. Since each
wiki owns its `index.db`, a per-wiki tokenizer (e.g. `unicode61 remove_diacritics
2` without `porter`) is a possible future refinement — see the multilingual
design doc §8.

---

## 5. Native Tool Layer

These functions are the CRUD primitives every other layer depends on. They know  
*how* to read/write the wiki and the DB; they do not know *why* or *when*.

| Module                | Key functions                                                                                                             | What it does                                                                                |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `tools/db.py`         | `open_db(path)`, `get_connection(path)`                                                                                   | Opens the SQLite DB (applies `sqlite_schema.sql`, idempotent); provides a context-manager connection |
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
  five `chat.completions.create` call sites in `wiki_generator.py` changed. `wrap()` is idempotent (a client
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

---

