# LLM Wiki

[![CI](https://github.com/Clod/llmwiki-marimo/actions/workflows/test.yml/badge.svg)](https://github.com/Clod/llmwiki-marimo/actions/workflows/test.yml)
![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)
![Tests](https://img.shields.io/badge/tests-418-brightgreen.svg)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

**English** · [Español](README_ES.md)

A personal, local-first wiki that ingests your documents, builds a structured knowledge base, and lets you read and chat with it — all on your machine, no cloud required.

Inspired by [Karpathy's LLM Wiki idea](https://x.com/karpathy/status/2039805659525644595).
The PDF-extraction and a few low-level ingestion pieces are adapted from [Lucas Astorian's open-source LLM Wiki](https://github.com/lucasastorian/llmwiki)
(Apache-2.0); the rest is an independent local-first build on Marimo + SQLite. See [`NOTICE`](NOTICE).

![The read app on the built-in sample wiki: page navigation on the left, a generated concept page in the middle, and the chat assistant on the right answering a cross-document question with a citation for every fact](docs/assets/read_app.png)

*The read app on the built-in sample wiki — navigation, a generated concept page, and a chat answer where every fact cites its source page. Below the chat: the **Save to wiki** form, the human-in-the-loop step that turns a good answer into a permanent page.*

▶ **[Watch the 1-minute demo](https://youtu.be/qXaPycsGXHw)** — a PDF ingested into a fresh wiki (concept pages, summary, lint auto-repair), then a chat answer where every fact cites its source.

---

## Highlights

**A self-contained, agentic LLM-wiki.** Most takes on Karpathy's idea point an
*external* agent — Claude Desktop, Cursor, an MCP client — at an Obsidian vault.
This one ships its own embedded agent: ingestion, agentic retrieval (the chat
assistant decides when to read a page vs. search), self-maintenance, and a
reading UI are a single app, with no external agent or plugin host to wire up.
The trade-off is honest — it's not an Obsidian plugin, so there's no graph view
or plugin ecosystem (see [Limitations](#limitations--non-goals)).

**AI / LLM engineering**

- **Wiki-first RAG** — reads a curated, interlinked encyclopedia first (`index.md` → wiki FTS5 → raw source chunks as a fallback), so knowledge is compiled once and compounds instead of being re-retrieved per query.
- **Per-wiki language (en/es, extensible)** — set `[wiki] language` in `wiki_config.toml` and the whole wiki — generated pages, section headers, *and* chat answers — is produced in that language, **regardless of the source documents' language**. Run an English wiki and a Spanish wiki side by side; adding a third language is one `Locale` entry.
- **LLM-as-judge eval packet** — one command bundles the questions, the model's own answers, the cited evidence, and source-vs-generated page pairs against a *frozen* 1–5 rubric, to score chat **and** ingestion quality (and compare models).
- **Model-suitability check** — a one-command PASS/FAIL on whether a given model clears the bar for off-corpus refusal, citations, and cited synthesis.
- **Evidence-based prompting** — the default system prompt embeds a worked, fully-cited example because testing proved that's what reliable cross-document citation took.
- **Self-maintaining wiki** — six lint checks (contradictions, stale pages, orphans, missing concepts, missing cross-refs, data gaps) with auto-repair of the safe ones.
- **Provider-agnostic, split-model** — any OpenAI-compatible endpoint; run a cheap local model for chat and a stronger one for ingestion, via `.env` alone.

**Engineering quality**

- **418 tests across three layers, ≈1:1 test-to-code** (6.7k test LOC vs 6.1k LOC in the framework-agnostic core, `base/`) — deterministic fake-LLM unit tests (no keys, no network); a frozen golden-corpus *characterization* regression that re-checks the real-ingest backbone without re-calling the model; and Playwright E2E on the live apps.
- **Framework-agnostic core** — all logic lives in `base/domain/{ingestion,chat,eval,lint,repair,tools}`; Marimo is only the UI at the edges, so the engine is exercised by unit tests without a browser.
- **Malleable UI** — because the GUI is marimo notebooks, the read app's three-panel layout is just a grid file ([`marimo/layouts/read_app.grid.json`](marimo/layouts/read_app.grid.json)): open the app with `marimo edit` and drag, resize, or re-stack the panels to suit your workflow, taste, or monitor — no frontend code to touch.
- **Security-conscious** — a path-traversal guard on the LLM-callable page reader, an explicit prompt-injection threat model, and a documented [`SECURITY.md`](SECURITY.md).
- **Local-first & private** — runs entirely on-device; each wiki is its own local-only git repo (version history for free); source files are never modified and nothing is pushed anywhere.
- **Scale-aware** — re-ingest skips unchanged files by content hash, lint compares only page pairs that share a source (not N²), and the overview synthesis is incremental.
- **Reproducible & clean** — pinned `uv.lock` for deterministic installs, zero `ruff` warnings, and no `TODO`/`FIXME` debt in the codebase.

**Transparency & docs**

- **Citation graph in SQLite** — every page→source and page→page edge is recorded and rebuilt deterministically, so provenance is queryable.
- **Opt-in tracing** (`WIKI_TRACE=1`) — emits a JSONL trace of the full LLM + data-flow per ingest, viewable in a dedicated trace-report app.
- **Documented end to end** — a 72 KB programmer manual, a SQLite data dictionary, a three-part UAT plan, and an honest Karpathy-alignment matrix grading what's done, partial, and deferred.

---

## How is this different from RAG / NotebookLM?

Classic RAG (and tools like NotebookLM or ChatGPT file uploads) re-discovers  
knowledge from scratch on every question: it retrieves chunks at query time and  
synthesises an answer that vanishes into chat history. Nothing accumulates.

LLM Wiki **compiles knowledge once and keeps it current**. Each ingested source  
is read, summarised, and integrated into a persistent, interlinked set of  
markdown pages — cross-references, contradictions, and synthesis are already  
written down before you ask anything. The wiki is a compounding artifact that  
gets richer with every document; the chat agent reads those curated pages first  
and only falls back to raw chunks when needed.

> Filing cabinet (SQLite + FTS5) vs. encyclopedia (human-readable markdown) —  
> this project maintains both, and the encyclopedia is the point.

---

## What it does

1. **Ingest** — drop PDFs or DOCXs into the ingest app (this only saves them to `sources/`), then click **Ingest** to run the pipeline. It extracts text page by page, chunks it with overlap, runs structured concept extraction, and creates / updates summary + concept pages plus the catalogue, overview, and timeline — then snapshots the result to the wiki's own git repo (optional; see [What ends up on disk](#what-ends-up-on-disk)).
2. **Read** — browse the generated wiki pages in a clean 3-column interface. Navigation, content viewer, and AI chat all in one.
3. **Chat** — ask questions about your documents. A PydanticAI agent reads curated wiki pages first and falls back to raw-source FTS5 only when needed. Streams responses with citations.
4. **Maintain** — run lint to surface orphans, stale pages, missing cross-references, and missing concepts; run repair to auto-fix the safe ones.

> **For developers:** the canonical reference is  
> [`docs/programmer_manual.md`](docs/programmer_manual.md) — workflows, prompts,  
> entry points, gaps, and the pending-work roadmap. Earlier design notes are in  
> [`docs/archive/`](docs/archive/).

---

## What ends up on disk

```
YOUR_WIKI_PATH/
├── sources/                 # Uploaded files (created by ingest app)
│   ├── paper.pdf
│   └── report.docx
├── wiki/                    # Generated by the LLM — you read it, the wiki writes it
│   ├── index.md             # Catalogue of every page
│   ├── overview.md          # Narrative synthesis (rewritten on each ingest)
│   ├── log.md               # Append-only timeline
│   ├── summaries/           # One per source document
│   │   ├── paper.md
│   │   └── report.md
│   └── concepts/            # Topic-centric, multi-source
│       └── interest-rates.md
├── wiki_config.toml         # Optional: customize chat assistant behavior
└── .llmwiki/
    ├── index.db             # SQLite: documents, chunks, FTS5 index, citation graph
    └── cache/               # Extraction cache (rebuildable)
```

Source files are never modified. Delete `.llmwiki/` anytime — re-ingest rebuilds it.

Your **`WIKI_PATH` workspace is its own git repo** (a separate repo from this
project's). Each ingest commits the generated `wiki/` as a labelled snapshot
(`ingest: paper.pdf`), giving you version history of the knowledge base for free.
It only ever stages `wiki/` and the `.gitignore` it creates — never your `sources/` or the database — and uses a
local `LLM Wiki <llmwiki@local>` identity, so your global git config is untouched.
Set `WIKI_AUTOCOMMIT=0` in `.env` to turn this off and manage the wiki's git
yourself (then LLM Wiki runs no `git init` and no commits).

**The wiki repo is local-only — nothing is pushed anywhere.** It has no remote
and stays entirely on your machine; LLM Wiki only ever commits locally, it never
pushes. That's deliberate: your sources and the knowledge derived from them are
private by default. If you *want* to back the wiki up or sync it across machines,
add your own remote — and use a **private** repo, since it holds your personal
knowledge:

```bash
cd "$WIKI_PATH"                                       # your wiki folder
git remote add origin git@github.com:you/my-wiki.git # a PRIVATE repo you own
git push -u origin HEAD
```

From then on, pushing is on you (`git push` whenever you like, or wire up your
own automation) — the app's job ends at the local commit.

> Each wiki is a **separate** repo from this project and from your other wikis.
> So a wiki you back up to GitHub is its own private repo — not a folder inside
> `llmwiki-marimo`, and nothing about your documents ever lands in the public
> project repo.

---

## Project structure

```
base/                   # Ingestion pipeline + chat agent (self-contained Python)
├── config.py              # pydantic-settings — reads .env
└── domain/
    ├── ingestion/         # PDF/DOCX → text → chunks → summary + concept pages
    ├── chat/              # PydanticAI agent + wiki/source/save tools
    ├── eval/              # Half-automated UAT: build a judge-ready eval packet
    ├── lint/              # Wiki health checks
    ├── repair/            # Auto-fixes for safe lint issues
    ├── tools/             # Native CRUD: wiki_fs, search, references, deletion, git_ops, db
    └── wiki_registry.py   # Multi-wiki picker: discovery + recent list + path hygiene

marimo/                # Marimo notebook apps
├── ingest_app.py          # Upload → ingest → wiki generation UI
├── read_app.py            # Read-only viewer + chat (3-column grid)
└── trace_report_app.py    # Ingestion trace viewer (WIKI_TRACE=1 runs)

database/
└── sqlite_schema.sql      # Canonical DB schema

docs/
├── programmer_manual.md   # Canonical developer reference
└── archive/               # Superseded design docs (historical)

tests/
├── unit/                  # 393 unit tests (FakeLLM, no network)
├── regression/            # 16 frozen golden-corpus tests (real ingest, no live model)
├── e2e/                   # 9 Playwright E2E tests (ingest + read app)
└── fixtures/              # Test PDFs + wiki config + golden corpus
```

---

## Prerequisites

- **Python 3.12+** and **[uv](https://docs.astral.sh/uv/)**
- An **OpenAI-compatible LLM API** (OpenRouter, Ollama, LM Studio, etc.)
- **LibreOffice** — only needed for DOCX ingestion:
    - macOS: `brew install --cask libreoffice`
    - Debian/Ubuntu: `sudo apt install libreoffice` (Fedora: `sudo dnf install libreoffice`)
    - Windows: `winget install TheDocumentFoundation.LibreOffice`
- **git** — *optional*; powers the wiki's version-history auto-commit. Most systems already have it; if it's missing, snapshots are skipped (with a warning) and ingestion still works — or set `WIKI_AUTOCOMMIT=0` to opt out.

---

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/Clod/llmwiki-marimo.git
cd llmwiki-marimo
uv sync
```

### 2. Configure

Copy `.env.example` to `.env` and fill in:

```env
WIKI_PATH=/path/to/your/wiki          # the wiki opened on launch (the default)

# Any OpenAI-compatible endpoint works. Example: Ollama (local, free).
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama                    # any non-empty string for Ollama
LLM_MODEL=llama3.2
```

`WIKI_PATH` is just the **default** — both apps have a wiki picker (top-left) so  
you can switch between multiple wikis at runtime without editing `.env`. It lists  
wikis discovered next to `WIKI_PATH` plus a recent list, and you can open any  
other folder by path. Set `WIKI_HOME=/path/to/wikis` to point discovery at a  
specific folder instead of the parent of `WIKI_PATH`.

See [LLM providers](#llm-providers) for Ollama and LM Studio config.

### 3. Ingest documents

```bash
uv run marimo run marimo/ingest_app.py --no-sandbox --port 2718
```

Open [http://localhost:2718](http://localhost:2718), drop in your PDFs or DOCXs, click **Ingest**.

### 4. Read and chat

```bash
uv run marimo run marimo/read_app.py --no-sandbox --port 2720
```

Open [http://localhost:2720](http://localhost:2720). Select a page on the left, read it in the middle, chat on the right.

> Using distinct ports (2718 for ingest, 2720 for read) lets you run both apps  
> at once without a collision — marimo defaults both to 2718 otherwise.

---

## LLM providers

The stack uses the OpenAI-compatible API everywhere. Switch providers by changing `.env` only — no code changes needed.

**Ollama (local, free):**

```env
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=llama3.2
```

**LM Studio (local, free):**

```env
LLM_BASE_URL=http://localhost:1234/v1
LLM_API_KEY=lm-studio
LLM_MODEL=local-model-name
```

**OpenRouter (cloud, hosted models):**

```env
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=sk-or-...
LLM_MODEL=anthropic/claude-haiku-4-5
```

**Split config** — use a cheap/local model for chat but a stronger model for wiki generation:

```env
LLM_BASE_URL=http://localhost:11434/v1   # chat uses this
LLM_API_KEY=ollama
LLM_MODEL=llama3.2

WIKI_LLM_BASE_URL=https://openrouter.ai/api/v1   # ingest uses this
WIKI_LLM_API_KEY=sk-or-...
WIKI_LLM_MODEL=anthropic/claude-haiku-4-5
```

If `WIKI_LLM_*` are blank, ingestion falls back to `LLM_*`.

> **Don't use too small a model for ingestion.** Summarisation, concept
> extraction, and contradiction-checking all lean on the model's reasoning, so a
> model that's underpowered for *your* documents yields thin summaries, weak
> citations, or hallucinated pages. What counts as "too small" depends on your
> corpus and your standards — judge it on the pages it actually produces. If wiki
> quality disappoints, raise the `WIKI_LLM_*` (ingest) model before blaming the
> pipeline; the split config above lets you do that while keeping chat on a
> smaller local model.

> **Chat grounding & citations scale with the chat model, too.** The chat agent's
> default prompt is strict — *answer only from your wiki, and cite every fact* —
> but a prompt is only a request; the model has to be capable of honouring it.
> A concrete example from testing on OpenRouter, same provider, same wiki, with
> the strict default prompt:
>
> | Question | `openai/gpt-4o-mini` | `openai/gpt-4o` |
> | --- | --- | --- |
> | "What's the capital of France?" (off-corpus) | sometimes answers "Paris" | declines — outside the wiki |
> | "Who is Cinderella?" (single fact) | answers, but cites the raw PDF or nothing | cites the curated wiki page |
> | "What do Cinderella and Snow White have in common?" (synthesis) | drops citations | cites every point to its source pages |
>
> Cross-document **synthesis** is the most demanding case — a weaker model gives up
> citations there first. Getting it cited reliably took *both* a capable model and
> a worked example of a fully-cited comparison, which is why that example is now
> baked into the default prompt. If chat answers arrive uncited or stray outside
> your documents, raise the chat model (`LLM_MODEL`) before assuming the agent is
> broken. You can keep a cheap model for ingestion and a stronger one for chat (or
> vice versa) via the split config above.
>
> **Not sure if a model clears the bar?** Run `uv run python scripts/eval_chat_model.py`
> — it asks the built-in sample wiki a few fixed questions and gives a PASS/FAIL on
> exactly these behaviours (off-corpus refusal, citations, cited synthesis). See
> [`docs/uat_test_plan.md`](docs/uat_test_plan.md) Part C.

---

## Customising the chat assistant

Create `wiki_config.toml` in your `WIKI_PATH`:

```toml
[assistant]
system_prompt = """
You are a personal investment wiki assistant.
Answer from the curated wiki first: read wiki/index.md, then search_wiki_fts;
only fall back to search_source_chunks when the wiki pages lack the detail.
Cite document name and page for specific facts.
"""

suggested_prompts = [
    "Summarize my investment portfolio",
    "What are the main risks?",
    "Which instruments offer the highest returns?",
]
```

Copy `wiki_config.example.toml` from the project root as a starting point. If the file is absent, generic defaults are used.

### Wiki content language

Add a `[wiki]` section to generate the whole wiki — pages, structural headers and
labels, and chat answers — in a given language, **regardless of the source
documents' language**:

```toml
[wiki]
language = "es"   # "en" (default) | "es"; extensible — add a Locale in base/domain/i18n.py
```

Language is a *per-wiki* property, so you can keep an English wiki and a Spanish
wiki side by side. Set it **before the first ingest**; an absent or unknown value
falls back to English. See [`docs/programmer_manual.md`](docs/programmer_manual.md) §8.

---

## Document formats

| Format | Parser             | Notes                          |
| ------ | ------------------ | ------------------------------ |
| PDF    | opendataloader-pdf | Text-heavy PDFs work well      |
| DOCX   | LibreOffice → PDF  | Requires LibreOffice installed |

**Text-based PDFs only.** Scanned / image-only PDFs are not OCR'd yet — they  
ingest as empty or garbled text. OCR for scanned PDFs is on the roadmap  
(see [`docs/programmer_manual.md`](docs/programmer_manual.md) §12).

---

## Testing

Three layers, fastest first.

**1. Fast regression gate** — deterministic, no LLM keys or running apps, finishes
in about a minute. Run it after any change:

```bash
uv run pytest tests/unit tests/regression -q
```

It asserts the structural invariants (DB integrity, FTS alignment, deletion
cascade, save mechanics, lint logic, git snapshots) over fake-LLM unit tests plus
a **frozen real-ingest "golden corpus"** — so the backbone is checked against a
real ingest without re-calling the model.

**2. End-to-end (Playwright)** — drives the actual marimo apps:

```bash
uv run playwright install chromium                            # once
HEADLESS=1 uv run pytest tests/e2e/test_ingest_app.py -v -s   # ingest pipeline
HEADLESS=1 uv run pytest tests/e2e/test_read_app.py  -v -s    # read app (uses the step-1 workspace)
```

**3. Acceptance & model check (manual)** — the human-judgment pass for the things
assertions can't grade. The full plan is **[`docs/uat_test_plan.md`](docs/uat_test_plan.md)**,
a user-acceptance test in three parts:

- **Part A** — the automated gate above.
- **Part B** — a manual checklist: does the chat stay grounded and cite sources?
  do generated pages read like real entries? do lint findings make sense?
- **Part C** — *is the model you picked good enough?* A one-command check of the
  chat model (no documents needed — it uses the built-in sample wiki):

  ```bash
  uv run python scripts/eval_chat_model.py    # PASS/FAIL for the chat model (LLM_MODEL)
  ```

Test PDFs live in `tests/fixtures/pdfs/`; the E2E workspace is gitignored and
rebuilt on each ingest run. Use the skills `/test-ingest`, `/test-read`, and
`/test-all` in Claude Code for self-testing.

### Automating the un-testable: the eval packet

Some behaviour simply can't be regression-tested — there's no deterministic "right
answer" for *is this chat reply well-grounded?* or *is this generated page faithful
to its source?* The output varies with the model and even run to run. The workaround
is to **move the judgement to an LLM, but keep it cheap and bias-resistant**: generate
a single self-contained markdown **eval packet** and paste it into one — or several —
capable chat models (a free Gemini / ChatGPT / Claude tab) to score against a fixed
1–5 rubric.

```bash
uv run python scripts/build_eval_packet.py                 # benchmark sample wiki
uv run python scripts/build_eval_packet.py --wiki PATH      # an existing wiki
uv run python scripts/build_eval_packet.py --skip-ingestion # chat only (cheap)
```

The packet bundles everything a judge needs — the questions, the model's own answers,
the cited pages, and (per source) the original text next to the pages the engine
generated — plus the rubric and a blank scorecard. It covers **chat quality** and
**ingestion quality**, records the two models it measured and a corpus hash so packets
are comparable, and is written to a gitignored `eval_reports/`. Generation is
automated; judging stays human-in-the-loop (paste to as many judges as you like and
average), so it doubles as a way to compare the models your wiki engine uses. Details
in [`docs/programmer_manual.md`](docs/programmer_manual.md) §9.

---

## Performance at scale

For a personal-sized wiki (tens to low-hundreds of documents) the pipeline stays
comfortable — nothing here grows quadratically with the document count:

- **Ingestion is incremental.** Unchanged files are skipped by content hash, so
  re-scanning a large `sources/` folder only re-processes what actually changed.
- **Lint doesn't compare every page against every other.** The cross-reference
  and contradiction checks only look at concept-page *pairs that cite a common
  source*, so their cost scales with how topically interconnected your wiki is —
  not with the raw document count. Unrelated pages are never compared.
- **The overview synthesis is incremental.** Each ingest folds the new document
  into the existing overview instead of re-reading the whole corpus.

The one cost that *can* grow is the **contradiction** lint check: it makes one
LLM call per shared-source page pair, so a single source cited by many concept
pages can make that (opt-in) check slow. It reports progress and never blocks
ingestion — everything else stays roughly linear.

---

## Limitations & non-goals

This is a working proof of concept of the LLM-Wiki pattern, not a finished  
product. The core loop — ingest → build/maintain wiki → read → chat with  
citations → lint → repair — is fully implemented. Some ideas from the original  
concept are **deliberately deferred** for the PoC:

- **No web search.** The chat agent answers only from *your* curated local  
corpus — it never reaches out to the web, and there's no automatic web→wiki  
loop. To bring in an outside source, fetch it yourself (e.g. save the article  
as a PDF) and then **ingest it manually** — dropping a file into `sources/`  
does nothing on its own. Open the ingest app and either (a) drag the file into  
the upload box and click **⚙️ Ingest uploaded file(s)**, or (b) put it in  
`WIKI_PATH/sources/` and click **🔄 Scan sources/ for changes**, which detects  
and ingests anything new or modified. Treat a document from an untrusted origin  
the way you'd treat untrusted code: its text reaches the chat agent, which can  
write wiki pages — see [`SECURITY.md`](SECURITY.md).
- **No image / vision handling.** Text-only ingestion — images embedded in a  
document are skipped, not described or summarised.
- **Text-based PDFs only.** No OCR yet, so a scanned / image-only PDF ingests as  
empty or garbled text. Use a text-based PDF or convert it first.
- **Output is markdown only — no visualisations or alternate formats.** The wiki  
records a full citation/link graph in the database (`document_references`:  
which page cites which source, which pages link to which), but there's no  
interactive **graph view** to *see* that shape, and no generators for slide  
decks (**Marp**) or spatial **canvas** layouts. You read the wiki as linked  
markdown pages — cross-links are clickable, the graph just isn't drawn.
- **Ingestion is automated, not a guided conversation.** Karpathy's flow has the  
LLM discuss a source with you and write pages under your direction; here you  
drop a file and the pipeline extracts → summarises → files it in one shot, with  
no mid-ingest review. You steer the wiki *afterwards*: open the resulting page  
in the read app, chat about the document, then save a corrected or synthesised  
answer back as a wiki page via the read app's **Save to wiki** form
(`save_to_wiki`). The agent only drafts and proposes — the save is your explicit  
click — so the human-in-the-loop step is post-hoc rather than during ingestion.

The rationale for each cut and the revisit plan live in  
[`docs/programmer_manual.md`](docs/programmer_manual.md) §12.

---

## Contributing & security

- Contribution setup, test workflow, and conventions: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- Security model and how to report issues: [`SECURITY.md`](SECURITY.md)

---

## License

Apache 2.0
