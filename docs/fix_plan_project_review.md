# Fix Plan: Project Review Remediation (Bugs + Docs)

> Persistent fix plan produced from a thorough project review. Intended for an
> implementing agent (Sonnet) to execute on branch
> `claude/project-review-fix-plan-x6xv2j`. Scope: **verified correctness bugs and
> documentation/config fixes only** — stylistic refactors are out of scope.

## Context

A thorough review of `llmwiki-marimo` (backend Python in `base/`, marimo
notebooks in `marimo/`, docs/specs, tests, config) surfaced a set of real
defects and documentation inconsistencies. This plan covers verified correctness
bugs and documentation/config fixes only — stylistic refactors (e.g. splitting
multi-widget marimo cells, shared UI constants) are explicitly out of scope.

Every item below was verified against the current source. Findings from the
exploration that turned out to be **false positives** (and are deliberately NOT
in this plan) include: "marimo cells must declare `settings`/`mo` as parameters"
(the `with app.setup:` block legitimately provides these to all cells),
"forward-referenced `auto_refresh` cell" (marimo resolves cells by dependency
DAG, not source order), and "SQL injection in deletion.py" (queries are correctly
parameterized). Do not act on those.

Each change should be small and targeted. Run the test suite after backend changes.

---

## Part A — Backend correctness bugs

### A1. LLM response `.content` can be `None` → AttributeError crash (HIGH)

`response.choices[0].message.content` is `None` whenever the model returns a
tool/function call or an empty/filtered completion. Calling `.strip()` or
passing it into `_strip_wrapping_fence(...)` then crashes. The codebase already
uses the safe idiom in one place (`base/domain/ingestion/trace.py:296` →
`resp.choices[0].message.content or ""`), so this is an inconsistency to close.

Apply the `or ""` guard (or equivalent) at every site:

- `base/domain/ingestion/wiki_generator.py:284` — `...content.strip()` → guard before `.strip()`
- `base/domain/ingestion/wiki_generator.py:392, 456, 487, 522` — `_strip_wrapping_fence(response.choices[0].message.content)` → pass `... or ""`
- `base/domain/repair/actions.py:284` — `...content.strip()` → guard
- `base/domain/lint/checks.py:229, 283` — `...content.strip()` → guard

Recommended pattern (matches existing code):
```python
content = response.choices[0].message.content or ""
# then content.strip() / _strip_wrapping_fence(content)
```
Check whether `_strip_wrapping_fence` already tolerates `""`; if a downstream
parser needs to distinguish "empty model output" from a real answer, log a
warning when content is falsy rather than silently proceeding.

### A2. Silent exception swallowing — add logging (MEDIUM)

These handlers discard the exception entirely (no log), which hides real DB /
search failures. Add a `logger.warning(..., exc_info=True)` (or `logger.debug`
where a miss is expected and benign) while preserving the existing
non-crashing fallback behavior. Do **not** change control flow.

- `base/domain/chat/wiki_tools.py:302-304` — `except Exception: pass` after the
  reference-table sync. Keep the file-save resilient, but log the swallowed error.
- `base/domain/tools/search.py:44-45` — `except Exception: return []` (FTS5
  failure). Log before returning `[]`.
- `marimo/read_app.py:164-165` — `except Exception: pass` around the keyword-map
  DB read in `left_panel`. Log (use the module `logger`, which exists in the
  setup block).
- `marimo/ingest_app.py:303-304` — `except Exception: pass` in
  `_PanelHandler.emit`. This one is inside a logging handler; guarding against
  recursive logging is reasonable, so keep it minimal — a comment is acceptable
  here, or drop to `logging.Handler.handleError`. Lowest priority of the four.

(Leave the `except Exception:` handlers that already log — e.g. `trace.py`,
`pipeline.py:459` — untouched.)

---

## Part B — Marimo notebook functional bug

### B1. Delete success/failure callouts never render (MEDIUM, user-visible)

`marimo/read_app.py` `delete_runner` cell, lines 259-266:
```python
    try:
        _delete_page(...)
        ...
        mo.callout(mo.md(f"✅ Deleted `{page_stem}`"), kind="success")   # 263
    except Exception as exc:
        mo.callout(mo.md(f"❌ Deletion failed: {exc}"), kind="danger")    # 265
    return
```
A marimo cell renders only its **last expression**. Here the callouts are bare
statements whose values are discarded, and the cell ends with `return`, so the
user gets **no feedback** on delete success or failure.

