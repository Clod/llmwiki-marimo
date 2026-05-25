# Component Guidelines

> Conventions for structuring Marimo notebook cells in this project.

---

## Overview

In Marimo, the equivalent of a "component" is a **cell** decorated with
`@app.cell`.  Cells have inputs (function parameters) and outputs (return
values).  Marimo builds a reactive DAG from these declarations.

---

## Cell Structure Conventions

### One concern per cell

Each cell should have a single, clear purpose:

| Cell name | Responsibility |
|-----------|---------------|
| `setup` / `app.setup` | Load env vars, create API client |
| `wiki_helpers` | Pure helper functions (read/write/scan) |
| `page_state` | `mo.state` for page list |
| `edit_state` | `mo.state` for edit mode + content version |
| `current_page` | Compute selected stem + load content from disk |
| `edit_panel` | Create editor widget + action buttons |
| `left_panel` | Render navigation sidebar (column 0) |
| `middle_panel` | Render content viewer / editor (column 1) |
| `chat_panel` | Define LLM respond function + render chat (column 2) |

### Multi-column grid layout (preferred over `mo.hstack`)

For side-by-side panels, use marimo's grid layout instead of a monolithic
assembler cell.  The workflow:

1. Write each panel as a normal cell (no `column=` needed initially)
2. Run `uv run marimo edit <file> --no-sandbox`
3. Drag cells into columns in the browser
4. Marimo writes `layouts/<name>.grid.json` and sets `layout_file=` in
   `marimo.App(...)` automatically — commit both files

```python
app = marimo.App(width="full", layout_file="layouts/read_app.grid.json")

@app.cell
def left_panel(mo, page_selector, ...):
    ...   # renders in column 0 per the layout file

@app.cell
def middle_panel(mo, editor, ...):
    ...   # renders in column 1

@app.cell
def chat_panel(mo, client, ...):
    ...   # renders in column 2
```

Each panel cell re-runs independently when only its own dependencies change —
much better reactivity than a single assembler cell that re-runs on any change.

---

## Import Rules

### Don't import the same name in multiple cells

Marimo tracks every name defined inside a cell as a "variable".  If two cells
both execute `from pydantic_ai import Agent`, Marimo reports a
`multiple-definitions` error and refuses to run.

**Wrong**:

```python
@app.cell
def setup():
    from pydantic_ai import Agent   # defines Agent
    ...

@app.cell
def create_agent():
    from pydantic_ai import Agent   # ERROR: Agent defined twice
    ...
```

**Correct** — two options:

Option A: import at **module level** (outside all cells):

```python
import marimo
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel

app = marimo.App(width="full")

@app.cell
def setup():
    ...   # no re-import here
```

Option B: import in **one cell and pass as a return value**:

```python
@app.cell
def setup():
    from pydantic_ai import Agent, RunContext
    from pydantic_ai.models.openai import OpenAIModel
    ...
    return ..., Agent, OpenAIModel, RunContext

@app.cell
def create_agent(Agent, OpenAIModel, ...):
    model = OpenAIModel(...)   # received from setup
    agent = Agent(model, ...)
    return agent
```

---

## Return Value Rules

### Never return `None` as a positional placeholder

Marimo's static analysis infers exported names from the **variable names** in
return statements.  Returning a literal `None` in a position has no name, which
can cause Marimo to fail resolving that output in downstream cells.

**Wrong**:

```python
if not is_editing():
    return edit_btn, None, None, None   # positions 2-4 have no name
```

**Correct** — always create every widget unconditionally:

```python
edit_btn   = mo.ui.button(...)
editor     = mo.ui.text_area(...)    # created every time; re-run = fresh widget
save_btn   = mo.ui.button(...)
cancel_btn = mo.ui.button(...)
return edit_btn, editor, save_btn, cancel_btn
```

The cost of creating a widget that is never displayed is negligible; the
benefit is static-analysis correctness and predictable reactive behaviour.

---

## `on_click` Callbacks vs Reactive Handler Cells

Use `on_click` when the button performs a **side effect** (write to disk, call
an API, update state).  Use a reactive handler cell only for lightweight state
that clearly belongs in the DAG.

