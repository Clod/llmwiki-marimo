---
name: test-all
description: Run the full E2E test suite (ingest then read app) in headless mode and report results.
---

# Test All — Full E2E Suite

Runs both E2E suites in the correct order. Always **headless**.

## Important: run sequentially, not together

Running both suites in a single pytest invocation causes a port conflict — both servers stay alive
for the session and port 2720 collides. Always use two separate commands:

```bash
cd "$(git rev-parse --show-toplevel)"

# Step 1 — ingest (wipes workspace, ingests 3 PDFs, populates DB + wiki/)
HEADLESS=1 uv run pytest tests/e2e/test_ingest_app.py -v -s

# Step 2 — read app (uses workspace populated by step 1)
HEADLESS=1 uv run pytest tests/e2e/test_read_app.py -v -s
```

## Expected results

| Suite | Tests | Passes means |
|-------|-------|-------------|
| `test_ingest_app.py` | 4/4 | Pipeline extracts, chunks, generates wiki, writes DB |
| `test_read_app.py` | 5/5 | Viewer loads, navigation works, chat panel renders |

## Failure triage

If `test_ingest_app.py` fails, `test_read_app.py` will skip (empty workspace). Fix ingest first.

See `/test-ingest` and `/test-read` for per-suite troubleshooting.

## Env vars

| Variable | Value | Purpose |
|----------|-------|---------|
| `HEADLESS` | `1` | Headless browser (always use `1` for self-testing) |
| `WIKI_DEBUG` | `1` | Verbose marimo logging (optional, for debugging) |