Fix: assign the callout to a variable in both branches and make it the cell's
final expression (remove the bare `return`):
```python
    try:
        _delete_page(...)
        set_page_list(scan_pages())
        set_selected_page(None)
        _result = mo.callout(mo.md(f"✅ Deleted `{page_stem}`"), kind="success")
    except Exception as exc:
        _result = mo.callout(mo.md(f"❌ Deletion failed: {exc}"), kind="danger")
    _result
```
Verify the surrounding cell signature/`return` tuple still satisfies marimo
(no other cell consumes a variable from this cell, so dropping `return` is safe —
confirm by reading the full cell).

---

## Part C — Documentation & config fixes

All verified against current files.

### C1. `CLAUDE.md` — duplicate skill rows
The Active Skills table lists `before-backend-dev` and `check-backend` a second
time (unprefixed) in addition to the `trellis:`-prefixed entries. Remove the two
duplicate unprefixed rows, keeping the `trellis:`-prefixed versions.

### C2. `.trellis/spec/backend/quality-guidelines.md:135` — non-existent test dir
`uv run pytest tests/unit/ tests/integration/ -v` references `tests/integration/`,
which does not exist. Real dirs are `tests/unit/`, `tests/regression/`,
`tests/e2e/`. Change to `uv run pytest tests/unit/ tests/regression/ -v`.

### C3. `.trellis/spec/backend/quality-guidelines.md:97` — broken doc reference
"See `type-safety.md` for details." — no `type-safety.md` exists under
`.trellis/spec/backend/` (only the frontend has one). Either inline a one-line
type-annotation guideline here, or repoint to the frontend file with the correct
relative path. Recommended: inline a brief note and drop the dangling link.

### C4. `.trellis/spec/backend/directory-structure.md:73` — stale test count
Line reads `├── unit/  # 137 tests ...`. This is outdated. **Recompute** the
real number with `uv run pytest tests/unit --collect-only -q` and update the
comment to match (do not hardcode a guessed value).

### C5. Inconsistent pytest flag across docs (LOW)
`README.md` and `docs/uat_test_plan.md` use `-q`; `CONTRIBUTING.md` uses `-v`
for the same `tests/unit tests/regression` command. Standardize on `-q` (matches
README and CI default) in `CONTRIBUTING.md`.

### C6. Document ad-hoc env vars (LOW)
`WIKI_HOME`, `WIKI_AUTOCOMMIT`, `WIKI_DEBUG`, `WIKI_TRACE` appear in
`.env.example` and are read directly via `os.environ.get()` in the marimo apps,
but are not part of the pydantic `Settings` in `base/config.py`. Add a short
comment block in `base/config.py` noting these are handled ad-hoc by the marimo
apps (not via pydantic-settings), so the divergence is intentional and discoverable.

---

## Out of scope (do NOT do)
- Splitting multi-widget marimo cells (e.g. `action_buttons` 3 buttons) — style/
  responsiveness guideline, deferred.
- Shared UI constants / config-tunable temperatures / schema numbering — design
  choices, not bugs.
- The i18n `regenerate_wiki_pages(language=...)` "not localized" item — documented
  v1 limitation, not a defect.

---

## Verification

1. **Lint/format**: `uv run ruff check .` (config ignores E501) — must stay clean.
2. **Backend unit + regression tests**: `uv run pytest tests/unit tests/regression -q`
   — all green. These cover ingestion, lint, repair, chat tools, db.
3. **Targeted A1 check**: add/adjust a unit test (or use existing fake LLM in
   `tests/helpers/fake_llm.py`) to return a response whose `message.content` is
   `None`, and assert `build_wiki_page`/extraction/lint/repair handle it without
   raising. Reuse the `FakeLLMClient` pattern already in `tests/unit/`.
4. **E2E (covers the marimo apps incl. delete flow)**: run the `test-ingest`
   then `test-read` skills (or `test-all`) in headless mode. The read-app E2E in
   `tests/e2e/test_read_app.py` exercises page rendering; confirm B1 doesn't break
   the `delete_runner` cell (app still loads, delete still works, and a callout
   now renders).
5. **Docs**: re-grep to confirm no remaining references to `tests/integration/`
   or `type-safety.md` from backend specs; confirm the updated test count matches
   `--collect-only`.

## Commit / branch
- Develop on `claude/project-review-fix-plan-x6xv2j` (create locally if needed).
- Suggested grouping: one commit for Part A (backend), one for Part B (notebook),
  one for Part C (docs/config). Push with `git push -u origin claude/project-review-fix-plan-x6xv2j`.
- Do **not** open a PR unless explicitly requested.
