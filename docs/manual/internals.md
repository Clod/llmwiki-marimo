# LLMWiki — Internals (§4, §5, §14)

> Part of the [LLMWiki Programmer Manual](programmer_manual.md). Section
> numbers are **global** — a `§N` always means the same section wherever it is
> cited. Where each lives:
>
> | Sections | File |
> |---|---|
> | §1 §2 §3 §10 §11 §13 | [`programmer_manual.md`](programmer_manual.md) — orientation, layers, directory map, constraints, glossary |
> | §6 | [`workflows.md`](workflows.md) — one entry per workflow, with contracts |
> | §4 §5 §14 | [`internals.md`](internals.md) — schema, tool layer, tracing |
> | §7 §8 §9 §15 | [`apps.md`](apps.md) — the web interface, configuration, testing, datasets |

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
| `source_kind`              | `'source'` / `'wiki'`                   | Raw source file (PDF, office, md, txt) vs LLM-generated markdown     |
| `status`                   | `'processing'` / `'ready'` / `'failed'` | Pipeline stage (see §10)                   |
| `path`                     | e.g. `/wiki/summaries/`                 | Directory path                             |
| `relative_path`            | UNIQUE                                  | Full path from workspace root — upsert key |
| `source_document_id`       | UUID or NULL                            | Summary pages point back to their source   |
| `content_hash`, `mtime_ns` | —                                       | Used by `detector.needs_ingestion`         |

### FTS5 tokenizer

`chunks_fts` uses `unicode61`, which **splits on hyphens**. An unquoted
`mortgage-backed` raises a MATCH syntax error ("no such column: backed") —
FTS5 parses the hyphen as a column filter — which `search_chunks` swallows,
returning `[]`. Quote the term (`"mortgage-backed"`) or use plain words.

The index holds `document_chunks.content_stemmed`: each fragment stemmed with
the Snowball stemmer of the wiki's language (`base/domain/text/stemming.py`).
`search_chunks` and the model's `search_source_chunks` stem each query the same
way before `MATCH`, so `plazo fijo` and `plazos fijos` find the same fragments,
and the results carry `content`, the original text. The coverage gate compares
dataset categories, concept-page titles and aliases by stem too; the blacklist
and the dataset keys compare by whole word. An index built before stemming is
rebuilt by `open_db` on first open, with no model call.

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

**Entry point:** `base/domain/tracing.py`. **Activation:** `WIKI_TRACE=1`.
**Status:** ✅ one chat turn, and one ingestion (a single file or a batch); lint
and repair are out of scope for v1.

