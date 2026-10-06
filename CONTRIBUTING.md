# Contributing to LLM Wiki

Thanks for your interest in improving LLM Wiki. This is a proof-of-concept
implementation of the LLM-Wiki pattern — contributions that sharpen the core
loop (ingest → maintain → read → chat → lint → repair) are especially welcome.

## Getting set up

```bash
git clone https://github.com/Clod/llmwiki-marimo.git
cd llmwiki-marimo
uv sync --group dev --group web
cp .env.example .env   # then fill in WIKI_PATH and your LLM_* values
```

Prerequisites: **Python 3.12+**, **[uv](https://docs.astral.sh/uv/)**, and an
OpenAI-compatible LLM endpoint (OpenRouter, Ollama, LM Studio, …). Java is needed to ingest PDF and
office files, and LibreOffice only for office files (DOCX, DOC, ODT, RTF). See the [README](README.md) for provider config.

## Running the web interface

```bash
uv run --group web uvicorn web.app:app --port 8765   # then open http://localhost:8765
```

The marimo apps in `marimo/` are being retired; new work goes to `web/` (see [`web/README.md`](web/README.md)).

## Tests

Unit tests use a `FakeLLM` and make **no network calls**; the regression suite replays a frozen golden corpus, also offline — run both before every PR:

```bash
uv run pytest tests/unit tests/regression -q
uv run playwright install chromium            # once, for the web tests
uv run --group web pytest -q tests/web
uv run ruff check .
```

The web tests simulate the model and need no network. The end-to-end tests of the marimo
apps (`tests/e2e/`) require a real LLM endpoint and are not part of CI.

CI runs two jobs on every push and PR to `master`: `unit` (the unit and regression suites and
`ruff`) and `web` (`tests/web`, with Chromium).

## Conventions

- **Many small files over few large ones.** Target 200–400 lines, ~800 as a
  soft ceiling for library/domain modules. The Marimo app files (`marimo/*.py`)
  are the pragmatic exception — a notebook app is a single cohesive cell graph
  that doesn't split cleanly across files, so `ingest_app.py` / `read_app.py`
  run longer by design.
- **Immutability.** Return new objects; don't mutate in place.
- **Marimo cell granularity.** One concern per cell. Do **not** stack many UI
  elements in a single cell — marimo re-runs the whole cell on any interaction,
  which resets sibling widgets and hurts responsiveness. Split UI by interaction
  concern and use `@app.cell(column=N)` for side-by-side layout.
- **Handle errors explicitly** and validate input at system boundaries.
- Keep the developer reference in [`docs/manual/programmer_manual.md`](docs/manual/programmer_manual.md)
  in sync when you change a workflow.

## Pull requests

1. Branch off `master`.
2. Keep changes focused; describe the *why*, not just the *what*.
3. Ensure `uv run pytest tests/unit tests/regression` and `uv run ruff check .` pass.
4. Update docs/tests alongside code.

## Commit messages

Conventional-commit style: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`,
`chore:`, `perf:`, `ci:`.
