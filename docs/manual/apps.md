# LLMWiki — Apps, Configuration & Testing (§7, §8, §9, §15)

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

The edges of the system: the web interface, what you can configure per workspace, how the project is tested, and
the optional datasets lane with its example domain overlay.

---

## 7. The web interface

The web interface is the interface of the project. It lives in `web/`: a FastAPI
application that renders HTML with Jinja2, updates the page with HTMX, and streams
chat answers and the progress of long operations as Server-Sent Events (SSE). The
routes hold no application logic: they call `base/services/` (`wiki.py`, `chat.py`,
`ingest.py`), which calls `base/domain/`. The layout of `web/`, the routes and the
environment variables are in [`web/README.md`](../../web/README.md); this section
describes each screen.

The marimo apps in `marimo/` are being retired. They stay in the repository until a
separate change removes them, and this manual does not describe them.

The application listens on `localhost` and has no authentication: it is for one user
on one machine. The interface is in English and in Spanish (the **EN | ES** switch at the right of
the header; §7.11); the labels quoted below are the English ones, the message ids of the templates. Every screen of a wiki has the same
header: the name of the project (a link to the picker), the wiki selector, and six
tabs, **Read**, **Ingest**, **Maintain**, **Relations**, **Vocabulary** and **History**.

### 7.1 Wiki picker

Route: `GET /` (`web/routes/picker.py`). The screen is titled "Choose a wiki" and
lists the wikis in a table with the columns Wiki, Path, Language, Sources and Pages,
and an **Open** link per row. The first wiki of the recent list carries the tag
"last opened". The list holds the default wiki (`WIKI_PATH`), the wiki folders under
`WIKI_HOME`, and the recent list (`~/.llmwiki/recent_wikis.json`, shared with the marimo
apps); a recent folder that is no longer a wiki (no `wiki/` nor `.llmwiki/`) is not
listed, and the picker rewrites the recent list without it (`wiki_registry.prune_recent`).
A row that comes only from the recent list has **Remove from the list**
(`POST /recent/remove`): the path leaves the list and the folder stays on disk. A wiki
that stops existing is never recreated: its screens answer 404 with a link to the
picker; only the default wiki of `WIKI_PATH` is created on first use. Under the table, **Open another folder…** opens the folder browser, a
modal dialog (`GET /browse`, `web/templates/_browse_dialog.html`). The dialog shows one
folder: its absolute path in a field (Enter or **Go** shows another), its subfolders
(hidden ones left out, those with `wiki/` or `.llmwiki/` tagged "wiki"), and `..` to go
up; a click on a subfolder shows it (`GET /browse/panel`). **Create folder** creates a
folder inside the one shown and shows it (`POST /browse/mkdir`; a name with "/" or a
leading dot, or one that exists, is refused). **Open this folder** posts to
`POST /open`, which accepts an absolute path to an existing folder
(`picker.check_folder`), adds it to the recent list and opens the wiki; a refused path
returns to the picker. A folder that holds no wiki yet (no `wiki/` nor `.llmwiki/`)
shows **Create a wiki here…** instead: a confirmation in the dialog says what is created
(`wiki/`, `sources/`, `.llmwiki/` and a git repository), and only **Create the wiki**
posts `create=true`. Without it, `POST /open` creates nothing in a plain folder and
does not list it. `GET /w/{wiki_id}/` opens a
wiki on its `overview` page, or on its first page, and a wiki without pages on Ingest.

`WIKI_PATH` is only the default selection. The pure logic is in
`base/domain/wiki_registry.py` (unit-tested, `tests/unit/test_wiki_registry.py`):

| Function | Role |
| --- | --- |
| `discover_wikis(home)` | immediate sub-folders of `home` that look like a wiki (`is_wiki_dir` → has `wiki/` or `.llmwiki/`), plus `home` itself |
| `merge_options(home, recent, active)` | ordered, de-duplicated option list: active first, then discovered, then recent |
| `load/save/push_recent(...)` | recent-wikis list persisted to `~/.llmwiki/recent_wikis.json` (most-recent-first, capped) |
| `clean_path_input(raw)` | strips surrounding quotes/whitespace from a pasted path ("Copy as Pathname" yields `'/a/b c'`) |
| `resolve_wiki_home(env_wiki_path)` | folder to scan: `$WIKI_HOME`, else parent of `WIKI_PATH`, else `~` |
| `short_label(path)` | compact label like `…/finanzas/my-wiki` |