| Situation | Use |
|-----------|-----|
| Write file to disk | `on_click` callback |
| Call external API | `on_click` callback |
| Update `mo.state` counter | `on_click` callback |
| Filter a displayed list | reactive cell |
| Refresh page list after create | reactive cell with `if btn.value` guard |

---

## Running Marimo

```bash
# Interactive (editable) mode — notebook can be modified
uv run marimo edit marimo_new/read_app.py --no-sandbox

# Run mode — UI is live, notebook source is read-only
uv run marimo run marimo_new/read_app.py --no-sandbox
```

Use `--no-sandbox` to skip marimo's isolated uv environment for inline
script deps — the project `.venv` (created by `uv sync`) has everything.

Both modes support interactive UI.  The difference is only whether the
notebook source file can be edited through the browser.

---

## In-App Navigation

### Never use JavaScript to trigger marimo state

Marimo sanitizes `mo.Html()` content via DOMPurify, which strips `<script>`
tags. Even when scripts execute, they run in an isolated context and cannot
reach React-controlled elements. `mo.Javascript()` does not exist.

**Wrong — all of these fail silently:**

```python
mo.Html("<script>window._nav = () => { ... }</script>")   # script stripped
mo.Javascript("...")                                        # AttributeError
# Dispatching native DOM events on React inputs does not trigger on_change
input.dispatchEvent(new Event('change', { bubbles: true }))
```

**Correct — stay inside marimo's reactive system:**

```python
# In a dedicated cell, create navigation widgets with on_click/on_change
@app.cell
def page_links_nav(mo, re, current_content, page_list, set_selected_page):
    ...
    buttons = [mo.ui.button(label=title, on_click=_make_handler(page)) ...]
    return mo.hstack(buttons, wrap=True)
```

### Create dynamic widgets in their own cell, not inside `main_layout`

Creating a variable number of `mo.ui.*` elements inside `main_layout` (which
re-runs on every state change) causes **React error #62** (hydration mismatch).

**Wrong:**

```python
@app.cell
def main_layout(...):
    nav_buttons = [mo.ui.button(...) for page in links]   # variable count → #62
    mo.hstack([scroll_view] + nav_buttons)
```

**Correct — separate cell, stable dependency:**

```python
@app.cell
def page_links_nav(mo, current_content, ...):
    buttons = [mo.ui.button(...) for ...]
    return mo.hstack(buttons, wrap=True) if buttons else mo.Html("")

@app.cell
def main_layout(..., nav_widget):   # receives pre-built element
    mo.vstack([scroll_view, nav_widget])
```

### Do not change the URL for in-app navigation

Using `href="/?page=slug"` or `mo.query_params()` causes full page reloads:
the chat history resets, state is lost, and any bad URL crashes the app.

**Correct:** call `set_selected_page(stem)` directly. The URL never changes,
the back button exits the app cleanly, and the chat is preserved.

### Watch out for trailing-comma returns

`return (nav_widget,)` is a **tuple** — marimo treats it as multiple outputs.
Use `return nav_widget` (no parentheses or trailing comma).

---

## Button Patterns

### `mo.ui.button().value` is always `None` without `on_click`

In marimo 0.23.x, a button with no `on_click` has `value = None` permanently.
Clicking the button DOES trigger dependent cells to re-run, but the value never
changes, so `if btn.value` is always `False`.

**Wrong:**
```python
ingest_btn = mo.ui.button(label="Ingest")   # value stays None

# This condition never fires:
if ingest_btn.value and upload.value:
    do_ingestion()
```

**Correct — always use `on_click`:**
```python
ingest_btn = mo.ui.button(label="Ingest", on_click=lambda _: set_trigger(time.time()))
```

### Long-running operations: trigger → runner pattern

`on_click` callbacks complete before marimo re-renders, so a long synchronous
operation inside `on_click` blocks the UI with no feedback.

**Correct pattern — button sets a trigger, a separate reactive cell does the work:**

