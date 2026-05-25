# Quality Guidelines

> Code quality standards for marimo frontend development.

---

## Overview

These standards apply to all marimo notebooks in `marimo_new/`. They exist to keep
notebooks readable and to prevent common reactive DAG bugs.

---

## Forbidden Patterns

### 1. Auto-ingestion on file drop

Never make a long-running runner cell depend on `upload.value` directly.
Use the trigger-capture pattern instead. See `hook-guidelines.md`.

### 2. Mutations to shared state outside `set_*` callbacks

Always use `set_*` setters from `mo.state()` — never mutate state in place.

```python
# WRONG
log_lines().append("new entry")

# CORRECT
set_log_lines(log_lines() + ["new entry"])
```

### 3. Printing instead of logging

Use `logging` throughout. `print()` bypasses the "wiki" logger hierarchy and
appears even in production runs.

```python
# WRONG
print("Processing", file_path)

# CORRECT
logger.info("Processing %s", file_path)
```

### 4. Hardcoded paths or credentials

All paths come from `settings` (pydantic-settings, resolved from `.env`).
No hardcoded API keys or file paths in cell code.

### 5. Importing domain code outside of setup / runner cells

Domain imports (`from domain.ingestion import ...`) go inside runner cells
wrapped in try/except. This keeps import errors visible in the UI rather than
crashing the whole notebook on load.

---

## Required Patterns

### Cell docstrings

Every `@app.cell` function has a one-line docstring explaining its role.

```python
@app.cell
def action_buttons(mo, upload, set_ingest_trigger, ...):
    """Buttons that fire triggers — they do no work themselves."""
```

### Explicit return tuples

Cells that export multiple values use an explicit return tuple. Single-value cells
return a one-tuple `(value,)`.

```python
@app.cell
def op_state(mo):
    log_lines, set_log_lines = mo.state([])
    return (log_lines, set_log_lines)
```

### Private prefix for cell-local variables

Variables that are not returned prefix with `_`. This avoids accidental dependency
injection and makes the cell's public API clear.

**Critical**: marimo does NOT export underscore-prefixed names to other cells, even
if they appear in an explicit `return` statement. Use plain names for anything a
downstream cell must receive.

```python
# WRONG — left_panel cannot receive _hint even though it's returned
return create_btn, _hint, page_selector

# CORRECT
return create_btn, name_hint, page_selector
```

### Never use `disabled=` on buttons that re-render per-keystroke

When a cell re-runs (e.g., on user input), it replaces all its output widgets with
new DOM elements. If the user clicks during that replacement, the click lands on the
old disabled element and is silently lost — requiring a second click.

Instead: keep the button always enabled and validate inside `on_click`.
Use `kind=` to give visual feedback, and a `mo.md` hint cell for error messages.

```python
# WRONG — disabled= causes click-loss on DOM replacement
create_btn = mo.ui.button(..., disabled=not _valid)

# CORRECT — always clickable, validate inside handler
def _create(_v):
    if not _valid:
        set_click_empty(True)
        return
    ...
create_btn = mo.ui.button(..., kind="success" if _valid else "neutral", on_click=_create)
```

### Separate cell for click-triggered notifications

If a button's `on_click` callback needs to update a hint shown in the same cell
(e.g., "enter a name"), it creates a self-loop: the cell both reads and writes the
same state, so marimo blocks the re-run.

Fix: put the hint state and its display in a **dedicated cell** that is NOT the cell
containing the button.

```python
@app.cell
def name_hint_cell(new_page_input, click_empty, page_list):
    """Reads click_empty — separate so page_controls can set it without self-loop."""
    _name = new_page_input.value.strip()
    if not _name and click_empty():
        name_hint = mo.md("⚠️ *enter a name*")
    elif _name and _name in page_list():
        name_hint = mo.md("⚠️ *already exists*")
    else:
        name_hint = mo.md("")
    return (name_hint,)

@app.cell
def page_controls(set_click_empty, ...):  # reads setter only — no self-loop
    def _create(_v):
        if not name:
            set_click_empty(True)   # triggers name_hint_cell, not page_controls
            return
    ...
```

---

## Testing Requirements

| Layer | Tool | Notes |
|-------|------|-------|
| E2E (marimo apps) | Playwright + pytest | `tests/e2e/` |
| Unit (domain logic) | pytest | `tests/unit/` |
| Integration (DB) | pytest | `tests/integration/` |

E2E tests start marimo with `--no-sandbox --headless --no-token` via subprocess.
Use socket polling (`socket.create_connection`) to wait for readiness — not output parsing.
Controlled by `HEADLESS=1` env var; default is headed for developer visibility.

---

## Code Review Checklist

- [ ] Each cell has a single, named concern
- [ ] Runner cells guard with `mo.stop(trigger() is None)`  
- [ ] Trigger-capture pattern used for buttons that start long operations
- [ ] No `print()` — use `logger.*` instead
- [ ] Private variables prefixed with `_`
- [ ] Domain imports inside runner cells with try/except
- [ ] No hardcoded paths or API keys
- [ ] Cell docstrings present
- [ ] Grid JSON committed alongside `.py` if layout changed
- [ ] Grid JSON entry count matches total cell count (setup block + all `@app.cell` defs); adding or removing cells shifts the positioned entries
- [ ] Wiki content mutations (write to disk) also update `document_chunks` in SQLite — otherwise FTS5 search returns stale results. If a feature writes wiki pages without updating the DB, either add the DB update or make the UI read-only and route writes through the ingest pipeline.

### Marimo chat widget — testing prompts

`mo.ui.chat(prompts=[...])` stores suggested prompts in the `data-prompts` attribute of the `<marimo-chatbot>` custom element. The actual prompt buttons render inside the Shadow DOM, which Playwright `text=` locators cannot reach.

In E2E tests, assert on the attribute directly:

```python
chatbot = page.locator("marimo-chatbot").first
prompts = json.loads(chatbot.get_attribute("data-prompts") or "[]")
assert len(prompts) > 0
```