`picker.wiki_row` reads the folder of each wiki without opening it: listing the wikis
creates nothing on disk, and a wiki without an index shows no counts.

### 7.2 Read — pages and conversation

Routes: `web/routes/pages.py` and `web/routes/chat.py`. The screen has three panels:
the page index, the open page and the conversation. The buttons **Index** and **Chat**
in the header show or hide the index and the conversation.

| Route | What it does |
| --- | --- |
| `GET /w/{wiki_id}/pages/{page}` | The page, rendered by `web/render.py`. The toolbar shows the path of the page and the actions **Edit**, **Relations**, **History** and **Delete**. |
| `GET /w/{wiki_id}/edit/{page}` | The editor: the Markdown at the left and a live preview at the right. The front-matter block is not edited. |
| `POST /w/{wiki_id}/preview/{page}` | The preview of the editor; the textarea sends it 500 ms after the last input. It saves nothing. |
| `POST /w/{wiki_id}/edit/{page}` | Saves the page as one commit. A page changed by someone else since it was opened is refused (`PageConflict`). |
| `POST /w/{wiki_id}/delete/{page}` | Deletes the page. The fixed pages (`index`, `overview`, `log`) cannot be deleted. The button **Delete** asks first: "Delete the page “…”?"; the links to the page are removed, and the page stays in the git history. |
| `GET /w/{wiki_id}/view/{filename}` | A source document: a PDF as it is, an office file as the PDF that ingestion converted it to, a `.txt` as text, a `.md` rendered read-only. A name outside `sources/` is a 404. |

**The index.** Pages are grouped (Resúmenes, Conceptos, General) with a count per group,
and a field "Buscar una página…" filters them by title.

**The conversation.** `POST /w/{wiki_id}/chat` registers the question and returns the
elements that open `GET /w/{wiki_id}/chat/{turn_id}/stream`, which streams the answer
over SSE. The selector **Mode** has three values (`web/deps.py:MODES`):

| Value | Label | Behaviour |
| --- | --- | --- |
| `pre-retrieval` | Pre-retrieval | Code searches the wiki before the model is called and hands over the pages it found. |
| `strict` | Strict | The model consults the wiki with tools; if it did not, the answer is replaced by a refusal (§15.3). |
| `streaming` | No verification | The answer appears as the model writes it. Code does not check that it comes from the wiki. |

Under each answer, one line names the mode and links up to three cited pages. A
conversation belongs to one wiki; the browser keeps its id per wiki in `sessionStorage`,
so the conversation survives navigation, the editor and a reload in the same tab
(`GET /w/{wiki_id}/chat/{conversation_id}/thread`). A new tab starts a new
conversation. **Clear** (`POST /w/{wiki_id}/chat/clear`) forgets the messages.

**Saving a conversation (§6.8).** **Save** asks for a title and its button
**Save** calls `POST /w/{wiki_id}/chat/draft`: the model drafts one concept page for
the whole conversation and the route returns the dialog "Revisar la página". Nothing is
written yet. The dialog holds the Markdown for editing; **Vista previa** switches to the
rendering (`POST /w/{wiki_id}/chat/preview`). **Save to wiki** calls
`POST /w/{wiki_id}/chat/save`, which writes the text exactly as edited, as one commit
(`services.chat.save_reviewed_page`). If a page with that title exists, the dialog
warns that the save replaces it. The agent has no write tool: the save is the user's
action.

### 7.3 Ingest

Routes: `web/routes/ingest.py`. `GET /w/{wiki_id}/ingest` shows two panels, "Add
documents" and "Folder sources/", the console "Progress" and the table "Sources".

