# LLMWiki — Apps, Configuration & Testing (§7, §8, §9, §15)

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

The edges of the system: the three Marimo notebooks that are the entire user
interface, what you can configure per workspace, how the project is tested, and
the optional datasets lane with its example domain overlay.

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

Three-column layout (cells tagged `@app.cell(column=N)` — no grid file; the
former `layouts/read_app.grid.json` was removed because a positional grid
desyncs whenever cells are added):

| Pane                  | Cell                       | Role                                                                                                   |
| --------------------- | -------------------------- | ------------------------------------------------------------------------------------------------------ |
| Left (top)            | `wiki_picker` / `wiki_add` / `wiki_add_runner` | Wiki dropdown + "open another folder" accordion (§7.1)                             |
| — (logic only)        | `wiki_state` / `wiki_context` | Active-wiki `mo.state`; derives `WIKI_PATH`/`wiki_db_path`/`wiki_chat_config`/`wiki_agent` on switch (§7.1) |
| Left                  | `left_panel`               | Page selector with refresh button (`scan_pages()`)                                                     |
| Left (below selector) | `delete_widget_cell`       | Renders `DeleteConfirmWidget` — disabled when no page is selected; resets event counter on page change |
| — (logic only)        | `delete_event_cell`        | Watches `delete_widget.event_id`; fires `set_delete_trigger` on confirm                                |
| — (logic only)        | `delete_runner`            | Calls `wiki_fs.delete_page`, rescans page list, clears selection                                       |
| Middle                | `middle_panel`             | Renders the selected page as markdown + nav links                                                      |
| Right                 | `chat_panel`               | PydanticAI agent + suggested prompts; grounding guardrail (§15) — strict → buffered + gated, off → streamed |
| Right                 | `guardrail_flag`, `guardrail_toggle` | "Strict mode" switch flips grounding; the flag is a plain dict read live inside `respond`, so toggling never rebuilds the chat |
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
`run_stream(...)` call, not to the factory. The agent pins
`ModelSettings(temperature=0.0)`: a grounding/traceability agent wants the single
most-likely, corpus-grounded continuation, and higher temperatures make it
intermittently skip the retrieval tools or drop citations. Temperature 0 makes the
retrieve-then-cite behaviour deterministic and reproducible — so a model's
`eval_chat_model.py` verdict (§9) is a property of the model, not sampling luck.
See the README "LLM providers" note for the empirical model-size floor (~12B local).

### `trace_report_app.py`

A read-only viewer for ingestion traces (§14). Point it at a directory, it
discovers every `trace.jsonl` run underneath, and renders each run two ways: a
human-readable per-document timeline (same layout as `scripts/render_trace.py`)
and an `mo.tree` of the raw events grouped by document. Payload channels
(`prompts`, `responses`, `extracted_text`, `chunks`, `markdown`) can be inlined
on demand. It only reads traces produced by `WIKI_TRACE=1` runs — it never
ingests or writes anything.

### Quick-start installer (`quickstart.py`)

`quickstart.py` (repo root) is a **stdlib-only** onboarding script — the only
prerequisite on the user's machine is **Python 3.12+** (no `uv`). It must run
*before* any dependency exists, so it imports nothing third-party and shells out
to `python -m venv`, `pip`, and (optionally) `ollama`.

What it does, in order: gates the Python version → copies a pre-ingested demo
from `examples/` into `wikis/<demo>/` → runs a provider wizard (**local Ollama
by default**, or any OpenAI-compatible endpoint such as LM Studio / OpenRouter;
`getpass` for keys) → writes `.env` (never
clobbering an existing one without consent) → `python -m venv .venv` +
`pip install -r requirements.txt` → optional `/models` reachability check →
advisory grounding check of the configured model(s)
(`scripts/eval_chat_model.py --brief`; `--no-eval` skips) → launches the read app.

```bash
python3 quickstart.py                                            # interactive
python3 quickstart.py --demo fairy-tales --provider ollama \
        --yes --no-launch                                        # unattended
```