The trace records one [OpenTelemetry](https://opentelemetry.io/) span per step
of the code, named by the identifier of its node in the diagrams of
`design_stemming.md` — `Q5` for the coverage-roster check, `I4` for text
extraction, `W1` for chunking. The span names of one trace are therefore the
path the run took through the diagram, and the path can be compared with the
diagram node by node. Model calls are spans too: Pydantic AI's own
instrumentation for the chat agent, and a proxied OpenAI client
(`tracing.wrap_openai`) for ingestion.

### 14.1 What it is — and what it is *not*

| | |
| --- | --- |
| ✅ **Is** | A debugging/observability artifact you read after a run (or feed to an LLM to audit). One JSONL span stream per workspace, in OpenTelemetry's own `ReadableSpan.to_json` format, plus content-addressed sidecars for heavy payloads. |
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
| `WIKI_TRACE` | `1` to enable (anything but unset/`0`/`false`) | Master switch, read by `tracing.enabled()`. Unset → a no-op tracer; chat and ingestion behaviour and output are byte-identical to an untraced run. |
| `WIKI_TRACE_CAPTURE` | `all` (default when unset) · `none` · CSV of channels | Which payload channels write sidecar files (see §14.4). Read by `tracing._channels()`. Unknown channel names are ignored with a warning. |

Output goes under the workspace (which is gitignored via `.llmwiki/`):

```
<workspace>/.llmwiki/traces/
├── spans.jsonl              # every span of every trace of this workspace, one per line
└── payloads/
    └── <sha256>.<ext>       # one content-addressed sidecar per captured payload
```

Every run — a chat turn or an ingest — appends to the same `spans.jsonl`;
there is no per-run directory. A run is identified by its `trace_id` (one chat
turn, or one ingest of a single file or a batch); one document inside a batch
is identified by the `document_id` on its `document` span.

### 14.3 The span file (`spans.jsonl`)

One JSON object per line, in the shape of `ReadableSpan.to_json`: `name`,
`context.trace_id`, `context.span_id`, `parent_id` (`null` for a root),
`start_time`, `end_time`, `status`, `attributes`, `events`. All spans of one
run share a `trace_id`; `parent_id` arranges them as a tree.

Root spans are named `turn` (one chat turn) or `ingest` (one ingestion). An
`ingest` root has one child span named `document` per file, carrying
`llmwiki.document_id` and `llmwiki.relative_path`
(`tracing.node("document", ...)`, `base/domain/ingestion/pipeline.py:120`).

Our own attributes carry the prefix `llmwiki.`:

| Attribute | Carried by | Meaning |
| --- | --- | --- |
| `llmwiki.node` | every node span | the diagram node identifier, same as the span name |
| `llmwiki.diagram` | every node span | `reading` or `writing` |
| `llmwiki.workspace` | the root span only | the workspace path; read by the file processor to route the span to its `spans.jsonl` |
| `llmwiki.conversation_id`, `llmwiki.turn`, `llmwiki.mode` | the `turn` root | which conversation and turn, and the chat mode |
| `llmwiki.<name>` | any node | the values that node computed, e.g. `llmwiki.answer` and `llmwiki.roster_size` on `Q5` |
| `llmwiki.<name>.sha256`, `llmwiki.<name>.bytes`, `llmwiki.<name>.ref` | a node with a heavy payload | written by `Node.payload`; `ref` is relative to `.llmwiki/traces/` |

Model calls are spans with `gen_ai.*` attributes: Pydantic AI's own spans for
the chat agent (`Agent.instrument_all`, wired in `tracing._get_provider`,
`base/domain/tracing.py:132`), and spans named `chat <model>` for ingestion,
written by `tracing.wrap_openai` (`base/domain/tracing.py:345`), with
`gen_ai.request.model`, `gen_ai.usage.input_tokens`,
`gen_ai.usage.output_tokens`, and the payloads `prompt`/`response`.

### 14.4 Sidecars & unpluggable channels

Heavy payloads never bloat the span file — `Node.payload`
(`base/domain/tracing.py:196`) writes them to `payloads/<sha256>.<ext>` and
records `sha256` and `bytes` on the span; the sidecar file itself is written
only when its channel is on. Sidecars are **content-addressed**, so identical
payloads are stored once.

The five channels are independently **unpluggable** via `WIKI_TRACE_CAPTURE`
(`tracing.CHANNELS`):

| Channel | Captures |
| ------- | -------- |
| `extracted_text` | the joined per-page source text |
| `chunks` | the FTS5 chunk list (JSON: index, page, token_count, start_char, content) |
| `prompts` | the full request `messages` for each LLM call |
| `responses` | the raw model response text for each LLM call |
| `markdown` | each generated concept / summary / overview page |

**Key invariant:** turning a channel *off* does **not** blind the trace structurally —
the node's span still carries `sha256` and `bytes`; only `ref` is absent, so the
sidecar was never written. So `WIKI_TRACE_CAPTURE=none` still lets you verify
*that* content existed and *whether it changed*, just not read it.

### 14.5 How it's wired

- **One root per run.** `tracing.root(name, workspace, **attrs)` opens the run's
  root span. `chat/preretrieval.py:321` opens `turn`; `ingestion/pipeline.py:120`
  and `ingestion/batch.py:88` open `ingest`. Called from inside a run already in
  progress — a batch ingesting several files — `root` reuses the current span
  instead of opening a second one (`base/domain/tracing.py:235`), so a batch is
  one trace.
- **One span per diagram node.** `tracing.node(node_id, diagram, **attrs)` opens
  the span of a node that does work; `tracing.mark(node_id, diagram, **attrs)`
  records a decision or a value as a zero-duration span. Both are called
  throughout `chat/preretrieval.py` (e.g. `:363` for `Q5`, `:394` for a refusal)
  and `ingestion/pipeline.py` (e.g. `:252` for `I4`, `:300` for `W3`).
- **Transparent client proxy.** `tracing.wrap_openai(client)`
  (`base/domain/tracing.py:345`) returns a client whose `chat.completions.create`
  is a span and whose real response object is returned untouched. It is
  idempotent (a client already wrapped is returned as-is), which matters on the
  batch path, and returns the client unchanged when tracing is off.
- **Disabled = free.** `tracing.enabled()` (`base/domain/tracing.py:68`) gates a
  module-level no-op tracer; every function above becomes a no-op and no file is
  created.
- **Best-effort.** A span-attribute or payload failure is caught and logged at
  debug level (`base/domain/tracing.py:182`, `:213`); tracing never breaks a
  chat turn or an ingest.

### 14.6 What each node covers

The node identifiers of one ingestion and one chat turn are the ones tabulated
in `design_stemming.md` §1.1.1 (writing: I1–I16, E1–E4, W1–W4) and §1.2.1
(reading: Q1–Q21, Q7a–Q7d, refusal causes R1, R2a, R2b, R3 and R4). Those two tables are the reference
for which node covers which line of code; this section does not repeat them.

### 14.7 Rendering — `scripts/render_trace.py`

`spans.jsonl` is machine-first; the render script turns it into a readable
timeline, one trace at a time, with spans indented by depth under their parent.

```bash
# Every trace in the file, in start order
python scripts/render_trace.py <workspace-or-spans.jsonl>

# One trace only
python scripts/render_trace.py <workspace> --trace <trace_id>

# Every turn of one conversation, in turn order
python scripts/render_trace.py <workspace> --conversation <conversation_id>

# One document, from its `document` span down
python scripts/render_trace.py <workspace> --doc <document_id>

# Inline the actual prompts + responses (resolves the sidecars)
python scripts/render_trace.py <workspace> --show prompts,responses
```

### 14.8 Cross-checking a trace against the DB

The intended audit: the `document` span's `llmwiki.document_id` and
`llmwiki.relative_path` should match a row of `documents`, and the fragment
count `W3` records (`fragments=len(chunks)`, `ingestion/pipeline.py:300`)
should equal the rows of `document_chunks` for that document. Since every span
that touches a document carries the same `document_id` — set once on `I3`
(`ingestion/pipeline.py:241`) and again on the `document` span — filtering
`spans.jsonl` by that value and joining on `documents.id` recovers the same
audit the ingestion trace's former `db_join_map` header gave directly. Hand
the filtered spans to an LLM and ask it to reconcile against `index.db`, or
script the join in a few lines of SQL + `json`.

### 14.9 Guarantees & references

- **No credentials.** Only the request `messages`, the response text, and
  token counts are recorded — never the API key (it lives on the client,
  which is never serialised).
- **Crash-safe.** Each span is appended on close (`_WorkspaceFileProcessor.on_end`,
  `base/domain/tracing.py:110`), so a trace is useful even if a long run is
  interrupted.
- **Code:** `base/domain/tracing.py` — `enabled`, `root`, `node`, `mark`,
  `Node.set`, `Node.payload`, `wrap_openai`, and the constants `READING`,
  `WRITING`, `CHANNELS`, `SPAN_FILE`, `ENV_VAR`, `CAPTURE_ENV_VAR`.
- **Tests:** `tests/unit/test_tracing.py` — activation, workspace routing,
  parent/child trace-id sharing, attribute coercion, payload channel
  toggling, root reuse, two workspaces in one process, and `wrap_openai`.
  `tests/unit/test_render_trace.py` covers the render script.

---