```python
# Cell 1: state + button — on_click just sets a timestamp trigger (fast)
@app.cell
def op_state(mo):
    trigger, set_trigger = mo.state(None)
    return trigger, set_trigger

@app.cell
def button_cell(mo, set_trigger):
    import time
    btn = mo.ui.button(label="Run", on_click=lambda _: set_trigger(time.time()))
    return (btn,)

# Cell 2: reactive runner — placed AFTER main_layout so spinner appears below UI
@app.cell
def runner(mo, trigger, WORKSPACE, DB_PATH, llm_client, set_log_lines):
    mo.stop(trigger() is None)          # skip on initial load
    _msgs = []
    def _cb(msg): _msgs.append(msg)
    with mo.status.spinner(title="Processing…"):
        do_work(WORKSPACE, DB_PATH, llm_client, _cb)
    set_log_lines(_msgs)
```

**Key rules:**
- `mo.stop(condition)` is the cell-level equivalent of an early `return`
- Runner cells must be placed **after** layout cells in the file so
  `mo.status.spinner()` renders below the UI, not above it
- `mo.status.spinner()` is a context manager — it shows during cell execution
  and cannot be embedded inside a layout element from another cell

### Trigger pattern: capture inputs at click time, not at run time

If the runner cell depends on **both** a trigger state and a UI widget (e.g.
`upload`), any change to the widget will re-execute the runner whenever the
trigger is non-None — causing auto-execution without a button click.

**Wrong — `upload` as a direct dependency causes auto-ingest after first click:**
```python
@app.cell
def ingest_runner(mo, ingest_trigger, upload, ...):
    mo.stop(ingest_trigger() is None)   # non-None after first click
    _files = upload.value               # re-runs on every new file drop!
    ...
```

**Correct — embed the files in the trigger at click time:**
```python
# In action_buttons cell — capture upload.value inside on_click:
ingest_btn = mo.ui.button(
    label="Ingest",
    on_click=lambda _: set_ingest_trigger((_t.time(), list(upload.value))),
)

# Runner has NO dependency on `upload` — only fires when trigger changes:
@app.cell
def ingest_runner(mo, ingest_trigger, ...):
    mo.stop(ingest_trigger() is None)
    _, _files = ingest_trigger()   # files captured at button-click time
    ...
```

### `allow_self_loops=True` resets state on every update

`mo.state(initial, allow_self_loops=True)` means the creating cell re-runs when
the state changes. If the creating cell calls `mo.state(initial, ...)` again on
re-run, the state resets to `initial` — losing all accumulated data.

**Wrong:**
```python
@app.cell
def log_state(mo):
    log_lines, set_log_lines = mo.state([], allow_self_loops=True)
    # Every call to set_log_lines() re-runs this cell and resets to []
    return log_lines, set_log_lines
```

**Correct:**
```python
@app.cell
def log_state(mo):
    log_lines, set_log_lines = mo.state([])   # no allow_self_loops
    return log_lines, set_log_lines
```

Only use `allow_self_loops=True` when the creating cell also READS the state
(e.g. a dropdown that modifies its own list).

### Cannot read `.value` in the cell that created the element

```python
# WRONG — RuntimeError at runtime
@app.cell
def upload_cell(mo, SOURCES_DIR):
    upload = mo.ui.file(filetypes=[".pdf"])
    if upload.value:           # ← RuntimeError: cannot access .value here
        save(upload.value)
    return (upload,)

# CORRECT — separate cell reads the value
@app.cell
def handle_upload(upload, SOURCES_DIR):
    if upload.value:
        save(upload.value)
    return
```

---

## Common Mistakes

### Mistake: `edit_switch` instead of `edit_btn`

**Symptom**: Switch state leaks into handler cells as an extra condition, causing
saves to silently fail when the switch state races against the button click.

**Fix**: Replace the edit toggle switch with a button.  The button only appears
in view mode; Save/Cancel buttons replace it in edit mode.  There is no shared
toggle state to check in the handler.

### Mistake: returning `None` placeholders

See the **Return Value Rules** section above.  Symptom: `NameError: name 'x' is not defined`
when a downstream cell tries to use an output that was `None` in one branch.

### Mistake: duplicate imports across cells

See the **Import Rules** section above.  Symptom: Marimo reports
`critical[multiple-definitions]: Variable 'X' is defined in multiple cells`.