| Control | Route | Effect |
| --- | --- | --- |
| File field and **Ingest** | `POST /w/{wiki_id}/ingest` | Saves the files to `sources/` and ingests them. The box "Full repair with the model, slower" adds the model-driven repair of the pages the ingestion touched. A file with an unsupported extension or an invalid name is refused with a line in the console. |
| **Scan sources/ for changes** | `POST /w/{wiki_id}/scan` | Ingests the files of `sources/` that are new or changed since their last ingestion. |
| **Delete** in a row of "Sources" | `POST /w/{wiki_id}/sources/{doc_id}/delete` | After a confirmation, deletes the source and its summary page as a streamed operation: the console shows each step (the summary page deleted, the pages marked stale, the file removed). The pages that cite it stay and are marked stale. The box "Also remove file from sources/" also removes the file; without it the next scan ingests the file again. |

The accepted extensions come from `base/domain/ingestion/formats.py`
(`SUPPORTED_EXTENSIONS`). The table lists each source with its status, number of pages
and update time, and links to `/w/{wiki_id}/view/{filename}`.

### 7.4 Maintain

Route: `GET /w/{wiki_id}/maintain`. A configuration line shows the model, the server,
Java and LibreOffice (`services.ingest.tool_status`). Four panels follow, and the same
console "Progress".

| Panel | Button | Route | Effect |
| --- | --- | --- | --- |
| Summary pages | **Regenerate summary pages** | `POST /w/{wiki_id}/regenerate` | Regenerates every summary page with the model, from the text already extracted (§6.6). Concept pages do not change. |
| Lint and repair | **Run Wiki Lint & Repair** | `POST /w/{wiki_id}/lint` | The wiki-wide lint and repair, with model calls (§6.1, §6.2). |
| Stale pages | **Delete N stale pages** | `POST /w/{wiki_id}/stale/delete` | Deletes the stale pages after a confirmation. |
| Index of the wiki | **Rebuild the index** | `POST /w/{wiki_id}/reindex` | Rebuilds the search database from `sources/` and `wiki/` with no model call (`services.wiki.reindex`). The previous index is kept as `.llmwiki/index.db.bak`. |

Each destructive button first shows a confirmation with its consequences. Only one
operation runs at a time on a wiki.

### 7.5 Relations

Routes: `web/routes/relations.py`. `GET /w/{wiki_id}/relations` draws the graph of the
wiki from `GET /w/{wiki_id}/relations.json` (`services.wiki.graph`, read from
`documents` and `document_references`) with force-graph. Concept pages, summary pages and
source documents are three kinds of node. The controls are the field "Buscar una página…",
the boxes "Show summaries" and "Show sources", and the buttons **Zoom in**,
**Zoom out** and **Fit**. A click on a page node opens the page; a click on a source
node opens the source in a new tab. With `?page=<page>&depth=1|2` the screen shows the
local graph of one page; the actions **See the whole graph** and **Read the page** leave
it.

### 7.6 Vocabulary

Routes: `web/routes/vocabulary.py`, `base/services/vocabulary.py`. `GET /w/{wiki_id}/vocabulary`
shows the vocabulary the pre-retrieval gate uses. The screen takes the height of the
window: the findings of `vocabulary_check` on top, then three columns, each list in its
own panel that scrolls.

| Panel | Content | Changes |
| --- | --- | --- |
| **Roster** | The names the wiki covers: the dataset categories and keys of `datasets/` and the titles of the ready concept pages, each with its origin; a concept page links to the page. Built on every question, never stored | Read only. The field **Search the roster…** shows the names that contain the text typed, without case nor accents. A dataset name (a category or a key) opens the dataset in a dialog (`GET …/vocabulary/dataset?category=…` or `?key=…`, `services.vocabulary.dataset_tables`): the file (`datasets/<category>.md`) as its title, the metric, unit, date and source, then one row per key and one column per metric and dimension, with the row of the key clicked marked |
| **Aliases** | The effective map of `load_config` (generated ⊕ hand-written − rejected), each alias marked **generated** or **hand-written** | **Add** a hand-written alias of a roster name; **Remove** a hand-written alias; **Reject** a generated alias |
| **Blacklist** | `[fuera_de_alcance] terminos` | **Add** a term the roster does not hold; **Remove** a term |
| **Rejected aliases** | `[falsos_sinonimos]`: the aliases a name must never have | **Accept again** |

