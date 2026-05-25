---
name: test-read
description: Run the E2E tests for marimo read_app in headless mode. Requires test-ingest to have run first.
---

# Test Read App

Run the E2E Playwright tests for the read-only wiki viewer + chat. Always run **headless**.

## Prerequisites

`tests/fixtures/workspace/` must be populated. Run `/test-ingest` first if the workspace is empty.

## What the tests do

1. Starts `marimo_new/read_app.py` on port 2720 with `WIKI_PATH` pointing at `tests/fixtures/workspace/`
2. Verifies: pages table visible with ingested pages
3. Verifies: clicking a row loads content in the middle panel
4. Verifies: refresh button reloads the page list
5. Verifies: no Edit/Save/Cancel/Create buttons exist (app is read-only)
6. Verifies: chat panel renders with prompts from `wiki_config.toml` (via `data-prompts` attribute)

## Run command

```bash
cd /Users/claudiograsso/Documents/finanzas/llmwiki
HEADLESS=1 uv run pytest tests/e2e/test_read_app.py -v -s
```

## Interpreting results

**5 passed** — read app is healthy.

**`No wiki pages found — run test_ingest_app.py first`** — workspace not populated. Run `/test-ingest` first.

**`marimo not reachable on port 2720 after 60s`** — port conflict (a previous test run may still hold it). Check: `lsof -i :2720`. Kill stale process, then retry.

**`Expected a page title (h2)`** — page content not loading. Likely a grid JSON mismatch after adding/removing cells; check `marimo_new/layouts/read_app.grid.json` entry count.

**`No prompts in marimo-chatbot data-prompts`** — `wiki_config.toml` not loaded. Check it exists in `tests/fixtures/workspace/`.

## Key files

| File | Purpose |
|------|---------|
| `tests/e2e/test_read_app.py` | The test |
| `tests/fixtures/workspace/` | Test wiki (populated by test-ingest) |
| `tests/fixtures/wiki_config.toml` | Chat prompts + system prompt for tests |
| `marimo_new/read_app.py` | App under test |
| `marimo_new/layouts/read_app.grid.json` | Grid layout (cell count must match) |
| `api_new/domain/chat/` | PydanticAI agent used by chat panel |