- **`requirements.txt`** is hash-pinned, regenerated from `uv.lock` with
  `uv export --no-dev --no-emit-project` — so the installer's plain-`pip` path
  reproduces the exact tested versions without uv, and `--no-emit-project`
  keeps the local package out (the marimo apps self-add `base/` to `sys.path`,
  so nothing needs installing as a package). Regenerate it whenever `uv.lock`
  changes.
- **`examples/<name>/`** demos are auto-discovered (any subfolder with a
  `wiki/` dir). Each is a complete pre-ingested workspace, so browsing works
  with no LLM; only chat calls the model. `examples/fairy-tales/.llmwiki/index.db`
  is force-added past the demo's own `.gitignore` (which excludes `.llmwiki/`).
- The step functions are factored to be importable, so a planned **tkinter**
  front-end can wrap them and fall back to the console wizard when `import
  tkinter` fails.

### Running locally

```bash
# Opens on $WIKI_PATH from .env (the default) — switch wikis in-app via the picker (§7.1)
uv run marimo run --no-sandbox marimo/ingest_app.py --port 2718
uv run marimo run --no-sandbox marimo/read_app.py --port 2720

# Start on a specific workspace (still switchable in-app afterwards)
WIKI_PATH=/path/to/workspace uv run marimo run --no-sandbox marimo/read_app.py --port 2720
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
— see the [ROADMAP](../../ROADMAP.md)). There is no PDF-backend selector setting today.

### `workspace/wiki_config.toml` (optional, per-workspace)

Two optional sections, each with built-in defaults (absent file → an English
wiki with the default assistant). See `wiki_config.example.toml` (English) or
`wiki_config_es.example.toml` (Spanish, `language = "es"`) for a template.

- **`[wiki] language`** — the wiki's **content language** (`"en"` default, `"es"`
  supported; extensible — add a `Locale` to `base/domain/i18n.py`). It is a
  *per-wiki* property, so one person can run an English wiki and a Spanish wiki
  side by side. The wiki language governs all generated output **regardless of
  the source documents' language**: summaries, concept pages, the overview, the
  index / See-also / Sources headers, the lint/repair regenerations, and the chat
  assistant's answers and default suggested prompts. It is resolved once by
  `domain.wiki_settings.load_wiki_language`, threaded through the ingestion
  pipeline (and the lint/repair pass and chat→wiki save), and applied to the chat
  agent's system prompt at creation. **Not** localized in v1: the marimo app UI,
  the `log.md` ingest log, the lint/repair *diagnostic* notes (contradiction /
  data-gap), and the legacy `regenerate_wiki_pages` path.
- **`[assistant] system_prompt` / `suggested_prompts`** — override the chat
  assistant. See §6.7 for an example. When `suggested_prompts` is omitted, the
  localized defaults for the wiki language are used.

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
uv run pytest tests/unit/ -v               # 470 unit tests — fast, no LLM
uv run pytest tests/e2e/ -v -s             # 11 E2E tests — live marimo + LLM (test_ingest_pdf is parametrized over 3 PDFs)
```

Slash commands: `/test-ingest`, `/test-read`, `/test-all`.

### Unit infrastructure

**`FakeLLMClient`** (`tests/helpers/fake_llm.py`) duck-types the OpenAI client.  
Configure responses before each test:

```python
llm = FakeLLMClient(response_content="## Fixed response")

# Sequential multi-step pipelines
llm.responses = ["JSON extraction", "Concept page", "Overview text"]
# Call index advances automatically; last response repeats if exhausted.

assert len(llm.calls) == 3
```

**`tmp_workspace`** (`tests/helpers/workspace.py`) yields a fresh disposable  
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

### Half-automated UAT eval packet

`scripts/build_eval_packet.py` generates a single self-contained markdown file
(an "eval packet") to a gitignored `eval_reports/`. The generation is automated;
the **judging** is done by pasting the packet into any capable chat model and
having it fill in the scorecard — so it scores LLM-output quality that
deterministic assertions can't.

