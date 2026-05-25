# Directory Structure

> How frontend (marimo) code is organized in this project.

---

## Overview

The frontend is built entirely with [marimo](https://marimo.io) reactive notebooks.
There is no React, Vue, or traditional web framework — all UI is Python cells rendered by marimo.

All active notebooks live in `marimo_new/`.

---

## Directory Layout

```
marimo_new/
├── ingest_app.py          # PDF/DOCX upload → ingestion → wiki generation
├── read_app.py            # Read-only wiki viewer + FTS5 chat (3-column grid)
├── chat_app.py            # Standalone chat testbed (same agent as read_app)
├── read_app_full.py       # Backup: version with edit/create (not active)
├── layouts/
│   └── read_app.grid.json # Grid layout config written by `marimo edit`
└── sources/               # Uploaded files (created at runtime, gitignored)
```

---

## Module Organization

Each marimo app is a single `.py` file containing all its cells.
Apps are self-contained: imports, config, UI, and business logic all live in the file.

**One app per user workflow:**
- `ingest_app.py` — ingestion workflow (upload PDF/DOCX → extract → chunk → generate wiki)
- `read_app.py` — read-only viewer + FTS5 chat (pages are read-only; content comes from ingest)
- `chat_app.py` — standalone agent testbed (same PydanticAI agent as read_app, no viewer)

**Shared domain logic** lives in `api_new/domain/` and is imported by the notebooks
via `sys.path` manipulation in the `setup` cell.

---

## Naming Conventions

| Item | Convention | Example |
|------|-----------|---------|
| App files | `snake_case_app.py` | `ingest_app.py` |
| Cell functions | `snake_case` | `def action_buttons(...)` |
| Layout files | `<app_name>.grid.json` | `read_app.grid.json` |
| Private helpers inside cells | `_prefixed` | `_cb`, `_files`, `_conn` |

---

## Grid Layout

Multi-column layouts are managed through `marimo edit` drag-and-drop, not by code alone:

1. Add `@app.cell(column=N)` to suggest placement
2. Run `uv run marimo edit marimo_new/<app>.py --no-sandbox`
3. Drag cells to desired columns
4. Marimo writes `layouts/<app>.grid.json` and sets `layout_file=` in `App()`
5. Commit both the `.py` and the `.grid.json`

The `layout_file=` parameter in `App()` is what actually enables multi-column display.
`column=N` alone is insufficient — the grid JSON must exist.

---

## Examples

- `marimo_new/read_app.py` — 3-column grid: navigation | content | chat
- `marimo_new/ingest_app.py` — single-column with trigger-capture pattern
