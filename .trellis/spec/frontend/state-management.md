# State Management

> How state is managed in Marimo notebooks in this project.

---

## Overview

This project uses **Marimo** for interactive notebooks. Marimo's reactive model
is fundamentally different from React or Svelte — there is no component tree.
Instead, cells are nodes in a DAG; a cell re-runs whenever any of its declared
inputs (function parameters) change.

State that needs to persist across cell re-runs (e.g. "is the user in edit
mode?") is managed via `mo.state`.

---

## `mo.state` — the reactive state primitive

```python
value, set_value = mo.state(initial)
# value  — a getter callable; cells that list it in their signature re-run when it changes
# set_value — a setter callable; calling it triggers dependents to re-run
```

### Rules

- Create state in a **dedicated cell** with no dependencies so it runs once on startup.
- Cells that need the **current value** include `value` in their signature.
- Cells that only need to **write** the state include `set_value` in their signature
  (they do NOT re-run when the state changes — only readers do).
- Calling `set_value` from an `on_click` callback does NOT re-run the cell that
  called it; Marimo schedules reactive re-runs after the callback returns.

### Example — edit mode

```python
@app.cell
def edit_state(mo):
    is_editing, set_is_editing = mo.state(False)
    return is_editing, set_is_editing
```

---

## Pattern: content_version — force a cell to re-read from disk

When a cell reads from disk (e.g. `read_page(selected_stem)`), it only re-runs
if one of its declared inputs changes.  After a save, the file has been updated
but the inputs are the same — the cell returns stale content.

**Fix**: add a `content_version` counter to the cell's inputs.  After every save,
increment the counter; this forces the cell to re-run and re-read the file.

```python
@app.cell
def edit_state(mo):
    is_editing, set_is_editing = mo.state(False)
    content_version, set_content_version = mo.state(0)
    return is_editing, set_is_editing, content_version, set_content_version

@app.cell
def current_page(page_selector, read_page, content_version):
    content_version()          # reactive dependency — value not used directly
    selected_stem = page_selector.value if page_selector.value != "(no pages)" else ""
    current_content = read_page(selected_stem) if selected_stem else ""
    return selected_stem, current_content
```

In the save callback:

```python
def _save(_v):
    write_page(selected_stem, editor.value)
    set_content_version(content_version() + 1)   # ← triggers re-read
    set_is_editing(False)
```

---

## Pattern: fresh widget on mode change

Because a cell re-runs whenever one of its inputs changes, you can get a
**brand-new widget** (with reset state) simply by declaring a state variable as
an input:

```python
@app.cell
def edit_panel(mo, is_editing, current_content, ...):
    # Every time is_editing changes (True→False or False→True), this cell
    # re-runs and creates a fresh mo.ui.text_area seeded from current_content.
    editor = mo.ui.text_area(value=current_content, rows=28, full_width=True)
    ...
```

This is how the Cancel button discards unsaved edits: the cell re-runs after
`set_is_editing(False)` is called, creating a new editor with the original
content — no explicit reset needed.

---

## Pattern: `on_click` for save operations

Marimo's reactive model has a subtle race: if you put a text-area widget in the
dependency list of a handler cell, the handler can execute before the browser
has sent the latest textarea value.

**Use `on_click`** on the button instead.  The callback is called synchronously
with the live widget value at click time:

```python
def _save(_v):
    write_page(selected_stem, editor.value)   # editor.value is current

save_btn = mo.ui.button(label="💾 Save", kind="success", on_click=_save)
```

### Wrong: reactive handler with editor dependency

```python
# BAD — races between textarea blur event and button click
@app.cell
def handle_save(save_btn, editor, selected_stem, write_page):
    if save_btn.value and selected_stem:
        write_page(selected_stem, editor.value)   # editor.value may be stale
    return
```

### Correct: on_click callback

```python
# GOOD — editor is captured by reference; .value is read at click time
def _save(_v):
    if selected_stem:
        write_page(selected_stem, editor.value)

save_btn = mo.ui.button(label="💾 Save", on_click=_save)
```

---

## Common Mistakes

### Mistake: stale view mode after save

**Symptom**: After saving, switching back to view mode shows the old (pre-save)
content.

**Cause**: The cell that reads from disk does not re-run because none of its
declared inputs changed.

**Fix**: Add a `content_version` counter as described above.

---

### Mistake: unsaved edits visible in view mode

**Symptom**: Switching from edit mode to view mode without saving shows the
user's typed-but-unsaved text.

**Cause**: View mode reads `editor.value` instead of the on-disk content.

**Fix**: View mode must read `current_content` (sourced from disk), not
`editor.value`.  Only use `editor.value` inside the editor panel itself.

---

### Mistake: unsaved edits persist after Cancel → Edit cycle

**Symptom**: User cancels, clicks Edit again — editor still shows the cancelled
edits.

**Cause**: The cell that creates the editor has not re-run, so the old widget
object (with the user's text in its `.value`) is reused.

**Fix**: Make the editor-creating cell depend on `is_editing` (or any state that
changes on cancel) so it re-runs and creates a fresh widget.