Two sections:

- **Part 1 — Chat grounding & citations.** Runs the fixed `domain.eval.rubric.CHAT_PROBES`
  through the chat model (`LLM_MODEL`) against the chat target wiki and inlines the
  pages each answer cited, plus the regex pre-screen from `domain.eval.graders`.
- **Part 2 — Ingestion faithfulness & coverage.** Per source: the extracted source
  text alongside the generated summary + concept pages (found via the `cites` edges,
  `domain.eval.reader`).

Modes and behaviour:

- **Default (benchmark).** Chat runs against the frozen golden corpus; ingestion
  **re-ingests** the four PDFs with the current `WIKI_LLM_MODEL` (real LLM calls).
- `--wiki PATH` targets an existing wiki and reads its pages as-is (no re-ingest).
- `--skip-chat` / `--skip-ingestion` omit a part. The packet header records both
  models, the corpus content hash, and the rubric version, so two packets are comparable.

```bash
uv run python scripts/build_eval_packet.py                 # benchmark corpus
uv run python scripts/build_eval_packet.py --wiki PATH      # an existing wiki
uv run python scripts/build_eval_packet.py --skip-ingestion # chat only (cheap)
```

The pure pieces — `domain.eval.packet` (truncation, corpus hash, rendering) and
`domain.eval.graders` — are unit-tested in `tests/unit/test_eval_packet.py` and
`tests/unit/test_eval_graders.py`; the DB queries in `domain.eval.reader` are
covered against the frozen corpus by `tests/regression/test_eval_reader_golden.py`.
`domain.tools.db.seed_workspace_row` (used to create a fresh DB before re-ingest) is
shared with `build_golden_corpus.py`.

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

## 15. Datasets, Grounding Guardrail & the `finance_argentina` Overlay

> Turns the wiki from a pure prose encyclopedia into a **knowledge-and-data**
> engine: alongside the durable concept pages it can carry **live, structured
> datasets**, and a domain overlay can compute **deterministic, cited advice**
> over them. The engine is domain-neutral; `finance_argentina` is the first
> overlay. Full design: `.trellis/spec/backend/datasets-format.md` (the
> executable format contract), `docs/design_datasets.md` (interface design),
> `docs/design_finance_argentina.md` (the overlay).

### 15.1 The two-kind-of-knowledge model

| Kind | Nature | Cadence | Pipeline |
|------|--------|---------|----------|
| **Conceptual** | distilled prose (what something *is*) | seldom | the concept pipeline (§6), unchanged |
| **Dataset** | structured tabular values (the current numbers) | periodic | this section — parsed structurally, replace-on-refresh, **never LLM-distilled** |

Datasets are **opt-in per workspace**: dormant unless `WORKSPACE/datasets/` holds
≥1 valid file (`datasets.source.has_active_datasets`). A wiki without it is
byte-identical to before (guarded by `tests/unit/test_chat_agent_datasets.py::test_optionality_guard`).

### 15.2 Dataset engine (`base/domain/datasets/`, domain-neutral)

- **Format** — one markdown file per category, `datasets/<categoria>.md`, with
  YAML front-matter declaring its shape (`type: dataset`, `categoria`, `formato`
  ∈ {`matriz`, `largo`}, `as_of`, `fuente`, + mapping keys) and one table.
  `parser.parse_dataset_markdown` flattens it to a normalized row `(categoria,
  clave, metrica, valor, unidad, dims, as_of, fuente)`. The reject-file / skip-row
  / warn validation matrix (all logged, never swallowed) is in the spec §5.
- **Access** — `models.DatasetSource` Protocol (`categories()`, `query()`);
  `source.LocalMarkdownSource` is the parse-on-read implementation. Backend-
  agnostic, so a remote service could implement the same Protocol. `query()`
  confines the (LLM-supplied) `categoria` to `datasets/` — a path-traversal guard
  mirroring `read_wiki_page`.
