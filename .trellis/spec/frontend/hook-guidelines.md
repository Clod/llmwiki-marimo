# Cell Guidelines (Hooks Equivalent)

> Marimo has no hooks. This file documents the equivalent patterns: cell decomposition,
> state, and reactive dependencies.

---

## Overview

In marimo, cells are the unit of reactive computation — analogous to hooks in React.
A cell runs whenever any of its parameters change. These conventions ensure cells are
focused, testable, and free of reactive bugs.

---

## Cell Decomposition Rules

### One concern per cell

Split cells by concern. Do not combine UI, state, and business logic in one cell.

**Good — separate cells:**
```python
@app.cell
def upload_widget(mo):
    upload = mo.ui.file(filetypes=[".pdf", ".docx"], multiple=True, label="Drop files")
    return (upload,)

@app.cell
def handle_upload(upload, SOURCES_DIR, logger):
    saved = []
    if upload.value:
        for _uf in upload.value:
            _dest = SOURCES_DIR / _uf.name
            if not _dest.exists():
                _dest.write_bytes(_uf.contents)
                saved.append(f"✅ Saved `{_uf.name}`")
    return (saved,)
```

**Bad — one cell does everything:**
```python
@app.cell
def upload_and_ingest(mo, SOURCES_DIR, DB_PATH, llm_client, llm_model):
    upload = mo.ui.file(...)
    # ... saves AND ingests AND updates log in one cell — avoid this
```

---

## Setup Cell

Shared imports and config always go in a dedicated `setup` cell (or `with app.setup:`
block for newer marimo). It must return every value other cells need.

```python
# ingest_app.py style (older marimo)
@app.cell
def setup():
    import marimo as mo
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv()
    WORKSPACE = Path(settings.WIKI_PATH).resolve()
    DB_PATH = str(WORKSPACE / ".llmwiki" / "index.db")
    return mo, WORKSPACE, DB_PATH, ...

# read_app.py style (newer marimo)
with app.setup:
    import marimo as mo
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv()
    WIKI_PATH = Path(os.environ["WIKI_PATH"])
```

---

## Trigger-Capture Pattern (Critical)

When a button should trigger a long-running operation, use the trigger-capture pattern.
**Do not** make the runner cell depend on `upload` directly — that causes auto-ingestion
on file drop without button click.

```python
# CORRECT: capture file list in trigger state at click time
@app.cell
def action_buttons(mo, upload, set_ingest_trigger, ...):
    import time as _t
    ingest_btn = mo.ui.button(
        label="⚙️ Ingest",
        on_click=lambda _: set_ingest_trigger((_t.time(), list(upload.value))),
    )
    return (ingest_btn,)

@app.cell
def ingest_runner(mo, ingest_trigger, ...):
    mo.stop(ingest_trigger() is None)
    _, _files = ingest_trigger()  # files captured at button-click time
    # runner does NOT depend on upload widget — no auto-fire on file drop
```

**Wrong — runner depends on upload:**
```python
@app.cell
def ingest_runner(mo, ingest_trigger, upload, ...):  # ← upload dependency triggers on drop
    mo.stop(ingest_trigger() is None)
    _files = list(upload.value)
```

---

## mo.stop Guard

Every runner cell must guard against `None` state at the top:

```python
@app.cell
def scan_runner(mo, scan_trigger, ...):
    mo.stop(scan_trigger() is None)
    # ... rest of runner
```

---

## Private Names in Cells

Variables that should not leak as cell outputs are prefixed with `_`:

```python
@app.cell
def sources_list(mo, SOURCES_DIR, DB_PATH):
    _conn = open_db(DB_PATH)      # private, not returned
    rows = _conn.execute(...).fetchall()
    _conn.close()
    return (rows,)
```

---

## Common Mistakes

- **Runner cell depends on upload widget** → file drop auto-triggers operation (see trigger-capture pattern)
- **Too many dependencies in one cell** → frequent re-runs, hard to debug
- **No `mo.stop(trigger is None)` guard** → runner executes on app load with `None` state
- **Returning mutable objects** → other cells mutate shared state unexpectedly
