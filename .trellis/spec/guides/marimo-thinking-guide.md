# Marimo Notebook Thinking Guide

> **Purpose**: Ask these questions before editing a Marimo notebook cell.

---

## Before Adding a New Cell

- [ ] Does this cell have one clear responsibility?
- [ ] Am I importing something already imported in another cell?
      → If yes: use module-level import or pass it as a return value.
- [ ] Will every branch of my cell return all the same names?
      → If no: Marimo static analysis will fail. Create all widgets unconditionally.

---

## Before Adding a Button with Side Effects

- [ ] Does the button write to disk, call an API, or update external state?
      → Use `on_click` callback, **not** a reactive handler cell.
- [ ] Does my handler cell have a `mo.ui` widget (text_area, etc.) in its dependency list?
      → Race condition risk. Move the write into `on_click` so the callback reads
        the live widget value, not a potentially stale snapshot.

→ See `frontend/component-guidelines.md` — "on_click vs Reactive Handler Cells"

---

## Before Designing an Edit / View Mode Toggle

- [ ] Will view mode ever need to show content that was just saved?
      → Use a `content_version` counter so the disk-reading cell re-runs after save.
- [ ] Should Cancel discard unsaved changes?
      → Make the editor-creating cell depend on `is_editing`; it re-runs on every
        mode change and creates a fresh widget from disk content.
- [ ] Am I using `editor.value` in view mode?
      → **Don't.** View mode must read from `current_content` (sourced from disk).

→ See `frontend/state-management.md` for the full patterns.

---

## Before Debugging a "Save doesn't work" Issue

1. Check if the handler cell has a `mo.ui` widget as a dependency → race condition.
2. Check if `edit_switch.value` or similar is guarding the save → remove redundant guards.
3. Check if the cell returns `None` placeholders → switch to unconditional widget creation.
4. Add a visible return value from the handler so Marimo treats it as a real node.

---

## Before Debugging Stale Content

1. Does view mode use `editor.value`? → It should use `current_content` instead.
2. Does the disk-reading cell have `content_version` as a dependency?
   → Without it, switching pages and then coming back re-reads; but saving does not.

---

## Before Adding Multi-Path Navigation (Back Button / History)

When multiple UI paths (table click, link button, back button) all change
the same page-selection state, route every navigation through a single
`navigate_to()` wrapper so history is always consistent:

```python
# In page_state cell — export navigate_to alongside set_selected_page
def navigate_to(page):
    set_prev_page(selected_page())   # snapshot before the jump
    set_selected_page(page)
```

- `set_selected_page` is still exported for non-navigation writes (e.g. setting to
  `None` after a delete).
- Every button/table `on_change` calls `navigate_to`, not `set_selected_page` directly.
- To sync a table's highlighted row after external navigation, add `selected_page` as
  a cell dependency and pass `initial_selection=[idx]` (row index as `list[int]`) to
  `mo.ui.table`. The cell re-runs on every navigation and rebuilds the table with the
  correct row pre-highlighted; `on_change` does not fire on programmatic selection.

---

## Before Writing a Multi-File Runner Cell That Uses WIKI_TRACE

`ingest_file` owns its own trace scope: when no outer scope is active it creates
a new `trace.jsonl`, finalises it, and resets `_active` to `None` — so a bare loop
produces **one trace file per file**, not one per run.

Always wrap the loop in `trace.run_scope` so all files land in a single trace:

```python
from domain.ingestion.trace import run_scope as _run_scope

with _run_scope(WORKSPACE, DB_PATH):
    for _f in _files:
        _result = _if(_fp, DB_PATH, WORKSPACE, llm_client, llm_model, _cb)
```

`run_scope` is a no-op when `WIKI_TRACE` is unset, so it is always safe to add.