- **Chat tool** — `chat/dataset_tools.query_dataset` returns a compact, cited
  markdown table; the agent quotes values verbatim with their `as_of` date.
  Registered on the agent only when the workspace has datasets, via the generic
  `extra_tools`/`extra_prompt` seam on `chat/agent.create_agent` (engine stays
  domain-agnostic).

### 15.3 Grounding guardrail (`base/domain/chat/guardrail.py`)

A deterministic post-check: a run is *grounded* iff some tool returned
substantive content (`has_grounding`); otherwise `enforce_grounding` replaces the
answer with a language-appropriate refusal (`REFUSAL_ES`/`REFUSAL_EN`). It catches
answers the model leaks despite the system prompt (general knowledge on "related"
topics). Wired in `read_app.respond`, toggled by the "Strict mode" GUI switch:

- **ON** → run to completion, then gate (refuse if ungrounded). Cannot stream —
  you can't retract text already shown.
- **OFF** → stream token-by-token (original UX), ungated.

The toggle is a plain shared dict read at call-time (not `mo.state`, which isn't
reliably live inside the async chat closure), so flipping it never rebuilds the
chat. Known limit: it catches *no-evidence* answers, not an answer that ignored
evidence it did retrieve (that needs answer-vs-source verification — deferred).

### 15.4 `finance_argentina` overlay (`base/domain/finance_argentina/`)

Concrete domain logic (Argentine personal finance), Spanish-facing. Reads the
engine's `DatasetSource` + dataset-file front-matter; the engine never imports it.

- **Requirements manifest** (`requirements.md` + `requirements.py`) — single
  source of truth: per category, which dataset `metricas` and concept
  `attributes` are required. Read by the validator (and, later, a producer).
- **Concept attributes** (`concept_attrs.py`) — finance vocabulary read from the
  dataset-file front-matter: `disponibilidad`, `plazos_dias`, `monto_minimo`,
  `moneda`, `metodo_calculo`, `metrica_tasa`, `depende_de` (the *factual* driver
  of variability for non-deterministic instruments — distinct from cited risk).
- **Validator** (`validator.py`) — a domain lint check over **structured md only**
  (datasets + concept attributes, never prose/PDFs); excludes a failing category
  from the advisory with an honest reason.
- **Formulae** (`formulae.py`) — deterministic `tea(metodo_calculo, r, term)` and
  `projected_gain(P, tea, horizon_days)`; raises on `no_deterministico` rather
  than fabricating.
- **Advisory** (`advisory.py`) — `estimate_alternatives(amount, horizon_months,
  …)`: validator gate → eligibility (currency / min amount / term-fit) → list
  **every** eligible option ranked by gain, plus a separate **variable-return**
  section (flagged "no estimable" with its `depende_de` driver). Every figure
  cited (value · `as_of` · `fuente`) under the stated assumption *"si la tasa
  actual se mantiene"*.
- **Tool + activation** (`agent_tool.py`) — the Spanish `estimar_alternativas`
  tool and `activate(workspace)`, which registers it (via `extra_tools`) only
  when the manifest validates with ≥1 passing category.

**Honesty guarantees:** cite source **and date** for every figure; gain math is
deterministic code, never the LLM; equities / inflation- / FX-linked instruments
are flagged *not estimable* rather than guessed.

### 15.5 Deferred (the "how" and beyond)

- **Producer / data feed** — datasets are authored by hand today (a future
  scheduled job or data-as-a-service would fill them). The cite-source-and-date
  guarantee is only as honest as the data fed in.
- **Held-to-maturity & scenario estimation** (bonds/LECAPs; "if inflation = X%")
  — would move some `no_deterministico` instruments into estimable, under an
  explicit stated assumption.
- **Multi-currency comparison** (FX across ARS/USD), **GRAN** (one concept page →
  several advisory categories), and **personal holdings**.
