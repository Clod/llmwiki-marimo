# Ingestion LLM + Data-Flow Trace (JSONL + sidecars)

## Goal
Give manual testing of the English-PDF ingestion an observability layer: a write-only,
opt-in trace that records (a) every LLM exchange and (b) the path each piece of
information takes through the pipeline, correlated so one PDF can be followed
extract → chunk → structured-extraction → concept pages → summary → overview.
The trace must be easy to hand to an LLM for analysis and cross-checking against the DB.

Explicit non-goal: NO replay, NO assertions, NO frozen golden output. (The
record/replay "cassette" idea was rejected — a prompt change would invalidate it.)

## Scope
- **Ingestion only** (`ingest_file`, `batch_ingest`, `scan_and_ingest`). Chat agent
  (PydanticAI) and lint/repair are out of scope for v1.

## Requirements
- **Format**: JSONL (one self-describing event per line). First line is a `meta`
  header carrying schema version + a `db_join_map` (event field ↔ DB table.column)
  so an analyzing LLM can join trace ↔ database without guessing.
- **Correlation keys aligned to the DB**: every event carries the identifiers the
  schema uses — `document_id` (documents.id), `relative_path`, `chunk_index`
  (document_chunks), `page` (document_pages), reference edges
  (document_references.source/target/type), `run_id`, monotonic `seq`.
- **Intermediate data captured** as content: extracted page text, chunk text,
  prompts, responses, generated markdown.
- **Heavy payloads live in sidecar files** (content-addressed by sha256) under the
  run directory; events reference them by relative path + sha256 + byte size. The
  structural JSONL stays compact and joinable.
- **Unpluggable payload channels**: each payload category can be switched off
  independently. When a channel is off the event STILL records sha256 + byte size
  (computed in-memory) but writes no sidecar — structure stays verifiable, bulk
  doesn't bloat.
- **LLM exchanges captured via a transparent proxy** around the client — zero
  changes to the ~6 `client.chat.completions.create` call sites. The proxy returns
  the real response object untouched.
- **API keys / credentials never written** to the trace.
- **Zero overhead when disabled** (Null tracer: every method a no-op, `wrap()`
  returns the client unchanged).

## Activation
- `WIKI_TRACE=1` turns the trace on (independent of `WIKI_DEBUG`).
- `WIKI_TRACE_CAPTURE` selects payload channels:
  - unset → default `all` (capture intermediate data, per the requirement)
  - `none` → core trace only (metadata + hashes, no sidecars)
  - comma list of `extracted_text,chunks,prompts,responses,markdown`
- Output: `<workspace>/.llmwiki/traces/<run_id>/` with
  - `trace.jsonl`  (events; line 1 = meta header)
  - `payloads/<sha256>.<ext>`  (sidecars for enabled channels)
  - `run_id` = `YYYYMMDDTHHMMSSZ-<6hex>` (UTC timestamp + short uuid)

## Event types
`meta`, `run_start`, `run_end`, `document_start`, `document_end`,
`stage_start`, `stage_end`, `llm_call`, `artifact`.

- `llm_call`: document_id, stage, model, params (temperature…), latency_ms, usage
  (prompt/completion/total tokens when present), prompt_ref/sha256/chars,
  response_ref/sha256/chars.
- `artifact`: channel, document_id, stage, name, ref/sha256/bytes, plus structural
  meta (page, chunk_index, count, relative_path) for the DB join.

## Architecture
- New module `base/domain/ingestion/trace.py`:
  - `IngestionTracer` (real) + `NullTracer` (disabled) sharing one interface.
  - contextvars hold current `document_id` + `stage` so the client proxy can tag
    `llm_call` without signature changes.
  - `tracer.wrap(client)` → `TracingClient` proxy (`.chat.completions.create`).
  - `tracer.document(...)`, `tracer.stage(...)` context managers.
  - `tracer.artifact(channel, name, content, **meta)`.
  - `run_scope(workspace)` context manager: creates+activates a tracer if none is
    active (single file), closes only the one it created (so a batch = one trace).
- Pipeline integration (contained edits in `pipeline.py` only):
  - entry points open `run_scope` and `tracer.wrap(llm_client)`.
  - wrap the per-file body in `tracer.document(...)`, and the existing Steps 4–10
    in `tracer.stage(...)`, emitting `artifact` for extracted text, chunks,
    extraction JSON, each concept md, summary md, overview md.
- Render helper `scripts/render_trace.py`: reads a `trace.jsonl` and prints a
  per-document readable timeline (resolves sidecar refs on demand).

## Acceptance Criteria
- [ ] With `WIKI_TRACE` unset, ingestion behaviour and output are byte-identical
      to today (Null tracer, no files written).
- [ ] `WIKI_TRACE=1` ingest of one PDF produces `trace.jsonl` whose first line is a
      `meta` header with `schema_version` + `db_join_map`.
- [ ] Every `client.chat.completions.create` during the run yields one `llm_call`
      event with model, latency, and prompt/response sha256.
- [ ] Each pipeline stage yields `stage_start`/`stage_end`; extracted text, chunks,
      generated concept/summary/overview markdown appear as `artifact` events.
- [ ] `WIKI_TRACE_CAPTURE=none` writes no sidecars but `artifact`/`llm_call` events
      still carry sha256 + byte size.
- [ ] `document_id`/`relative_path`/`chunk_index` in the trace match the rows the
      same run wrote to the DB (manually verifiable against the join map).
- [ ] No API key appears anywhere in `trace.jsonl` or sidecars.
- [ ] Unit tests cover: Null no-op path, channel toggling, sha/size always present,
      client proxy transparency, meta header shape. `pytest` + ruff/black clean.

## Technical Notes
- All ingestion LLM calls are non-streaming `client.chat.completions.create`
  returning `response.choices[0].message.content` — the proxy reads usage +
  content defensively (tolerate missing `.usage`).
- Content-addressed sidecars dedupe identical payloads automatically.
- Logging guidelines forbid logging full text on the `wiki` logger; this trace is a
  SEPARATE opt-in artifact channel, not the logger, so capturing full text here is
  intentional and consistent with that guidance.
