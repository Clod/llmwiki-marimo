---
name: test-ingest
description: Run the E2E ingestion test for marimo ingest_app in headless mode and report results.
---

# Test Ingest App

Run the E2E Playwright test for the marimo ingest app. Always run **headless**.

## What the test does

1. Wipes `tests/fixtures/workspace/` clean
2. Copies `tests/fixtures/wiki_config.toml` into the fresh workspace
3. Starts `marimo_new/ingest_app.py` on port 2719 with `WIKI_PATH` injected via env
4. Ingests three PDFs one at a time from `tests/fixtures/pdfs/`
5. Polls SQLite DB after each ingestion until `status='ready'`
6. Asserts: `status='ready'`, `page_count > 0`, `content NOT NULL`, wiki page linked via `source_document_id`
7. Final check: 3 ready source docs + ≥3 wiki pages in DB

## Run command

```bash
cd /Users/claudiograsso/Documents/finanzas/llmwiki
HEADLESS=1 uv run pytest tests/e2e/test_ingest_app.py -v -s
```

## Interpreting results

**4 passed** — ingestion pipeline is healthy.

**`Missing test PDFs`** — copy the three PDFs into `tests/fixtures/pdfs/` before running.

**`marimo not reachable on port 2719 after 60s`** — server failed to start. Check:
- Port in use? `lsof -i :2719`
- Deps installed? `uv sync`
- Marimo binary exists? `ls .venv/bin/marimo`

**`ingestion FAILED: <error>`** — pipeline error (LLM, LibreOffice, DB). Check `.env` for `LLM_BASE_URL` and `LLM_API_KEY`.

**`No wiki page linked`** — source doc ready but wiki generation failed. Check LLM connectivity.

**`Timeout (300 s)`** — LLM very slow. Check API quota.

## Key files

| File | Purpose |
|------|---------|
| `tests/e2e/test_ingest_app.py` | The test |
| `tests/fixtures/pdfs/` | Source PDFs (must be present) |
| `tests/fixtures/workspace/` | Created by the test (gitignored) |
| `marimo_new/ingest_app.py` | App under test |
| `api_new/domain/ingestion/pipeline.py` | Ingestion pipeline |
| `.env` | `WIKI_PATH` (overridden by test), `LLM_*` |