`POST /w/{wiki_id}/vocabulary/{op}` applies one change under the wiki's lock and returns
the body of the screen. `domain.chat.config_writer` rewrites only what changes in
`wiki_config.toml`, with `tomlkit`: comments, order and spelling stay. Every change is one
commit of the wiki (`vocabulary: …`). A rejected alias leaves the effective map, and the
ingestion does not write it into `.llmwiki/aliases.generated.toml` again
(`alias_generation._without_rejected`). A change applies from the next question. A
refusal (an empty or repeated term, a covered term for the blacklist, an alias of a name
the roster does not hold, an alias that is the name of another topic, another operation
running) is worded in its panel.

### 7.7 History

Routes: `web/routes/history.py`. `GET /w/{wiki_id}/history` lists the points of the
wiki, one per commit of its git repository: date, message, short identifier, changed
pages, and either "● index copy" or "no copy". The newest point is "current". If
`WIKI_AUTOCOMMIT` is 0, there is no history.

- **Go back to this point** loads a confirmation (`GET …/history/commit/{sha}/confirm`)
  that says what changes and how the index comes back, or why the return is refused:
  there are uncommitted changes in `wiki/` or in `wiki_config.toml`, or the index cannot
  be restored. A point covers `wiki/`, `.gitignore` and `wiki_config.toml`: going back
  restores the vocabulary lists too, and the confirmation says so when they change. A
  point from before `wiki_config.toml` was tracked keeps the current file.
  `POST …/history/commit/{sha}/revert` runs the return as a streamed operation. The
  return is a new commit; the history is not rewritten (`base/domain/rollback/`).
- `GET …/history/page/{page}` lists the commits of one page; `…/at/{sha}` shows the page
  at that commit and `…/diff/{sha}` compares it with the current text.

### 7.8 State and operations

The state is in memory (`web/state.py`) and is lost on restart: open wikis,
conversations, pending turns and running operations. The wikis on disk are not affected.
An operation (ingestion, scan, regeneration, lint, reindex, return to a point) runs in a
thread under the lock of its wiki and writes progress lines to a queue that
`GET /w/{wiki_id}/ops/{op_id}/events` streams over SSE. A second operation on the same
wiki is refused with "Another operation is running on this wiki."

### 7.9 Quick-start installer (`quickstart.py`)

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
(`scripts/eval_chat_model.py --brief`; `--no-eval` skips) → launches the web
interface: `python -m uvicorn web.app:app --port 2720` with the Python of the venv
(`launch_command`), and opens the browser on `http://localhost:2720`, the wiki
picker, two seconds later (`open_browser_soon`). `--port` changes the port.

```bash
python3 quickstart.py                                            # interactive
python3 quickstart.py --demo fairy-tales --provider ollama \
        --yes --no-launch                                        # unattended
```

- **`requirements.txt`** is hash-pinned, regenerated from `uv.lock` with
  `uv export --no-dev --group web --no-emit-project` — so the installer's plain-`pip`
  path reproduces the exact tested versions without uv and includes the `web` group,
  and `--no-emit-project` keeps the local package out (`web/app.py` adds `base/` to
  `sys.path`, so nothing needs installing as a package). Regenerate it whenever
  `uv.lock` changes. `tests/unit/test_quickstart_launch.py` checks that the web
  dependencies are in it.
- **`examples/<name>/`** demos are auto-discovered (any subfolder with a
  `wiki/` dir). Each is a complete pre-ingested workspace, so browsing works
  with no LLM; only chat calls the model. `examples/fairy-tales/.llmwiki/index.db`
  is force-added past the demo's own `.gitignore` (which excludes `.llmwiki/`).
- The step functions are factored to be importable, so a planned **tkinter**
  front-end can wrap them and fall back to the console wizard when `import
  tkinter` fails.

### 7.10 Running locally

```bash
uv sync --group web
# Opens on the picker; $WIKI_PATH from .env is the default wiki (switch wikis in the header, §7.1)
uv run --group web uvicorn web.app:app --port 8765

# Start with a specific default wiki and wiki folder
WIKI_PATH=/path/to/workspace WIKI_HOME=/path/to/folder-of-wikis \
  uv run --group web uvicorn web.app:app --port 8765
```

