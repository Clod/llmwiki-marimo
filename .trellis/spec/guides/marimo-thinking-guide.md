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

---

## Before Showing Live Progress From a Background Operation

Long work (LLM calls, ingestion) runs in a `mo.Thread` and reports via a state
setter (`set_log_lines`). Two traps make the progress panel look frozen mid-run:

- [ ] Does any cell **block the kernel** with a poll-loop, e.g.
      `with mo.status.spinner(): while running_op() is not None: time.sleep(0.1)`?
      → It holds the single kernel thread for the whole operation, so every reactive
        re-render — including the progress panel the worker thread is updating —
        queues up and only flushes when the op ends. The panel appears to "sleep".
        **Never block the kernel just to show a spinner.**
- [ ] Are you relying on the worker thread's `set_state` *alone* to repaint the panel?
      → Background-thread UI updates are buffered, so the panel may not stream even
        without a blocking cell. Drive the repaint from the frontend instead.

**Fix — a 1s `mo.ui.refresh` mounted only while an op runs:**

```python
@app.cell
def auto_refresh(mo, running_op):
    # Frontend-driven ticker; mounted only while running (idle → no polling).
    auto_refresh = mo.ui.refresh(default_interval="1s") if running_op() is not None else None
    auto_refresh if auto_refresh is not None else mo.md("")
    return (auto_refresh,)

@app.cell(column=1)
def activity_log(mo, log_lines, auto_refresh):
    if auto_refresh is not None:
        auto_refresh.value          # depend on the tick → repaint each interval
    _lines = log_lines()
    mo.md("\n".join(f"- {l}" for l in _lines))
```

- The tick comes from the **frontend**, so the panel repaints independently of the
  worker thread — *as long as no other cell is blocking the kernel*.
- Show a **non-blocking** indicator (`mo.md("⏳ Running…")` gated on `running_op()`),
  not a `mo.status.spinner` poll-loop.
- Fill long *silent* steps too: thread a `progress_cb` into the slow callee (e.g. the
  pairwise LLM lint) so it emits a line per unit of work — otherwise even a live panel
  shows nothing for the duration and the user thinks it hung.
- Exception: a short **synchronous** op done inside the cell itself (e.g. a quick
  delete) can still use `with mo.status.spinner(): ...`. The rule is only about
  background-threaded work whose progress another cell must display.

**Keep the newest line in view (CSS, no JS):** a streaming log wants a fixed-height,
scrollable panel that auto-sticks to the bottom. `mo` has no scroll container, so wrap
the rendered markdown in an `mo.Html` div and use `flex-direction: column-reverse` —
it pins the scroll to the bottom of a chronological list with no JS, and it re-pins on
every auto-refresh repaint:

```python
_body = mo.md("\n".join(f"- {l}" for l in _lines)) if _lines else mo.md("_No activity yet._")
_scroll = mo.Html(
    '<div style="display:flex; flex-direction:column-reverse; '
    'max-height:14em; overflow-y:auto; padding-right:8px;">'
    f'{_body.text}'        # `.text` = the rendered HTML of an mo.md object
    '</div>'
)
```

- The list stays in normal (chronological) order; `column-reverse` only moves the
  scroll anchor to the bottom, so the newest line is always visible.
- Use `max-height` (not fixed `height`) so a short log doesn't leave a big empty box;
  note that with few lines `column-reverse` parks them at the *bottom* of the panel.
- `mo.md(...).text` is the same disk-to-HTML trick `read_app.py`'s `middle_panel` uses
  to embed rendered markdown inside a custom container.

