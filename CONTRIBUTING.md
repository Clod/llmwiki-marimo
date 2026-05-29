# Contributing to LLM Wiki

Thanks for your interest in improving LLM Wiki. This is a proof-of-concept
implementation of the LLM-Wiki pattern — contributions that sharpen the core
loop (ingest → maintain → read → chat → lint → repair) are especially welcome.

## Getting set up

```bash
git clone https://github.com/Clod/llmwiki-marimo.git
cd llmwiki-marimo
uv sync
cp .env.example .env   # then fill in WIKI_PATH and your LLM_* values
```

Prerequisites: **Python 3.12+**, **[uv](https://docs.astral.sh/uv/)**, and an
OpenAI-compatible LLM endpoint (OpenRouter, Ollama, LM Studio, …). LibreOffice is
only needed for DOCX ingestion. See the [README](README.md) for provider config.

## Running the apps

```bash
uv run marimo run marimo/ingest_app.py --no-sandbox   # ingest documents
uv run marimo run marimo/read_app.py   --no-sandbox   # read + chat
```

## Tests

Unit tests use a `FakeLLM` and make **no network calls** — run them before every PR:

```bash
uv run pytest tests/unit -v
uv run ruff check .
```

End-to-end tests drive the marimo apps with Playwright and require a real LLM
endpoint, so they are not part of CI:

```bash
uv run playwright install chromium            # once
HEADLESS=1 uv run pytest tests/e2e/test_ingest_app.py -v -s   # populates the workspace
HEADLESS=1 uv run pytest tests/e2e/test_read_app.py   -v -s   # uses that workspace
```

CI runs the unit suite and `ruff` on every push and PR to `master`.

## Conventions

- **Many small files over few large ones.** Target 200–400 lines, 800 max.
- **Immutability.** Return new objects; don't mutate in place.
- **Marimo cell granularity.** One concern per cell. Do **not** stack many UI
  elements in a single cell — marimo re-runs the whole cell on any interaction,
  which resets sibling widgets and hurts responsiveness. Split UI by interaction
  concern and use `@app.cell(column=N)` for side-by-side layout.
- **Handle errors explicitly** and validate input at system boundaries.
- Keep the developer reference in [`docs/programmer_manual.md`](docs/programmer_manual.md)
  in sync when you change a workflow.

## Pull requests

1. Branch off `master`.
2. Keep changes focused; describe the *why*, not just the *what*.
3. Ensure `uv run pytest tests/unit` and `uv run ruff check .` pass.
4. Update docs/tests alongside code.

## Commit messages

Conventional-commit style: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`,
`chore:`, `perf:`, `ci:`.