### 7.11 Interface language

The interface language is not the wiki language. The wiki language (`[wiki].language`, §8) governs
what the model writes and what ends up inside the wiki: page bodies, the saved conversation
(`services.chat.conversation_markdown`) and `log.md`. The interface language is the language of the
labels, tooltips, confirmations, notices and console lines that `web/` writes; the wiki never
receives it. A Spanish wiki can be read with the English interface, and the reverse.

The language is chosen, in this order, by the cookie `ui_lang` (`en` or `es`), set by the
**EN | ES** switch at the right of the header on every screen (`POST /lang`, which returns to the
page it was on); by the browser's `Accept-Language`; and by English. `<html lang>` follows it.
The message ids are the English texts, written `_("…")` in the routes and in the templates; the
Spanish catalog is `web/locale/es/LC_MESSAGES/messages.po`, compiled in memory when the application
starts. The scripts hold no human text: they read `data-*` attributes the templates render. A third
language needs one catalog and one entry in `LANGUAGES` (`web/i18n.py`); `web/README.md` has the
steps. Left in English on both languages: the progress lines `base/domain/` emits during ingestion,
lint and repair, and the messages `services` return.

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
  agent's system prompt at creation. **Not** localized in v1: the `log.md` ingest log, the lint/repair *diagnostic* notes (contradiction /
  data-gap), and the legacy `regenerate_wiki_pages` path.
- **`[assistant] system_prompt` / `suggested_prompts`** — override the chat
  assistant. See §6.7 for an example. When `suggested_prompts` is omitted, the
  localized defaults for the wiki language are used.

### Environment flags

| Flag                       | Effect                                                                                       |
| -------------------------- | ------------------------------------------------------------------------------------------- |
| `WIKI_DEBUG=1`             | Shows the debug panel of the marimo `ingest_app.py` (the web interface does not read it)     |
| `WIKI_HOME=…`              | Folder the wiki picker scans for sibling wikis (default: parent of `WIKI_PATH`). See §7.1.   |
| `WIKI_AUTOCOMMIT=0`        | Disable the per-ingest git auto-commit of `wiki/` in the workspace (default: on). Falsy values `0/false/no/off`; read by `git_ops.autocommit_enabled` — skips both `init_wiki_repo` and `auto_commit`. |
| `HEADLESS=1`               | Used by the E2E test suite for non-interactive Playwright runs                               |
| `WIKI_TRACE=1`             | Turns on the opt-in trace: one OpenTelemetry span per diagram node, for a chat turn and for an ingest. See §14.                    |
| `WIKI_TRACE_CAPTURE=…`     | Selects the trace's payload channels: `all` (default) · `none` · CSV of `extracted_text,chunks,prompts,responses,markdown`. See §14. |

---

## 9. Testing

### Run

```bash
uv run pytest tests/unit tests/regression -q       # 1057 unit + 16 regression tests — fast, no LLM, no network
uv run --group web pytest -q tests/web             # 239 tests: 209 routes, rendering and design checks, 30 Playwright flows
uv run pytest tests/e2e/ -v -s                     # 23 E2E tests — live marimo apps + LLM (not in CI; goes away with the marimo apps)
```

CI (`.github/workflows/test.yml`) runs the first two commands, in the jobs `unit` and `web`. No test of the first two calls a model.
The Playwright flows of `tests/web/e2e/` start the application on a free port over a copy of `examples/finanzas-argentinas`
and skip when Playwright cannot start a browser; the `web` job installs Chromium.

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

### E2E infrastructure (marimo apps)

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
topics). Wired in `services.chat.chat_turn` (mode `strict`, chosen with the **Mode** selector of the reading screen, §7.2):

- **ON** → run to completion, then gate (refuse if ungrounded). Cannot stream —
  you can't retract text already shown.
- **Other modes** → `streaming` streams token-by-token, ungated; `pre-retrieval` retrieves in code before the model is called.

The mode is chosen per question, so changing it never rebuilds the agents. Known limit: it catches *no-evidence* answers, not an answer that ignored
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
